"""TuxAmp's icon: Tux the penguin holding a lightning bolt.

Drawn from simple shapes on a 256x256 design grid, rendered 4x larger and
filtered down, so it stays smooth at any size: desktop icons (16-512px), the
window icon, and the badge in the corner of the main window.
"""

import math

import pygame

BLACK = (22, 22, 28)
RIM = (120, 124, 140)           # thin light edge so Tux shows up on dark backgrounds
WHITE = (246, 246, 240)
SHADE = (205, 208, 214)
BEAK = (250, 178, 30)
BEAK_DK = (205, 120, 10)
FEET = (250, 178, 30)
FEET_DK = (205, 120, 10)
BOLT = (255, 214, 60)
BOLT_DK = (170, 105, 0)

# the classic Winamp-style bolt shape, on a 16x16 grid
_BOLT = [(10, 1), (3, 9), (7, 9), (5, 15), (13, 6), (9, 6), (11, 1)]

_cache = {}


def _ellipse(s, color, cx, cy, w, h, k):
    pygame.draw.ellipse(s, color, pygame.Rect(round((cx - w / 2) * k), round((cy - h / 2) * k),
                                              round(w * k), round(h * k)))


def _poly(s, color, pts, k):
    pygame.draw.polygon(s, color, [(round(x * k), round(y * k)) for x, y in pts])


def _flipper(cx, cy, length, width, angle_deg):
    """A tapered flipper: points of a long teardrop rotated about its root."""
    a = math.radians(angle_deg)
    pts = []
    for i in range(24):
        t = i / 23 * math.pi
        x = math.sin(t) * width / 2 * (1 - 0.5 * (1 - math.cos(t)) / 2)
        y = (1 - math.cos(t)) / 2 * length
        pts.append((x, y))
    pts += [(-x, y) for x, y in reversed(pts)]
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]


def _bolt(ox, oy, size, tilt):
    a = math.radians(tilt)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y in _BOLT:
        x, y = (x - 8) * size / 16, (y - 8) * size / 16
        out.append((ox + x * ca - y * sa, oy + x * sa + y * ca))
    return out


def _draw(s, k):
    # --- rim: everything a bit larger in a light grey, for dark backgrounds
    rim = 3.5
    _ellipse(s, RIM, 122, 152, 150 + 2 * rim, 178 + 2 * rim, k)            # body
    _ellipse(s, RIM, 122, 78, 102 + 2 * rim, 96 + 2 * rim, k)              # head
    _poly(s, RIM, _flipper(58, 128, 86, 34 + 2 * rim, 28), k)              # left flipper (down)

    # --- body and head
    _ellipse(s, BLACK, 122, 152, 150, 178, k)
    _ellipse(s, BLACK, 122, 78, 102, 96, k)
    _poly(s, BLACK, _flipper(58, 128, 86, 34, 28), k)

    # --- white belly with a soft shadow on one side
    _ellipse(s, SHADE, 124, 168, 112, 138, k)
    _ellipse(s, WHITE, 120, 166, 102, 132, k)

    # --- face: two white eye patches, pupils looking up at the bolt
    _ellipse(s, WHITE, 106, 72, 30, 42, k)
    _ellipse(s, WHITE, 138, 72, 30, 42, k)
    _ellipse(s, BLACK, 110, 70, 13, 19, k)
    _ellipse(s, BLACK, 141, 69, 13, 19, k)
    _ellipse(s, WHITE, 113, 65, 4, 5, k)
    _ellipse(s, WHITE, 144, 64, 4, 5, k)

    # --- beak
    _ellipse(s, BEAK_DK, 123, 100, 50, 22, k)
    _ellipse(s, BEAK, 123, 96, 50, 18, k)

    # --- feet
    _ellipse(s, FEET_DK, 86, 238, 64, 26, k)
    _ellipse(s, FEET_DK, 158, 238, 64, 26, k)
    _ellipse(s, FEET, 84, 235, 60, 20, k)
    _ellipse(s, FEET, 160, 235, 60, 20, k)

    # --- lightning bolt held up in the right flipper
    outline = _bolt(212, 62, 128, 8)
    fill = _bolt(212, 62, 108, 8)
    _poly(s, BOLT_DK, outline, k)
    _poly(s, BOLT, fill, k)
    # right flipper raised in front of the bolt, so Tux is gripping it
    arm = (166, 140, 64, 34, 219)
    _poly(s, RIM, _flipper(*arm[:3], arm[3] + 2 * rim, arm[4]), k)
    _poly(s, BLACK, _flipper(*arm), k)


def render(w, h=None):
    """Tux holding a lightning bolt, as an RGBA surface of the given size."""
    h = h or w
    key = (w, h)
    if key in _cache:
        return _cache[key]
    side = min(w, h)
    k = side * 4 / 256                       # supersample 4x, then filter down
    big = pygame.Surface((round(256 * k), round(256 * k)), pygame.SRCALPHA)
    big.fill((0, 0, 0, 0))
    _draw(big, k)
    img = pygame.transform.smoothscale(big, (side, side))
    if (w, h) != (side, side):
        out = pygame.Surface((w, h), pygame.SRCALPHA)
        out.fill((0, 0, 0, 0))
        out.blit(img, ((w - side) // 2, (h - side) // 2))
        img = out
    if len(_cache) > 64:                     # a zoom drag asks for many sizes
        _cache.clear()
    _cache[key] = img
    return img
