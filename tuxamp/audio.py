"""Playback engine built on pygame.mixer (SDL_mixer).

Tracks are decoded fully into memory (numpy int16) in a background thread,
which gives us sample-accurate seeking, stereo balance, a 10-band EQ and
real sample data for the visualizer.  Formats that cannot be decoded into a
Sound (MIDI, some trackers) fall back to streaming via mixer.music.

While casting (self.remote is a cast.CastSession) the device plays the audio
and its position drives the clock, so the time display, seek bar and
visualizers follow what the device is playing.
"""

import threading
import time

import numpy as np
import pygame

from . import cast

EQ_FREQS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]


def _design_eq_fir(rate, preamp_db, bands_db, ntaps=4096):
    freqs = np.fft.rfftfreq(ntaps, 1.0 / rate)
    logf = np.log10(np.maximum(freqs, 20.0))
    db = np.interp(logf, np.log10(EQ_FREQS), bands_db) + preamp_db
    mag = 10.0 ** (db / 20.0)
    h = np.fft.irfft(mag, ntaps)
    h = np.roll(h, ntaps // 2) * np.hanning(ntaps)
    return h.astype(np.float64)


def _apply_fir(x, h, cancel=lambda: False):
    """Overlap-add FFT convolution of x (n, ch) with linear-phase FIR h."""
    n, ch = x.shape
    m = len(h)
    block = 1 << 16
    nfft = 1
    while nfft < block + m - 1:
        nfft <<= 1
    H = np.fft.rfft(h, nfft)[:, None]
    out = np.zeros((n + m - 1, ch), dtype=np.float32)
    for s in range(0, n, block):
        if cancel():
            return None
        seg = x[s:s + block].astype(np.float32)
        y = np.fft.irfft(np.fft.rfft(seg, nfft, axis=0) * H, nfft, axis=0)
        ln = min(len(seg) + m - 1, len(out) - s)
        out[s:s + ln] += y[:ln]
    d = m // 2
    return np.clip(out[d:d + n], -32768, 32767).astype(np.int16)


class Engine:
    def __init__(self):
        pygame.mixer.pre_init(44100, -16, 2, 2048)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(2)
        self.rate, _, self.nch = pygame.mixer.get_init()
        self.channel = pygame.mixer.Channel(0)

        self.path = None
        self.raw = None          # decoded int16 array (n, ch)
        self.pcm = None          # array actually being played (EQ applied)
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
        self._eq_gen = 0
        self._eq_pending = None
        self._eq_due = None
        self._autoplay = False
        self.remote = None       # cast.CastSession while casting
        self.title = ""          # shown on the cast device

    # ------------------------------------------------------------ loading
    def load(self, path, autoplay=True):
        self.stop()
        self._load_gen += 1
        gen = self._load_gen
        self.path = path
        self.raw = self.pcm = None
        self.mode = None
        self.duration = 0.0
        self.loading = True
        self._autoplay = autoplay
        eq = self._eq_params()
        threading.Thread(target=self._loader, args=(gen, path, eq), daemon=True).start()

    def _loader(self, gen, path, eq):
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
            if eq is not None and gen == self._load_gen:
                result["pcm"] = _apply_fir(arr, eq, lambda: gen != self._load_gen)
        except Exception as e:
            result["error"] = e
        with self._lock:
            if gen == self._load_gen:
                self._pending = result

    def _eq_params(self):
        if not self.eq_on:
            return None
        if self.preamp == 0 and not any(self.bands):
            return None
        return _design_eq_fir(self.rate, self.preamp, self.bands)

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
            arr = self.pcm if self.pcm is not None else self.raw
            i = int(pos * self.rate)
            snd = pygame.sndarray.make_sound(np.ascontiguousarray(arr[i:]))
            self.channel.stop()
            self.channel.play(snd)
            self._apply_volume()
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
        v = self.volume
        if self.mode == "music":
            pygame.mixer.music.set_volume(v)
            return
        left = v * min(1.0, 1.0 - self.balance)
        right = v * min(1.0, 1.0 + self.balance)
        self.channel.set_volume(left, right)

    def set_eq(self, on, preamp, bands):
        self.eq_on = on
        self.preamp = preamp
        self.bands = list(bands)
        # debounce: recompute shortly after the user stops dragging
        self._eq_due = time.monotonic() + 0.25

    def _start_eq_job(self):
        self._eq_gen += 1
        gen = self._eq_gen
        load_gen = self._load_gen
        raw = self.raw
        fir = self._eq_params()
        if fir is None:
            with self._lock:
                self._eq_pending = {"gen": gen, "load": load_gen, "pcm": None}
            return

        def job():
            pcm = _apply_fir(raw, fir, lambda: gen != self._eq_gen)
            if pcm is None:
                return
            with self._lock:
                if gen == self._eq_gen:
                    self._eq_pending = {"gen": gen, "load": load_gen, "pcm": pcm}

        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------ casting
    def _balance_gains(self):
        return min(1.0, 1.0 - self.balance), min(1.0, 1.0 + self.balance)

    def _cast_source(self, pos):
        if self.mode == "sound":
            arr = self.pcm if self.pcm is not None else self.raw
            return cast.PcmSource(arr, self.rate, int(pos * self.rate), self._balance_gains)
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
    def samples(self, n=1024):
        """Return a (n,) float array (mono, -1..1) at the playhead, or None."""
        if self.mode != "sound" or self.state != "playing":
            return None
        arr = self.pcm if self.pcm is not None else self.raw
        i = int(self.position() * self.rate)
        seg = arr[i:i + n]
        if len(seg) < n:
            return None
        return seg.mean(axis=1) / 32768.0

    def samples_stereo(self, n=2048):
        """Return a (n, 2) float32 array (-1..1) at the playhead, or None."""
        if self.mode != "sound" or self.state != "playing":
            return None
        arr = self.pcm if self.pcm is not None else self.raw
        i = int(self.position() * self.rate)
        seg = arr[i:i + n]
        if len(seg) < n:
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
            eqres, self._eq_pending = self._eq_pending, None
        if res is not None and res["gen"] == self._load_gen:
            self.loading = False
            if "raw" in res:
                self.mode = "sound"
                self.raw = res["raw"]
                self.pcm = res.get("pcm")
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
            if self.mode == "sound" and self.raw is not None:
                self._start_eq_job()
        if eqres is not None and eqres["load"] == self._load_gen and self.mode == "sound":
            self.pcm = eqres["pcm"]
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
            if self.mode == "sound" and not self.channel.get_busy():
                self.state = "stopped"
                events.append("ended")
            elif self.mode == "music" and not pygame.mixer.music.get_busy():
                self.state = "stopped"
                events.append("ended")
        return events
