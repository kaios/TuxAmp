"""TuxDrop - a full-screen, music-reactive visualizer in the spirit of
Winamp's MilkDrop and AVS plugins.

Like MilkDrop, every frame starts from the previous one: it is zoomed,
rotated and faded (the "warp" feedback loop), then new shapes driven by the
music are drawn on top. Rendering happens at a low internal resolution and is
smoothly scaled up to the screen, which gives the soft, glowing look.
"""

import colorsys
import math
import random
import time

import numpy as np
import pygame

from .widgets import get_font


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return int(r * 255), int(g * 255), int(b * 255)


# ====================================================================== audio
class Analyzer:
    """Turns raw samples into the values presets react to (MilkDrop-style).

    bass/mid/treb are *relative* to their recent average, so 1.0 means
    "normal for this song" and a kick drum spikes to 1.5-2.5.
    """

    def __init__(self, rate=44100):
        self.rate = rate
        self.n = 2048
        self.window = np.hanning(self.n).astype(np.float32)
        freqs = np.fft.rfftfreq(self.n, 1.0 / rate)
        self.band_idx = [np.where((freqs >= lo) & (freqs < hi))[0]
                         for lo, hi in ((20, 250), (250, 2000), (2000, 12000))]
        edges = np.geomspace(30, 16000, 49)
        self.spec_edges = np.searchsorted(freqs, edges)
        self.long = np.full(3, 1e-3)
        self.short = np.zeros(3)
        self.bass = self.mid = self.treb = 0.0
        self.vol = 0.0
        self.beat = False
        self.beat_count = 0
        self._last_beat = 0.0
        self.spec = np.zeros(48)
        self.wave_l = np.zeros(576)
        self.wave_r = np.zeros(576)
        self.wave = np.zeros(576)

    def update(self, stereo, now):
        if stereo is None:
            stereo = np.zeros((self.n, 2), np.float32)
        mono = stereo.mean(axis=1)
        mag = np.abs(np.fft.rfft(mono[:self.n] * self.window))
        energy = np.array([np.sqrt((mag[i] ** 2).sum()) if len(i) else 0.0 for i in self.band_idx])
        self.short = self.short * 0.4 + energy * 0.6
        self.long = self.long * 0.985 + energy * 0.015
        rel = self.short / (self.long + 1e-3 + 0.02 * self.long.max())
        self.bass, self.mid, self.treb = (float(x) for x in np.clip(rel, 0, 3))
        self.vol = float(np.sqrt((mono ** 2).mean()))
        # beat: bass spike well above its running average, debounced
        self.beat = (energy[0] > self.long[0] * 1.45 and energy[0] > 1.0
                     and now - self._last_beat > 0.28)
        if self.beat:
            self._last_beat = now
            self.beat_count += 1
        spec = np.zeros(48)
        for i in range(48):
            a, b = self.spec_edges[i], max(self.spec_edges[i] + 1, self.spec_edges[i + 1])
            spec[i] = mag[a:b].max()
        spec = np.clip((20 * np.log10(spec + 1e-9) + 5) / 55, 0, 1)
        self.spec = np.maximum(spec, self.spec * 0.85)
        self.wave_l = stereo[:576, 0].astype(np.float64)
        self.wave_r = stereo[:576, 1].astype(np.float64)
        self.wave = mono[:576].astype(np.float64)


# ====================================================================== presets
class Preset:
    name = "Preset"
    flash_on_beat = True

    def __init__(self, viz):
        self.v = viz

    def frame(self, a, t):
        raise NotImplementedError


class HyperspaceTunnel(Preset):
    name = "Hyperspace Tunnel"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.035 + 0.035 * a.bass, angle=1.2 * math.sin(t * 0.25) + 0.6 * (a.treb - 1),
               decay=0.93)
        cx, cy = v.iw / 2, v.ih / 2
        base_r = v.ih * (0.16 + 0.05 * a.bass)
        w = a.wave
        pts = []
        for i in range(0, 576, 4):
            th = i / 576 * 2 * math.pi
            r = base_r * (1 + 0.9 * w[i])
            pts.append((cx + r * math.cos(th), cy + r * math.sin(th)))
        v.glow_lines(pts, hsv(t * 0.05), closed=True, width=2)
        if a.beat:
            r = base_r * 1.6
            pygame.draw.circle(v.fb, hsv(t * 0.05 + 0.5, 0.4), (int(cx), int(cy)), int(r), 2)


class BlackHoleBloom(Preset):
    name = "Black Hole Bloom"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=0.985 - 0.012 * a.bass, angle=-2.0 - 2.0 * a.treb, decay=0.955)
        cx, cy = v.iw / 2, v.ih / 2
        r0 = v.ih * (0.08 + 0.03 * a.bass)
        n = 48
        for k in range(2 * n):
            i = k if k < n else 2 * n - 1 - k
            val = a.spec[i]
            th = k / (2 * n) * 2 * math.pi + t * 0.3
            r1 = r0 + v.ih * (0.04 + 0.42 * val)
            c = hsv(i / n * 0.8 + t * 0.03, 0.9, 0.4 + 0.6 * val)
            pygame.draw.line(v.fb, c, (cx + r0 * math.cos(th), cy + r0 * math.sin(th)),
                             (cx + r1 * math.cos(th), cy + r1 * math.sin(th)), 2)


class Kaleidoscope(Preset):
    name = "Kaleidoscope Dreams"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.012 + 0.02 * a.mid, angle=1.5 + a.bass, decay=0.94)
        cx, cy = v.iw * 0.3, v.ih * 0.3
        s = v.ih * 0.6
        L, R = a.wave_l, a.wave_r
        pts = [(cx + (L[i] - R[i]) * s * 0.7, cy - (L[i] + R[i]) * s * 0.5) for i in range(0, 576, 3)]
        v.glow_lines(pts, hsv(t * 0.07, 0.8), width=2)
        pygame.draw.circle(v.fb, hsv(t * 0.07 + 0.33), (int(cx), int(cy)), int(3 + 6 * a.bass))
        v.mirror4()


class NeonRain(Preset):
    name = "Neon Rain"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.0, angle=0, dy=-3 - int(3 * a.bass), decay=0.9)
        for k, (w, off) in enumerate(((a.wave_l, 0.0), (a.wave_r, 0.33), (a.wave, 0.66))):
            y0 = v.ih * (0.62 + 0.1 * k)
            pts = [(i / 575 * v.iw, y0 + w[i] * v.ih * 0.35) for i in range(0, 576, 3)]
            v.glow_lines(pts, hsv(t * 0.04 + off, 0.9), width=1 + (k == 2))


class PlasmaPulse(Preset):
    name = "Plasma Pulse"
    flash_on_beat = False

    def __init__(self, viz):
        super().__init__(viz)
        self.pw, self.ph = 128, 72
        x = np.linspace(0, 1, self.pw)[:, None]
        y = np.linspace(0, 1, self.ph)[None, :]
        self.x, self.y = x, y
        self.d = np.sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2)

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.02, angle=0.5 + a.mid, decay=0.82)
        tt = t * (0.6 + 0.3 * a.mid)
        f = (np.sin(self.x * 9 + tt) + np.sin(self.y * 7 - tt * 1.3)
             + np.sin((self.x + self.y) * 6 + tt * 0.7) + np.sin(self.d * 18 - tt * 2 - a.bass * 2))
        h = t * 0.05
        rgb = np.empty((self.pw, self.ph, 3), np.float32)
        rgb[..., 0] = np.sin(f * math.pi * 0.5 + h * 6.28)
        rgb[..., 1] = np.sin(f * math.pi * 0.5 + h * 6.28 + 2.1)
        rgb[..., 2] = np.sin(f * math.pi * 0.5 + h * 6.28 + 4.2)
        # the feedback loop accumulates ~5x, so feed it gently
        gain = 5 + 17 * min(1.0, a.vol * 4 + 0.15 * a.bass)
        rgb = ((rgb + 1) * gain).clip(0, 255).astype(np.uint8)
        layer = pygame.transform.smoothscale(pygame.surfarray.make_surface(rgb), (v.iw, v.ih))
        v.fb.blit(layer, (0, 0), special_flags=pygame.BLEND_ADD)
        pts = [(i / 575 * v.iw, v.ih / 2 + a.wave[i] * v.ih * 0.4) for i in range(0, 576, 3)]
        v.glow_lines(pts, (255, 255, 255), width=1)


class StarfieldWarp(Preset):
    name = "Starfield Warp"

    def __init__(self, viz):
        super().__init__(viz)
        self.n = 260
        self.x = np.random.uniform(-1, 1, self.n)
        self.y = np.random.uniform(-1, 1, self.n)
        self.z = np.random.uniform(0.05, 1, self.n)
        self.hue = np.random.uniform(0, 1, self.n)

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.0, angle=0.3 * math.sin(t * 0.2), decay=0.72)
        cx, cy = v.iw / 2, v.ih / 2
        fov = v.ih * 0.5
        px, py = cx + self.x / self.z * fov, cy + self.y / self.z * fov
        self.z -= 0.004 + 0.02 * a.bass ** 1.5
        dead = (self.z <= 0.02) | (np.abs(px - cx) > v.iw) | (np.abs(py - cy) > v.ih)
        k = int(dead.sum())
        if k:
            self.x[dead] = np.random.uniform(-1, 1, k)
            self.y[dead] = np.random.uniform(-1, 1, k)
            self.z[dead] = 1.0
            self.hue[dead] = (t * 0.05 + np.random.uniform(0, 0.15, k)) % 1
        nx, ny = cx + self.x / self.z * fov, cy + self.y / self.z * fov
        for i in range(self.n):
            if dead[i]:
                continue
            b = min(1.0, (1.1 - self.z[i]))
            c = hsv(self.hue[i], 0.5 if a.beat else 0.25, b)
            pygame.draw.line(v.fb, c, (px[i], py[i]), (nx[i], ny[i]), 1 + (self.z[i] < 0.3))


class SpiralGalaxy(Preset):
    name = "Spiral Galaxy"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=0.985, angle=3 + 2 * a.mid, decay=0.92)
        cx, cy = v.iw / 2, v.ih / 2
        arms = 3
        for k in range(arms):
            for i in range(48):
                th = i * 0.13 + k * 2 * math.pi / arms + t * 0.8
                r = 4 + i * v.ih * 0.0095
                val = a.spec[i]
                c = hsv(i / 48 * 0.6 + t * 0.06 + k * 0.1, 0.85, 0.5 + 0.5 * val)
                pygame.draw.circle(v.fb, c, (int(cx + r * math.cos(th)), int(cy + r * math.sin(th))),
                                   max(1, int(1 + val * 4)))


class ElectricFlower(Preset):
    name = "Electric Flower"

    def __init__(self, viz):
        super().__init__(viz)
        self.petals = 5

    def frame(self, a, t):
        v = self.v
        if a.beat:
            self.petals = random.choice((3, 4, 5, 6, 7, 8, 9))
        v.warp(zoom=1.025, angle=-1.0 - a.treb, decay=0.9)
        cx, cy = v.iw / 2, v.ih / 2
        R = v.ih * (0.22 + 0.06 * a.bass)
        pts = []
        for i in range(0, 576, 3):
            th = i / 576 * 2 * math.pi
            r = R * (0.65 + 0.35 * math.sin(self.petals * th + t * 2)) + a.wave[i] * R * 0.6
            pts.append((cx + r * math.cos(th), cy + r * math.sin(th)))
        v.glow_lines(pts, hsv(t * 0.06 + 0.1, 0.7), closed=True, width=2)
        pts2 = [(cx + 0.5 * (x - cx), cy + 0.5 * (y - cy)) for x, y in pts]
        v.glow_lines(pts2, hsv(t * 0.06 + 0.6, 0.7), closed=True, width=1)


class AVSPulseGrid(Preset):
    name = "AVS Pulse Grid"

    def frame(self, a, t):
        v = self.v
        v.warp(zoom=1.0 + 0.03 * a.bass, angle=0, decay=0.86)
        cols, rows = 16, 9
        cw, ch = v.iw / cols, v.ih / rows
        for gx in range(cols):
            val = a.spec[int(gx / cols * 47)]
            for gy in range(rows):
                lit = (rows - gy) / rows <= val
                if lit:
                    c = hsv(0.33 - 0.33 * (rows - gy) / rows + t * 0.02, 0.9, 0.9)
                    pygame.draw.rect(v.fb, c, (gx * cw + 2, gy * ch + 2, cw - 4, ch - 4), 0 if a.beat else 1)
        pts = [(i / 575 * v.iw, v.ih / 2 + a.wave[i] * v.ih * 0.45) for i in range(0, 576, 3)]
        v.glow_lines(pts, (255, 255, 255), width=1)


PRESETS = [HyperspaceTunnel, BlackHoleBloom, Kaleidoscope, NeonRain, PlasmaPulse,
           StarfieldWarp, SpiralGalaxy, ElectricFlower, AVSPulseGrid]


# ====================================================================== engine
class TuxDrop:
    PRESET_SECONDS = 25.0

    def __init__(self, screen_size, rate=44100):
        self.an = Analyzer(rate)
        self.t0 = time.monotonic()
        self.locked = False
        self.overlays = {}
        self.resize(screen_size)
        self.presets = [P(self) for P in PRESETS]
        self.index = random.randrange(len(self.presets))
        self.switched_at = 0.0
        self.show("preset", self.presets[self.index].name, 4)
        self.show("help", "ESC exit   SPACE next preset   BACKSPACE previous   L lock preset   "
                          "F full screen / window   Z X C V B transport", 7)

    def resize(self, size):
        self.sw, self.sh = max(1, size[0]), max(1, size[1])
        self.iw = 400
        self.ih = max(120, min(400, int(self.iw * self.sh / self.sw)))
        self.fb = pygame.Surface((self.iw, self.ih))
        self.tmp = pygame.Surface((self.iw, self.ih))
        self.font = get_font(max(14, self.sh // 32))
        self.small = get_font(max(12, self.sh // 48))

    # --------------------------------------------------- drawing primitives
    def warp(self, zoom=1.0, angle=0.0, dx=0, dy=0, decay=0.94):
        """MilkDrop-style feedback: re-draw last frame zoomed/rotated and faded."""
        if zoom != 1.0 or angle:
            src = pygame.transform.rotozoom(self.fb, angle, zoom)
        else:
            src = self.fb
        self.tmp.fill((0, 0, 0))
        self.tmp.blit(src, ((self.iw - src.get_width()) // 2 + dx, (self.ih - src.get_height()) // 2 + dy))
        self.fb, self.tmp = self.tmp, self.fb
        d = int(255 * decay)
        self.fb.fill((d, d, d), special_flags=pygame.BLEND_MULT)

    def glow_lines(self, pts, color, closed=False, width=1):
        if len(pts) < 2:
            return
        pts = [(float(x), float(y)) for x, y in pts]
        dim = tuple(c // 3 for c in color)
        pygame.draw.lines(self.fb, dim, closed, pts, width + 3)
        pygame.draw.lines(self.fb, color, closed, pts, width)

    def mirror4(self):
        hw, hh = self.iw // 2, self.ih // 2
        q = self.fb.subsurface((0, 0, hw, hh)).copy()
        self.fb.blit(pygame.transform.flip(q, True, False), (self.iw - hw, 0))
        self.fb.blit(pygame.transform.flip(q, False, True), (0, self.ih - hh))
        self.fb.blit(pygame.transform.flip(q, True, True), (self.iw - hw, self.ih - hh))

    # --------------------------------------------------- control
    def show(self, key, text, secs):
        self.overlays[key] = (text, time.monotonic() + secs, secs)

    def next_preset(self, step=1, announce=True):
        self.index = (self.index + step) % len(self.presets)
        self.switched_at = time.monotonic() - self.t0
        if announce:
            self.show("preset", self.presets[self.index].name + ("  [locked]" if self.locked else ""), 3)

    def random_preset(self):
        choices = [i for i in range(len(self.presets)) if i != self.index]
        self.index = random.choice(choices)
        self.switched_at = time.monotonic() - self.t0
        self.show("preset", self.presets[self.index].name, 3)

    def toggle_lock(self):
        self.locked = not self.locked
        self.show("preset", self.presets[self.index].name + ("  [locked]" if self.locked else "  [unlocked]"), 3)

    # --------------------------------------------------- per frame
    def frame(self, screen, stereo):
        now = time.monotonic()
        t = now - self.t0
        a = self.an
        a.update(stereo, now)
        if not self.locked and t - self.switched_at > self.PRESET_SECONDS and (a.beat or t - self.switched_at > self.PRESET_SECONDS + 4):
            self.random_preset()
        p = self.presets[self.index]
        p.frame(a, t)
        if a.beat and p.flash_on_beat:
            # brighten what is already there (~+35%) instead of adding flat gray
            self.tmp.blit(self.fb, (0, 0))
            self.tmp.fill((90, 90, 90), special_flags=pygame.BLEND_MULT)
            self.fb.blit(self.tmp, (0, 0), special_flags=pygame.BLEND_ADD)
        pygame.transform.smoothscale(self.fb, screen.get_size(), screen)
        self._draw_overlays(screen, now)

    def _draw_overlays(self, screen, now):
        sw, sh = screen.get_size()
        for key, (text, until, secs) in list(self.overlays.items()):
            left = until - now
            if left <= 0:
                del self.overlays[key]
                continue
            alpha = int(255 * min(1.0, left / 1.0))
            f = self.small if key == "help" else self.font
            surf = f.render(text, True, (255, 255, 255))
            shadow = f.render(text, True, (0, 0, 0))
            surf.set_alpha(alpha)
            shadow.set_alpha(alpha)
            if key == "preset":
                pos = (sw // 40, sh // 30)
            elif key == "title":
                pos = ((sw - surf.get_width()) // 2, sh - sh // 8)
            else:
                pos = ((sw - surf.get_width()) // 2, sh - sh // 18)
            screen.blit(shadow, (pos[0] + 2, pos[1] + 2))
            screen.blit(surf, pos)
