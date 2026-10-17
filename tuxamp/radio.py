"""Internet radio: SHOUTcast / Icecast streams and the SHOUTcast directory.

A Stream connects to a station (following .pls / .m3u playlists and
redirects), reads the in-band "ICY" song titles, and decodes the audio to
16-bit stereo PCM at the mixer's rate on its own thread, ready for the
engine's chunked playback (so the EQ, balance and visualizers work on radio
too). Stations are spoken to with plain HTTP/1.0 over a socket, because older
SHOUTcast servers answer "ICY 200 OK", which Python's HTTP client rejects.

MP3 (most SHOUTcast stations) is decoded with SDL_mixer's own decoder,
through pygame, in pieces of about half a second. Each piece is decoded
together with the few frames before it (which rebuild the decoder's state)
and a couple after it (which the resampler swallows), and only the part in
between is kept, so the pieces join seamlessly. Other formats (AAC, Ogg,
HLS) are decoded with ffmpeg when it is installed.
"""

import collections
import io
import json
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import numpy as np
import pygame

USER_AGENT = "TuxAmp"                 # the app sets this to "TuxAmp/<version>"
DIRECTORY = "https://directory.shoutcast.com"
TUNEIN = "http://yp.shoutcast.com/sbin/tunein-station.pls?id={}"
MAX_BUFFER = 120.0                    # seconds of decoded audio held while paused
FORMATS = {"audio/mpeg": "MP3", "audio/aacp": "AAC+", "audio/aac": "AAC", "audio/ogg": "OGG",
           "application/ogg": "OGG", "audio/opus": "OPUS", "audio/flac": "FLAC"}
PLAYLIST_TYPES = ("audio/x-scpls", "audio/scpls", "audio/x-mpegurl", "audio/mpegurl",
                  "application/x-mpegurl", "application/vnd.apple.mpegurl",
                  "application/pls+xml", "application/xspf+xml")
MP3_TYPES = ("audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg", "audio/x-mp3", "audio/mpg")


def is_url(s):
    return isinstance(s, str) and re.match(r"(?i)^(https?|icy)://\S+$", s.strip()) is not None


def ffmpeg():
    return shutil.which("ffmpeg")


class StreamError(Exception):
    pass


# ================================================================ HTTP
def connect(url, timeout=10.0, icy=True):
    """GET url over HTTP/1.0, following redirects. Returns (file, headers, final url)
    with the body ready to read from file; headers have lower-case names."""
    for _ in range(6):
        u = urllib.parse.urlsplit(re.sub(r"(?i)^icy://", "http://", url.strip()))
        if u.scheme not in ("http", "https") or not u.hostname:
            raise StreamError("not a web address")
        port = u.port or (443 if u.scheme == "https" else 80)
        try:
            sock = socket.create_connection((u.hostname, port), timeout=timeout)
            if u.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            req = (f"GET {path} HTTP/1.0\r\nHost: {u.netloc}\r\nUser-Agent: {USER_AGENT}\r\n"
                   "Accept: */*\r\n" + ("Icy-MetaData: 1\r\n" if icy else "") + "Connection: close\r\n\r\n")
            sock.sendall(req.encode("latin-1", "replace"))
            f = sock.makefile("rb")
            sock.close()                  # the file keeps the connection open
            line = f.readline(1024).decode("latin-1").strip()
            headers = {}
            while True:
                h = f.readline(8192).decode("latin-1")
                if h in ("\r\n", "\n", ""):
                    break
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
        except ssl.SSLError as e:
            raise StreamError(f"secure connection failed ({e.reason or e})")
        except socket.timeout:
            raise StreamError("the station did not answer")
        except OSError as e:
            raise StreamError(e.strerror or str(e))
        m = re.match(r"(?:HTTP/\d(?:\.\d)?|ICY)\s+(\d{3})\s*(.*)", line)
        if not m:
            f.close()
            raise StreamError("not a radio station")
        status = int(m.group(1))
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            f.close()
            new = urllib.parse.urljoin(url, headers["location"])
            if new == url or urllib.parse.urlsplit(new).path.lower().endswith((".html", ".htm")):
                # SHOUTcast sends listeners to its web page when this stream is off the air
                raise StreamError("the station is not on the air at this address")
            url = new
            continue
        if status != 200:
            f.close()
            raise StreamError(f"the station said {status} {m.group(2)}".strip())
        return f, headers, url
    raise StreamError("too many redirects")


def parse_playlist(text, base=""):
    """Stream addresses (with titles) in a .pls, .m3u or .xspf playlist."""
    out = []
    if "[playlist]" in text[:200].lower():
        titles = dict(re.findall(r"(?im)^title(\d+)\s*=\s*(.*?)\s*$", text))
        for n, u in re.findall(r"(?im)^file(\d+)\s*=\s*(\S+)\s*$", text):
            out.append((urllib.parse.urljoin(base, u), titles.get(n, "")))
    elif "<playlist" in text[:500].lower():
        for u in re.findall(r"<location>\s*([^<\s]+)\s*</location>", text):
            out.append((urllib.parse.urljoin(base, u), ""))
    else:
        title = ""
        for ln in text.splitlines():
            ln = ln.strip()
            if ln.upper().startswith("#EXTINF:"):
                title = ln.split(",", 1)[1].strip() if "," in ln else ""
            elif ln and not ln.startswith("#"):
                out.append((urllib.parse.urljoin(base, ln), title))
                title = ""
    return [(u, t) for u, t in out if is_url(u)]


def parse_icy_title(meta):
    """StreamTitle from an ICY metadata block (bytes)."""
    raw = meta.rstrip(b"\0")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    m = re.search(r"StreamTitle='(.*?)';(?:\s*\w+=|\s*$)", text, re.S) or re.search(r"StreamTitle='(.*)'", text, re.S)
    return m.group(1).strip() if m else None


# ================================================================ MP3 decoding
_BITRATES = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
             2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3_header(b, i):
    """(frame length, sample rate, samples per frame, key) of the MPEG audio
    layer III frame header at b[i], or None."""
    if b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
        return None
    ver, layer = (b[i + 1] >> 3) & 3, (b[i + 1] >> 1) & 3
    bri, sri, pad = b[i + 2] >> 4, (b[i + 2] >> 2) & 3, (b[i + 2] >> 1) & 1
    if ver == 1 or layer != 1 or not 0 < bri < 15 or sri == 3:
        return None
    sr = _RATES[ver][sri]
    br = _BITRATES[1 if ver == 3 else 2][bri] * 1000
    length = (144 if ver == 3 else 72) * br // sr + pad
    return length, sr, 1152 if ver == 3 else 576, (ver, sri, b[i + 3] >> 6)


class Mp3Decoder:
    """Turns an MP3 byte stream into PCM pieces at the mixer's rate."""
    N, OV, TAIL = 24, 4, 2       # frames kept per piece, frames before / after it
    MATCH, SEARCH, FADE = 1024, 48, 64

    def __init__(self, rate):
        self.rate = rate
        self.buf = bytearray()
        self.frames = []          # frames not yet decoded, starting at frame number self.first
        self.first = 0
        self.key = None           # header fields that stay the same in a stream
        self.samplerate = None
        self.spf = 1152
        self.prev = None          # end of the audio given out so far (for matching the next piece)
        self.ext = None           # what the previous piece decoded just after its end

    def _split(self):
        b, i = self.buf, 0
        while i + 4 <= len(b):
            h = _mp3_header(b, i)
            if h is None or (self.key is not None and h[3] != self.key):
                i += 1
                continue
            n = h[0]
            if i + n + 4 > len(b):
                break                 # wait for the rest of the frame (and the next header)
            if self.key is None:
                nh = _mp3_header(b, i + n)
                if nh is None or nh[3] != h[3]:
                    i += 1            # not really a frame: the next one must follow on
                    continue
                self.key, self.samplerate, self.spf = h[3], h[1], h[2]
            self.frames.append(bytes(b[i:i + n]))
            i += n
        del b[:i]

    def _pos(self, f):
        return f * self.spf * self.rate // self.samplerate

    def _join(self, pcm, x):
        """Where in pcm the audio given out so far ends: about x, found exactly by
        matching the overlap (a resampler's timing is never quite the nominal one)."""
        if self.prev is None:
            return x
        m = self.prev.astype(np.int32)
        best = None
        for s in range(-self.SEARCH, self.SEARCH + 1):
            a = x + s - len(m)
            if a < 0 or x + s > len(pcm):
                continue
            d = int(np.abs(pcm[a:x + s].astype(np.int32) - m).sum())
            if best is None or d < best[0]:
                best = (d, s)
        return x + (best[1] if best else 0)

    def feed(self, data):
        """Add MP3 bytes; returns a list of int16 (n, 2) arrays."""
        self.buf += data
        self._split()
        out = []
        while len(self.frames) >= self.OV + self.N + self.TAIL:
            k = self.first + self.OV     # first frame given out from this piece
            take = self.frames[:self.OV + self.N + self.TAIL]
            pcm = pygame.sndarray.array(pygame.mixer.Sound(file=io.BytesIO(b"".join(take))))
            if pcm.ndim == 1:
                pcm = np.repeat(pcm[:, None], 2, axis=1)
            pcm = pcm[:, :2]
            base = self._pos(k - self.OV)
            a = self._join(pcm, self._pos(k) - base)
            e = a + self._pos(k + self.N) - self._pos(k)
            seg = pcm[a:e].copy()
            if len(seg) < e - a:      # never expected: pad rather than lose time
                seg = np.concatenate([seg, np.zeros((e - a - len(seg), 2), np.int16)])
            if self.ext is not None and len(self.ext) and not np.array_equal(seg[:len(self.ext)], self.ext):
                # smooth over any sub-sample difference where the pieces meet
                f = len(self.ext)
                r = (np.arange(f, dtype=np.float32) / f)[:, None]
                seg[:f] = np.round(self.ext * (1 - r) + seg[:f] * r).astype(np.int16)
            self.prev = seg[-self.MATCH:].copy()
            self.ext = pcm[e:e + self.FADE].copy()
            out.append(np.ascontiguousarray(seg))
            del self.frames[:self.N]
            self.first += self.N
        return out


# ================================================================ streams
class Stream:
    """One station, playing. Read state, error, name, title, bitrate,
    samplerate, codec; take audio with read()."""

    def __init__(self, url, rate):
        self.url = url
        self.rate = rate
        self.stream_url = None        # the address of the audio itself (after playlists)
        self.state = "connecting"     # connecting | playing | ended | error
        self.error = None
        self.name = self.genre = self.title = ""
        self.bitrate = self.samplerate = None
        self.codec = ""
        self.mime = "audio/mpeg"
        self._q = collections.deque()
        self._frames = 0
        self._lock = threading.Lock()
        self._closed = False
        self._file = None
        self._proc = None
        threading.Thread(target=self._run, daemon=True, name="radio").start()

    # ------------------------------------------------ for the engine
    def available(self):
        return self._frames

    def read(self, n):
        """Up to n frames of decoded audio (int16 (k, 2)); k may be 0."""
        parts, got = [], 0
        with self._lock:
            while self._q and got < n:
                a = self._q[0]
                if len(a) <= n - got:
                    parts.append(self._q.popleft())
                else:
                    parts.append(a[:n - got])
                    self._q[0] = a[n - got:]
                got += len(parts[-1])
            self._frames -= got
        if not parts:
            return np.zeros((0, 2), np.int16)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def close(self):
        self._closed = True
        for x in (self._file, self._proc):
            try:
                if x is self._proc and x is not None:
                    x.kill()
                elif x is not None:
                    x.close()
            except Exception:
                pass

    # ------------------------------------------------ worker
    def _push(self, pcm):
        if not len(pcm):
            return
        while self._frames > MAX_BUFFER * self.rate and not self._closed:
            time.sleep(0.1)           # paused: hold back (the station waits, or drops us)
        with self._lock:
            self._q.append(pcm)
            self._frames += len(pcm)
        if self.state == "connecting":
            self.state = "playing"

    def _run(self):
        tries = 0
        played = False
        while not self._closed:
            try:
                self._play_once()
                if self._closed:
                    return
                raise StreamError("the station closed the connection")
            except StreamError as e:
                err = str(e)
            except Exception as e:      # a broken stream must never take TuxAmp down
                err = f"{e.__class__.__name__}: {e}"
            if self._closed:
                return
            played = played or self._frames > 0 or self.state == "playing"
            tries += 1
            if not played or tries > 5:
                self.error = err
                self.state = "error" if not played else "ended"
                return
            time.sleep(min(1.0 * tries, 3.0))   # a dropped station: reconnect

    def _open(self):
        """Connect, following playlists, until we reach audio."""
        url = self.url
        for _ in range(4):
            f, h, final = connect(url)
            ct = h.get("content-type", "").split(";")[0].strip().lower()
            path = urllib.parse.urlsplit(final).path.lower()
            is_list = ct in PLAYLIST_TYPES or (path.endswith((".pls", ".m3u", ".m3u8", ".xspf"))
                                               and not ct.startswith("audio/mpeg"))
            if not is_list and ct.startswith("text/"):
                peek = f.peek(512)[:512]
                is_list = b"[playlist]" in peek.lower() or peek.lstrip().startswith(b"#EXTM3U") \
                    or b"http" in peek[:200]
            if not is_list:
                return f, h, final, ct
            text = f.read(256 * 1024).decode("utf-8", "replace")
            f.close()
            if "#EXT-X-" in text:         # HLS: only ffmpeg can follow it
                return None, h, final, "hls"
            entries = parse_playlist(text, final)
            if not entries:
                raise StreamError("the playlist has no stations in it")
            if not self.name and entries[0][1]:
                self.name = re.sub(r"^\(#\d+ - [\d/]+\)\s*", "", entries[0][1])
            err = None
            deadline = time.monotonic() + 25
            for u, _ in entries[:12]:          # a station's playlist lists its mirrors: try them
                if self._closed or time.monotonic() > deadline:
                    break
                try:
                    f, h, final = connect(u, timeout=5.0)
                    ct = h.get("content-type", "").split(";")[0].strip().lower()
                    if ct in PLAYLIST_TYPES:
                        f.close()
                        url = u
                        break
                    return f, h, final, ct
                except StreamError as e:
                    err = e
            else:
                raise err or StreamError("no station in the playlist answered")
        raise StreamError("too many playlists")

    def _play_once(self):
        f, h, final, ct = self._open()
        self._file = f
        self.stream_url = final
        self.name = h.get("icy-name") or self.name
        self.genre = h.get("icy-genre", self.genre)
        info = dict(re.findall(r"(\w+)=([\d.]+)", h.get("ice-audio-info", "")))
        br = h.get("icy-br") or info.get("bitrate") or info.get("ice-bitrate")
        try:
            self.bitrate = int(float(br.split(",")[0])) if br else self.bitrate
        except ValueError:
            pass
        sr = h.get("icy-sr") or info.get("samplerate") or info.get("ice-samplerate")
        try:
            self.samplerate = int(float(sr)) if sr else self.samplerate
        except ValueError:
            pass
        metaint = int(h.get("icy-metaint", "0") or 0) if h.get("icy-metaint", "0").isdigit() else 0

        if ct.startswith("text/") or ct.endswith(("html", "xml", "json")):
            raise StreamError("this address is a web page, not a radio stream")
        if ct == "hls" or (ct not in MP3_TYPES and ct not in ("", "application/octet-stream")):
            self.codec = "HLS" if ct == "hls" else FORMATS.get(ct, ct.split("/")[-1].upper())
            if not ffmpeg():
                raise StreamError(f"this station sends {self.codec} audio, which needs ffmpeg "
                                  "(sudo apt install ffmpeg)")
            self.mime = ct if ct != "hls" else "application/x-mpegurl"
            self._via_ffmpeg(f, metaint, final if ct == "hls" else None)
            return
        self.codec, self.mime = "MP3", "audio/mpeg"
        dec = Mp3Decoder(self.rate)
        for data in self._audio(f, metaint):
            for pcm in dec.feed(data):
                self._push(pcm)
            if dec.samplerate:
                self.samplerate = dec.samplerate
            if self._closed:
                return
            if len(dec.buf) > 256 * 1024:
                raise StreamError("this station does not send MP3 audio")

    def _audio(self, f, metaint):
        """The audio bytes, with the ICY song titles taken out."""
        while not self._closed:
            try:
                data = f.read(metaint) if metaint else f.read1(16384)
            except (OSError, ValueError) as e:
                if self._closed:
                    return
                raise StreamError(getattr(e, "strerror", None) or "connection lost")
            if not data:
                return
            yield data
            if metaint and len(data) == metaint:
                try:
                    n = f.read(1)
                    meta = f.read(n[0] * 16) if n and n[0] else b""
                except (OSError, ValueError):
                    return
                if not n:
                    return
                if meta:
                    t = parse_icy_title(meta)
                    if t is not None:
                        self.title = t

    def _via_ffmpeg(self, f, metaint, url=None):
        args = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
        args += ["-user_agent", USER_AGENT, "-i", url] if url else ["-i", "pipe:0"]
        args += ["-vn", "-f", "s16le", "-ac", "2", "-ar", str(self.rate), "pipe:1"]
        if url and f is not None:
            f.close()
        self._proc = p = subprocess.Popen(args, stdin=subprocess.DEVNULL if url else subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def pump():
            try:
                for data in self._audio(f, metaint):
                    p.stdin.write(data)
            except (OSError, ValueError, StreamError):
                pass
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
        if not url:
            threading.Thread(target=pump, daemon=True, name="radio-pump").start()
        try:
            while not self._closed:
                b = p.stdout.read(4096 * 4)
                if not b:
                    break
                b = b[:len(b) // 4 * 4]
                self._push(np.frombuffer(b, np.int16).reshape(-1, 2).copy())
        finally:
            p.kill()
            p.wait()


# ================================================================ SHOUTcast directory
def _station(d):
    fmt = d.get("Format") or ""
    return {"name": (d.get("Name") or "").strip(), "url": TUNEIN.format(d["ID"]),
            "genre": d.get("Genre") or "", "bitrate": d.get("Bitrate"),
            "format": FORMATS.get(fmt, fmt.split("/")[-1].upper()),
            "listeners": d.get("Listeners"), "now": d.get("CurrentTrack") or ""}


def _directory(path, form):
    req = urllib.request.Request(DIRECTORY + path, data=urllib.parse.urlencode(form).encode(),
                                 headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.load(r)
    except (OSError, ValueError) as e:
        raise StreamError(f"the SHOUTcast directory did not answer ({getattr(e, 'reason', e)})")
    return [_station(d) for d in data if d.get("ID") and d.get("Name")]


def directory_search(query):
    """Stations in the SHOUTcast directory matching query (name, genre or song)."""
    return _directory("/Search/UpdateSearch", {"query": query})


def directory_top():
    """The SHOUTcast directory's most-listened stations right now."""
    return _directory("/Home/Top", {})
