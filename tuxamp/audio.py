"""Playback engine built on pygame.mixer (SDL_mixer).

Tracks are decoded fully into memory (numpy int16) in a background thread,
which gives us sample-accurate seeking, stereo balance, a 10-band EQ and
real sample data for the visualizer.  Formats that cannot be decoded into a
Sound (MIDI, some trackers) fall back to streaming via mixer.music.

Decoded tracks are played in short chunks (about 23 ms) that a feeder thread
queues on the mixer channel just ahead of the playhead. The EQ (with its
preamp) and the balance are applied to each chunk as it is made, so moving a
slider is heard within about 0.1 s, faded in over ~12 ms so it never clicks,
without ever stopping or restarting playback.

Internet radio (mode "stream") goes through the same chunked playback: a
radio.Stream decodes the station on its own thread, and the feeder moves its
audio into a ring buffer just ahead of the playhead, so the EQ, balance and
visualizers work on radio too.

While casting (self.remote is a cast.CastSession) the device plays the audio
and its position drives the clock, so the time display, seek bar and
visualizers follow what the device is playing. The stream is sent at playback
pace, only a few seconds ahead of the device, with the EQ applied as it is
sent, so EQ changes reach the device without re-sending (and re-buffering) it.
"""

import collections
import sys
import threading
import time

import numpy as np
import pygame

from . import cast, radio

EQ_FREQS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]
NTAPS = 4096                   # EQ filter length
CHUNK = 1024                   # frames per queued chunk (~23 ms at 44.1 kHz)
NFFT = 8192                    # FFT size for one chunk: >= CHUNK + NTAPS - 1
BUFFER = 2048                  # sound card buffer (frames)
FADE = 512                     # an EQ / balance change fades in over this many frames
CAST_LEAD = 3.0                # while casting, send the audio this far (s) ahead of the device
CAST_HEAD = 4.0                # ... and this much at the start, before the device plays
STREAM_PREBUF = 1.5            # radio: seconds buffered before playing (and after a dropout)
STREAM_RING = 20.0             # radio: seconds the ring buffer holds


def _design_eq_fir(rate, preamp_db, bands_db, ntaps=NTAPS):
    freqs = np.fft.rfftfreq(ntaps, 1.0 / rate)
    logf = np.log10(np.maximum(freqs, 20.0))
    db = np.interp(logf, np.log10(EQ_FREQS), bands_db) + preamp_db
    mag = 10.0 ** (db / 20.0)
    h = np.fft.irfft(mag, ntaps)
    h = np.roll(h, ntaps // 2) * np.hanning(ntaps)
    return h.astype(np.float64)


def _filter(raw, a, n, H):
    """Frames [a, a+n) of raw (n <= CHUNK) through the linear-phase EQ filter
    whose spectrum is H, as float32. Reads the neighbouring samples it needs
    straight from raw, so any part of a track can be filtered on its own."""
    d = NTAPS // 2
    s, e = a + d - (NTAPS - 1), a + n + d
    seg = np.zeros((e - s, raw.shape[1]), np.float32)
    lo, hi = max(s, 0), min(e, len(raw))
    if hi > lo:
        seg[lo - s:hi - s] = raw[lo:hi]
    y = np.fft.irfft(np.fft.rfft(seg, NFFT, axis=0) * H, NFFT, axis=0)
    return y[NTAPS - 1:NTAPS - 1 + n].astype(np.float32)


class Engine:
    def __init__(self):
        # let the feeder thread in quickly even while the main thread is busy
        # (e.g. drawing the visualizer), so the next chunk is always queued in time
        sys.setswitchinterval(0.001)
        pygame.mixer.pre_init(44100, -16, 2, BUFFER)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(2)
        self.rate, _, self.nch = pygame.mixer.get_init()
        self.channel = pygame.mixer.Channel(0)

        self.path = None
        self.raw = None          # decoded int16 array (n, ch)
        self.mode = None         # "sound" | "music" | "stream" | None
        self.duration = 0.0
        self.state = "stopped"   # stopped | playing | paused
        self.loading = False

        self._start = 0.0        # position (sec) at which playback started
        self._t0 = 0.0
        self._pause_pos = 0.0

        self.volume = 0.75
        self.balance = 0.0
        self.eq_on = False
        self.preamp = 0.0
        self.bands = [0.0] * 10

        self._lock = threading.Lock()
        self._pending = None     # result handed from loader thread
        self._load_gen = 0
        self._autoplay = False
        self.remote = None       # cast.CastSession while casting
        self.title = ""          # shown on the cast device

        # chunked playback (see the module docstring)
        self._H = None           # EQ filter spectrum, None = EQ off / flat
        self._feed_lock = threading.RLock()
        self._feeding = False
        self._feed_pos = 0       # next frame to queue
        self._fed_H = None       # filter / balance used for the last chunk queued,
        self._fed_gains = (1.0, 1.0)   # so a change can be cross-faded
        self._fed = collections.deque(maxlen=10)  # (start frame, chunk) for the visualizers
        self._idle_seen = False  # the channel looked empty at the previous check
        self.underruns = 0       # times the feeder fell behind (for testing)
        self._cast_H = None      # EQ bands only: while casting, the preamp is the device volume
        self._cast_fed = (None, (1.0, 1.0))   # filter / balance last sent to the device
        self._cast_last = None   # (frame, chunk) last made for the device
        self._cast_lock = threading.Lock()
        self._cast_src = None    # the stream the device is reading
        # internet radio
        self.stream = None       # radio.Stream while a station is connected
        self.stream_url = None   # the station's audio address, once known (for casting)
        self.stream_mime = "audio/mpeg"
        self.error_msg = None    # why the last station failed
        self._ring = None        # ring buffer; self.raw is a view of its filled part
        self._ring_base = 0      # stream frame number of self.raw[0]
        self._stream_wait = True # filling up before playing
        threading.Thread(target=self._feeder, daemon=True, name="audio-feed").start()

    # ------------------------------------------------------------ loading
    def load(self, path, autoplay=True):
        self.stop()
        self._load_gen += 1
        gen = self._load_gen
        self.path = path
        self.raw = None
        self.mode = None
        self.duration = 0.0
        self.loading = True
        self._autoplay = autoplay
        self.error_msg = None
        if radio.is_url(path):
            self.stream_url = None
            self.stream = radio.Stream(path, self.rate)      # picked up in update()
            return
        threading.Thread(target=self._loader, args=(gen, path), daemon=True).start()

    def _loader(self, gen, path):
        result = {"gen": gen, "path": path}
        try:
            snd = pygame.mixer.Sound(path)
            arr = pygame.sndarray.array(snd)
            del snd
            if arr.ndim == 1:
                arr = arr[:, None]
            if arr.dtype != np.int16:
                arr = arr.astype(np.int16)
            result["raw"] = np.ascontiguousarray(arr)
        except Exception as e:
            result["error"] = e
        with self._lock:
            if gen == self._load_gen:
                self._pending = result

    def _eq_spectrum(self, preamp=True):
        pre = self.preamp if preamp else 0.0
        if not self.eq_on or (pre == 0 and not any(self.bands)):
            return None
        h = _design_eq_fir(self.rate, pre, self.bands)
        return np.fft.rfft(h, NFFT)[:, None]

    # ------------------------------------------------------------ chunked playback
    def _gains(self):
        return min(1.0, 1.0 - self.balance), min(1.0, 1.0 + self.balance)

    def _render(self, a, n, H):
        """Frames [a, a+n) through the EQ filter H (None = no EQ), as float32."""
        if H is None:
            return self.raw[a:a + n].astype(np.float32)
        return _filter(self.raw, a, n, H)

    def _mix(self, a, n, old, new):
        """Frames [a, a+n) (n <= CHUNK) with filter and balance new = (H, gains),
        cross-faded from old across the first FADE frames, as int16."""
        (H0, g0), (H, g) = old, new
        ramp = np.minimum(np.arange(n, dtype=np.float32) / FADE, 1.0)[:, None]
        if H is H0:
            out = self._render(a, n, H)
        else:
            out = self._render(a, n, H0) * (1 - ramp) + self._render(a, n, H) * ramp
        if out.shape[1] == 2:
            if g != g0:
                g0, g1 = np.asarray(g0, np.float32), np.asarray(g, np.float32)
                out *= g0 + (g1 - g0) * ramp
            elif g != (1.0, 1.0):
                out *= np.asarray(g, np.float32)
        return np.clip(out, -32768, 32767).astype(np.int16)

    def _make_chunk(self):
        """The next chunk to queue. An EQ or balance change since the last
        chunk is cross-faded across this one, so it never clicks."""
        a = self._feed_pos
        n = min(CHUNK, len(self.raw) - a)
        new = (self._H, self._gains())
        pcm = self._mix(a, n, (self._fed_H, self._fed_gains), new)
        self._fed_H, self._fed_gains = new
        self._feed_pos = a + n
        self._fed.append((a, pcm))
        return pygame.sndarray.make_sound(pcm)

    def _feed(self, start=False):
        """Keep one chunk queued behind the one playing (call with _feed_lock held)."""
        if not self._feeding or self.raw is None:
            return
        if self.mode == "stream" and not self._stream_ready():
            return
        if self._feed_pos >= len(self.raw):
            return
        if start:
            self.channel.play(self._make_chunk())
            return
        busy, queued = self.channel.get_busy(), self.channel.get_queue() is not None
        if busy and not queued:
            self.channel.queue(self._make_chunk())
            self._idle_seen = False
        elif not busy and not queued:
            # When a chunk ends, the mixer reports the channel idle for a moment
            # while it starts the queued one. Only an idle channel seen twice in
            # a row has really run dry; restarting it earlier would skip a chunk.
            if self._idle_seen:
                self.underruns += 1
                self.channel.play(self._make_chunk())
                self._idle_seen = False
            else:
                self._idle_seen = True
        else:
            self._idle_seen = False

    # ------------------------------------------------------------ internet radio
    def _stream_ready(self):
        """Radio: make sure the next chunk (and the EQ's look-ahead) is in the
        ring buffer. False while the station's audio has not arrived yet."""
        s = self.stream
        if s is None:
            return False
        if self._stream_wait:
            ahead = s.available() + len(self.raw) - self._feed_pos
            if ahead < STREAM_PREBUF * self.rate and s.state in ("connecting", "playing"):
                return False
            self._stream_wait = False
        need = self._feed_pos + CHUNK + NTAPS // 2
        self._stream_fill(need)
        if len(self.raw) >= need:
            return True
        if not self.channel.get_busy():
            self._stream_wait = True      # ran dry: buffer up again before carrying on
        return False

    def _stream_fill(self, need):
        n = len(self.raw)
        if need <= n:
            return
        cap = len(self._ring)
        if need + CHUNK * 8 > cap:
            # make room: keep only what the EQ filter still needs to look back at
            drop = max(0, self._feed_pos - NTAPS)
            keep = n - drop
            self._ring[:keep] = self._ring[drop:n]
            n = keep
            self._feed_pos -= drop
            self._ring_base += drop
            self._fed = collections.deque(((a - drop, p) for a, p in self._fed), maxlen=10)
        data = self.stream.read(min(need - n + CHUNK * 8, cap - n))     # a little ahead
        self._ring[n:n + len(data)] = data
        self.raw = self._ring[:n + len(data)]

    def _stream_reset(self):
        """An empty ring buffer for a station starting (or reconnecting)."""
        if self._ring is None:
            self._ring = np.zeros((int(STREAM_RING * self.rate), 2), np.int16)
        self.raw = self._ring[:0]
        self._ring_base = 0
        self._feed_pos = 0
        self._fed.clear()
        self._stream_wait = True

    def _close_stream(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def _feeder(self):
        while True:
            time.sleep(0.005)
            with self._feed_lock:
                try:
                    self._feed()
                except pygame.error:
                    pass

    # ------------------------------------------------------------ transport
    def play(self):
        if self.loading:
            self._autoplay = True
            return
        if self.mode is None:
            return
        if self.state == "paused":
            self.unpause()
            return
        self._start_at(0.0 if self.state == "stopped" else self.position())

    def _start_at(self, pos):
        pos = max(0.0, min(pos, max(0.0, self.duration - 0.05))) if self.duration else max(0.0, pos)
        if self.remote is not None:
            self._stop_local()
            self.remote.load(self._cast_source(pos), self.title or "TuxAmp")
        elif self.mode == "stream":
            with self._feed_lock:
                self.channel.stop()
                if self.stream is None:          # e.g. back from casting: tune in again
                    self.stream = radio.Stream(self.path, self.rate)
                    self._stream_reset()
                self._fed_H, self._fed_gains = self._H, self._gains()
                self._feeding = True
                self._idle_seen = False
                self._apply_volume()
                self._feed(start=True)
                self._feed()
            pos = 0.0
        elif self.mode == "sound":
            with self._feed_lock:
                self.channel.stop()
                self._fed.clear()
                self._feed_pos = min(int(pos * self.rate), len(self.raw))
                self._fed_H, self._fed_gains = self._H, self._gains()
                self._feeding = True
                self._idle_seen = False
                self._apply_volume()
                self._feed(start=True)       # first chunk plays now,
                self._feed()                 # the second is queued behind it
        elif self.mode == "music":
            pygame.mixer.music.play(start=pos)
            self._apply_volume()
        self._start = pos
        self._t0 = time.monotonic()
        self.state = "playing"

    def pause(self):
        if self.state == "playing":
            self._pause_pos = self.position()
            if self.remote is not None:
                self.remote.pause()
            elif self.mode in ("sound", "stream"):
                self.channel.pause()
            else:
                pygame.mixer.music.pause()
            self.state = "paused"
        elif self.state == "paused":
            self.unpause()

    def unpause(self):
        if self.state != "paused":
            return
        if self.remote is not None:
            self.remote.resume()
        elif self.mode in ("sound", "stream"):
            self.channel.unpause()
        else:
            pygame.mixer.music.unpause()
        self._start = self._pause_pos
        self._t0 = time.monotonic()
        self.state = "playing"

    def stop(self):
        self._stop_local()
        self._end_cast_stream()
        if self.remote is not None and self.state != "stopped":
            self.remote.stop()
        if self.stream is not None or self.mode == "stream":
            # stopping a station hangs up; playing again tunes in afresh
            with self._feed_lock:
                self._close_stream()
                if self.mode == "stream":
                    self.mode, self.raw = None, None
            self.loading = False
        self.state = "stopped"
        self._start = 0.0

    def seek(self, pos):
        if self.mode is None or self.mode == "stream":       # a live station can't seek
            return
        if self.state == "playing":
            self._start_at(pos)
        elif self.state == "paused":
            self._start_at(pos)
            self.pause()
        else:
            self._start = pos

    def _stop_local(self):
        with self._feed_lock:
            self._feeding = False
            self.channel.stop()
        try:
            pygame.mixer.music.stop()
        except pygame.error:
            pass

    def position(self):
        if self.state == "playing" and self.remote is not None:
            p = self.remote.position()
        elif self.state == "playing" and self.mode == "stream":
            # what has been played: queued audio minus what is still waiting to be heard
            p = max(0.0, (self._ring_base + self._feed_pos - 2 * CHUNK - BUFFER) / self.rate)
        elif self.state == "playing":
            p = self._start + (time.monotonic() - self._t0)
        elif self.state == "paused":
            p = self._pause_pos
        else:
            p = 0.0
        if self.duration:
            p = min(p, self.duration)
        return p

    # ------------------------------------------------------------ mixing
    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        self._apply_volume()
        if self.remote is not None:
            self.remote.set_volume(self.remote_volume())    # the device's own volume

    def set_balance(self, b):
        self.balance = max(-1.0, min(1.0, b))
        self._apply_volume()

    def _apply_volume(self):
        # Balance is applied to the samples (SDL_mixer forgets a channel's
        # panning every time a chunk ends); the channel volume persists.
        if self.mode == "music":
            pygame.mixer.music.set_volume(self.volume)
        else:
            self.channel.set_volume(self.volume)

    def set_eq(self, on, preamp, bands):
        """New EQ settings. Local playback picks them up with the next chunk."""
        old = (self.eq_on, self.preamp, list(self.bands))
        self.eq_on = on
        self.preamp = preamp
        self.bands = list(bands)
        self._H = self._eq_spectrum()
        self._cast_H = self._eq_spectrum(preamp=False)    # picked up as the stream is sent
        if self.remote is not None and (old[0], old[1]) != (on, preamp):
            # the preamp acts on the device's volume: heard right away
            self.remote.set_volume(self.remote_volume())

    def remote_volume(self):
        """Volume for a cast device: TuxAmp's volume with the preamp applied."""
        v = self.volume
        if self.eq_on:
            v *= 10.0 ** (self.preamp / 20.0)
        return max(0.0, min(1.0, v))

    # ------------------------------------------------------------ casting
    def _cast_render(self, a, n):
        """Frames [a, a+n) for the device: EQ bands and balance as they are now,
        cross-faded from what was sent before."""
        out = np.empty((n, self.raw.shape[1]), np.int16)
        i = 0
        while i < n:
            c, pcm = self._cast_chunk((a + i) // CHUNK * CHUNK)
            lo = a + i - c
            k = min(len(pcm) - lo, n - i)
            out[i:i + k] = pcm[lo:lo + k]
            i += k
        return out

    def _cast_chunk(self, c):
        """The whole chunk at frame c for the device, made once even if it is
        sent in pieces (the cross-fade must span a whole chunk)."""
        with self._cast_lock:
            if self._cast_last is None or self._cast_last[0] != c:
                new = (self._cast_H, self._gains())
                self._cast_last = (c, self._mix(c, min(CHUNK, len(self.raw) - c), self._cast_fed, new))
                self._cast_fed = new
            return self._cast_last

    def _cast_source(self, pos):
        self._end_cast_stream()
        if self.mode == "stream":
            # the device tunes in to the station itself
            return cast.UrlSource(self.stream_url or self.path, self.stream_mime)
        if self.mode != "sound":
            return cast.FileSource(self.path, pos, self.duration)
        src = cast.PcmSource(self.raw, self.rate, int(pos * self.rate), render=self._cast_render)
        remote, t0 = self.remote, [None]

        def pace():
            # how far into the track the device may have been sent by now:
            # a few seconds past where it is playing, so EQ changes reach it soon
            now = time.monotonic()
            if t0[0] is None:
                t0[0] = now
            lim = int((remote.position() + CAST_LEAD) * self.rate)
            if remote.state not in ("playing", "paused"):
                # still starting (or buffering): a head start that keeps growing,
                # in case the device wants more before it starts
                lim = max(lim, src.start + int((CAST_HEAD + now - t0[0]) * self.rate))
            return lim
        src.pace = pace
        self._cast_src = src
        with self._cast_lock:
            self._cast_fed, self._cast_last = (self._cast_H, self._gains()), None
        return src

    def _end_cast_stream(self):
        if self._cast_src is not None:
            self._cast_src.active = False           # the device has moved on from it
            self._cast_src = None

    def start_cast(self, session):
        """Move playback to a cast device, carrying on from the same spot."""
        pos, state = self.position(), self.state
        if self.remote is not None:
            self.remote.close()
        self._stop_local()
        if self.mode == "stream":
            with self._feed_lock:
                self._close_stream()          # the device downloads it instead
        self.remote = session
        if self.eq_on and self.preamp:
            session.set_volume(self.remote_volume())
        if self.mode is not None and state != "stopped":
            self._start_at(pos)
            if state == "paused":
                self.pause()

    def stop_cast(self, wait=0.0):
        """Bring playback back to this computer, carrying on from the same spot."""
        if self.remote is None:
            return
        pos, state = self.position(), self.state
        self.remote.close(wait)
        self.remote = None
        self._end_cast_stream()
        if self.mode is not None and state != "stopped":
            self._start_at(pos)
            if state == "paused":
                self.pause()

    # ------------------------------------------------------------ visualizer
    def _played(self, i, n):
        """Frames [i, i+n) as heard (EQ applied), or None near the end."""
        raw = self.raw
        if raw is None:
            return None
        if self.mode == "stream":
            i -= self._ring_base
        if i < 0 or i + n > len(raw):
            return None
        seg = raw[i:i + n].copy()
        if self.remote is None:
            for a, pcm in list(self._fed):          # use the EQ'd chunks where we have them
                lo, hi = max(i, a), min(i + n, a + len(pcm))
                if hi > lo:
                    seg[lo - i:hi - i] = pcm[lo - a:hi - a]
        return seg

    def samples(self, n=1024):
        """Return a (n,) float array (mono, -1..1) at the playhead, or None."""
        if self.mode not in ("sound", "stream") or self.state != "playing":
            return None
        seg = self._played(int(self.position() * self.rate), n)
        if seg is None:
            return None
        return seg.mean(axis=1) / 32768.0

    def samples_stereo(self, n=2048):
        """Return a (n, 2) float32 array (-1..1) at the playhead, or None."""
        if self.mode not in ("sound", "stream") or self.state != "playing":
            return None
        seg = self._played(int(self.position() * self.rate), n)
        if seg is None:
            return None
        if seg.shape[1] == 1:
            seg = np.repeat(seg, 2, axis=1)
        return seg[:, :2].astype(np.float32) / 32768.0

    # ------------------------------------------------------------ main-loop pump
    def update(self):
        """Call every frame. Returns list of events: 'loaded', 'error', 'ended'."""
        events = []
        with self._lock:
            res, self._pending = self._pending, None
        if res is not None and res["gen"] == self._load_gen:
            self.loading = False
            if "raw" in res:
                self.mode = "sound"
                self.raw = res["raw"]
                self.duration = len(self.raw) / self.rate
            else:
                try:
                    pygame.mixer.music.load(res["path"])
                    self.mode = "music"
                except pygame.error:
                    self.mode = None
            if self.mode is None:
                events.append("error")
            else:
                events.append("loaded")
                if self._autoplay:
                    self._start_at(0.0)
        s = self.stream
        if self.loading and s is not None:
            if s.state == "error":
                self.loading = False
                self.error_msg = s.error
                self._close_stream()
                events.append("error")
            elif s.available() >= STREAM_PREBUF * self.rate or (s.state == "ended" and s.available()):
                self.loading = False
                self.mode = "stream"
                self.duration = 0.0
                self.stream_url, self.stream_mime = s.stream_url, s.mime
                with self._feed_lock:
                    self._stream_reset()
                events.append("loaded")
                if self._autoplay:
                    self._start_at(0.0)
        if self.remote is not None:
            r = self.remote
            if r.state == "error":
                events.append("casterror")
            elif self.state == "playing" and r.state == "finished":
                self.state = "stopped"
                events.append("ended")
            elif self.state == "playing" and r.state == "stopped":
                self.state = "stopped"          # stopped from the device itself
                self._start = 0.0
        elif self.state == "playing" and self.mode == "stream":
            if s is not None and s.stream_url:
                self.stream_url = s.stream_url
            with self._feed_lock:
                gone = (s is None or s.state in ("error", "ended")) and (s is None or not s.available()) \
                    and not self.channel.get_busy() and len(self.raw) - self._feed_pos < CHUNK + NTAPS // 2
            if gone:
                self.error_msg = s.error if s is not None else None
                self.stop()
                events.append("streamend")
        elif self.state == "playing":
            if self.mode == "sound":
                with self._feed_lock:
                    done = self._feed_pos >= len(self.raw) and not self.channel.get_busy()
                    if done:
                        self._feeding = False
                if done:
                    self.state = "stopped"
                    events.append("ended")
            elif self.mode == "music" and not pygame.mixer.music.get_busy():
                self.state = "stopped"
                events.append("ended")
        return events
