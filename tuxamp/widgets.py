"""Screen-resolution overlays: popup menus and the file browser."""

import os

import pygame

from .meta import is_audio

PLAYLIST_EXTS = {".m3u", ".m3u8", ".pls"}

MENU_BG = (212, 208, 200)
MENU_FG = (0, 0, 0)
MENU_HI = (10, 36, 106)
MENU_HI_FG = (255, 255, 255)
MENU_DIS = (128, 128, 128)


def get_font(px):
    names = "arial,liberationsans,dejavusans,freesans,helvetica"
    try:
        return pygame.font.SysFont(names, px)
    except Exception:
        return pygame.font.Font(None, px)


class PopupMenu:
    """Classic Windows-style popup menu. items: list of (label, callback) or None for separator."""

    def __init__(self, items, pos, screen_size, scale):
        self.items = items
        self.font = get_font(max(11, int(7 * scale)))
        self.row = self.font.get_linesize() + 4
        self.sep = 7
        w = max((self.font.size(it[0])[0] for it in items if it), default=50) + 40
        h = sum(self.sep if it is None else self.row for it in items) + 6
        x, y = pos
        sw, sh = screen_size
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.rect = pygame.Rect(x, y, w, h)
        self.hover = -1

    def _index_at(self, pos):
        if not self.rect.collidepoint(pos):
            return -1
        y = self.rect.y + 3
        for i, it in enumerate(self.items):
            hgt = self.sep if it is None else self.row
            if y <= pos[1] < y + hgt:
                return i if it is not None else -1
            y += hgt
        return -1

    def handle(self, ev):
        """Returns True while menu stays open."""
        if ev.type == pygame.MOUSEMOTION:
            self.hover = self._index_at(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button in (1, 3):
            i = self._index_at(ev.pos)
            if i >= 0:
                cb = self.items[i][1]
                if cb:
                    cb()
                return False
            if not self.rect.collidepoint(ev.pos):
                return False
        elif ev.type == pygame.MOUSEBUTTONDOWN and not self.rect.collidepoint(ev.pos):
            return False
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            return False
        return True

    def draw(self, screen):
        r = self.rect
        screen.fill(MENU_BG, r)
        pygame.draw.line(screen, (255, 255, 255), r.topleft, (r.right - 2, r.top))
        pygame.draw.line(screen, (255, 255, 255), r.topleft, (r.left, r.bottom - 2))
        pygame.draw.line(screen, (64, 64, 64), (r.left, r.bottom - 1), (r.right - 1, r.bottom - 1))
        pygame.draw.line(screen, (64, 64, 64), (r.right - 1, r.top), (r.right - 1, r.bottom - 1))
        y = r.y + 3
        for i, it in enumerate(self.items):
            if it is None:
                cy = y + self.sep // 2
                pygame.draw.line(screen, (128, 128, 128), (r.x + 3, cy), (r.right - 4, cy))
                pygame.draw.line(screen, (255, 255, 255), (r.x + 3, cy + 1), (r.right - 4, cy + 1))
                y += self.sep
                continue
            label, cb = it[0], it[1]
            fg = MENU_FG if cb else MENU_DIS
            if i == self.hover and cb:
                screen.fill(MENU_HI, (r.x + 3, y, r.w - 6, self.row))
                fg = MENU_HI_FG
            if len(it) > 2 and it[2]:
                screen.blit(self.font.render("✓", True, fg), (r.x + 6, y + 2))
            screen.blit(self.font.render(label, True, fg), (r.x + 22, y + 2))
            y += self.row


class FileBrowser:
    """Full-window file picker drawn in TuxAmp's colours.

    on_done(paths) receives a list of chosen files/directories (may be empty).
    """

    def __init__(self, start_dir, screen_size, scale, on_done, title="Open file(s)"):
        self.dir = start_dir if os.path.isdir(start_dir) else os.path.expanduser("~")
        self.on_done = on_done
        self.title = title
        self.scale = scale
        self.font = get_font(max(11, int(7 * scale)))
        self.small = get_font(max(10, int(6 * scale)))
        self.row = self.font.get_linesize() + 2
        self.selected = set()
        self.anchor = None
        self.scroll = 0
        self.last_click = (None, 0)
        self.show_hidden = False
        self.resize(screen_size)
        self.refresh()

    def resize(self, size):
        w, h = size
        self.rect = pygame.Rect(0, 0, w, h)
        pad = 8
        self.header = pygame.Rect(pad, pad, w - 2 * pad, self.row * 2 + 6)
        bh = self.row + 10
        self.list_rect = pygame.Rect(pad, self.header.bottom + 4, w - 2 * pad - 14,
                                     h - self.header.bottom - 4 - bh - 2 * pad)
        self.scroll_rect = pygame.Rect(self.list_rect.right + 2, self.list_rect.y, 12, self.list_rect.h)
        labels = [("up", "Up"), ("home", "Home"), ("addfolder", "Add folder"),
                  ("add", "Add selected"), ("cancel", "Cancel")]
        self.buttons = []
        x = pad
        by = h - pad - bh
        for key, lab in labels:
            bw = self.font.size(lab)[0] + 16
            self.buttons.append((key, lab, pygame.Rect(x, by, bw, bh)))
            x += bw + 6
        self.pressed = None

    @property
    def visible_rows(self):
        return max(1, self.list_rect.h // self.row)

    def refresh(self):
        try:
            names = os.listdir(self.dir)
        except OSError:
            names = []
        dirs, files = [], []
        for n in names:
            if n.startswith(".") and not self.show_hidden:
                continue
            p = os.path.join(self.dir, n)
            if os.path.isdir(p):
                dirs.append(n)
            elif is_audio(n) or os.path.splitext(n)[1].lower() in PLAYLIST_EXTS:
                files.append(n)
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        self.entries = [("..", True)] if os.path.dirname(self.dir) != self.dir else []
        self.entries += [(d, True) for d in dirs] + [(f, False) for f in files]
        self.selected = set()
        self.anchor = None
        self.scroll = 0

    def _enter(self, name):
        if name == "..":
            self.dir = os.path.dirname(self.dir)
        else:
            self.dir = os.path.join(self.dir, name)
        self.refresh()

    def _finish(self, paths):
        self.on_done(paths)
        return False

    def _chosen(self):
        out = []
        for i in sorted(self.selected):
            name, isdir = self.entries[i]
            if name != "..":
                out.append(os.path.join(self.dir, name))
        return out

    def handle(self, ev):
        """Returns True while the browser stays open."""
        if ev.type == pygame.KEYDOWN:
            n = len(self.entries)
            cur = max(self.selected) if self.selected else -1
            if ev.key == pygame.K_ESCAPE:
                return self._finish([])
            if ev.key == pygame.K_BACKSPACE:
                self._enter("..")
            elif ev.key in (pygame.K_DOWN, pygame.K_UP, pygame.K_PAGEDOWN, pygame.K_PAGEUP,
                            pygame.K_HOME, pygame.K_END) and n:
                step = {pygame.K_DOWN: 1, pygame.K_UP: -1,
                        pygame.K_PAGEDOWN: self.visible_rows, pygame.K_PAGEUP: -self.visible_rows,
                        pygame.K_HOME: -n, pygame.K_END: n}[ev.key]
                cur = max(0, min(n - 1, cur + step))
                self.selected = {cur}
                self.anchor = cur
                self._ensure_visible(cur)
            elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if len(self.selected) == 1:
                    name, isdir = self.entries[cur]
                    if isdir:
                        self._enter(name)
                        return True
                if self.selected:
                    return self._finish(self._chosen())
            elif ev.key == pygame.K_a and ev.mod & pygame.KMOD_CTRL:
                self.selected = {i for i, e in enumerate(self.entries) if not e[1]}
            elif ev.key == pygame.K_h and ev.mod & pygame.KMOD_CTRL:
                self.show_hidden = not self.show_hidden
                self.refresh()
            return True
        if ev.type == pygame.MOUSEWHEEL:
            self.scroll -= ev.y * 3
            self._clamp_scroll()
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for key, _, r in self.buttons:
                if r.collidepoint(ev.pos):
                    self.pressed = key
                    return True
            if self.scroll_rect.collidepoint(ev.pos):
                self._scroll_to_y(ev.pos[1])
                self.dragging_scroll = True
                return True
            if self.list_rect.collidepoint(ev.pos):
                idx = self.scroll + (ev.pos[1] - self.list_rect.y) // self.row
                if 0 <= idx < len(self.entries):
                    now = pygame.time.get_ticks()
                    if self.last_click[0] == idx and now - self.last_click[1] < 400:
                        name, isdir = self.entries[idx]
                        self.last_click = (None, 0)
                        if isdir:
                            self._enter(name)
                        else:
                            return self._finish([os.path.join(self.dir, name)])
                        return True
                    self.last_click = (idx, now)
                    mods = pygame.key.get_mods()
                    if mods & pygame.KMOD_SHIFT and self.anchor is not None:
                        a, b = sorted((self.anchor, idx))
                        self.selected = set(range(a, b + 1))
                    elif mods & pygame.KMOD_CTRL:
                        self.selected ^= {idx}
                        self.anchor = idx
                    else:
                        self.selected = {idx}
                        self.anchor = idx
            return True
        if ev.type == pygame.MOUSEMOTION and getattr(self, "dragging_scroll", False):
            self._scroll_to_y(ev.pos[1])
            return True
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self.dragging_scroll = False
            key, self.pressed = self.pressed, None
            for k, _, r in self.buttons:
                if k == key and r.collidepoint(ev.pos):
                    if k == "cancel":
                        return self._finish([])
                    if k == "up":
                        self._enter("..")
                    elif k == "home":
                        self.dir = os.path.expanduser("~")
                        self.refresh()
                    elif k == "addfolder":
                        chosen = [p for p in self._chosen() if os.path.isdir(p)]
                        return self._finish(chosen or [self.dir])
                    elif k == "add":
                        chosen = self._chosen()
                        if chosen:
                            return self._finish(chosen)
            return True
        return True

    def _scroll_to_y(self, y):
        n = len(self.entries)
        frac = (y - self.scroll_rect.y) / max(1, self.scroll_rect.h)
        self.scroll = int(frac * n - self.visible_rows / 2)
        self._clamp_scroll()

    def _clamp_scroll(self):
        self.scroll = max(0, min(self.scroll, len(self.entries) - self.visible_rows))

    def _ensure_visible(self, i):
        if i < self.scroll:
            self.scroll = i
        elif i >= self.scroll + self.visible_rows:
            self.scroll = i - self.visible_rows + 1
        self._clamp_scroll()

    def draw(self, screen):
        screen.fill((44, 44, 60), self.rect)
        # header
        pygame.draw.rect(screen, (0, 0, 0), self.header)
        pygame.draw.rect(screen, (90, 90, 115), self.header, 1)
        screen.blit(self.font.render(self.title, True, (230, 200, 90)), (self.header.x + 6, self.header.y + 3))
        path = self.dir
        while self.small.size(path)[0] > self.header.w - 12 and len(path) > 4:
            path = "…" + path[2:]
        screen.blit(self.small.render(path, True, (0, 226, 0)), (self.header.x + 6, self.header.y + 4 + self.row))
        # list
        lr = self.list_rect
        screen.fill((0, 0, 0), lr)
        pygame.draw.rect(screen, (90, 90, 115), lr.inflate(2, 2), 1)
        clip = screen.get_clip()
        screen.set_clip(lr)
        for row in range(self.visible_rows + 1):
            i = self.scroll + row
            if i >= len(self.entries):
                break
            name, isdir = self.entries[i]
            y = lr.y + row * self.row
            if i in self.selected:
                screen.fill((0, 0, 198), (lr.x, y, lr.w, self.row))
            if isdir:
                label = "[" + name + "]"
                col = (230, 200, 90)
            else:
                label = name
                col = (0, 255, 0)
            screen.blit(self.font.render(label, True, col), (lr.x + 4, y + 1))
        screen.set_clip(clip)
        # scrollbar
        sr = self.scroll_rect
        screen.fill((24, 24, 32), sr)
        n = len(self.entries)
        if n > self.visible_rows:
            th = max(16, int(sr.h * self.visible_rows / n))
            ty = sr.y + int((sr.h - th) * self.scroll / max(1, n - self.visible_rows))
        else:
            th, ty = sr.h, sr.y
        _bevel(screen, pygame.Rect(sr.x, ty, sr.w, th), False)
        # buttons
        for key, lab, r in self.buttons:
            _bevel(screen, r, self.pressed == key)
            t = self.font.render(lab, True, (20, 20, 30))
            screen.blit(t, t.get_rect(center=r.center))
        hint = "Double-click: open/add   Ctrl/Shift-click: multi-select   Ctrl+H: hidden files"
        ht = self.small.render(hint, True, (150, 150, 170))
        last = self.buttons[-1][2]
        if last.right + 12 + ht.get_width() < self.rect.w:
            screen.blit(ht, (last.right + 12, last.centery - ht.get_height() // 2))


def _bevel(screen, r, pressed):
    face = (160, 160, 178) if pressed else (189, 189, 206)
    hi, lo = ((90, 90, 105), (235, 235, 245)) if pressed else ((235, 235, 245), (90, 90, 105))
    screen.fill(face, r)
    pygame.draw.line(screen, hi, r.topleft, (r.right - 1, r.top))
    pygame.draw.line(screen, hi, r.topleft, (r.left, r.bottom - 1))
    pygame.draw.line(screen, lo, (r.left, r.bottom - 1), (r.right - 1, r.bottom - 1))
    pygame.draw.line(screen, lo, (r.right - 1, r.top), (r.right - 1, r.bottom - 1))
