"""Dependency-free tag and stream-info reader for common audio formats.

Returns a dict with any of: title, artist, album, duration (seconds),
bitrate (kbps), samplerate (Hz), channels.
"""

import os
import struct

AUDIO_EXTS = {
    ".mp3", ".mp2", ".wav", ".wave", ".ogg", ".oga", ".opus", ".flac",
    ".mod", ".xm", ".it", ".s3m", ".mid", ".midi", ".aif", ".aiff", ".voc",
}


def is_audio(path):
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def read_info(path):
    info = {}
    try:
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            if ext in (".mp3", ".mp2"):
                _mp3(f, path, info)
            elif ext in (".wav", ".wave"):
                _wav(f, info)
            elif ext == ".flac":
                _flac(f, info)
            elif ext in (".ogg", ".oga", ".opus"):
                _ogg(f, path, info)
    except Exception:
        pass
    return info


def display_title(path, info):
    title = (info.get("title") or "").strip()
    artist = (info.get("artist") or "").strip()
    if title and artist:
        return f"{artist} - {title}"
    if title:
        return title
    return os.path.splitext(os.path.basename(path))[0]


# ---------------------------------------------------------------- ID3 / MP3

def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _dec_text(data):
    if not data:
        return ""
    enc, body = data[0], data[1:]
    try:
        if enc == 0:
            s = body.decode("latin-1")
        elif enc == 1:
            s = body.decode("utf-16")
        elif enc == 2:
            s = body.decode("utf-16-be")
        else:
            s = body.decode("utf-8")
    except Exception:
        s = body.decode("latin-1", "replace")
    return s.replace("\x00", " ").strip()


def _id3v2(f, info):
    f.seek(0)
    hdr = f.read(10)
    if len(hdr) < 10 or hdr[:3] != b"ID3":
        return 0
    ver = hdr[3]
    flags = hdr[5]
    size = _syncsafe(hdr[6:10])
    total = size + 10 + (10 if flags & 0x10 else 0)
    data = f.read(size)
    pos = 0
    if flags & 0x40 and ver >= 3:  # extended header
        if ver == 4:
            pos = _syncsafe(data[0:4])
        else:
            pos = struct.unpack(">I", data[0:4])[0] + 4
    want3 = {b"TIT2": "title", b"TPE1": "artist", b"TALB": "album"}
    want2 = {b"TT2": "title", b"TP1": "artist", b"TAL": "album"}
    while pos < len(data):
        if ver == 2:
            if pos + 6 > len(data):
                break
            fid = data[pos:pos + 3]
            fsize = int.from_bytes(data[pos + 3:pos + 6], "big")
            pos += 6
            key = want2.get(fid)
        else:
            if pos + 10 > len(data):
                break
            fid = data[pos:pos + 4]
            if ver == 4:
                fsize = _syncsafe(data[pos + 4:pos + 8])
            else:
                fsize = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            pos += 10
            key = want3.get(fid)
        if not fid.strip(b"\x00") or fsize <= 0:
            break
        if key:
            info[key] = _dec_text(data[pos:pos + fsize])
        pos += fsize
    return total


_BR = {
    # (version_is_mpeg1, layer): table
    (True, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (True, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (True, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (False, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (False, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_BR[(False, 3)] = _BR[(False, 2)]
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3(f, path, info):
    start = _id3v2(f, info)
    fsize = os.path.getsize(path)
    f.seek(start)
    buf = f.read(65536)
    i = 0
    while i < len(buf) - 4:
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            b1, b2, b3 = buf[i + 1], buf[i + 2], buf[i + 3]
            ver = (b1 >> 3) & 3
            layer = 4 - ((b1 >> 1) & 3)
            bri = b2 >> 4
            sri = (b2 >> 2) & 3
            if ver != 1 and layer != 4 and 0 < bri < 15 and sri != 3:
                mpeg1 = ver == 3
                br = _BR[(mpeg1, layer)][bri]
                sr = _SR[ver][sri]
                mode = b3 >> 6
                info["bitrate"] = br
                info["samplerate"] = sr
                info["channels"] = 1 if mode == 3 else 2
                spf = 1152 if (layer > 1 and (mpeg1 or layer == 2)) else (576 if layer == 3 else 384)
                # Xing/Info VBR header
                if mpeg1:
                    off = 36 if mode != 3 else 21
                else:
                    off = 21 if mode != 3 else 13
                tag = buf[i + off:i + off + 4]
                frames = None
                if tag in (b"Xing", b"Info"):
                    fl = struct.unpack(">I", buf[i + off + 4:i + off + 8])[0]
                    if fl & 1:
                        frames = struct.unpack(">I", buf[i + off + 8:i + off + 12])[0]
                elif buf[i + 36:i + 40] == b"VBRI":
                    frames = struct.unpack(">I", buf[i + 50:i + 54])[0]
                audio_bytes = fsize - start - i
                if frames:
                    dur = frames * spf / sr
                    info["duration"] = dur
                    if dur > 0:
                        info["bitrate"] = int(audio_bytes * 8 / dur / 1000)
                elif br:
                    info["duration"] = audio_bytes * 8 / (br * 1000)
                break
        i += 1
    if "title" not in info and fsize > 128:
        f.seek(fsize - 128)
        t = f.read(128)
        if t[:3] == b"TAG":
            def s(b):
                return b.split(b"\x00")[0].decode("latin-1").strip()
            if s(t[3:33]):
                info["title"] = s(t[3:33])
            if s(t[33:63]):
                info["artist"] = s(t[33:63])
            if s(t[63:93]):
                info["album"] = s(t[63:93])


# ---------------------------------------------------------------- WAV

def _wav(f, info):
    hdr = f.read(12)
    if hdr[:4] != b"RIFF" or hdr[8:12] != b"WAVE":
        return
    byterate = None
    while True:
        ch = f.read(8)
        if len(ch) < 8:
            break
        cid, size = ch[:4], struct.unpack("<I", ch[4:])[0]
        if cid == b"fmt ":
            d = f.read(size)
            _, chans, sr, byterate = struct.unpack("<HHII", d[:12])
            info["channels"] = chans
            info["samplerate"] = sr
            info["bitrate"] = byterate * 8 // 1000
        elif cid == b"data":
            if byterate:
                info["duration"] = size / byterate
            break
        else:
            f.seek(size + (size & 1), 1)


# ---------------------------------------------------------------- Vorbis comments

def _vcomments(data, info):
    try:
        vlen = struct.unpack("<I", data[:4])[0]
        p = 4 + vlen
        n = struct.unpack("<I", data[p:p + 4])[0]
        p += 4
        for _ in range(n):
            ln = struct.unpack("<I", data[p:p + 4])[0]
            p += 4
            kv = data[p:p + ln].decode("utf-8", "replace")
            p += ln
            if "=" in kv:
                k, v = kv.split("=", 1)
                k = k.lower()
                if k in ("title", "artist", "album") and k not in info:
                    info[k] = v
    except Exception:
        pass


def _flac(f, info):
    if f.read(4) != b"fLaC":
        return
    while True:
        h = f.read(4)
        if len(h) < 4:
            break
        last = h[0] & 0x80
        btype = h[0] & 0x7F
        size = int.from_bytes(h[1:4], "big")
        d = f.read(size)
        if btype == 0:
            sr = (d[10] << 12) | (d[11] << 4) | (d[12] >> 4)
            chans = ((d[12] >> 1) & 7) + 1
            total = ((d[13] & 0x0F) << 32) | struct.unpack(">I", d[14:18])[0]
            info["samplerate"] = sr
            info["channels"] = chans
            if sr:
                info["duration"] = total / sr
        elif btype == 4:
            _vcomments(d, info)
        if last:
            break
    if info.get("duration"):
        info["bitrate"] = int(f.seek(0, 2) * 8 / info["duration"] / 1000)


def _ogg_packets(f, maxpages=40):
    """Yield the first few logical packets of an Ogg stream."""
    pkt = b""
    for _ in range(maxpages):
        h = f.read(27)
        if len(h) < 27 or h[:4] != b"OggS":
            return
        nseg = h[26]
        lacing = f.read(nseg)
        for l in lacing:
            pkt += f.read(l)
            if l < 255:
                yield pkt
                pkt = b""


def _ogg(f, path, info):
    pre_skip = 0
    rate = None
    for n, pkt in enumerate(_ogg_packets(f)):
        if n == 0:
            if pkt[:7] == b"\x01vorbis":
                info["channels"] = pkt[11]
                rate = struct.unpack("<I", pkt[12:16])[0]
                nominal = struct.unpack("<i", pkt[20:24])[0]
                if nominal > 0:
                    info["bitrate"] = nominal // 1000
            elif pkt[:8] == b"OpusHead":
                info["channels"] = pkt[9]
                pre_skip = struct.unpack("<H", pkt[10:12])[0]
                rate = 48000
            else:
                return
            info["samplerate"] = rate
        elif n == 1:
            if pkt[:7] == b"\x03vorbis":
                _vcomments(pkt[7:], info)
            elif pkt[:8] == b"OpusTags":
                _vcomments(pkt[8:], info)
            break
    if rate:
        size = os.path.getsize(path)
        f.seek(max(0, size - 65536))
        tail = f.read()
        idx = tail.rfind(b"OggS")
        if idx >= 0 and idx + 14 <= len(tail):
            gran = struct.unpack("<q", tail[idx + 6:idx + 14])[0]
            if gran > 0:
                info["duration"] = (gran - pre_skip) / rate
                if "bitrate" not in info and info["duration"] > 0:
                    info["bitrate"] = int(size * 8 / info["duration"] / 1000)
