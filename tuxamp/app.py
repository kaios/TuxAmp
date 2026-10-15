"""TuxAmp main application: main window, equalizer and playlist editor."""

import json
import math
import os
import random
import threading
import time
import queue

import numpy as np
import pygame

from . import cast, font, icon, sdlext, skin
from .audio import Engine
from .meta import is_audio, read_info, display_title
from .widgets import PopupMenu, FileBrowser, get_font, PLAYLIST_EXTS

VERSION = "v0.1.1 alpha"
AUTHOR = "Shawn Rapaz"
RELEASE_DATE = "2026-10-15"
APP_TITLE = "TuxAmp"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "tuxamp")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
INTRO_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "intro.wav")
INTRO_TEXT = "TUXAMP, IT REALLY WHIPS THE PENGUIN'S ASS!"

W = skin.W
ROW_H = 11
# playlist window: resizable in Winamp-style 25x29 px steps
SIZE_GRIP = 10                 # bottom-right zoom grip on the main / EQ window
MIN_SCALE, MAX_SCALE = 1.0, 6.0  # free zoom range of the bottom-right corner grip
SNAP = 0.015                   # a zoom this close to a whole number snaps to it (pixel-perfect)

EQ_PRESETS = [
    ("Flat", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
    ("Classical", [0, 0, 0, 0, 0, 0, -7.2, -7.2, -7.2, -9.6]),
    ("Club", [0, 0, 8, 5.6, 5.6, 5.6, 3.2, 0, 0, 0]),
    ("Dance", [9.6, 7.2, 2.4, 0, 0, -5.6, -7.2, -7.2, 0, 0]),
    ("Full Bass", [-8, 9.6, 9.6, 5.6, 1.6, -4, -8, -10.4, -11.2, -11.2]),
    ("Full Bass & Treble", [7.2, 5.6, 0, -7.2, -4.8, 1.6, 8, 11.2, 12, 12]),
    ("Full Treble", [-9.6, -9.6, -9.6, -4, 2.4, 11.2, 12, 12, 12, 12]),
    ("Laptop Speakers", [4.8, 11.2, 5.6, -3.2, -2.4, 1.6, 4.8, 9.6, 12, 12]),
    ("Large Hall", [10.4, 10.4, 5.6, 5.6, 0, -4.8, -4.8, -4.8, 0, 0]),
    ("Live", [-4.8, 0, 4, 5.6, 5.6, 5.6, 4, 2.4, 2.4, 2.4]),
    ("Party", [7.2, 7.2, 0, 0, 0, 0, 0, 0, 7.2, 7.2]),
    ("Pop", [-1.6, 4.8, 7.2, 8, 5.6, 0, -2.4, -2.4, -1.6, -1.6]),
    ("Reggae", [0, 0, 0, -5.6, 0, 6.4, 6.4, 0, 0, 0]),
    ("Rock", [8, 4.8, -5.6, -8, -3.2, 4, 8.8, 11.2, 11.2, 11.2]),
    ("Ska", [-2.4, -4.8, -4, 0, 4, 5.6, 8.8, 9.6, 11.2, 9.6]),
    ("Soft", [4.8, 1.6, 0, -2.4, 0, 4, 8, 9.6, 11.2, 12]),
    ("Soft Rock", [4, 4, 2.4, 0, -4, -5.6, -3.2, 0, 2.4, 8.8]),
    ("Techno", [8, 5.6, 0, -5.6, -4.8, 0, 8, 9.6, 9.6, 8.8]),
]


def fmt_time(sec):
    if sec is None:
        return ""
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60}:{sec % 60:02d}"


class Track:
    __slots__ = ("path", "info", "title")

    def __init__(self, path):
        self.path = path
        self.info = None
        self.title = os.path.splitext(os.path.basename(path))[0]

    @property
    def duration(self):
        return self.info.get("duration") if self.info else None


class TuxAmp:
    def __init__(self, files=(), scale=None, framed=False, intro=None):
        pygame.init()
        try:
            pygame.display.set_icon(icon.render(64))     # window / taskbar icon
        except pygame.error:
            pass
        pygame.display.set_caption(APP_TITLE)
        self.engine = Engine()
        self.framed = framed

        cfg = self._load_config()
        self.scale = self._clamp_scale(scale or cfg.get("scale") or self._auto_scale())
        self.show_eq = cfg.get("show_eq", True)
        self.show_pl = cfg.get("show_pl", True)
        self.shade = False
        self.shuffle = cfg.get("shuffle", False)
        self.repeat = cfg.get("repeat", False)
        self.time_remaining = cfg.get("time_remaining", False)
        self.vis_mode = cfg.get("vis_mode", 0)  # 0 spectrum, 1 oscilloscope, 2 off
        # The three windows always share one size and zoom together.
        self.pl_w, self.pl_h = skin.W, skin.PL_H
        self.viz = None
        self.viz_fullscreen = cfg.get("viz_fullscreen", True)
        self.viz_window_size = tuple(cfg.get("viz_window_size", (960, 540)))
        self._cursor = None
        self._last_vis_click = 0
        self.last_dir = cfg.get("last_dir", os.path.expanduser("~"))
        self.eq_auto = cfg.get("eq_auto", False)
        self.intro_enabled = cfg.get("intro", True)
        self.intro_channel = pygame.mixer.Channel(1)
        self._pending_play = None
        e = self.engine
        e.volume = cfg.get("volume", 0.75)
        e.balance = cfg.get("balance", 0.0)
        e.eq_on = cfg.get("eq_on", False)
        e.preamp = cfg.get("preamp", 0.0)
        e.bands = cfg.get("bands", [0.0] * 10)

        self.playlist = []
        self.current = -1
        self.selected = set()
        self.sel_anchor = None
        self.pl_scroll = 0
        self.info_q = queue.Queue()
        threading.Thread(target=self._info_worker, daemon=True).start()

        self.pressed = None          # (section, id) of the button held down
        self.drag = None             # active slider / window drag
        self.menu = None
        self.browser = None
        self.focused = True
        self.focus_area = "main"
        self.temp_msg = None
        self.temp_msg_until = 0
        self.marquee_off = 0.0
        self.marquee_key = None
        self.last_pl_click = (None, 0)
        self.vis_bars = np.zeros(19)
        self.vis_peaks = np.zeros(19)
        self.vis_peak_vel = np.zeros(19)
        self.track_info = {}
        self.error_count = 0
        self.cast_server = None
        self.cast_devices = []       # found by the last network search
        self.cast_searching = False
        self.cast_found_at = None
        self._cast_result = None
        self._cast_menu = None
        self._cast_menu_pos = (0, 0)

        self._open_window()
        self.pl_font = None
        self._make_fonts()
        self.bg_main = self._render_main_bg()
        self.bg_eq = self._render_eq_bg()
        self.bg_pl = self._render_pl_bg()

        if files:
            self.add_paths(files)
            if self.playlist:
                self._pending_play = 0   # starts once the intro voice finishes
        else:
            for p in cfg.get("playlist", []):
                if os.path.exists(p):
                    self._append(p)
            idx = cfg.get("current", -1)
            if 0 <= idx < len(self.playlist):
                self.current = idx
                self.selected = {idx}
        if e.eq_on:
            e.set_eq(e.eq_on, e.preamp, e.bands)
        if self.intro_enabled if intro is None else intro:
            self.play_intro()

    def play_intro(self):
        """The startup voice, in the spirit of Winamp's famous llama sample."""
        try:
            snd = pygame.mixer.Sound(INTRO_WAV)
        except (pygame.error, FileNotFoundError):
            return
        self.intro_channel.play(snd)
        self.intro_channel.set_volume(max(0.35, self.engine.volume))
        self.flash(INTRO_TEXT, snd.get_length() + 2.0)

    def toggle_intro(self):
        self.intro_enabled = not self.intro_enabled
        self.flash("INTRO VOICE: " + ("ON" if self.intro_enabled else "OFF"), 1.5)

    # ================================================================ setup
    def _auto_scale(self):
        try:
            h = pygame.display.Info().current_h
            return 3 if h >= 1440 else 2
        except Exception:
            return 2

    def _load_config(self):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self):
        e = self.engine
        cfg = {
            "scale": round(self.scale, 3), "show_eq": self.show_eq, "show_pl": self.show_pl,
            "shuffle": self.shuffle, "repeat": self.repeat,
            "time_remaining": self.time_remaining, "vis_mode": self.vis_mode,
            "last_dir": self.last_dir,
            "viz_fullscreen": self.viz_fullscreen, "viz_window_size": list(self.viz_window_size), "volume": e.volume, "balance": e.balance,
            "intro": self.intro_enabled, "eq_on": e.eq_on, "eq_auto": self.eq_auto, "preamp": e.preamp, "bands": e.bands,
            "playlist": [t.path for t in self.playlist], "current": self.current,
        }
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=1)
        except OSError:
            pass

    def _make_fonts(self):
        px = max(9, int(round(8.5 * self.scale)))
        cache = self.__dict__.setdefault("_font_cache", {})
        if px not in cache:          # SysFont is slow; a zoom drag asks for many sizes
            cache[px] = get_font(px)
        self.pl_font = cache[px]

    def _clamp_scale(self, s, snap=SNAP):
        s = max(MIN_SCALE, min(MAX_SCALE, float(s)))
        if abs(s - round(s)) < snap:
            s = float(round(s))
        return s

    def screen_size(self):
        return round(self.win_w() * self.scale), round(self.base_height() * self.scale)

    def _sections(self):
        """List of (name, y, h) for the visible windows, top to bottom."""
        out = [("main", 0, skin.SHADE_H if self.shade else skin.MAIN_H)]
        y = out[0][2]
        if self.show_eq:
            out.append(("eq", y, skin.EQ_H))
            y += skin.EQ_H
        if self.show_pl:
            out.append(("pl", y, self.pl_h))
            y += self.pl_h
        return out

    def win_w(self):
        return max(W, self.pl_w) if self.show_pl else W

    def pl_geo(self):
        """Playlist-window layout; everything right/bottom-anchored moves when it is resized."""
        w, h = self.pl_w, self.pl_h
        by = h - 27
        return {
            "list": pygame.Rect(12, 20, w - 32, h - 58),
            "scroll": pygame.Rect(w - 16, 20, 8, h - 58),
            "close": (w - 15, 3, 9, 9),
            "btn_y": by,
            "listopts": (w - 45, by, 25, 18),
            "info": (w - 145, h - 32, 96, 9),
            "mini_x": w - 144, "mini_y": h - 18,
            "mtime": (w - 92, h - 19, 40, 9),
            "resize": (w - 20, h - 20, 20, 20),
            "bottom": (0, h - 36, w, 36),
        }

    def base_height(self):
        s = self._sections()
        return s[-1][1] + s[-1][2]

    def _sdl_window(self):
        return self._win

    def _window_pos(self):
        """Where we last put the window.  Reading the position back from X is
        unreliable (it lags behind moves, badly so over a remote display), so
        we track it ourselves and only fall back to asking X."""
        if self._pos is not None:
            return self._pos
        if self._win is not None:
            try:
                return tuple(self._win.position)
            except Exception:
                pass
        return None

    def _move_window(self, pos):
        win = self._sdl_window()
        if win is None:
            return
        pos = (int(pos[0]), int(pos[1]))
        if pos != self._pos:
            win.position = pos
            self._pos = pos
            self._moved_at = pygame.time.get_ticks()

    def _wrap_sdl_window(self):
        # pygame stores a raw, un-referenced pointer to this Window object
        # inside the SDL window and attaches it to every later event.  If the
        # object is ever garbage collected, the next event reads freed memory
        # and segfaults - so we keep every wrapper alive for the whole session.
        try:
            from pygame._sdl2.video import Window
            win = Window.from_display_module()
        except Exception:
            return None
        self._win_refs.append(win)
        return win

    def _resize_in_place(self, size):
        """Change the size of the existing skin window without set_mode().

        set_mode() rebuilds the window surface and waits on X several times
        (100+ ms); resizing the SDL window directly takes a few ms, and pygame
        swaps in the new window surface by itself.  Used for every step of a
        zoom drag so it keeps up with the mouse."""
        win = self._win
        if win is None or getattr(self, "_skin_window", False) is not True:
            return False
        try:
            win.size = size
            pygame.event.pump()
            surf = pygame.display.get_surface()
        except Exception:
            return False
        if surf is None or surf.get_size() != tuple(size):
            return False
        self.screen = surf
        return True

    def _open_window(self, pos=None):
        if not hasattr(self, "_win_refs"):
            self._win_refs = []
            self._win = None
            self._pos = None
        flags = 0 if self.framed else pygame.NOFRAME
        size = self.screen_size()
        if pos is not None or not self._resize_in_place(size):
            self.screen = pygame.display.set_mode(size, flags)
            self._skin_window = True
        base_size = (self.win_w(), self.base_height())
        if getattr(self, "base", None) is None or self.base.get_size() != base_size:
            self.base = pygame.Surface(base_size)
        self._up = None
        self._win = self._wrap_sdl_window()
        sdlext.keep_contents_on_resize(self)
        # A plain re-size leaves the window where it is; re-sending the
        # position on every step of a zoom drag only makes it judder.
        if pos is not None and self._win is not None:
            self._win.position = pos
            self._pos = tuple(pos)
            self._moved_at = pygame.time.get_ticks()
        if self.browser:
            self.browser.resize(size)

    def relayout(self):
        self._open_window()
        self.menu = None

    # ================================================================ playlist
    def _info_worker(self):
        while True:
            t = self.info_q.get()
            info = read_info(t.path)
            t.info = info
            t.title = display_title(t.path, info)

    def _append(self, path):
        t = Track(path)
        self.playlist.append(t)
        self.info_q.put(t)

    def add_paths(self, paths):
        added = 0
        for p in paths:
            p = os.path.abspath(os.path.expanduser(p))
            if os.path.isdir(p):
                self.last_dir = p
                for root, dirs, files in os.walk(p):
                    dirs.sort(key=str.lower)
                    for fn in sorted(files, key=str.lower):
                        if is_audio(fn):
                            self._append(os.path.join(root, fn))
                            added += 1
            elif os.path.isfile(p):
                self.last_dir = os.path.dirname(p)
                ext = os.path.splitext(p)[1].lower()
                if ext in PLAYLIST_EXTS:
                    added += self.load_playlist_file(p)
                elif is_audio(p):
                    self._append(p)
                    added += 1
        return added

    def load_playlist_file(self, path):
        base = os.path.dirname(path)
        n = 0
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return 0
        for ln in lines:
            ln = ln.strip()
            if path.lower().endswith(".pls"):
                if not ln.lower().startswith("file") or "=" not in ln:
                    continue
                ln = ln.split("=", 1)[1].strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("file://"):
                from urllib.parse import unquote
                ln = unquote(ln[7:])
            p = ln if os.path.isabs(ln) else os.path.join(base, ln)
            if os.path.isfile(p) and is_audio(p):
                self._append(p)
                n += 1
        return n

    def save_playlist(self):
        d = os.path.join(os.path.expanduser("~"), "Music")
        if not os.path.isdir(d):
            d = os.path.expanduser("~")
        path = os.path.join(d, "tuxamp-playlist.m3u")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in self.playlist:
                    dur = int(t.duration) if t.duration else -1
                    f.write(f"#EXTINF:{dur},{t.title}\n{t.path}\n")
            self.flash(f"SAVED {path}")
        except OSError as e:
            self.flash(f"SAVE FAILED: {e}")

    def play_index(self, i):
        if not (0 <= i < len(self.playlist)):
            return
        self.current = i
        self.track_info = {}
        self.engine.title = self.playlist[i].title
        self.engine.load(self.playlist[i].path, autoplay=True)
        self.marquee_off = 0
        self._ensure_pl_visible(i)

    def next_track(self, manual=True):
        n = len(self.playlist)
        if not n:
            return
        if self.shuffle and n > 1:
            choices = [i for i in range(n) if i != self.current]
            self.play_index(random.choice(choices))
            return
        i = self.current + 1
        if i >= n:
            if self.repeat or manual:
                i = 0
            else:
                self.current = n - 1
                return
        self.play_index(i)

    def prev_track(self):
        n = len(self.playlist)
        if not n:
            return
        if self.engine.position() > 3 and self.engine.state == "playing":
            self.engine.seek(0)
            return
        if self.shuffle and n > 1:
            self.play_index(random.randrange(n))
            return
        self.play_index((self.current - 1) % n)

    def remove_selected(self):
        if not self.selected:
            return
        cur = self.playlist[self.current] if 0 <= self.current < len(self.playlist) else None
        self.playlist = [t for i, t in enumerate(self.playlist) if i not in self.selected]
        self.selected = set()
        self.current = self.playlist.index(cur) if cur in self.playlist else -1
        self._clamp_pl_scroll()

    def crop_selected(self):
        self.selected = set(range(len(self.playlist))) - self.selected
        self.remove_selected()

    def clear_playlist(self):
        self.engine.stop()
        self.playlist = []
        self.selected = set()
        self.current = -1
        self.pl_scroll = 0

    def _sort(self, key):
        cur = self.playlist[self.current] if 0 <= self.current < len(self.playlist) else None
        sel = [self.playlist[i] for i in self.selected]
        if key == "random":
            random.shuffle(self.playlist)
        elif key == "reverse":
            self.playlist.reverse()
        else:
            self.playlist.sort(key=key)
        self.current = self.playlist.index(cur) if cur else -1
        self.selected = {self.playlist.index(t) for t in sel}

    @property
    def pl_rows(self):
        return self.pl_geo()["list"].h // ROW_H

    def _clamp_pl_scroll(self):
        self.pl_scroll = max(0, min(self.pl_scroll, len(self.playlist) - self.pl_rows))

    def _ensure_pl_visible(self, i):
        if i < self.pl_scroll:
            self.pl_scroll = i
        elif i >= self.pl_scroll + self.pl_rows:
            self.pl_scroll = i - self.pl_rows + 1
        self._clamp_pl_scroll()

    # ================================================================ actions
    def flash(self, msg, secs=2.5):
        self.temp_msg = msg
        self.temp_msg_until = time.monotonic() + secs

    def do_play(self):
        e = self.engine
        if e.state == "paused":
            e.unpause()
        elif e.mode is None and not e.loading:
            if self.selected and self.current not in self.selected:
                self.play_index(min(self.selected))
            elif 0 <= self.current < len(self.playlist):
                self.play_index(self.current)
            elif self.playlist:
                self.play_index(0)
            else:
                self.open_browser()
        else:
            e.seek(0) if e.state == "playing" else e.play()

    def open_browser(self, play=True):
        if not self.show_pl:
            self.show_pl = True
            self.relayout()

        def done(paths):
            self.browser = None
            if not paths:
                return
            start = len(self.playlist)
            self.add_paths(paths)
            if play and len(self.playlist) > start:
                self.play_index(start)
            elif not play and self.engine.mode is None and len(self.playlist) > start and self.current < 0:
                self.current = start

        self.browser = FileBrowser(self.last_dir, self.screen.get_size(), self.scale, done,
                                   "TuxAmp - Open file(s)" if play else "TuxAmp - Add file(s) to playlist")

    def set_eq_values(self, preamp=None, bands=None, on=None):
        e = self.engine
        e.set_eq(e.eq_on if on is None else on,
                 e.preamp if preamp is None else preamp,
                 e.bands if bands is None else bands)

    def set_scale(self, s):
        self.scale = self._clamp_scale(s)
        self._make_fonts()
        self.relayout()

    def quit(self):
        self.running = False

    # ================================================================ menus
    def popup(self, items, base_pos=None):
        if base_pos is None:
            pos = pygame.mouse.get_pos()
        else:
            pos = (round(base_pos[0] * self.scale), round(base_pos[1] * self.scale))
        self.menu = PopupMenu(items, pos, self.screen.get_size(), self.scale)

    def main_menu(self):
        e = self.engine
        self.popup([
            (f"TuxAmp {VERSION} by {AUTHOR}", None),
            None,
            ("Play file...          L", lambda: self.open_browser(True)),
            ("Add to playlist...", lambda: self.open_browser(False)),
            None,
            ("Main window", None, True),
            ("Equalizer            Alt+G", self.toggle_eq, self.show_eq),
            ("Playlist editor      Alt+E", self.toggle_pl, self.show_pl),
            None,
            ("Full-screen visualizer (TuxDrop)   Ctrl+Shift+K", self.start_viz),
            ("Visualization: Spectrum", lambda: setattr(self, "vis_mode", 0), self.vis_mode == 0),
            ("Visualization: Oscilloscope", lambda: setattr(self, "vis_mode", 1), self.vis_mode == 1),
            ("Visualization: Off", lambda: setattr(self, "vis_mode", 2), self.vis_mode == 2),
            None,
            ("Cast to device...     Ctrl+Shift+C", self.cast_menu, e.remote is not None),
            None,
            ("Size 1x", lambda: self.set_scale(1), self.scale == 1),
            ("Size 2x", lambda: self.set_scale(2), self.scale == 2),
            ("Size 3x", lambda: self.set_scale(3), self.scale == 3),
            None,
            ("Play / Restart      X", self.do_play),
            ("Pause               C", e.pause),
            ("Stop                V", e.stop),
            ("Previous            Z", self.prev_track),
            ("Next                B", self.next_track),
            ("Shuffle             S", self.toggle_shuffle, self.shuffle),
            ("Repeat              R", self.toggle_repeat, self.repeat),
            None,
            ("Play intro voice at startup", self.toggle_intro, self.intro_enabled),
            ("About TuxAmp", lambda: self.flash(f"TUXAMP {VERSION}  ***  BY {AUTHOR}, {RELEASE_DATE}  ***  "
                                                 "CODED WITH CLAUDE AI", 9)),
            ("Exit", self.quit),
        ])

    # ================================================================ casting
    def cast_menu(self, pos=None):
        """The Cast menu: this computer plus the devices found on the network."""
        if pos is None:
            pos = pygame.mouse.get_pos()
        self._cast_menu_pos = pos
        if not self.cast_searching and (self.cast_found_at is None
                                        or time.monotonic() - self.cast_found_at > 30):
            self.cast_search()
        self._show_cast_menu()

    def _show_cast_menu(self):
        r = self.engine.remote
        devs = list(self.cast_devices)
        if r is not None and all(d.key != r.device.key for d in devs):
            devs.insert(0, r.device)
        items = [("Play on:", None), None,
                 ("This computer", self.cast_stop, r is None)]
        for d in devs:
            items.append((d.label, lambda d=d: self.cast_to(d), r is not None and r.device.key == d.key))
        if self.cast_searching:
            items.append(("Searching the network...", None))
        elif not devs:
            items.append(("No cast devices found", None))
        items += [None, ("Search again", lambda: (self.cast_search(), self._show_cast_menu()))]
        self.menu = PopupMenu(items, self._cast_menu_pos, self.screen.get_size(), self.scale)
        self._cast_menu = self.menu

    def cast_search(self):
        if self.cast_searching:
            return
        self.cast_searching = True

        def work():
            try:
                found = cast.discover(3.0)
            except Exception:
                found = []
            self._cast_result = found

        threading.Thread(target=work, daemon=True).start()

    def _cast_poll(self):
        """Main loop: pick up search results and show them in the open menu."""
        if self._cast_result is None:
            return
        self.cast_devices, self._cast_result = self._cast_result, None
        self.cast_searching = False
        self.cast_found_at = time.monotonic()
        if self.menu is not None and self.menu is self._cast_menu:
            hover = self.menu.hover
            self._show_cast_menu()
            self.menu.hover = hover

    def cast_to(self, dev):
        r = self.engine.remote
        if r is not None and r.device.key == dev.key:
            return
        if self.cast_server is None:
            self.cast_server = cast.MediaServer()
        self.engine.start_cast(cast.CastSession(dev, self.cast_server))
        self.flash(f"CASTING TO {dev.name.upper()}", 3)

    def cast_stop(self):
        if self.engine.remote is not None:
            self.engine.stop_cast()
            self.flash("PLAYING ON THIS COMPUTER", 3)

    def toggle_eq(self):
        self.show_eq = not self.show_eq
        self.relayout()

    def toggle_pl(self):
        self.show_pl = not self.show_pl
        self.relayout()

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.flash("SHUFFLE: " + ("ON" if self.shuffle else "OFF"), 1.5)

    def toggle_repeat(self):
        self.repeat = not self.repeat
        self.flash("REPEAT: " + ("ON" if self.repeat else "OFF"), 1.5)

    def presets_menu(self, pos):
        items = []
        for name, vals in EQ_PRESETS:
            items.append((name, lambda v=vals: self.set_eq_values(bands=list(v), preamp=0.0, on=True)))
        items += [None, ("Reset to flat", lambda: self.set_eq_values(bands=[0.0] * 10, preamp=0.0))]
        self.popup(items, pos)

    # ================================================================ hit testing
    def _main_spots(self):
        if self.shade:
            return [
                ("menu", (6, 3, 9, 9)), ("min", (240, 3, 9, 9)), ("shade", (250, 3, 9, 9)),
                ("close", (260, 3, 9, 9)),
                ("s_prev", (166, 3, 7, 8)), ("s_play", (174, 3, 7, 8)), ("s_pause", (182, 3, 7, 8)),
                ("s_stop", (190, 3, 7, 8)), ("s_next", (198, 3, 7, 8)),
                ("title", (0, 0, W, 14)),
            ]
        return [
            ("menu", (6, 3, 9, 9)), ("min", (240, 3, 9, 9)), ("shade", (250, 3, 9, 9)),
            ("close", (260, 3, 9, 9)), ("title", (0, 0, W, 14)),
            ("c_o", (10, 22, 8, 8)), ("c_a", (10, 30, 8, 8)), ("c_i", (10, 38, 8, 8)),
            ("c_d", (10, 46, 8, 8)), ("c_v", (10, 54, 8, 8)),
            ("time", (36, 25, 66, 15)), ("vis", (24, 43, 76, 16)),
            ("volume", (107, 57, 68, 13)), ("balance", (177, 57, 38, 13)),
            ("eqbtn", (219, 58, 23, 12)), ("plbtn", (242, 58, 23, 12)),
            ("pos", (16, 72, 248, 10)),
            ("prev", (16, 88, 23, 18)), ("play", (39, 88, 23, 18)), ("pause", (62, 88, 23, 18)),
            ("stop", (85, 88, 23, 18)), ("next", (108, 88, 22, 18)), ("eject", (136, 89, 22, 16)),
            ("shuffle", (164, 89, 46, 15)), ("repeat", (211, 89, 28, 15)), ("logo", skin.LOGO_RECT),
        ]

    def _eq_spots(self):
        spots = [("close", (260, 3, 9, 9)), ("title", (0, 0, W, 14)),
                 ("on", (14, 18, 26, 12)), ("auto", (40, 18, 32, 12)), ("presets", (217, 18, 44, 12)),
                 ("preamp", (21, 38, 14, 63))]
        for i in range(10):
            spots.append((f"band{i}", (78 + 18 * i, 38, 14, 63)))
        return spots

    def _pl_spots(self):
        g = self.pl_geo()
        by, mx, my = g["btn_y"], g["mini_x"], g["mini_y"]
        return [("resize", g["resize"]), ("close", g["close"]), ("title", (0, 0, self.pl_w, 14)),
                ("list", g["list"]), ("scroll", g["scroll"]),
                ("add", (14, by, 25, 18)), ("rem", (43, by, 25, 18)), ("sel", (72, by, 25, 18)),
                ("misc", (101, by, 25, 18)), ("listopts", g["listopts"]),
                ("m_prev", (mx, my, 7, 8)), ("m_play", (mx + 8, my, 7, 8)), ("m_pause", (mx + 16, my, 7, 8)),
                ("m_stop", (mx + 24, my, 7, 8)), ("m_next", (mx + 32, my, 7, 8)),
                ("m_eject", (mx + 40, my, 8, 8))]

    def hit(self, bx, by):
        for name, y0, h in self._sections():
            if y0 <= by < y0 + h:
                if name in ("main", "eq") and bx >= W:
                    return name, "title", y0
                if (name, y0, h) == self._sections()[-1] and name != "pl" and not (name == "main" and self.shade) \
                        and bx >= W - SIZE_GRIP and by >= y0 + h - SIZE_GRIP:
                    return name, "sizegrip", y0
                spots = {"main": self._main_spots, "eq": self._eq_spots, "pl": self._pl_spots}[name]()
                for sid, r in spots:
                    if pygame.Rect(r).collidepoint(bx, by - y0):
                        return name, sid, y0
                return name, None, y0
        return None, None, 0

    def to_base(self, pos):
        return int(pos[0] / self.scale), int(pos[1] / self.scale)

    # ================================================================ events
    def handle_event(self, ev):
        if ev.type == pygame.QUIT:
            self.quit()
            return
        if ev.type == pygame.WINDOWFOCUSGAINED:
            self.focused = True
        elif ev.type == pygame.WINDOWFOCUSLOST:
            self.focused = False
        elif ev.type == pygame.WINDOWMOVED:
            # moved by someone else (window manager, keyboard shortcut...).
            # Ignore the echoes of our own moves, which arrive late.
            if not self.viz and not self.drag and pygame.time.get_ticks() - getattr(self, "_moved_at", 0) > 500:
                self._pos = (ev.x, ev.y)
        elif ev.type == pygame.DROPFILE:
            start = len(self.playlist)
            self.add_paths([ev.file])
            if self.engine.mode is None and not self.engine.loading and len(self.playlist) > start:
                self.play_index(start)
            return
        if self.viz:
            self.viz_event(ev)
            return

        if self.browser:
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION,
                           pygame.MOUSEWHEEL, pygame.KEYDOWN):
                if not self.browser.handle(ev):
                    self.browser = None
            return
        if self.menu:
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.KEYDOWN):
                menu = self.menu
                keep = menu.handle(ev)
                if self.menu is menu and not keep:
                    self.menu = None
            return

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if ev.button in (1, 3):
                self.mouse_down(ev)
        elif ev.type == pygame.MOUSEBUTTONUP:
            if ev.button == 1:
                self.mouse_up(ev)
        elif ev.type == pygame.MOUSEMOTION:
            self.mouse_move(ev)
        elif ev.type == pygame.MOUSEWHEEL:
            self.mouse_wheel(ev)
        elif ev.type == pygame.KEYDOWN:
            self.key_down(ev)

    def mouse_down(self, ev):
        bx, by = self.to_base(ev.pos)
        sec, sid, y0 = self.hit(bx, by)
        if sec is None:
            return
        self.focus_area = sec
        ly = by - y0
        if ev.button == 3:
            if sec == "pl" and sid == "list":
                self._pl_click(bx, ly, right=True)
                self.popup([
                    ("Play item", lambda: self.play_index(min(self.selected)) if self.selected else None),
                    None,
                    ("Remove selected      Del", self.remove_selected),
                    ("Crop selection", self.crop_selected),
                    ("Select all        Ctrl+A", lambda: setattr(self, "selected", set(range(len(self.playlist))))),
                ])
            else:
                self.main_menu()
            return

        if sid == "title":
            win = self._sdl_window()
            pos = self._window_pos()
            if win is not None and pos is not None and not self.framed:
                # Follow the pointer's desktop position, polled every frame:
                # the window moves exactly as far and as fast as the mouse.
                g = sdlext.global_mouse()
                if g is not None:
                    self.drag = ("window", (g[0], g[1]), pos, True)
                    sdlext.capture_mouse(True)
                else:
                    self.drag = ("window", (pos[0] + ev.pos[0], pos[1] + ev.pos[1]), pos, False)
            now = pygame.time.get_ticks()
            if sec == "main" and getattr(self, "_last_title_click", 0) and now - self._last_title_click < 350:
                self.shade = not self.shade
                self.relayout()
                self.drag = None
                self._last_title_click = 0
                return
            self._last_title_click = now if sec == "main" else 0
            return

        if sid in ("resize", "sizegrip"):
            # The bottom-right corner zooms the main window, equalizer and
            # playlist together.  While dragging, the pointer is often outside
            # the window, where X stops sending events - so poll the global
            # mouse every frame instead.
            g = sdlext.global_mouse()
            start = (g[0], g[1]) if g else ev.pos
            self.drag = ("scale", start, self.scale, g is not None)
            sdlext.capture_mouse(True)
            return

        sliders_main = {"volume", "balance", "pos"}
        if sec == "main" and sid in sliders_main:
            if sid == "pos" and self.engine.mode is None:
                return
            self.drag = ("slider", sec, sid, y0)
            self._slider_update(sec, sid, bx, ly)
            return
        if sec == "eq" and sid and (sid == "preamp" or sid.startswith("band")):
            self.drag = ("slider", sec, sid, y0)
            self._slider_update(sec, sid, bx, ly)
            return
        if sec == "pl" and sid == "list":
            self._pl_click(bx, ly)
            return
        if sec == "pl" and sid == "scroll":
            self.drag = ("slider", sec, sid, y0)
            self._slider_update(sec, sid, bx, ly)
            return
        if sid:
            self.pressed = (sec, sid)

    def _set_cursor(self, kind):
        if kind != self._cursor:
            self._cursor = kind
            try:
                pygame.mouse.set_cursor(kind)
            except (pygame.error, TypeError):
                pass

    def mouse_move(self, ev):
        if not self.drag:
            bx, by = self.to_base(ev.pos)
            sec, sid, _ = self.hit(bx, by)
            resize = (sec == "pl" and sid == "resize") or sid == "sizegrip"
            self._set_cursor(pygame.SYSTEM_CURSOR_SIZENWSE if resize else pygame.SYSTEM_CURSOR_ARROW)
            return
        if self.drag[0] == "scale":
            if not self.drag[3]:
                self._scale_drag(ev.pos)
            return
        if self.drag[0] == "window":
            if self.drag[3]:
                self._poll_drag()
            else:
                (gx0, gy0), (wx0, wy0) = self.drag[1], self.drag[2]
                cx, cy = self._pos
                self._move_window((wx0 + cx + ev.pos[0] - gx0, wy0 + cy + ev.pos[1] - gy0))
            return
        _, sec, sid, y0 = self.drag
        bx, by = self.to_base(ev.pos)
        self._slider_update(sec, sid, bx, by - y0)

    def _global_drag(self):
        return bool(self.drag) and self.drag[0] in ("scale", "window") and self.drag[-1] is True

    def _poll_drag(self):
        """Called every frame (and on every motion event) during a window move
        or resize in global-mouse mode, so the pointer can never outrun it."""
        g = sdlext.global_mouse()
        if g is None:
            return
        x, y, down = g
        if not down:
            self._end_resize()
            return
        kind = self.drag[0]
        if kind == "scale":
            self._scale_drag((x, y))
        elif kind == "window":
            (gx0, gy0), (wx0, wy0) = self.drag[1], self.drag[2]
            self._move_window((wx0 + x - gx0, wy0 + y - gy0))

    def _end_resize(self):
        was_zoom = self.drag and self.drag[0] == "scale"
        self.drag = None
        sdlext.capture_mouse(False)
        if was_zoom and self._clamp_scale(self.scale) != self.scale:
            self.set_scale(self.scale)            # let go next to 2x? make it exactly 2x

    def _scale_drag(self, pos):
        _, start, s0, _ = self.drag
        bw, bh = self.win_w(), self.base_height()
        # keep the aspect ratio; the corner follows whichever axis the pointer leads on
        want = max((bw * s0 + pos[0] - start[0]) / bw, (bh * s0 + pos[1] - start[1]) / bh)
        try:
            dw, dh = pygame.display.get_desktop_sizes()[0]
            px, py = self._window_pos() or (0, 0)
            px, py = px % dw if dw else 0, py % dh if dh else 0
            # never grow past the bottom/right edge of the screen
            want = min(want, max(s0, min((dw - px) / bw, (dh - py) / bh)))
        except Exception:
            pass
        ns = self._clamp_scale(want, snap=0)     # no snapping mid-drag: it would jump
        old = self.screen.get_size()
        self.scale = ns
        if self.screen_size() != old:
            self._make_fonts()
            self._open_window()
            self.draw()               # paint the new size straight away: no blank frame
            sdlext.capture_mouse(True)

    def mouse_up(self, ev):
        if self.drag and self.drag[0] in ("scale", "window"):
            g = sdlext.global_mouse() if self.drag[-1] is True else None
            if g is not None:             # catch up with where the pointer was let go
                if self.drag[0] == "scale":
                    self._scale_drag(g[:2])
                else:
                    (gx0, gy0), (wx0, wy0) = self.drag[1], self.drag[2]
                    self._move_window((wx0 + g[0] - gx0, wy0 + g[1] - gy0))
            self._end_resize()
            return
        if self.drag:
            if self.drag[0] == "slider" and self.drag[2] == "pos":
                bx, _ = self.to_base(ev.pos)
                frac = max(0.0, min(1.0, (bx - 16 - 14) / (248 - 29)))
                self.engine.seek(frac * self.engine.duration if self.engine.duration else 0)
            self.drag = None
            return
        if not self.pressed:
            return
        sec, sid = self.pressed
        self.pressed = None
        bx, by = self.to_base(ev.pos)
        hsec, hsid, _ = self.hit(bx, by)
        if (hsec, hsid) != (sec, sid):
            return
        self.click(sec, sid, bx, by)

    def mouse_wheel(self, ev):
        bx, by = self.to_base(pygame.mouse.get_pos())
        sec, sid, _ = self.hit(bx, by)
        if sec == "pl":
            self.pl_scroll -= ev.y * 3
            self._clamp_pl_scroll()
        else:
            self.engine.set_volume(self.engine.volume + ev.y * 0.03)
            self.flash(f"VOLUME: {int(self.engine.volume * 100)}%", 1)

    def click(self, sec, sid, bx, by):
        e = self.engine
        if sid == "close":
            if sec == "main":
                self.quit()
            elif sec == "eq":
                self.toggle_eq()
            elif sec == "pl":
                self.toggle_pl()
            return
        if sec == "main":
            if sid == "menu" or sid == "c_o":
                self.main_menu()
            elif sid == "min":
                pygame.display.iconify()
            elif sid == "shade":
                self.shade = not self.shade
                self.relayout()
            elif sid == "c_d":
                self.set_scale(int(self.scale) % 3 + 1)
            elif sid == "c_v":
                self.start_viz()
            elif sid == "c_i":
                t = self.cur_track()
                if t:
                    inf = t.info or {}
                    self.flash(f"{os.path.basename(t.path)}  {inf.get('bitrate', '?')}KBPS "
                               f"{inf.get('samplerate', '?')}HZ  {fmt_time(t.duration)}", 5)
            elif sid == "c_a":
                self.flash("TUXAMP " + VERSION, 2)
            elif sid == "time":
                self.time_remaining = not self.time_remaining
            elif sid == "vis":
                now = pygame.time.get_ticks()
                if now - self._last_vis_click < 400:      # double-click: go full screen
                    self.vis_mode = (self.vis_mode - 1) % 3
                    self._last_vis_click = 0
                    self.start_viz()
                else:
                    self.vis_mode = (self.vis_mode + 1) % 3
                    self._last_vis_click = now
            elif sid == "eqbtn":
                self.toggle_eq()
            elif sid == "plbtn":
                self.toggle_pl()
            elif sid in ("prev", "s_prev"):
                self.prev_track()
            elif sid in ("play", "s_play"):
                self.do_play()
            elif sid in ("pause", "s_pause"):
                e.pause()
            elif sid in ("stop", "s_stop"):
                e.stop()
            elif sid in ("next", "s_next"):
                self.next_track()
            elif sid == "eject":
                self.open_browser(True)
            elif sid == "shuffle":
                self.toggle_shuffle()
            elif sid == "repeat":
                self.toggle_repeat()
            elif sid == "logo":
                self.play_intro()
        elif sec == "eq":
            if sid == "on":
                self.set_eq_values(on=not e.eq_on)
            elif sid == "auto":
                self.eq_auto = not self.eq_auto
            elif sid == "presets":
                self.presets_menu((217, self._sec_y("eq") + 31))
        elif sec == "pl":
            y = self._sec_y("pl")
            if sid == "add":
                self.popup([("Add file(s)...", lambda: self.open_browser(False)),
                            ("Add folder...", lambda: self.open_browser(False)),
                            ("Load playlist (.m3u/.pls)...", lambda: self.open_browser(False))],
                           (14, y + self.pl_h - 27))
            elif sid == "rem":
                self.popup([("Remove selected", self.remove_selected),
                            ("Crop (remove unselected)", self.crop_selected),
                            ("Remove all", self.clear_playlist)], (43, y + self.pl_h - 27))
            elif sid == "sel":
                n = len(self.playlist)
                self.popup([("Select all", lambda: setattr(self, "selected", set(range(n)))),
                            ("Select none", lambda: setattr(self, "selected", set())),
                            ("Invert selection", lambda: setattr(self, "selected", set(range(n)) - self.selected))],
                           (72, y + self.pl_h - 27))
            elif sid == "misc":
                self.popup([("Sort by title", lambda: self._sort(lambda t: t.title.lower())),
                            ("Sort by filename", lambda: self._sort(lambda t: os.path.basename(t.path).lower())),
                            ("Sort by path", lambda: self._sort(lambda t: t.path.lower())),
                            None,
                            ("Randomize list", lambda: self._sort("random")),
                            ("Reverse list", lambda: self._sort("reverse")),
                            None,
                            ("File info", lambda: self.click("main", "c_i", 0, 0))],
                           (101, y + self.pl_h - 27))
            elif sid == "listopts":
                self.popup([("New list (clear)", self.clear_playlist),
                            ("Save list to ~/Music/tuxamp-playlist.m3u", self.save_playlist),
                            ("Load list...", lambda: self.open_browser(False))],
                           (self.pl_w - 95, y + self.pl_h - 27))
            elif sid == "m_prev":
                self.prev_track()
            elif sid == "m_play":
                self.do_play()
            elif sid == "m_pause":
                e.pause()
            elif sid == "m_stop":
                e.stop()
            elif sid == "m_next":
                self.next_track()
            elif sid == "m_eject":
                self.open_browser(True)

    def _sec_y(self, name):
        for n, y, _ in self._sections():
            if n == name:
                return y
        return 0

    def _slider_update(self, sec, sid, bx, ly):
        e = self.engine
        if sec == "main":
            if sid == "volume":
                frac = (bx - 107 - 7) / (68 - 14)
                e.set_volume(max(0.0, min(1.0, frac)))
                self.flash(f"VOLUME: {int(round(e.volume * 100))}%", 1)
            elif sid == "balance":
                frac = (bx - 177 - 7) / (38 - 14)
                b = max(-1.0, min(1.0, frac * 2 - 1))
                if abs(b) < 0.08:
                    b = 0.0
                e.set_balance(b)
                if b == 0:
                    self.flash("BALANCE: CENTER", 1)
                else:
                    self.flash(f"BALANCE: {int(abs(b) * 100)}% {'LEFT' if b < 0 else 'RIGHT'}", 1)
            elif sid == "pos":
                frac = max(0.0, min(1.0, (bx - 16 - 14) / (248 - 29)))
                self.seek_preview = frac
                self.flash(f"SEEK TO: {fmt_time(frac * e.duration)}/{fmt_time(e.duration)}", 0.6)
        elif sec == "eq":
            db = max(-12.0, min(12.0, 12 - (ly - 38 - 5) / (63 - 11) * 24))
            if abs(db) < 0.7:
                db = 0.0
            db = round(db, 1)
            if sid == "preamp":
                self.set_eq_values(preamp=db)
                self.flash(f"PREAMP: {db:+.1f} DB", 1)
            else:
                i = int(sid[4:])
                bands = list(e.bands)
                bands[i] = db
                self.set_eq_values(bands=bands)
                f = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000][i]
                fl = f"{f // 1000}KHZ" if f >= 1000 else f"{f}HZ"
                self.flash(f"EQ: {fl}: {db:+.1f} DB", 1)
        elif sec == "pl" and sid == "scroll":
            n = len(self.playlist)
            sr = self.pl_geo()["scroll"]
            frac = (ly - sr.y - 9) / max(1, sr.h - 18)
            self.pl_scroll = int(round(frac * (n - self.pl_rows)))
            self._clamp_pl_scroll()

    def _pl_click(self, bx, ly, right=False):
        idx = self.pl_scroll + (ly - self.pl_geo()["list"].y) // ROW_H
        if not (0 <= idx < len(self.playlist)):
            if not right:
                self.selected = set()
            return
        now = pygame.time.get_ticks()
        if not right and self.last_pl_click[0] == idx and now - self.last_pl_click[1] < 400:
            self.play_index(idx)
            self.last_pl_click = (None, 0)
            return
        self.last_pl_click = (idx, now)
        mods = pygame.key.get_mods()
        if right:
            if idx not in self.selected:
                self.selected = {idx}
                self.sel_anchor = idx
        elif mods & pygame.KMOD_SHIFT and self.sel_anchor is not None:
            a, b = sorted((self.sel_anchor, idx))
            self.selected = set(range(a, b + 1))
        elif mods & pygame.KMOD_CTRL:
            self.selected ^= {idx}
            self.sel_anchor = idx
        else:
            self.selected = {idx}
            self.sel_anchor = idx

    def key_down(self, ev):
        e = self.engine
        k = ev.key
        ctrl = ev.mod & pygame.KMOD_CTRL
        alt = ev.mod & pygame.KMOD_ALT
        if alt and k == pygame.K_g:
            self.toggle_eq()
        elif alt and k == pygame.K_e:
            self.toggle_pl()
        elif ctrl and k == pygame.K_a:
            self.selected = set(range(len(self.playlist)))
        elif ctrl and k == pygame.K_q:
            self.quit()
        elif ctrl and ev.mod & pygame.KMOD_SHIFT and k == pygame.K_c:
            self.cast_menu()
        elif k == pygame.K_k:
            self.start_viz()
        elif k == pygame.K_z:
            self.prev_track()
        elif k == pygame.K_x:
            self.do_play()
        elif k in (pygame.K_c, pygame.K_SPACE):
            e.pause() if e.state != "stopped" else self.do_play()
        elif k == pygame.K_v:
            e.stop()
        elif k == pygame.K_b:
            self.next_track()
        elif k == pygame.K_l:
            self.open_browser(True)
        elif k == pygame.K_s:
            self.toggle_shuffle()
        elif k == pygame.K_r:
            self.toggle_repeat()
        elif k == pygame.K_LEFT:
            e.seek(max(0, e.position() - 5))
        elif k == pygame.K_RIGHT:
            e.seek(e.position() + 5)
        elif k == pygame.K_DELETE:
            self.remove_selected()
        elif self.focus_area == "pl" and k in (pygame.K_UP, pygame.K_DOWN, pygame.K_PAGEUP,
                                               pygame.K_PAGEDOWN, pygame.K_HOME, pygame.K_END):
            n = len(self.playlist)
            if not n:
                return
            cur = max(self.selected) if self.selected else (self.current if self.current >= 0 else -1)
            step = {pygame.K_UP: -1, pygame.K_DOWN: 1, pygame.K_PAGEUP: -self.pl_rows,
                    pygame.K_PAGEDOWN: self.pl_rows, pygame.K_HOME: -n, pygame.K_END: n}[k]
            cur = max(0, min(n - 1, cur + step))
            self.selected = {cur}
            self.sel_anchor = cur
            self._ensure_pl_visible(cur)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.selected:
            self.play_index(min(self.selected))
        elif k == pygame.K_UP:
            e.set_volume(e.volume + 0.02)
            self.flash(f"VOLUME: {int(round(e.volume * 100))}%", 1)
        elif k == pygame.K_DOWN:
            e.set_volume(e.volume - 0.02)
            self.flash(f"VOLUME: {int(round(e.volume * 100))}%", 1)
        elif k == pygame.K_ESCAPE and self.shade:
            self.shade = False
            self.relayout()

    def cur_track(self):
        if 0 <= self.current < len(self.playlist):
            return self.playlist[self.current]
        return None

    # ================================================================ static backgrounds
    def _render_main_bg(self):
        s = pygame.Surface((W, skin.MAIN_H))
        skin.frame_background(s, (0, 0, W, skin.MAIN_H))
        # big display panel
        skin.bevel(s, (8, 20, 259, 46), raised=False)
        skin.inset(s, (22, 24, 81, 37))
        # vis dot grid
        for y in range(43, 59, 2):
            for x in range(24, 100, 2):
                s.set_at((x, y), skin.LCD_DOT)
        skin.inset(s, (109, 24, 157, 12))
        skin.inset(s, (110, 41, 20, 9))
        skin.text(s, 132, 43, "kbps", skin.LABEL)
        skin.inset(s, (154, 41, 14, 9))
        skin.text(s, 170, 43, "khz", skin.LABEL)
        # clutterbar
        skin.inset(s, (10, 22, 8, 41), face=(28, 28, 40))
        for i, ch in enumerate("OAIDV"):
            skin.text(s, 12, 24 + i * 8, ch, (150, 150, 170))
        # position groove
        skin.inset(s, (16, 74, 248, 6), face=(15, 15, 22))
        skin.logo(s)
        return s

    def _render_eq_bg(self):
        s = pygame.Surface((W, skin.EQ_H))
        skin.frame_background(s, (0, 0, W, skin.EQ_H))
        skin.inset(s, (86, 17, 113, 19))
        skin.text(s, 44, 39, "+12db", skin.LABEL)
        skin.text(s, 48, 66, "+0db", skin.LABEL)
        skin.text(s, 46, 95, "-12db", skin.LABEL)
        skin.text(s, 14, 104, "PREAMP", skin.LABEL)
        for i, lab in enumerate(["60", "170", "310", "600", "1K", "3K", "6K", "12K", "14K", "16K"]):
            cx = 78 + 18 * i + 7
            skin.text(s, cx - (font.text_width(lab) - 1) // 2, 104, lab, skin.LABEL)
        # scale tick marks
        for y in (40, 68, 97):
            s.fill(skin.FRAME_HI, (70, y, 5, 1))
        return s

    def _render_pl_bg(self):
        g = self.pl_geo()
        w, h = self.pl_w, self.pl_h
        s = pygame.Surface((w, h))
        skin.frame_background(s, (0, 0, w, h))
        skin.inset(s, g["list"], face=skin.PL_BG)
        skin.inset(s, g["scroll"], face=(20, 20, 28))
        skin.bevel(s, g["bottom"])
        skin.inset(s, g["info"])
        skin.inset(s, g["mtime"])
        # resize grip: drag it to resize the playlist window
        for i in range(4):
            for j in range(i + 1):
                s.fill(skin.FRAME_HI, (w - 6 - j * 3, h - 13 + i * 3, 2, 2))
                s.fill(skin.FRAME_LO, (w - 5 - j * 3, h - 12 + i * 3, 1, 1))
        return s

    # ================================================================ drawing
    def draw(self):
        b = self.base
        if b.get_width() > W:
            b.fill((14, 14, 20))
        for name, y, h in self._sections():
            if name == "main":
                if self.shade:
                    self.draw_shade(b, y)
                else:
                    self.draw_main(b, y)
            elif name == "eq":
                self.draw_eq(b, y)
            elif name == "pl":
                self.draw_pl(b, y)
        self._present(b)
        if not self.shade:
            self._draw_logo()
        if self.show_pl:
            self.draw_pl_text(self._sec_y("pl"))
        if self.browser:
            self.browser.draw(self.screen)
        if self.menu:
            self.menu.draw(self.screen)
        pygame.display.flip()

    def _draw_logo(self):
        """Tux with his lightning bolt in the main window's corner badge,
        drawn after zooming so he is sharp at every size."""
        x, y, w, h = skin.LOGO_RECT
        sc = self.scale
        x0, y0 = round((x + 1) * sc), round((y + 1) * sc)
        x1, y1 = round((x + w - 1) * sc), round((y + h - 1) * sc)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            self.screen.blit(icon.render(x1 - x0, y1 - y0), (x0, y0))

    def _present(self, b):
        size = self.screen.get_size()
        if self.scale == int(self.scale):
            pygame.transform.scale(b, size, self.screen)
            return
        # in-between zoom: blow up by the next whole number (sharp pixels),
        # then filter down to the exact size - smooth, without going blurry
        n = math.ceil(self.scale)
        up_size = (b.get_width() * n, b.get_height() * n)
        if self._up is None or self._up.get_size() != up_size:
            self._up = pygame.Surface(up_size, 0, self.screen)
        pygame.transform.scale(b, up_size, self._up)
        try:
            pygame.transform.smoothscale(self._up, size, self.screen)
        except (ValueError, pygame.error):
            pygame.transform.scale(b, size, self.screen)

    def _is_pressed(self, sec, sid):
        return self.pressed == (sec, sid) and self._hover_is(sec, sid)

    def _hover_is(self, sec, sid):
        bx, by = self.to_base(pygame.mouse.get_pos())
        h = self.hit(bx, by)
        return (h[0], h[1]) == (sec, sid)

    def marquee_text(self):
        now = time.monotonic()
        if self.temp_msg and now < self.temp_msg_until:
            return self.temp_msg, True
        self.temp_msg = None
        t = self.cur_track()
        if t is None:
            return f"TUXAMP {VERSION}  ***  DROP FILES HERE OR PRESS L TO OPEN", True
        dur = self.engine.duration or t.duration
        txt = f"{self.current + 1}. {t.title}"
        if dur:
            txt += f" ({fmt_time(dur)})"
        if self.engine.loading:
            txt = "LOADING... " + txt
        r = self.engine.remote
        if r is not None:
            if r.state == "connecting":
                txt = f"CONNECTING TO {r.name}...  " + txt
            elif r.state == "loading" and self.engine.state == "playing":
                txt = f"BUFFERING ON {r.name}...  " + txt
            else:
                txt = f"[CAST: {r.name}]  " + txt
        return txt, True

    def draw_main(self, s, oy):
        e = self.engine
        s.blit(self.bg_main, (0, oy))
        skin.titlebar(s, oy, "TUXAMP", self.focused)
        if self.pressed and self.pressed[0] == "main":
            sid = self.pressed[1]
            rects = dict(self._main_spots())
            if sid in ("menu", "min", "shade", "close") and self._is_pressed("main", sid):
                r = pygame.Rect(rects[sid]).move(0, oy)
                skin.button(s, r, True)

        # ---- play status indicator
        sx, sy = 26, oy + 28
        if e.state == "playing":
            pygame.draw.polygon(s, skin.LCD_GREEN, [(sx, sy), (sx, sy + 8), (sx + 4, sy + 4)])
        elif e.state == "paused":
            s.fill(skin.LCD_GREEN, (sx, sy, 2, 9))
            s.fill(skin.LCD_GREEN, (sx + 4, sy, 2, 9))
        elif e.mode is not None:
            s.fill(skin.LCD_GREEN, (sx, sy + 1, 7, 7))
        if e.loading:
            if int(time.monotonic() * 4) % 2:
                s.fill(skin.LCD_GREEN, (sx - 2, sy + 10, 2, 2))

        # ---- time
        if e.state != "stopped" and e.mode is not None:
            blink_off = e.state == "paused" and int(time.monotonic() * 2) % 2
            if not blink_off:
                pos = e.position()
                if self.time_remaining and e.duration:
                    t = max(0, e.duration - pos)
                    s.fill(skin.LCD_GREEN, (38, oy + 32, 6, 1))
                else:
                    t = pos
                t = int(t)
                m, sec = t // 60, t % 60
                if m > 99:
                    m, sec = m // 60, m % 60  # show h:mm when very long
                digits = f"{m:02d}{sec:02d}"
                for ch, x in zip(digits, (48, 60, 78, 90)):
                    font.draw_big_digit(s, x, oy + 26, ch, skin.LCD_GREEN)
                s.fill(skin.LCD_GREEN, (72, oy + 29, 2, 2))
                s.fill(skin.LCD_GREEN, (72, oy + 35, 2, 2))

        # ---- visualizer
        self.draw_vis(s, 24, oy + 43)

        # ---- marquee
        self.draw_marquee(s, pygame.Rect(111, oy + 27, 153, 6))

        # ---- kbps / khz / stereo
        t = self.cur_track()
        info = (t.info or {}) if t else {}
        if t and e.mode is not None:
            br = info.get("bitrate")
            sr = info.get("samplerate")
            ch = info.get("channels", 2)
            if br:
                bs = str(br)[:4]
                skin.text(s, 129 - font.text_width(bs), oy + 43, bs, skin.LCD_GREEN)
            if sr:
                ks = str(int(round(sr / 1000)))
                skin.text(s, 167 - font.text_width(ks), oy + 43, ks, skin.LCD_GREEN)
            mono_on, st_on = ch == 1, ch >= 2
        else:
            mono_on = st_on = False
        skin.text(s, 205, oy + 43, "mono", skin.LED_ON if mono_on else (60, 60, 75))
        skin.text(s, 232, oy + 43, "stereo", skin.LED_ON if st_on else (60, 60, 75))

        # ---- sliders
        drag_id = self.drag[2] if self.drag and self.drag[0] == "slider" else None
        skin.h_slider(s, (107, oy + 57, 68, 13), e.volume, 14, skin.level_color(e.volume),
                      pressed=drag_id == "volume")
        skin.h_slider(s, (177, oy + 57, 38, 13), (e.balance + 1) / 2, 14,
                      skin.level_color(abs(e.balance)), pressed=drag_id == "balance")
        # centre mark of balance
        s.fill(skin.FRAME_LO, (195, oy + 56, 2, 1))

        # ---- EQ / PL toggles
        for sid, r, label, on in (("eqbtn", (219, 58, 23, 12), "EQ", self.show_eq),
                                  ("plbtn", (242, 58, 23, 12), "PL", self.show_pl)):
            rr = pygame.Rect(r).move(0, oy)
            skin.button(s, rr, self._is_pressed("main", sid))
            s.fill(skin.LED_ON if on else skin.LED_OFF, (rr.x + 3, rr.y + 5, 3, 3))
            skin.text(s, rr.x + 9, rr.y + 4, label, skin.ICON)

        # ---- position bar
        if e.mode is not None and e.duration:
            if drag_id == "pos":
                frac = getattr(self, "seek_preview", 0)
            else:
                frac = e.position() / e.duration if e.duration else 0
            tx = 16 + int(round(frac * (248 - 29)))
            skin.button(s, (tx, oy + 72, 29, 10), drag_id == "pos")
            for k in (-3, 0, 3):
                s.fill(skin.BTN_LO, (tx + 14 + k, oy + 74, 1, 6))

        # ---- transport
        for sid, r, ic in (("prev", (16, 88, 23, 18), skin.icon_prev),
                           ("play", (39, 88, 23, 18), skin.icon_play),
                           ("pause", (62, 88, 23, 18), skin.icon_pause),
                           ("stop", (85, 88, 23, 18), skin.icon_stop),
                           ("next", (108, 88, 22, 18), skin.icon_next),
                           ("eject", (136, 89, 22, 16), skin.icon_eject)):
            rr = pygame.Rect(r).move(0, oy)
            pr = self._is_pressed("main", sid)
            skin.button(s, rr, pr)
            ic(s, rr.move(1, 1) if pr else rr)

        # ---- shuffle / repeat
        rr = pygame.Rect(164, oy + 89, 46, 15)
        skin.button(s, rr, self._is_pressed("main", "shuffle"))
        s.fill(skin.LED_ON if self.shuffle else skin.LED_OFF, (rr.x + 3, rr.y + 6, 3, 3))
        skin.text(s, rr.x + 8, rr.y + 5, "SHUFFLE", skin.ICON)
        rr = pygame.Rect(211, oy + 89, 28, 15)
        skin.button(s, rr, self._is_pressed("main", "repeat"))
        s.fill(skin.LED_ON if self.repeat else skin.LED_OFF, (rr.x + 4, rr.y + 6, 4, 3))
        # repeat loop arrow
        x0, y0 = rr.x + 11, rr.y + 4
        pygame.draw.rect(s, skin.ICON, (x0, y0, 12, 7), 1)
        s.fill(skin.BTN, (x0 + 4, y0 + 6, 4, 1))
        pygame.draw.polygon(s, skin.ICON, [(x0 + 4, y0 + 4), (x0 + 4, y0 + 8), (x0 + 2, y0 + 6)])

    def draw_shade(self, s, oy):
        e = self.engine
        skin.titlebar(s, oy, "", self.focused)
        s.fill(skin.TITLE_BG, (18, oy + 2, 220, 10))
        skin.inset(s, (20, oy + 3, 100, 8))
        clip = s.get_clip()
        self.draw_marquee(s, pygame.Rect(21, oy + 4, 98, 6))
        s.set_clip(clip)
        skin.inset(s, (124, oy + 3, 36, 8))
        if e.state != "stopped" and e.mode is not None:
            skin.text(s, 127, oy + 4, fmt_time(e.position()), skin.LCD_GREEN)
        for sid, r, ic in (("s_prev", (166, 3, 7, 8), "prev"), ("s_play", (174, 3, 7, 8), "play"),
                           ("s_pause", (182, 3, 7, 8), "pause"), ("s_stop", (190, 3, 7, 8), "stop"),
                           ("s_next", (198, 3, 7, 8), "next")):
            self._mini_btn(s, pygame.Rect(r).move(0, oy), ic, self._is_pressed("main", sid))
        if e.duration:
            frac = e.position() / e.duration
            skin.inset(s, (208, oy + 5, 28, 4), face=(15, 15, 22))
            s.fill(skin.GOLD, (208 + int(frac * 25), oy + 5, 3, 4))

    def _mini_btn(self, s, r, kind, pressed):
        s.fill(skin.BTN_PRESSED if pressed else skin.BTN, r)
        c = skin.ICON
        x, y = r.x + 1, r.y + 1
        if kind == "prev":
            s.fill(c, (x, y + 1, 1, 5))
            pygame.draw.polygon(s, c, [(x + 4, y + 1), (x + 4, y + 5), (x + 2, y + 3)])
        elif kind == "play":
            pygame.draw.polygon(s, c, [(x + 1, y), (x + 1, y + 6), (x + 4, y + 3)])
        elif kind == "pause":
            s.fill(c, (x + 1, y + 1, 1, 5))
            s.fill(c, (x + 3, y + 1, 1, 5))
        elif kind == "stop":
            s.fill(c, (x + 1, y + 1, 4, 4))
        elif kind == "next":
            pygame.draw.polygon(s, c, [(x, y + 1), (x, y + 5), (x + 2, y + 3)])
            s.fill(c, (x + 4, y + 1, 1, 5))
        elif kind == "eject":
            pygame.draw.polygon(s, c, [(x, y + 3), (x + 5, y + 3), (x + 2, y)])
            s.fill(c, (x, y + 5, 6, 1))

    def draw_marquee(self, s, r):
        txt, scroll = self.marquee_text()
        clip = s.get_clip()
        s.set_clip(r)
        w = font.text_width(txt)
        if scroll and w > r.w:
            key = txt
            if key != self.marquee_key:
                self.marquee_key = key
                self.marquee_off = 0
            full = txt + "  ***  "
            fw = font.text_width(full)
            off = int(self.marquee_off) % fw
            surf = font.render_text(full, skin.LCD_GREEN)
            s.blit(surf, (r.x - off, r.y))
            s.blit(surf, (r.x - off + fw, r.y))
        else:
            s.blit(font.render_text(txt, skin.LCD_GREEN), (r.x, r.y))
        s.set_clip(clip)

    def draw_vis(self, s, x, y):
        if self.vis_mode == 2:
            return
        samples = self.engine.samples(1024)
        if self.vis_mode == 0:
            if samples is not None:
                win = samples * np.hanning(len(samples))
                mag = np.abs(np.fft.rfft(win))
                freqs = np.fft.rfftfreq(len(samples), 1.0 / self.engine.rate)
                edges = np.geomspace(40, 16000, 20)
                idx = np.searchsorted(freqs, edges)
                vals = np.zeros(19)
                for i in range(19):
                    a, b = idx[i], max(idx[i] + 1, idx[i + 1])
                    vals[i] = mag[a:b].max() if b > a else 0
                db = 20 * np.log10(vals + 1e-9)
                target = np.clip((db + 10) / 52 * 16, 0, 16)
            else:
                target = np.zeros(19)
            self.vis_bars = np.maximum(target, self.vis_bars - 1.2)
            rising = self.vis_bars >= self.vis_peaks
            self.vis_peaks = np.where(rising, self.vis_bars, self.vis_peaks - self.vis_peak_vel)
            self.vis_peak_vel = np.where(rising, 0.0, self.vis_peak_vel + 0.05)
            self.vis_peaks = np.maximum(self.vis_peaks, 0)
            for i in range(19):
                h = int(round(self.vis_bars[i]))
                bx = x + i * 4
                for k in range(h):
                    # colour by absolute height row, like Winamp's "fire" style
                    s.fill(skin.VIS_SPEC[15 - k], (bx, y + 15 - k, 3, 1))
                p = int(round(self.vis_peaks[i]))
                if p > 0:
                    s.fill(skin.VIS_PEAK, (bx, y + 16 - p, 3, 1))
        else:
            if samples is None:
                for i in range(76):
                    s.set_at((x + i, y + 8), skin.VIS_OSC[2])
                return
            pts = samples[np.linspace(0, 575, 76).astype(int)]
            prev = None
            for i, v in enumerate(pts):
                yy = int(round(8 - v * 14))
                yy = max(0, min(15, yy))
                col = skin.VIS_OSC[min(4, abs(yy - 8) // 2)]
                if prev is not None:
                    a, b = sorted((prev, yy))
                    s.fill(col, (x + i, y + a, 1, b - a + 1))
                else:
                    s.set_at((x + i, y + yy), col)
                prev = yy

    def draw_eq(self, s, oy):
        e = self.engine
        s.blit(self.bg_eq, (0, oy))
        skin.titlebar(s, oy, "TUXAMP EQUALIZER", self.focused, buttons=("close",), menu=False)
        if self._is_pressed("eq", "close"):
            skin.button(s, (260, oy + 3, 9, 9), True)
        for sid, r, lab, on in (("on", (14, 18, 26, 12), "ON", e.eq_on),
                                ("auto", (40, 18, 32, 12), "AUTO", self.eq_auto)):
            rr = pygame.Rect(r).move(0, oy)
            skin.button(s, rr, self._is_pressed("eq", sid))
            s.fill(skin.LED_ON if on else skin.LED_OFF, (rr.x + 3, rr.y + 5, 3, 3))
            skin.text(s, rr.x + 9, rr.y + 4, lab, skin.ICON)
        rr = pygame.Rect(217, oy + 18, 44, 12)
        skin.button(s, rr, self._is_pressed("eq", "presets"))
        skin.text_center(s, rr, "PRESETS")
        drag_id = self.drag[2] if self.drag and self.drag[0] == "slider" else None
        skin.v_slider(s, (21, oy + 38, 14, 63), (12 - e.preamp) / 24, drag_id == "preamp")
        for i in range(10):
            skin.v_slider(s, (78 + 18 * i, oy + 38, 14, 63), (12 - e.bands[i]) / 24, drag_id == f"band{i}")
        self.draw_eq_graph(s, 86, oy + 17)

    def draw_eq_graph(s_self, s, x, y):
        e = s_self.engine
        gw, gh = 113, 19
        # preamp line
        py = y + 9 - int(round(e.preamp / 12 * 8))
        s.fill((50, 50, 70), (x, py, gw, 1))
        s.fill((30, 30, 40), (x, y + 9, gw, 1))
        # catmull-rom spline through 10 band points
        pts = list(e.bands)
        n = len(pts)
        xs = np.linspace(0, n - 1, gw)
        prev = None
        for i, t in enumerate(xs):
            k = min(int(t), n - 2)
            u = t - k
            p0 = pts[max(0, k - 1)]
            p1, p2 = pts[k], pts[k + 1]
            p3 = pts[min(n - 1, k + 2)]
            v = 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
                       + (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)
            yy = int(round(9 - max(-12, min(12, v)) / 12 * 8))
            col = skin.level_color((v + 12) / 24) if e.eq_on else (110, 110, 120)
            if prev is not None:
                a, b = sorted((prev, yy))
                s.fill(col, (x + i, y + 1 + a, 1, b - a + 1))
            prev = yy

    def draw_pl(self, s, oy):
        s.blit(self.bg_pl, (0, oy))
        g = self.pl_geo()
        skin.titlebar(s, oy, "TUXAMP PLAYLIST", self.focused, buttons=("close",), menu=False, w=self.pl_w)
        if self._is_pressed("pl", "close"):
            skin.button(s, pygame.Rect(g["close"]).move(0, oy), True)
        # scrollbar thumb
        n = len(self.playlist)
        rows = self.pl_rows
        sr = g["scroll"]
        if n > rows:
            ty = sr.y + int((sr.h - 18) * self.pl_scroll / (n - rows))
        else:
            ty = sr.y
        skin.button(s, (sr.x, oy + ty, sr.w, 18))
        # bottom buttons
        by = g["btn_y"]
        for sid, r, lab in (("add", (14, by, 25, 18), "ADD"), ("rem", (43, by, 25, 18), "REM"),
                            ("sel", (72, by, 25, 18), "SEL"), ("misc", (101, by, 25, 18), "MISC"),
                            ("listopts", g["listopts"], "LIST")):
            rr = pygame.Rect(r).move(0, oy)
            skin.button(s, rr, self._is_pressed("pl", sid))
            skin.text_center(s, rr, lab)
        # info: selected/total time
        tot = sum(t.duration or 0 for t in self.playlist)
        seltot = sum(self.playlist[i].duration or 0 for i in self.selected if i < n)
        info = f"{fmt_time(seltot)}/{fmt_time(tot)}"
        if any(t.info is None for t in self.playlist):
            info += "+"
        skin.text(s, g["info"][0] + 2, oy + g["info"][1] + 2, info, skin.LCD_GREEN)
        # mini transport
        spots = dict(self._pl_spots())
        for sid, kind in (("m_prev", "prev"), ("m_play", "play"), ("m_pause", "pause"),
                          ("m_stop", "stop"), ("m_next", "next"), ("m_eject", "eject")):
            self._mini_btn(s, pygame.Rect(spots[sid]).move(0, oy), kind, self._is_pressed("pl", sid))
        e = self.engine
        if e.state != "stopped" and e.mode is not None:
            skin.text(s, g["mtime"][0] + 3, oy + g["mtime"][1] + 2, fmt_time(e.position()), skin.LCD_GREEN)
        # selection highlight + rows are drawn at screen resolution in draw_pl_text

    def draw_pl_text(self, oy):
        sc = self.scale
        f = self.pl_font
        lr = self.pl_geo()["list"]
        lx = round(lr.x * sc)
        lw = round(lr.w * sc)
        top = round((oy + lr.y) * sc)
        rh = ROW_H * sc
        clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(lx, top, lw, round(lr.h * sc)))
        for row in range(self.pl_rows + 1):
            i = self.pl_scroll + row
            if i >= len(self.playlist):
                break
            t = self.playlist[i]
            y = top + round(row * rh)
            if i in self.selected:
                self.screen.fill(skin.PL_SELBG, (lx, y, lw, top + round((row + 1) * rh) - y))
            col = skin.PL_CURRENT if i == self.current else skin.PL_NORMAL
            dur = fmt_time(t.duration)
            ds = f.render(dur, False, col)
            right = lx + lw - round(3 * sc)
            title = f"{i + 1}. {t.title}"
            maxw = right - ds.get_width() - round(4 * sc) - (lx + round(2 * sc))
            ts = f.render(title, False, col)
            if ts.get_width() > maxw:
                ts = ts.subsurface((0, 0, max(1, maxw), ts.get_height()))
            ty = y + int(rh - ts.get_height()) // 2
            self.screen.blit(ts, (lx + round(2 * sc), ty))
            self.screen.blit(ds, (right - ds.get_width(), ty))
        self.screen.set_clip(clip)

    # ================================================================ TuxDrop visualizer
    def start_viz(self):
        if self.viz:
            return
        from .viz import TuxDrop
        self.menu = self.browser = self.drag = self.pressed = None
        self._saved_pos = self._window_pos()
        self._set_viz_display()
        self.viz = TuxDrop(self.screen.get_size(), self.engine.rate)
        t = self.cur_track()
        if t is not None and self.engine.state != "stopped":
            self.viz.show("title", t.title, 5)

    def _set_viz_display(self):
        self._skin_window = False
        if self.viz_fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.screen = pygame.display.set_mode(self.viz_window_size, pygame.RESIZABLE)
            pygame.mouse.set_visible(True)
        pygame.display.set_caption("TuxAmp - TuxDrop visualizer")
        self._win = self._wrap_sdl_window()
        if self.viz:
            self.viz.resize(self.screen.get_size())

    def stop_viz(self):
        if not self.viz:
            return
        self.viz = None
        pygame.mouse.set_visible(True)
        pygame.display.set_caption(APP_TITLE)
        if self.viz_fullscreen:
            # leave full screen first; going straight to the borderless
            # skin window can keep the full-screen size
            pygame.display.set_mode((round(W * self.scale), round(skin.MAIN_H * self.scale)))
        self._open_window(pos=self._saved_pos)

    def toggle_viz_fullscreen(self):
        self.viz_fullscreen = not self.viz_fullscreen
        self._set_viz_display()

    def viz_event(self, ev):
        v = self.viz
        if ev.type == pygame.KEYDOWN:
            k = ev.key
            alt = ev.mod & pygame.KMOD_ALT
            if k == pygame.K_ESCAPE or k == pygame.K_k:
                self.stop_viz()
            elif k in (pygame.K_SPACE, pygame.K_n, pygame.K_RIGHTBRACKET):
                v.next_preset(1)
            elif k in (pygame.K_BACKSPACE, pygame.K_p, pygame.K_LEFTBRACKET):
                v.next_preset(-1)
            elif k == pygame.K_l or k == pygame.K_SCROLLLOCK:
                v.toggle_lock()
            elif k == pygame.K_f or (alt and k in (pygame.K_RETURN, pygame.K_KP_ENTER)):
                self.toggle_viz_fullscreen()
            elif k in (pygame.K_h, pygame.K_F1):
                v.show("help", "ESC exit   SPACE next preset   BACKSPACE previous   L lock preset   "
                               "F full screen / window   Z X C V B transport", 6)
            elif k in (pygame.K_z, pygame.K_x, pygame.K_c, pygame.K_v, pygame.K_b,
                       pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                self.key_down(ev)
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            if ev.button == 3:
                self.stop_viz()
            elif ev.button == 1:
                now = pygame.time.get_ticks()
                if now - getattr(self, "_last_viz_click", 0) < 400:
                    self.toggle_viz_fullscreen()
                    self._last_viz_click = 0
                else:
                    self._last_viz_click = now
        elif ev.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED) and not self.viz_fullscreen:
            self.screen = pygame.display.get_surface()
            self.viz_window_size = self.screen.get_size()
            v.resize(self.viz_window_size)

    # ================================================================ main loop
    def run(self):
        clock = pygame.time.Clock()
        self.running = True
        while self.running:
            for ev in pygame.event.get():
                self.handle_event(ev)
            if self._pending_play is not None and not self.intro_channel.get_busy():
                self.play_index(self._pending_play)
                self._pending_play = None
            for evt in self.engine.update():
                if evt == "ended":
                    self.next_track(manual=False)
                elif evt == "error":
                    t = self.cur_track()
                    self.flash(f"CANNOT PLAY {os.path.basename(t.path) if t else ''}", 3)
                    self.error_count += 1
                    if self.error_count < len(self.playlist):
                        self.next_track(manual=False)
                elif evt == "casterror":
                    msg = self.engine.remote.error
                    self.engine.stop_cast()
                    self.flash(f"CAST FAILED: {msg}  ***  PLAYING ON THIS COMPUTER".upper(), 8)
                elif evt == "loaded":
                    self.error_count = 0
                    # pick up duration / format details for display
                    t = self.cur_track()
                    if t is not None:
                        if t.info is None:
                            t.info = read_info(t.path)
                            t.title = display_title(t.path, t.info)
                        if self.engine.duration:
                            t.info["duration"] = self.engine.duration
                        if self.viz:
                            self.viz.show("title", t.title, 5)
            self._cast_poll()
            if self._global_drag():
                self._poll_drag()
            self.marquee_off += 0.6
            if self.viz:
                self.viz.frame(self.screen, self.engine.samples_stereo(2048))
                pygame.display.flip()
                clock.tick(60)
            else:
                self.draw()
                clock.tick(60 if self._global_drag() else 30)
        self.save_config()
        self.engine.stop()
        if self.engine.remote is not None:
            self.engine.remote.close(wait=2.0)     # stop the device and let go of it
        pygame.quit()
