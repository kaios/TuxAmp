"""Tiny ctypes bridge to SDL functions pygame does not expose.

We bind to the *same* SDL library pygame already loaded (found through
/proc/self/maps), so we share its state. Everything degrades to None if the
library cannot be found.
"""

import ctypes
import os

_lib = False


def _sdl():
    global _lib
    if _lib is not False:
        return _lib
    _lib = None
    try:
        with open("/proc/self/maps") as f:
            paths = {line.split()[-1] for line in f if "/" in line}
        for p in sorted(paths):
            name = os.path.basename(p)
            # core library only, e.g. libSDL2-2.0.so.0 or libSDL2-2-<hash>.0.so.0 (wheel builds)
            if name.startswith(("libSDL2-2.0", "libSDL2-2-")) and ".so" in name:
                lib = ctypes.CDLL(p)
                lib.SDL_GetGlobalMouseState.restype = ctypes.c_uint32
                lib.SDL_GetGlobalMouseState.argtypes = [ctypes.POINTER(ctypes.c_int)] * 2
                lib.SDL_CaptureMouse.argtypes = [ctypes.c_int]
                _lib = lib
                break
    except Exception:
        _lib = None
    return _lib


def global_mouse():
    """(x, y, left_button_down) in desktop coordinates, or None."""
    lib = _sdl()
    if lib is None:
        return None
    x, y = ctypes.c_int(), ctypes.c_int()
    buttons = lib.SDL_GetGlobalMouseState(ctypes.byref(x), ctypes.byref(y))
    return x.value, y.value, bool(buttons & 1)


def capture_mouse(on):
    """Keep receiving mouse events while the pointer is outside the window."""
    lib = _sdl()
    if lib is not None:
        try:
            lib.SDL_CaptureMouse(1 if on else 0)
        except Exception:
            pass


def clipboard_text():
    """The text on the clipboard, or "" (pygame 2.6 has no clipboard call of its own)."""
    lib = _sdl()
    if lib is None:
        return ""
    try:
        lib.SDL_GetClipboardText.restype = ctypes.c_void_p
        lib.SDL_free.argtypes = [ctypes.c_void_p]
        p = lib.SDL_GetClipboardText()
        if not p:
            return ""
        try:
            return ctypes.string_at(p).decode("utf-8", "replace")
        finally:
            lib.SDL_free(p)
    except Exception:
        return ""


_x11 = False


def _xlib():
    global _x11
    if _x11 is not False:
        return _x11
    _x11 = None
    try:
        import ctypes.util
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.CDLL(name)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                                ctypes.POINTER(_XSetWindowAttributes)]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        _x11 = lib
    except Exception:
        _x11 = None
    return _x11


class _XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("cursor", ctypes.c_ulong)]


_CW_BACK_PIXMAP, _CW_BIT_GRAVITY, _NORTH_WEST_GRAVITY = 1 << 0, 1 << 4, 1


def keep_contents_on_resize(app):
    """Stop X from blanking the window every time it changes size.

    By default X throws the window's picture away on a resize and fills it
    with the background colour until the app redraws - that is the flicker you
    see while dragging the size grip.  NorthWest bit gravity keeps the old
    picture pinned to the top-left corner and no background means nothing gets
    painted over it, so the next frame simply replaces it.
    """
    try:
        import pygame
        if pygame.display.get_driver() != "x11":
            return
        wid = pygame.display.get_wm_info().get("window")
        if not wid or getattr(app, "_gravity_set_for", None) == wid:
            return
        lib = _xlib()
        if lib is None:
            return
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return
        attrs = _XSetWindowAttributes()
        attrs.background_pixmap = 0             # None
        attrs.bit_gravity = _NORTH_WEST_GRAVITY
        lib.XChangeWindowAttributes(dpy, wid, _CW_BACK_PIXMAP | _CW_BIT_GRAVITY, ctypes.byref(attrs))
        lib.XFlush(dpy)
        lib.XCloseDisplay(dpy)
        app._gravity_set_for = wid
    except Exception:
        pass
