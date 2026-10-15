"""Playback engine built on pygame.mixer (SDL_mixer).

Tracks are decoded fully into memory (numpy int16) in a background thread,
which gives us sample-accurate seeking, stereo balance, a 10-band EQ and
real sample data for the visualizer.  Formats that cannot be decoded into a
Sound (MIDI, some trackers) fall back to streaming via mixer.music.

Decoded tracks are played in short chunks (about 93 ms) that a feeder thread
queues on the mixer channel just ahead of the playhead. The EQ (with its
preamp) and the balance are applied to each chunk as it is made, so moving a
slider takes effect within a fraction of a second, cross-faded from the old
setting, without ever stopping or restarting playback.

While casting (self.remote is a cast.CastSession) the device plays the audio
and its position drives the clock, so the time display, seek bar and
visualizers follow what the device is playing.
"""

import collections
import threading
import time

import numpy as np
import pygame

from . import cast

EQ_FREQS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]
NTAPS = 4096                   # EQ filter length
CHUNK = 4096                   # frames per queued chunk (~93 ms at 44.1 kHz)
NFFT = 8192                    # FFT size for one chunk: >= CHUNK + NTAPS - 1


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
        pygame.mixer.pre_init(44100, -16, 2, 2048)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(2)
        self.rate, _, self.nch = pygame.mixer.get_init()
        self.channel = pygame.mixer.Channel(0)

        self.path = None
        self.raw = None          # decoded int16 array (n, ch)
        self.mode = None         # "sound" | "music" | None
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
        self._eq_due = None      # when to re-send the audio to a cast device after an EQ change
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
        self._fed = collections.deque(maxlen=6)   # (start frame, chunk) for the visualizers
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

    def _eq_spectrum(self):
        if not self.eq_on or (self.preamp == 0 and not any(self.bands)):
            return None
        h = _design_eq_fir(self.rate, self.preamp, self.bands)
        return np.fft.rfft(h, NFFT)[:, None]

    # ------------------------------------------------------------ chunked playback
    def _gains(self):
        return min(1.0, 1.0 - self.balance), min(1.0, 1.0 + self.balance)

    def _render(self, a, n, H):
        """Frames [a, a+n) through the EQ filter H (None = no EQ), as float32."""
        if H is None:
            return self.raw[a:a + n].astype(np.float32)
        return _filter(self.raw, a, n, H)

    def _make_chunk(self):
        """The next chunk to queue. An EQ or balance change since the last
        chunk is cross-faded across this one, so it never clicks."""
        a = self._feed_pos
        n = min(CHUNK, len(self.raw) - a)
        H, gains = self._H, self._gains()
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
        if H is self._fed_H:
            out = self._render(a, n, H)
        else:
            out = self._render(a, n, self._fed_H) * (1 - ramp) + self._render(a, n, H) * ramp
        if out.shape[1] == 2:
            if gains != self._fed_gains:
                g0, g1 = np.asarray(self._fed_gains, np.float32), np.asarray(gains, np.float32)
                out *= g0 + (g1 - g0) * ramp
            elif gains != (1.0, 1.0):
                out *= np.asarray(gains, np.float32)
        self._fed_H, self._fed_gains = H, gains
        self._feed_pos = a + n
        pcm = np.clip(out, -32768, 32767).astype(np.int16)
        self._fed.append((a, pcm))
        return pygame.sndarray.make_sound(pcm)

    def _feed(self):
        """Keep one chunk queued behind the one playing (call with _feed_lock held)."""
        if not self._feeding or self.raw is None or self._feed_pos >= len(self.raw):
            return
        if not self.channel.get_busy():
            self.channel.play(self._make_chunk())
        elif self.channel.get_queue() is None:
            self.channel.queue(self._make_chunk())

    def _feeder(self):
        while True:
            time.sleep(0.01)
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
        elif self.mode == "sound":
            with self._feed_lock:
                self.channel.stop()
                self._fed.clear()
                self._feed_pos = min(int(pos * self.rate), len(self.raw))
                self._fed_H, self._fed_gains = self._H, self._gains()
                self._feeding = True
                self._apply_volume()
                self._feed()                 # first chunk plays now,
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
            elif self.mode == "sound":
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
        elif self.mode == "sound":
            self.channel.unpause()
        else:
            pygame.mixer.music.unpause()
        self._start = self._pause_pos
        self._t0 = time.monotonic()
        self.state = "playing"

    def stop(self):
        self._stop_local()
        if self.remote is not None and self.state != "stopped":
            self.remote.stop()
        self.state = "stopped"
        self._start = 0.0

    def seek(self, pos):
        if self.mode is None:
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
            self.remote.set_volume(self.volume)    # the device's own volume

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
        self.eq_on = on
        self.preamp = preamp
        self.bands = list(bands)
        self._H = self._eq_spectrum()
        if self.remote is not None:
            # a cast device has already fetched audio far ahead: re-send it
            # from the current spot once the slider has stopped moving
            self._eq_due = time.monotonic() + 0.6

    # ------------------------------------------------------------ casting
    def _cast_render(self, a, n):
        out = np.empty((n, self.raw.shape[1]), np.int16)
        H = self._H
        for i in range(0, n, CHUNK):
            k = min(CHUNK, n - i)
            out[i:i + k] = np.clip(self._render(a + i, k, H), -32768, 32767)
        return out

    def _cast_source(self, pos):
        if self.mode == "sound":
            return cast.PcmSource(self.raw, self.rate, int(pos * self.rate), self._gains,
                                  render=self._cast_render)
        return cast.FileSource(self.path, pos, self.duration)

    def start_cast(self, session):
        """Move playback to a cast device, carrying on from the same spot."""
        pos, state = self.position(), self.state
        if self.remote is not None:
            self.remote.close()
        self._stop_local()
        self.remote = session
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
        if self.mode is not None and state != "stopped":
            self._start_at(pos)
            if state == "paused":
                self.pause()

    # ------------------------------------------------------------ visualizer
    def _played(self, i, n):
        """Frames [i, i+n) as heard (EQ applied), or None near the end."""
        if i + n > len(self.raw):
            return None
        seg = self.raw[i:i + n].copy()
        if self.remote is None:
            for a, pcm in list(self._fed):          # use the EQ'd chunks where we have them
                lo, hi = max(i, a), min(i + n, a + len(pcm))
                if hi > lo:
                    seg[lo - i:hi - i] = pcm[lo - a:hi - a]
        return seg

    def samples(self, n=1024):
        """Return a (n,) float array (mono, -1..1) at the playhead, or None."""
        if self.mode != "sound" or self.state != "playing":
            return None
        seg = self._played(int(self.position() * self.rate), n)
        if seg is None:
            return None
        return seg.mean(axis=1) / 32768.0

    def samples_stereo(self, n=2048):
        """Return a (n, 2) float32 array (-1..1) at the playhead, or None."""
        if self.mode != "sound" or self.state != "playing":
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
        if self._eq_due is not None and time.monotonic() >= self._eq_due:
            self._eq_due = None
            if self.remote is not None and self.mode == "sound":
                if self.state == "playing":
                    self._start_at(self.position())
                elif self.state == "paused":
                    p = self._pause_pos
                    self._start_at(p)
                    self.pause()
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
