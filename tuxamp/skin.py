"""Procedural recreation of the classic Winamp 2.x base skin.

Everything is drawn at Winamp's native 275px width and scaled up by the app.
"""

import pygame

from . import font

# ---------------------------------------------------------------- palette
FRAME = (57, 57, 77)
FRAME_TEX = (63, 63, 84)
FRAME_HI = (115, 115, 142)
FRAME_LO = (22, 22, 31)
TITLE_BG = (33, 33, 48)
GOLD = (222, 190, 90)
GOLD_DK = (128, 100, 38)
GRAY_STRIPE = (110, 110, 125)
GRAY_STRIPE_DK = (70, 70, 82)
BTN = (189, 189, 206)
BTN_PRESSED = (150, 150, 168)
BTN_HI = (238, 238, 246)
BTN_LO = (88, 88, 104)
ICON = (25, 25, 35)
LCD = (0, 0, 0)
LCD_GREEN = (0, 232, 0)
LCD_DIM = (0, 72, 0)
LCD_DOT = (24, 33, 41)
LABEL = (190, 190, 210)
LED_ON = (0, 255, 0)
LED_OFF = (30, 70, 30)

# Winamp default viscolor.txt
VIS_SPEC = [(239, 49, 16), (206, 41, 16), (214, 90, 0), (214, 102, 0), (214, 115, 0),
            (198, 123, 8), (222, 165, 24), (214, 181, 33), (189, 222, 41), (148, 222, 33),
            (41, 206, 16), (50, 190, 16), (57, 181, 16), (49, 156, 8), (41, 148, 0), (24, 132, 8)]
VIS_OSC = [(255, 255, 255), (214, 214, 222), (181, 189, 189), (160, 170, 175), (148, 156, 165)]
VIS_PEAK = (150, 150, 150)

PL_NORMAL = (0, 255, 0)
PL_CURRENT = (255, 255, 255)
PL_SELBG = (0, 0, 198)
PL_BG = (0, 0, 0)

W = 275
MAIN_H = 116
EQ_H = 116
PL_H = 232
SHADE_H = 14


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def level_color(t):
    """green -> yellow -> red for t in 0..1"""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return lerp((40, 200, 20), (230, 210, 30), t * 2)
    return lerp((230, 210, 30), (225, 40, 20), (t - 0.5) * 2)


# ---------------------------------------------------------------- primitives

def bevel(s, r, raised=True, face=None, hi=FRAME_HI, lo=FRAME_LO):
    r = pygame.Rect(r)
    if face is not None:
        s.fill(face, r)
    a, b = (hi, lo) if raised else (lo, hi)
    s.fill(a, (r.x, r.y, r.w, 1))
    s.fill(a, (r.x, r.y, 1, r.h))
    s.fill(b, (r.x, r.bottom - 1, r.w, 1))
    s.fill(b, (r.right - 1, r.y, 1, r.h))


def inset(s, r, face=LCD):
    """Recessed black panel with a 1px bevel around it."""
    r = pygame.Rect(r)
    bevel(s, r.inflate(2, 2), raised=False)
    s.fill(face, r)


def button(s, r, pressed=False, face=None):
    r = pygame.Rect(r)
    face = face or (BTN_PRESSED if pressed else BTN)
    s.fill(ICON, r)
    inner = r.inflate(-2, -2)
    s.fill(face, inner)
    if pressed:
        s.fill(BTN_LO, (inner.x, inner.y, inner.w, 1))
        s.fill(BTN_LO, (inner.x, inner.y, 1, inner.h))
    else:
        s.fill(BTN_HI, (inner.x, inner.y, inner.w, 1))
        s.fill(BTN_HI, (inner.x, inner.y, 1, inner.h))
        s.fill(BTN_LO, (inner.x, inner.bottom - 1, inner.w, 1))
        s.fill(BTN_LO, (inner.right - 1, inner.y, 1, inner.h))


def text(s, x, y, txt, color=LABEL):
    s.blit(font.render_text(txt, color), (x, y))


def text_center(s, r, txt, color=ICON, dy=0):
    r = pygame.Rect(r)
    w = font.text_width(txt) - 1
    s.blit(font.render_text(txt, color), (r.x + (r.w - w) // 2, r.y + (r.h - 5) // 2 + dy))


def frame_background(s, r):
    r = pygame.Rect(r)
    s.fill(FRAME, r)
    # subtle diagonal brushed texture
    for y in range(r.y, r.bottom):
        for x in range(r.x + (y % 4), r.right, 4):
            s.set_at((x, y), FRAME_TEX)
    bevel(s, r)


def titlebar(s, y, label, active, buttons=("min", "shade", "close"), menu=True, w=W):
    s.fill(TITLE_BG, (0, y, w, 14))
    bevel(s, (0, y, w, 14))
    c1, c2 = (GOLD, GOLD_DK) if active else (GRAY_STRIPE, GRAY_STRIPE_DK)
    tw = font.text_width(label) - 1
    tx = (w - tw) // 2
    left = 18 if menu else 6
    right = w - 6 - 10 * len(buttons)
    for i, yy in enumerate(range(y + 4, y + 10)):
        col = c1 if i % 2 == 0 else c2
        s.fill(col, (left, yy, tx - 6 - left, 1))
        s.fill(col, (tx + tw + 6, yy, right - (tx + tw + 6), 1))
    text(s, tx, y + 4, label, (255, 255, 255) if active else (180, 180, 190))
    if menu:
        r = pygame.Rect(6, y + 3, 9, 9)
        button(s, r)
        s.fill(c1 if active else ICON, (8, y + 6, 5, 3))
    bx = w - 6 - 10 * len(buttons) + 1
    for b in buttons:
        r = pygame.Rect(bx, y + 3, 9, 9)
        button(s, r)
        if b == "min":
            s.fill(ICON, (bx + 2, y + 8, 5, 2))
        elif b == "shade":
            s.fill(ICON, (bx + 2, y + 5, 5, 1))
            s.fill(ICON, (bx + 2, y + 7, 5, 1))
        elif b == "close":
            for k in range(5):
                s.set_at((bx + 2 + k, y + 5 + k), ICON)
                s.set_at((bx + 6 - k, y + 5 + k), ICON)
        bx += 10


# ---------------------------------------------------------------- icons

def icon_prev(s, r):
    cx, cy = r.centerx, r.centery
    s.fill(ICON, (cx - 6, cy - 4, 2, 9))
    pygame.draw.polygon(s, ICON, [(cx - 4, cy), (cx, cy - 4), (cx, cy + 4)])
    pygame.draw.polygon(s, ICON, [(cx, cy), (cx + 4, cy - 4), (cx + 4, cy + 4)])


def icon_next(s, r):
    cx, cy = r.centerx, r.centery
    pygame.draw.polygon(s, ICON, [(cx - 5, cy - 4), (cx - 5, cy + 4), (cx - 1, cy)])
    pygame.draw.polygon(s, ICON, [(cx - 1, cy - 4), (cx - 1, cy + 4), (cx + 3, cy)])
    s.fill(ICON, (cx + 3, cy - 4, 2, 9))


def icon_play(s, r):
    cx, cy = r.centerx, r.centery
    pygame.draw.polygon(s, ICON, [(cx - 3, cy - 5), (cx - 3, cy + 5), (cx + 3, cy)])


def icon_pause(s, r):
    cx, cy = r.centerx, r.centery
    s.fill(ICON, (cx - 4, cy - 4, 3, 9))
    s.fill(ICON, (cx + 1, cy - 4, 3, 9))


def icon_stop(s, r):
    cx, cy = r.centerx, r.centery
    s.fill(ICON, (cx - 4, cy - 4, 9, 9))


def icon_eject(s, r):
    cx, cy = r.centerx, r.centery
    pygame.draw.polygon(s, ICON, [(cx - 5, cy + 1), (cx + 5, cy + 1), (cx, cy - 5)])
    s.fill(ICON, (cx - 5, cy + 3, 11, 2))


LOGO_RECT = (246, 87, 21, 21)


def logo(s, oy=0):
    """Badge frame in the corner of the main window.  The Tux-with-a-bolt
    picture inside is drawn at screen resolution by the app (icon.render)."""
    x, y, w, h = LOGO_RECT
    s.fill((20, 20, 30), (x, y + oy, w, h))
    bevel(s, (x, y + oy, w, h))


# ---------------------------------------------------------------- sliders

def h_slider(s, r, frac, thumb_w, color, pressed=False):
    r = pygame.Rect(r)
    inset(s, r.inflate(0, -6), face=(15, 15, 22))
    bar = r.inflate(-2, -8)
    s.fill(color, bar)
    # notches
    for x in range(bar.x + 2, bar.right, 3):
        s.fill(lerp(color, (0, 0, 0), 0.35), (x, bar.y, 1, bar.h))
    tx = r.x + int(round(frac * (r.w - thumb_w)))
    button(s, (tx, r.y + 1, thumb_w, r.h - 2), pressed)
    s.fill(BTN_LO, (tx + thumb_w // 2 - 2, r.y + 4, 1, r.h - 8))
    s.fill(BTN_LO, (tx + thumb_w // 2, r.y + 4, 1, r.h - 8))
    s.fill(BTN_LO, (tx + thumb_w // 2 + 2, r.y + 4, 1, r.h - 8))


def v_slider(s, r, frac_from_top, pressed=False):
    """EQ slider. frac_from_top: 0 = +12dB, 1 = -12dB."""
    r = pygame.Rect(r)
    groove = pygame.Rect(r.centerx - 2, r.y, 5, r.h)
    inset(s, groove.inflate(-2, -2), face=(10, 10, 16))
    th = 11
    ty = r.y + int(round(frac_from_top * (r.h - th)))
    col = level_color(1.0 - frac_from_top)
    s.fill(col, (groove.x + 1, ty + th // 2, groove.w - 2, groove.bottom - 1 - (ty + th // 2)))
    button(s, (r.x + 1, ty, r.w - 2, th), pressed)
    s.fill(BTN_LO, (r.x + 4, ty + 5, r.w - 8, 1))
