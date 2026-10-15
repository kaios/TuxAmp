"""Casting: play TuxAmp's audio on smart devices on the local network.

Two kinds of device are supported, using only the Python standard library:

* Google Cast (Chromecast, Google / Nest speakers, Android / Google TV and the
  many TVs and soundbars with Chromecast built in): found with mDNS and
  controlled over the Cast v2 protocol.
* DLNA / UPnP media renderers (most smart TVs, Sonos, Kodi, many AV
  receivers): found with SSDP and controlled over UPnP AVTransport.

TuxAmp serves the audio itself from a small HTTP server. A track that was
decoded in memory is sent as WAV with the EQ and balance applied, so the
device plays what TuxAmp would play. Every seek starts a new stream at the
seek position, which works on all devices, even ones that can't seek.
"""

import collections
import http.server
import json
import os
import queue
import re
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np

HTTP_PORT = 47900              # preferred port for the media server (any free port if taken)


class CastError(Exception):
    pass


def local_ip_for(host):
    """The address of this computer on the network that reaches host."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


# ====================================================================== devices
class Device:
    def __init__(self, kind, name, host, port=None, uid=None, model="",
                 av_url=None, av_type=None, rc_url=None, rc_type=None):
        self.kind = kind            # "cast" | "dlna"
        self.name = name
        self.host = host
        self.port = port
        self.uid = uid or f"{kind}:{host}:{port}"
        self.model = model
        self.av_url, self.av_type = av_url, av_type
        self.rc_url, self.rc_type = rc_url, rc_type

    @property
    def key(self):
        return (self.kind, self.uid)

    @property
    def label(self):
        return f"{self.name}  ({'Chromecast' if self.kind == 'cast' else 'DLNA'})"

    def __repr__(self):
        return f"<Device {self.label} {self.host}:{self.port}>"


def discover(timeout=3.0):
    """Search the local network for Chromecast and DLNA devices."""
    found = {}
    lock = threading.Lock()

    def run(fn):
        try:
            devs = fn(timeout)
        except OSError:
            devs = []
        with lock:
            for d in devs:
                found.setdefault(d.key, d)

    threads = [threading.Thread(target=run, args=(fn,), daemon=True)
               for fn in (discover_cast, discover_dlna)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 4)
    with lock:
        return sorted(found.values(), key=lambda d: (d.name.lower(), d.kind))


# --------------------------------------------------------------- mDNS (Cast)
CAST_SERVICE = "_googlecast._tcp.local"


def _dns_name(buf, i):
    labels, end, hops = [], None, 0
    while True:
        n = buf[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0 == 0xC0:
            if end is None:
                end = i + 2
            i = ((n & 0x3F) << 8) | buf[i + 1]
            hops += 1
            if hops > 30:
                raise ValueError("DNS name loop")
            continue
        labels.append(buf[i + 1:i + 1 + n].decode("utf-8", "replace"))
        i += 1 + n
    return ".".join(labels), (end if end is not None else i)


def _dns_query(name, qtype=12):
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    # 0x8001: class IN with the "unicast response" bit
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + q + struct.pack(">HH", qtype, 0x8001)


def _dns_records(buf):
    """Yield (name, type, data) for every resource record in a DNS message."""
    _, _, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
    i = 12
    for _ in range(qd):
        _, i = _dns_name(buf, i)
        i += 4
    for _ in range(an + ns + ar):
        name, i = _dns_name(buf, i)
        rtype, _, _, rdlen = struct.unpack(">HHIH", buf[i:i + 10])
        i += 10
        rd = i
        i += rdlen
        if rtype == 12:                                   # PTR
            yield name, rtype, _dns_name(buf, rd)[0]
        elif rtype == 33:                                 # SRV
            port = struct.unpack(">H", buf[rd + 4:rd + 6])[0]
            yield name, rtype, (port, _dns_name(buf, rd + 6)[0])
        elif rtype == 16:                                 # TXT
            txt, j = {}, rd
            while j < i:
                ln = buf[j]
                k, _, v = buf[j + 1:j + 1 + ln].decode("utf-8", "replace").partition("=")
                txt[k] = v
                j += 1 + ln
            yield name, rtype, txt
        elif rtype == 1 and rdlen == 4:                   # A
            yield name, rtype, socket.inet_ntoa(buf[rd:rd + 4])


def parse_cast_replies(packets):
    """Build Chromecast devices from mDNS answers: [(bytes, sender_ip), ...]."""
    ptr, srv, txt, addr, src = set(), {}, {}, {}, {}
    for data, ip in packets:
        try:
            for name, rtype, val in _dns_records(data):
                low = name.lower()
                if rtype == 12 and low == CAST_SERVICE:
                    ptr.add(val)
                elif rtype == 33:
                    srv[name] = val
                    src.setdefault(name, ip)
                elif rtype == 16:
                    txt[name] = val
                    src.setdefault(name, ip)
                elif rtype == 1:
                    addr[low] = val
        except (ValueError, IndexError, struct.error):
            continue
    devs = []
    for inst in ptr:
        port, target = srv.get(inst, (8009, None))
        host = addr.get((target or "").lower()) or src.get(inst)
        if not host:
            continue
        t = txt.get(inst, {})
        name = t.get("fn") or inst.split(".")[0]
        devs.append(Device("cast", name, host, port, uid=t.get("id") or inst, model=t.get("md", "")))
    return devs


def discover_cast(timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.25)
    packets = []
    try:
        s.bind(("", 0))
        q = _dns_query(CAST_SERVICE)
        end = time.monotonic() + timeout
        sends = [0.0, 0.8, 2.0]
        start = time.monotonic()
        while time.monotonic() < end:
            if sends and time.monotonic() - start >= sends[0]:
                sends.pop(0)
                try:
                    s.sendto(q, ("224.0.0.251", 5353))
                except OSError:
                    pass
            try:
                data, (ip, _) = s.recvfrom(9000)
                packets.append((data, ip))
            except socket.timeout:
                pass
    finally:
        s.close()
    return parse_cast_replies(packets)


# --------------------------------------------------------------- SSDP (DLNA)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def parse_dlna_description(xml_bytes, location):
    """Build DLNA renderers from a UPnP device description."""
    root = _strip_ns(ET.fromstring(xml_bytes))
    base = (root.findtext("URLBase") or "").strip() or location
    host = urllib.parse.urlparse(location).hostname
    devs = []
    for dev in root.iter("device"):
        if "MediaRenderer" not in (dev.findtext("deviceType") or ""):
            continue
        av = rc = None
        for svc in dev.iter("service"):
            st = (svc.findtext("serviceType") or "").strip()
            url = urllib.parse.urljoin(base, (svc.findtext("controlURL") or "").strip())
            if ":AVTransport:" in st and av is None:
                av = (url, st)
            elif ":RenderingControl:" in st and rc is None:
                rc = (url, st)
        if av is None:
            continue
        name = (dev.findtext("friendlyName") or host).strip()
        devs.append(Device("dlna", name, host, uid=(dev.findtext("UDN") or location).strip(),
                           model=(dev.findtext("modelName") or "").strip(),
                           av_url=av[0], av_type=av[1],
                           rc_url=rc[0] if rc else None, rc_type=rc[1] if rc else None))
    return devs


def discover_dlna(timeout=3.0):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           f"MX: 2\r\nST: {RENDERER}\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.25)
    locations = []
    try:
        s.bind(("", 0))
        end = time.monotonic() + timeout
        sends = [0.0, 1.0]
        start = time.monotonic()
        while time.monotonic() < end:
            if sends and time.monotonic() - start >= sends[0]:
                sends.pop(0)
                try:
                    s.sendto(msg, ("239.255.255.250", 1900))
                except OSError:
                    pass
            try:
                data, _ = s.recvfrom(9000)
            except socket.timeout:
                continue
            m = re.search(rb"^location:\s*(\S+)", data, re.I | re.M)
            if m:
                loc = m.group(1).decode("latin-1")
                if loc not in locations and loc.startswith("http"):
                    locations.append(loc)
    finally:
        s.close()

    devs, lock = [], threading.Lock()

    def fetch(loc):
        try:
            with urllib.request.urlopen(loc, timeout=3) as r:
                got = parse_dlna_description(r.read(1 << 20), loc)
        except Exception:
            return
        with lock:
            devs.extend(got)

    threads = [threading.Thread(target=fetch, args=(loc,), daemon=True) for loc in locations]
    for t in threads:
        t.start()
    for t in threads:
        t.join(4)
    with lock:
        return list(devs)


# ================================================================ media server
class PcmSource:
    """A WAV stream of decoded audio, starting at frame `start`."""
    mime, ext = "audio/wav", "wav"

    def __init__(self, pcm, rate, start, gains=None, render=None):
        self.pcm, self.rate = pcm, rate
        self.render = render                # (first frame, count) -> int16 frames, e.g. with EQ
        self.ch = pcm.shape[1]
        self.start = max(0, min(start, len(pcm)))
        bpf = self.ch * 2
        self.frames = min(len(pcm) - self.start, (0xFFFFFFFF - 36) // bpf)
        self.offset = self.start / rate     # where the stream starts in the track
        self.start_time = 0.0               # where the device should start in the stream
        self.duration = self.frames / rate
        self.size = 44 + self.frames * bpf
        self.gains = gains                  # () -> (left, right), read live

    def _header(self):
        n = self.frames * self.ch * 2
        return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + n, b"WAVE", b"fmt ", 16, 1,
                           self.ch, self.rate, self.rate * self.ch * 2, self.ch * 2, 16, b"data", n)

    def read(self, off, n):
        out = b""
        if off < 44:
            out = self._header()[off:off + n]
            off, n = off + len(out), n - len(out)
        if n <= 0 or off >= self.size:
            return out
        bpf = self.ch * 2
        d = off - 44
        f0, f1 = d // bpf, min(self.frames, -(-(d + n) // bpf))
        if self.render is not None:
            seg = self.render(self.start + f0, f1 - f0)
        else:
            seg = self.pcm[self.start + f0:self.start + f1]
        g = self.gains() if self.gains else None
        if g and self.ch == 2 and tuple(g) != (1.0, 1.0):
            seg = (seg.astype(np.float32) * np.asarray(g, np.float32)).astype(np.int16)
        cut = d - f0 * bpf
        return out + np.ascontiguousarray(seg).tobytes()[cut:cut + n]


FILE_TYPES = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
}


class FileSource:
    """The original file, for tracks TuxAmp streams instead of decoding."""

    def __init__(self, path, start_time=0.0, duration=0.0):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        self.mime = FILE_TYPES.get(ext, "application/octet-stream")
        self.ext = ext.lstrip(".") or "bin"
        self.size = os.path.getsize(path)
        self.offset = 0.0               # the device reports positions in the whole track
        self.start_time = start_time
        self.duration = duration

    def read(self, off, n):
        with open(self.path, "rb") as f:
            f.seek(off)
            return f.read(n)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TuxAmp"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self._serve(False)

    def do_GET(self):
        self._serve(True)

    def _serve(self, body):
        try:
            src = self.server.sources.get(self.path.split("?")[0])
            if src is None:
                self.send_error(404)
                return
            size = src.size
            start, end, status = 0, size - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", src.mime)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("contentFeatures.dlna.org",
                             "DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            off = start
            while body and off <= end:
                chunk = src.read(off, min(1 << 16, end - off + 1))
                if not chunk:
                    break
                self.wfile.write(chunk)
                off += len(chunk)
        except (OSError, ValueError):
            self.close_connection = True


class MediaServer:
    """Serves the tracks being cast to the devices over HTTP."""

    def __init__(self):
        self._httpd = None
        self._sources = collections.OrderedDict()

    def _start(self):
        if self._httpd is not None:
            return
        for port in (HTTP_PORT, 0):
            try:
                httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _Handler)
                break
            except OSError:
                continue
        httpd.daemon_threads = True
        httpd.sources = self._sources
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True, name="cast-http").start()

    @property
    def port(self):
        self._start()
        return self._httpd.server_address[1]

    def publish(self, src):
        """Make src available; returns its path on the server."""
        self._start()
        path = f"/tuxamp/{uuid.uuid4().hex[:16]}.{src.ext}"
        self._sources[path] = src
        while len(self._sources) > 4:          # devices may still read the previous few
            self._sources.popitem(last=False)
        return path

    def url(self, path, host):
        return f"http://{local_ip_for(host)}:{self.port}{path}"

    def close(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


# ================================================================ Google Cast
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def cast_encode(src, dst, ns, payload):
    """A Cast v2 CastMessage (protobuf), framed with its 4-byte length."""
    def s(num, text):
        b = text.encode()
        return _varint(num << 3 | 2) + _varint(len(b)) + b
    body = (_varint(1 << 3) + _varint(0) + s(2, src) + s(3, dst) + s(4, ns)
            + _varint(5 << 3) + _varint(0) + s(6, json.dumps(payload)))
    return struct.pack(">I", len(body)) + body


def cast_decode(body):
    """Decode a CastMessage into {field_number: value}."""
    fields, i = {}, 0
    while i < len(body):
        key, shift = 0, 0
        while True:
            b = body[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, shift = 0, 0
            while True:
                b = body[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            fields[num] = v
        elif wt == 2:
            ln, shift = 0, 0
            while True:
                b = body[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            fields[num] = body[i:i + ln]
            i += ln
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            raise ValueError("bad protobuf wire type")
    return fields


NS_CONN = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
MEDIA_RECEIVER = "CC1AD845"         # Google's Default Media Receiver app


class _Chromecast:
    """One connection to a Google Cast device. A single thread owns the TLS
    socket (an SSL connection must not be read and written from two threads
    at once); other threads queue what they send."""

    def __init__(self, dev, report):
        self.dev, self.report = dev, report
        self.sock = None
        self.transport = self.app_session = self.msid = None
        self.rid = 0
        self._out = queue.Queue()
        self._cond = threading.Condition()
        self._replies = {}
        self._receiver = {}              # latest RECEIVER_STATUS
        self._closed = False
        self._io_thread = None
        self._last_ping = self._last_status = 0.0

    def open(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE      # Cast devices use self-signed certificates
        raw = socket.create_connection((self.dev.host, self.dev.port or 8009), timeout=5)
        self.sock = ctx.wrap_socket(raw)
        self._send("receiver-0", NS_CONN, {"type": "CONNECT"})
        self._io_thread = threading.Thread(target=self._io, daemon=True, name="cast-io")
        self._io_thread.start()

    def _send(self, dst, ns, payload):
        if self._closed:
            raise CastError("connection to the device was lost")
        self._out.put(cast_encode("sender-0", dst, ns, payload))

    def _command(self, dst, ns, payload):
        self.rid += 1
        payload["requestId"] = self.rid
        self._send(dst, ns, payload)
        return self.rid

    def _request(self, dst, ns, payload, timeout=10.0):
        rid = self._command(dst, ns, payload)
        end = time.monotonic() + timeout
        with self._cond:
            while rid not in self._replies:
                left = end - time.monotonic()
                if self._closed:
                    raise CastError("connection to the device was lost")
                if left <= 0:
                    raise CastError(f"no answer to {payload['type']}")
                self._cond.wait(left)
            return self._replies.pop(rid)

    def _io(self):
        buf = b""
        try:
            while True:
                while True:                          # send everything queued
                    try:
                        data = self._out.get_nowait()
                    except queue.Empty:
                        break
                    if data is None:                 # close() was called
                        return
                    self.sock.settimeout(5)
                    self.sock.sendall(data)
                self.sock.settimeout(0.05)
                try:
                    chunk = self.sock.recv(65536)
                except (socket.timeout, ssl.SSLWantReadError):
                    continue
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= 4:
                    n = struct.unpack(">I", buf[:4])[0]
                    if len(buf) < 4 + n:
                        break
                    body, buf = buf[4:4 + n], buf[4 + n:]
                    self._handle(cast_decode(body))
        except (OSError, ValueError, struct.error):
            pass
        finally:
            with self._cond:
                self._closed = True
                self._cond.notify_all()
            try:
                self.sock.close()
            except OSError:
                pass

    def _handle(self, f):
        ns = f.get(4, b"").decode()
        src = f.get(2, b"").decode()
        try:
            data = json.loads(f.get(6, b"{}"))
        except ValueError:
            return
        kind = data.get("type")
        if ns == NS_HEARTBEAT and kind == "PING":
            self._out.put(cast_encode("sender-0", src, NS_HEARTBEAT, {"type": "PONG"}))
            return
        if ns == NS_CONN and kind == "CLOSE":
            if src == self.transport:            # the player app was closed on the device
                self.transport = self.msid = None
                self.report("stopped", None)
            return
        if ns == NS_MEDIA and kind == "MEDIA_STATUS":
            self._media_status(data)
        rid = data.get("requestId")
        with self._cond:
            if kind == "RECEIVER_STATUS":
                self._receiver = data.get("status") or {}
            if rid:
                self._replies[rid] = data
            self._cond.notify_all()

    def _media_status(self, data):
        for st in data.get("status") or []:
            if self.msid is None or st.get("mediaSessionId") != self.msid:
                continue                     # an earlier track's session
            ps = st.get("playerState")
            if ps == "PLAYING":
                state = "playing"
            elif ps == "PAUSED":
                state = "paused"
            elif ps == "BUFFERING":
                state = "loading"
            else:
                reason = st.get("idleReason")
                state = {"FINISHED": "finished", "ERROR": "error"}.get(reason, "stopped")
                if state == "error":
                    self.report("error", None, "the device could not play this track")
                    return
            self.report(state, st.get("currentTime"))

    def _ensure_app(self):
        if self.transport:
            return
        r = self._request("receiver-0", NS_RECEIVER, {"type": "LAUNCH", "appId": MEDIA_RECEIVER}, 20)
        if r.get("type") != "RECEIVER_STATUS":
            raise CastError(f"the device would not start its media player ({r.get('reason') or r.get('type')})")
        # the player may show up in a later status message than the reply
        end = time.monotonic() + 20
        with self._cond:
            while True:
                for app in self._receiver.get("applications") or []:
                    if app.get("appId") == MEDIA_RECEIVER and app.get("transportId"):
                        self.transport = app["transportId"]
                        self.app_session = app.get("sessionId")
                if self.transport or self._closed or time.monotonic() >= end:
                    break
                self._cond.wait(end - time.monotonic())
        if not self.transport:
            raise CastError("the device did not start its media player")
        self._send(self.transport, NS_CONN, {"type": "CONNECT"})

    def load(self, url, mime, title, duration, start=0.0):
        self._ensure_app()
        self.msid = None
        media = {"contentId": url, "contentUrl": url, "contentType": mime, "streamType": "BUFFERED",
                 "metadata": {"metadataType": 3, "title": title, "albumName": "TuxAmp"}}
        if duration:
            media["duration"] = duration
        r = self._request(self.transport, NS_MEDIA, {"type": "LOAD", "media": media, "autoplay": True,
                                                     "currentTime": start}, 30)
        if r.get("type") != "MEDIA_STATUS" or not r.get("status"):
            raise CastError(f"the device refused the track ({r.get('reason') or r.get('type')})")
        self.msid = r["status"][0].get("mediaSessionId")
        self._media_status(r)

    def _media(self, kind):
        if self.transport and self.msid is not None:
            self._command(self.transport, NS_MEDIA, {"type": kind, "mediaSessionId": self.msid})

    def pause(self):
        self._media("PAUSE")

    def resume(self):
        self._media("PLAY")

    def stop(self):
        self._media("STOP")

    def set_volume(self, v):
        self._command("receiver-0", NS_RECEIVER, {"type": "SET_VOLUME", "volume": {"level": round(v, 3)}})

    def poll(self):
        if self._closed:
            raise CastError("connection to the device was lost")
        now = time.monotonic()
        if now - self._last_ping >= 5:
            self._last_ping = now
            self._send("receiver-0", NS_HEARTBEAT, {"type": "PING"})
        if now - self._last_status >= 2:
            self._last_status = now
            self._media("GET_STATUS")

    def close(self):
        try:
            if self.app_session:             # close the player so the TV goes back to normal
                self._command("receiver-0", NS_RECEIVER, {"type": "STOP", "sessionId": self.app_session})
            self._send("receiver-0", NS_CONN, {"type": "CLOSE"})
        except CastError:
            pass
        self._out.put(None)                  # the I/O thread sends the above, then hangs up
        if self._io_thread is not None:
            self._io_thread.join(2)


# ================================================================ DLNA / UPnP
def _hms(sec):
    sec = max(0.0, sec)
    return f"{int(sec // 3600)}:{int(sec // 60 % 60):02d}:{sec % 60:06.3f}"


def _parse_hms(text):
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) != 3:
        return None
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def didl(url, mime, title, duration):
    dur = f' duration="{_hms(duration)}"' if duration else ""
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="tuxamp" parentID="-1" restricted="1">'
            f'<dc:title>{escape(title)}</dc:title>'
            '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:DLNA.ORG_OP=01;'
            f'DLNA.ORG_FLAGS=01700000000000000000000000000000"{dur}>{escape(url)}</res>'
            '</item></DIDL-Lite>')


def soap(url, service, action, args=(), timeout=5):
    """Call a UPnP action; returns the response's values as a dict."""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args)
    env = ('<?xml version="1.0" encoding="utf-8"?>'
           '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(url, env, {"Content-Type": 'text/xml; charset="utf-8"',
                                            "SOAPAction": f'"{service}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(1 << 20)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = _strip_ns(ET.fromstring(e.read(1 << 16))).findtext(".//errorDescription") or ""
        except ET.ParseError:
            pass
        raise CastError(f"{action} failed ({detail or e.code})") from None
    except OSError as e:
        raise CastError(f"{action} failed ({getattr(e, 'reason', e)})") from None
    root = _strip_ns(ET.fromstring(data))
    resp = root.find(f".//{action}Response")
    return {c.tag: (c.text or "") for c in resp} if resp is not None else {}


class _Dlna:
    STATES = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "PAUSED_RECORDING": "paused",
              "TRANSITIONING": "loading", "STOPPED": "stopped", "NO_MEDIA_PRESENT": "stopped"}

    def __init__(self, dev, report):
        self.dev, self.report = dev, report
        self.loaded = False
        self._last_poll = 0.0

    def _av(self, action, *args):
        return soap(self.dev.av_url, self.dev.av_type, action, (("InstanceID", 0),) + args)

    def open(self):
        self._av("GetTransportInfo")         # check that the device answers

    def load(self, url, mime, title, duration, start=0.0):
        self.loaded = False
        try:
            self._av("Stop")
        except CastError:
            pass                             # nothing was playing
        self._av("SetAVTransportURI", ("CurrentURI", url),
                 ("CurrentURIMetaData", didl(url, mime, title, duration)))
        self._av("Play", ("Speed", "1"))
        if start > 0:
            try:
                self._av("Seek", ("Unit", "REL_TIME"), ("Target", _hms(start).split(".")[0]))
            except CastError:
                pass
        self.loaded = True
        self._last_poll = 0.0

    def pause(self):
        self._av("Pause")

    def resume(self):
        self._av("Play", ("Speed", "1"))

    def stop(self):
        self._av("Stop")

    def set_volume(self, v):
        if self.dev.rc_url:
            soap(self.dev.rc_url, self.dev.rc_type, "SetVolume",
                 (("InstanceID", 0), ("Channel", "Master"), ("DesiredVolume", round(v * 100))))

    def poll(self):
        now = time.monotonic()
        if not self.loaded or now - self._last_poll < 1.0:
            return
        self._last_poll = now
        state = self.STATES.get(self._av("GetTransportInfo").get("CurrentTransportState"), "loading")
        try:
            pos = _parse_hms(self._av("GetPositionInfo").get("RelTime", ""))
        except CastError:
            pos = None
        self.report(state, pos)

    def close(self):
        try:
            if self.loaded:
                self.stop()
        except CastError:
            pass


# ================================================================ session
class CastSession:
    """Plays on one device. The main thread calls load/pause/resume/stop/
    set_volume/close (they return at once; the network work happens on a
    worker thread) and reads state, error and position()."""

    def __init__(self, device, server):
        self.device = device
        self.name = device.name
        self.server = server
        self._lock = threading.Lock()
        self.gen = 0                 # one per load; reports for older loads are ignored
        self._ready_gen = -1
        self.state = "connecting"    # connecting|idle|loading|playing|paused|stopped|finished|error
        self.error = None
        self.offset = 0.0
        self.duration = 0.0
        self._pos = 0.0
        self._at = time.monotonic()
        self._played = False
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cast")
        self._thread.start()

    # ------------------------------------------------ main-thread API
    def load(self, source, title):
        with self._lock:
            self.gen += 1
            self.offset = source.offset
            self.duration = source.duration
            self._pos = source.start_time
            self._at = time.monotonic()
            self._played = False
            if self.state != "error":
                self.state = "loading"
            gen = self.gen
        self._q.put(("load", gen, source, title))

    def pause(self):
        self._q.put(("pause",))

    def resume(self):
        self._q.put(("resume",))

    def stop(self):
        with self._lock:
            self.gen += 1
            if self.state != "error":
                self.state = "stopped"
        self._q.put(("stop",))

    def set_volume(self, v):
        self._q.put(("volume", v))

    def close(self, wait=0.0):
        self._q.put(("close",))
        if wait:
            self._thread.join(wait)

    def position(self):
        """Seconds into the track being played (offset of the stream included)."""
        with self._lock:
            p = self._pos
            if self.state == "playing":
                p += time.monotonic() - self._at
            if self.duration:
                p = min(p, self.duration)
            return self.offset + p

    # ------------------------------------------------ worker thread
    def _report(self, state, pos, error=None):
        with self._lock:
            if self._ready_gen != self.gen or self.state == "error":
                return
            now = time.monotonic()
            if self.state == "playing":           # freeze the running clock first
                self._pos += now - self._at
                self._at = now
            if state == "error":
                self.state, self.error = "error", error or "playback failed"
                return
            if state == "playing":
                self._played = True
            elif state == "stopped":
                if not self._played:
                    return                         # some devices say STOPPED while starting
                near_end = not self.duration or self._pos >= self.duration - 3
                state = "finished" if near_end else "stopped"
            if pos is not None and state != "finished":
                self._pos, self._at = float(pos), now
            self.state = state

    def _fail(self, err):
        with self._lock:
            self.state = "error"
            self.error = str(err) or err.__class__.__name__

    def _run(self):
        try:
            ctl = (_Chromecast if self.device.kind == "cast" else _Dlna)(self.device, self._report)
            ctl.open()
        except (OSError, CastError, ET.ParseError) as e:
            self._fail(f"cannot connect to {self.name}: {getattr(e, 'strerror', None) or e}")
            ctl = None
        else:
            with self._lock:
                if self.state == "connecting":
                    self.state = "idle"
        failures = 0
        while True:
            try:
                cmds = [self._q.get(timeout=0.25)]
            except queue.Empty:
                cmds = []
            while True:
                try:
                    cmds.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if any(c[0] == "close" for c in cmds):
                if ctl is not None:
                    try:
                        ctl.close()
                    except (OSError, CastError):
                        pass
                return
            # only the newest load matters, and only the newest volume:
            # commands before them are out of date
            last = max((i for i, c in enumerate(cmds) if c[0] in ("load", "stop")), default=0)
            vols = [c for c in cmds if c[0] == "volume"][-1:]
            cmds = [c for c in cmds[last:] if c[0] != "volume"]
            for c in vols + cmds:
                if ctl is None or self.state == "error":
                    break
                try:
                    self._do(ctl, c)
                except (OSError, CastError, ET.ParseError, ValueError) as e:
                    self._fail(e)
            if ctl is not None and self.state not in ("error",):
                try:
                    ctl.poll()
                    failures = 0
                except (OSError, CastError, ET.ParseError, ValueError) as e:
                    failures += 1
                    if failures >= 3:
                        self._fail(e)

    def _do(self, ctl, c):
        kind = c[0]
        if kind == "load":
            _, gen, source, title = c
            if gen != self.gen:
                return
            url = self.server.url(self.server.publish(source), self.device.host)
            ctl.load(url, source.mime, title, source.duration, source.start_time)
            with self._lock:
                if gen == self.gen:
                    self._ready_gen = gen
        elif kind == "pause":
            ctl.pause()
        elif kind == "resume":
            ctl.resume()
        elif kind == "stop":
            ctl.stop()
        elif kind == "volume":
            ctl.set_volume(c[1])
