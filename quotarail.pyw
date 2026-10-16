"""Quota Rail: your AI usage limits, pinned to the edge of the screen.

Every AI is one small file in the providers folder. The built-in ones read only files the AI apps already
write on this PC (no network, no tokens):
  providers/claude.py   Claude desktop's plan-usage-history.json
  providers/codex.py    Codex's own session logs (~/.codex/sessions)
To add another AI, copy providers/_template.py to providers/<name>.py and fill in read().

Hover a ring for its limits and reset times. Click a ring to refresh, drag the column to move it along
the edge, right-click for options.

Windows:  pythonw quotarail.pyw          (no console window)
macOS:    python3 quotarail.pyw          (needs Python with Tk 8.6+: python.org's installer, or
                                      Homebrew's python plus `brew install python-tk`)
Demo:     add --demo to show sample figures
"""

import base64
import ctypes
import datetime as dt
import importlib.util
import json
import math
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import zlib

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

HERE = os.path.dirname(os.path.abspath(__file__))
PROVIDERS_DIR = os.path.join(HERE, "providers")
CONFIG_PATH = os.path.join(HERE, "config.json")
CUSTOM_PATH = os.path.join(HERE, "custom_ais.json")  # AIs added from the menu; can hold API keys, so not in git

sys.path.insert(0, HERE)
import custom_ais  # noqa: E402  (needs HERE on the path first)

POLL_SECS = 30
ACTIVITY_SECS = 2
TICK_MS = 80
ANIM_MS = 50
SPIN_SECS = 1.2    # one turn of the working spinner
PULSE_SECS = 1.4   # one breath of the waiting ring
SPIN_FRAMES = 36
PULSE_FRAMES = 16
HIDE_DELAY_MS = 250
PEEK_MS = 5000
REDRAW_MS = 30_000
ALERT_MARKS = (80, 100)

BG = "#000000"
TEXT = "#f5f5f5"
DIM = "#8b8b8b"
TRACK = "#2b2b2b"
GREEN = "#22c55e"
YELLOW = "#facc15"
RED = "#ef4444"
AMBER = "#f59e0b"   # needs you now (pulses, so it can't be mistaken for the usage yellow)
BLUE = "#60a5fa"    # finished, waiting for your reply: a colour the usage scale never uses

DEFAULTS = {"edge": "right", "offset": 160, "autohide": False, "sounds": True, "provider_settings": {}}


def now_ms():
    return time.time() * 1000


# ---------- config ----------

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---------- providers ----------

def _ms(v):
    if isinstance(v, (int, float)) and v > 0:
        return v * 1000 if v < 1e12 else float(v)
    return None


def normalize(data):
    if not isinstance(data, dict):
        raise ValueError("read() must return a dict")
    windows = []
    for w in data.get("windows") or []:
        used = w.get("used")
        windows.append({
            "label": str(w.get("label") or "Usage"),
            "used": None if used is None else max(0.0, min(100.0, float(used))),
            "resets_at": _ms(w.get("resets_at")),
            "estimated": bool(w.get("estimated")),
        })
    return {"windows": windows, "updated": _ms(data.get("updated")) or now_ms(), "note": data.get("note")}


STATES = ("waiting", "working", "idle")


def normalize_sessions(items):
    sessions = []
    for s in items or []:
        if not isinstance(s, dict):
            continue
        state = s.get("state") if s.get("state") in STATES else "idle"
        sessions.append({"name": str(s.get("name") or "Session"), "state": state,
                         "detail": s.get("detail"), "where": s.get("where")})
    sessions.sort(key=lambda s: (STATES.index(s["state"]), s["name"].lower()))
    return sessions


def effective(w, now):
    """A window whose reset time has passed is at zero, whatever was last read."""
    if w["resets_at"] is not None and w["resets_at"] <= now:
        return (0.0 if w["used"] is not None else None), None, True
    return w["used"], w["resets_at"], False


class Provider:
    def __init__(self, key, module, load_error=None):
        self.key = key
        self.module = module
        self.name = getattr(module, "NAME", key.title())
        self.order = getattr(module, "ORDER", 100)
        self.icon = getattr(module, "ICON", None)
        self.data = None
        self.error = load_error
        self.sessions = []
        self.seen_activity = False  # the first reading has no "before", so it never alerts
        self.custom = isinstance(module, custom_ais.CustomAI)
        self.visible = self.check_available()

    def check_available(self):
        """A provider can hide its ring until its AI is set up on this PC, by defining available()."""
        check = getattr(self.module, "available", None)
        if not callable(check):
            return True
        try:
            return bool(check())
        except Exception:
            return True

    def state(self):
        """What the ring shows. A session blocked on you beats one working, which beats one that has finished
        and is waiting for your reply ("reply")."""
        states = {s["state"] for s in self.sessions}
        for state, shown in (("waiting", "waiting"), ("working", "working"), ("idle", "reply")):
            if state in states:
                return shown
        return None

    def headline(self, now):
        if not self.data:
            return None
        vals = [effective(w, now)[0] for w in self.data["windows"]]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None


def load_providers():
    providers = []
    try:
        names = sorted(os.listdir(PROVIDERS_DIR))
    except OSError:
        names = []
    for fn in names:
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        key = fn[:-3]
        spec = importlib.util.spec_from_file_location(f"quotarail_provider_{key}", os.path.join(PROVIDERS_DIR, fn))
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            providers.append(Provider(key, module))
        except Exception as e:  # a broken provider shows its error instead of stopping the app
            providers.append(Provider(key, module, f"Couldn't load {fn}: {e}"))
    for i, entry in enumerate(custom_ais.load_entries(CUSTOM_PATH)):
        providers.append(Provider(f"custom-{entry['id']}", custom_ais.CustomAI(entry, 60 + i)))
    providers.sort(key=lambda p: (p.order, p.key))
    return providers


class _Demo:
    def __init__(self, name, order, icon, windows, sessions=()):
        self.NAME, self.ORDER, self.ICON, self.windows, self.sessions = name, order, icon, windows, sessions

    def read(self):
        now = time.time()
        return {"windows": [{"label": l, "used": u, "resets_at": now + r if r else None} for l, u, r in self.windows],
                "updated": now - 180}

    def activity(self):
        return [{"name": n, "state": s, "detail": d} for n, s, d in self.sessions]


def demo_providers():
    return [Provider(k, m) for k, m in [
        ("claude", _Demo("Claude", 10, "claude", [("Current session", 73, 51 * 60), ("Weekly · all models", 7, 5.2 * 86400)],
                         [("my-app", "working", None), ("website", "idle", None)])),
        ("codex", _Demo("Codex", 20, "codex", [("Current session", 21, 3 * 3600), ("Weekly", 9, 4 * 86400)],
                        [("api-server", "waiting", "needs your approval")])),
        ("gemini", _Demo("Gemini", 30, None, [("Daily requests", 52, 9 * 3600)])),
    ]]


# ---------- formatting ----------

def level_color(pct):
    if pct is None:
        return DIM
    return RED if pct >= 70 else YELLOW if pct >= 50 else GREEN


def fmt_pct(p):
    return "—" if p is None else f"{p:.0f}%"


def fmt_span(ms):
    mins = max(1, round(ms / 60_000))
    d, rem = divmod(mins, 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m} min"


def fmt_reset(reset, rolled, estimated, now):
    if rolled:
        return "Reset · fresh window"
    if reset is None:
        return ""
    approx = "~" if estimated else ""
    if reset - now < 24 * 3600 * 1000:
        return f"Resets in {approx}{fmt_span(reset - now)}"
    d = dt.datetime.fromtimestamp(reset / 1000)
    if reset - now >= 6 * 24 * 3600 * 1000:  # a weekday alone could mean this week or next
        return f"Resets {approx}{d:%a}, {d:%b} {d.day}"
    return f"Resets {approx}{d:%a} {d.hour % 12 or 12}:{d:%M} {d:%p}"


def fmt_updated(updated, now):
    age = now - updated
    return "Updated just now" if age < 60_000 else f"Updated {fmt_span(age)} ago"


# ---------- pixel drawing (antialiased, pure Python) ----------

def _rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _clamp(v):
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _photo(w, h, pixel):
    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join("{" + " ".join(pixel(x, y) for x in range(w)) + "}" for y in range(h)))
    return img


_cache = {}


def cached(key, make):
    if key not in _cache:
        _cache[key] = make()
    return _cache[key]


def ring_image(size, frac, color):
    frac = _clamp(frac)

    def make():
        bg, track, fill = _rgb(BG), _rgb(TRACK), _rgb(color)
        c = size / 2
        ro = size / 2 - 0.5
        ri = ro - max(3.0, size * 0.11)
        end = frac * 2 * math.pi

        def pixel(x, y):
            dx, dy = x + 0.5 - c, y + 0.5 - c
            r = math.hypot(dx, dy)
            cov = _clamp(ro - r + 0.5) * _clamp(r - ri + 0.5)
            if cov <= 0:
                return BG
            a = math.atan2(dx, -dy) % (2 * math.pi)
            if frac >= 1:
                arc = 1.0
            elif frac <= 0:
                arc = 0.0
            else:
                d = min(a, end - a) if a <= end else -min(a - end, 2 * math.pi - a)
                arc = _clamp(d * r + 0.5)
            return "#%02x%02x%02x" % _mix(bg, _mix(track, fill, arc), cov)
        return _photo(size, size, pixel)
    return cached(("ring", size, round(frac * 360), color), make)


def bar_image(w, h, frac, color):
    frac = _clamp(frac)

    def make():
        bg, track, fill = _rgb(BG), _rgb(TRACK), _rgb(color)
        r = h / 2

        def pixel(x, y):
            px, py = x + 0.5, y + 0.5
            cx = min(max(px, r), w - r)
            cov = _clamp(0.5 - (math.hypot(px - cx, py - r) - r))
            if cov <= 0:
                return BG
            on = _clamp(frac * w - px + 0.5) if frac > 0 else 0.0
            return "#%02x%02x%02x" % _mix(bg, _mix(track, fill, on), cov)
        return _photo(w, h, pixel)
    return cached(("bar", w, h, round(frac * 400), color), make)


def _seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    t = _clamp(((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy or 1))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def _claude_segments():
    segs = []
    for k in range(12):
        a = k * math.pi / 6 + math.pi / 12
        outer = 0.98 if k % 2 == 0 else 0.74
        segs.append((0.16 * math.cos(a), 0.16 * math.sin(a), outer * math.cos(a), outer * math.sin(a)))
    return segs, 0.105


GLYPHS = {
    "claude": _claude_segments(),
    # a terminal prompt ">_"
    "codex": ([(-0.72, -0.5, -0.12, 0.0), (-0.12, 0.0, -0.72, 0.5), (0.12, 0.55, 0.78, 0.55)], 0.12),
}


def glyph_image(size, name, color=TEXT):
    def make():
        segs, radius = GLYPHS[name]
        scale = size / 2 - 0.5
        bg, fg = _rgb(BG), _rgb(color)

        def pixel(x, y):
            px, py = (x + 0.5 - size / 2) / scale, (y + 0.5 - size / 2) / scale
            d = min(_seg_dist(px, py, *s) for s in segs) - radius
            cov = _clamp(0.5 - d * scale)
            return BG if cov <= 0 else "#%02x%02x%02x" % _mix(bg, fg, cov)
        return _photo(size, size, pixel)
    return cached(("glyph", size, name, color), make)


# ---------- curved edges ----------
# Each returns coverage(x, y) in 0..1 for a strip drawn as if it sat at the top of a shape on the right-hand
# side; mirrored() turns it to face the bottom or the left. Curves are antialiased against the desktop.

def flare(px, py, x0, r):
    """The concave fillet between a horizontal edge at y=r and a vertical edge at x=x0+r, curving round (x0, 0)."""
    if px <= x0 or py >= r:
        return 0.0
    return _clamp(math.hypot(px - x0, py) - r + 0.5)


def rounded_top(px, py, w, top, r, left=True, right=True):
    """A body whose top edge is at y=top with the chosen top corners rounded to radius r."""
    if py <= top - 1:
        return 0.0
    if left and px < r and py < top + r:
        return _clamp(r - math.hypot(px - r, py - (top + r)) + 0.5)
    if right and px > w - r and py < top + r:
        return _clamp(r - math.hypot(px - (w - r), py - (top + r)) + 0.5)
    return _clamp(py - top + 0.5)


def column_cap(w, ear, r):
    """Column end: rounded on the open side, flaring into the screen edge on the other."""
    return lambda px, py: max(rounded_top(px, py, w, ear, r, right=False), flare(px, py, w - ear, ear))


def card_cap(w, horn, r):
    """Card top: rounded on the far side, a curved tail rising towards the column on the near side."""
    return lambda px, py: max(rounded_top(px, py, w, horn, r, right=False), flare(px, py, w - horn, horn))


def card_base(w, r):
    return lambda px, py: rounded_top(px, py, w, 0, r)


def mirrored(cov, w, h, flip_x=False, flip_y=False):
    return lambda px, py: cov(w - px if flip_x else px, h - py if flip_y else py)


# ---------- platform plumbing ----------

def win32_dpi_aware():
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


_instance_lock = None


def single_instance():
    """False if Quota Rail is already running for this user."""
    global _instance_lock
    if IS_WIN:
        ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\QuotaRailSingleInstance")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    import fcntl
    _instance_lock = open(os.path.join(tempfile.gettempdir(), f"quota-rail-{os.getuid()}.lock"), "w")
    try:
        fcntl.flock(_instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def play_sound(event):
    """A short system sound: a gentle one when work finishes, a more noticeable one when an AI needs you."""
    try:
        if IS_WIN:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if event == "waiting" else winsound.MB_ICONASTERISK)
        elif IS_MAC:
            name = "Funk" if event == "waiting" else "Glass"
            subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])
    except Exception:
        pass


AUTOSTART_NAME = "Quota Rail"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/com.quotarail.plist")


def _launch_command():
    """How to start this copy of Quota Rail with no console window."""
    python = sys.executable
    if IS_WIN:
        windowless = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(windowless):
            python = windowless
    return python, os.path.abspath(__file__)


def autostart_enabled():
    """Whether Quota Rail starts when you sign in: your user's Run key on Windows, a LaunchAgent on macOS."""
    if IS_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except OSError:
            return False
    return IS_MAC and os.path.isfile(_LAUNCH_AGENT)


def set_autostart(on):
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                python, script = _launch_command()
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, f'"{python}" "{script}"')
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    elif IS_MAC:
        if on:
            python, script = _launch_command()
            os.makedirs(os.path.dirname(_LAUNCH_AGENT), exist_ok=True)
            with open(_LAUNCH_AGENT, "w", encoding="utf-8") as f:
                f.write(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>Label</key><string>com.quotarail</string>\n'
                    f'  <key>ProgramArguments</key><array><string>{python}</string><string>{script}</string></array>\n'
                    '  <key>RunAtLoad</key><true/>\n'
                    '</dict></plist>\n')
        elif os.path.isfile(_LAUNCH_AGENT):
            os.remove(_LAUNCH_AGENT)


def open_folder(path):
    if IS_WIN:
        os.startfile(path)
    else:
        subprocess.Popen(["open" if IS_MAC else "xdg-open", path])


def win32_style(win, rounded):
    """No taskbar button, never takes focus, and Windows 11's smooth corners (or none) without a border."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(win.wm_frame(), 16)
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, (ex | 0x80 | 0x08000000) & ~0x40000)  # TOOLWINDOW | NOACTIVATE
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2 if rounded else 1)), 4)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)
    except (AttributeError, OSError, ValueError):
        pass


class _BlendFunction(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_ubyte)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                ("planes", ctypes.c_uint16), ("bits", ctypes.c_uint16), ("compression", ctypes.c_uint32),
                ("image_size", ctypes.c_uint32), ("xppm", ctypes.c_int32), ("yppm", ctypes.c_int32),
                ("used", ctypes.c_uint32), ("important", ctypes.c_uint32)]


_gdi = None


def _gdi_api():
    """user32/gdi32 with handle-sized types, so nothing is truncated on 64-bit Python."""
    global _gdi
    if _gdi is None:
        from ctypes import wintypes as w
        u, g = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        u.GetDC.restype, u.GetDC.argtypes = w.HDC, [w.HWND]
        u.ReleaseDC.argtypes = [w.HWND, w.HDC]
        u.UpdateLayeredWindow.argtypes = [w.HWND, w.HDC, ctypes.POINTER(w.POINT), ctypes.POINTER(w.SIZE), w.HDC,
                                          ctypes.POINTER(w.POINT), w.DWORD, ctypes.POINTER(_BlendFunction), w.DWORD]
        u.UpdateLayeredWindow.restype = w.BOOL
        u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = [w.HWND, ctypes.c_int], ctypes.c_long
        u.SetWindowLongW.argtypes, u.SetWindowLongW.restype = [w.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = w.HDC, [w.HDC]
        g.CreateDIBSection.restype = w.HBITMAP
        g.CreateDIBSection.argtypes = [w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
        g.SelectObject.restype, g.SelectObject.argtypes = w.HGDIOBJ, [w.HDC, w.HGDIOBJ]
        g.DeleteObject.argtypes, g.DeleteDC.argtypes = [w.HGDIOBJ], [w.HDC]
        _gdi = (u, g, w)
    return _gdi


def render_alpha(w, h, coverage):
    """coverage(px, py) sampled at pixel centres, as one alpha byte per pixel, row by row."""
    out = bytearray(w * h)
    for yy in range(h):
        row = yy * w
        for xx in range(w):
            a = coverage(xx + 0.5, yy + 0.5)
            if a > 0:
                out[row + xx] = round(a * 255)
    return out


def rgba_png(w, h, rgba):
    """An RGBA PNG from w*h*4 bytes, encoded without any image library."""
    raw = bytearray()
    for yy in range(h):
        raw.append(0)  # no filter
        raw += rgba[yy * w * 4:(yy + 1) * w * 4]

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def black_png(w, h, alpha, rgb=(0, 0, 0)):
    """A single-colour RGBA PNG with the given alpha."""
    rgba = bytearray(bytes(rgb) + b"\0") * (w * h)
    rgba[3::4] = alpha
    return rgba_png(w, h, rgba)


def read_png(path):
    """(w, h, RGBA bytes) of an 8-bit RGB or RGBA PNG, decoded without any image library."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, head = 8, bytearray(), None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I", data[pos:pos + 4])[0], data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if not head:
        raise ValueError("no PNG header")
    w, h, depth, colour, _, _, interlace = head
    if depth != 8 or colour not in (2, 6) or interlace:
        raise ValueError("only 8-bit, non-interlaced RGB/RGBA PNGs are supported")
    bpp = 4 if colour == 6 else 3
    stride = w * bpp
    raw = zlib.decompress(bytes(idat))
    prev, out, i = bytearray(stride), bytearray(), 0
    for _ in range(h):
        kind, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):  # undo the PNG row filters
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            if kind == 1:
                line[x] = (line[x] + a) & 255
            elif kind == 2:
                line[x] = (line[x] + b) & 255
            elif kind == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif kind == 4:
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += line if bpp == 4 else b"".join(bytes(line[j:j + 3]) + b"\xff" for j in range(0, stride, 3))
        prev = line
    return w, h, out


def png_icon(path, size, drop_grey=False):
    """A PNG logo scaled smoothly to fit size x size, keeping its transparency. drop_grey removes a grey or dark
    background behind a colourful mark (for a logo that only comes as an app tile), keeping just the mark."""
    w, h, px = read_png(path)
    if drop_grey:
        for i in range(0, len(px), 4):
            r, g, b = px[i], px[i + 1], px[i + 2]
            px[i + 3] = round(px[i + 3] * _clamp(((max(r, g, b) - min(r, g, b)) / 255 - 0.12) / 0.2))
    # crop to what's visible, as a square, so every logo fills the ring the same way
    xs = [i // 4 % w for i in range(3, len(px), 4) if px[i] > 16]
    ys = [i // 4 // w for i in range(3, len(px), 4) if px[i] > 16]
    if not xs:
        raise ValueError("the image is empty")
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    side = max(x1 - x0, y1 - y0)
    x0, y0 = (x0 + x1 - side) / 2, (y0 + y1 - side) / 2
    step = side / size
    out = bytearray(size * size * 4)
    for oy in range(size):
        for ox in range(size):
            tr = tg = tb = ta = n = 0
            for sy in range(int(y0 + oy * step), max(int(y0 + oy * step) + 1, int(y0 + (oy + 1) * step))):
                for sx in range(int(x0 + ox * step), max(int(x0 + ox * step) + 1, int(x0 + (ox + 1) * step))):
                    n += 1
                    if 0 <= sx < w and 0 <= sy < h:
                        j = (sy * w + sx) * 4
                        a = px[j + 3]
                        tr, tg, tb, ta = tr + px[j] * a, tg + px[j + 1] * a, tb + px[j + 2] * a, ta + a
            k = (oy * size + ox) * 4
            if ta:  # average colour weighted by alpha, so transparent pixels don't darken the edges
                out[k:k + 4] = bytes((tr // ta, tg // ta, tb // ta, ta // n))
    return tk.PhotoImage(data=base64.b64encode(rgba_png(size, size, out)))


def overlay(size, color, coverage, key):
    """A see-through image laid over a ring, so the icon underneath still shows."""
    return cached(("overlay", size, color, key), lambda: tk.PhotoImage(
        data=base64.b64encode(black_png(size, size, render_alpha(size, size, coverage), _rgb(color)))))


def spinner_frame(size, ring_inner, i):
    """'Working': a short arc with a fading tail, turning just inside the usage ring."""
    c = size / 2
    ro, ri = ring_inner - 1.0, ring_inner - 3.2
    head = i / SPIN_FRAMES * 2 * math.pi
    tail = 1.9  # radians

    def cov(px, py):
        dx, dy = px - c, py - c
        r = math.hypot(dx, dy)
        band = _clamp(ro - r + 0.5) * _clamp(r - ri + 0.5)
        if band <= 0:
            return 0.0
        behind = (head - math.atan2(dx, -dy)) % (2 * math.pi)  # how far this point trails the head
        if behind > tail:
            return 0.0
        return band * (1 - behind / tail) ** 1.6 * _clamp(behind * r + 0.5)
    return overlay(size, TEXT, cov, ("spin", ring_inner, i))


def inner_ring(size, ring_inner, color, level=1.0):
    """A thin full circle in the spinner's track, inside the usage ring, so the usage colours stay untouched."""
    c = size / 2
    ro, ri = ring_inner - 1.0, ring_inner - 3.2

    def cov(px, py):
        r = math.hypot(px - c, py - c)
        return _clamp(ro - r + 0.5) * _clamp(r - ri + 0.5) * level
    return overlay(size, color, cov, ("inner", ring_inner, round(level * 100)))


def pulse_frame(size, ring_inner, i):
    """'Needs you': the inner ring breathing in amber."""
    level = 0.25 + 0.75 * (0.5 - 0.5 * math.cos(i / PULSE_FRAMES * 2 * math.pi))
    return inner_ring(size, ring_inner, AMBER, level)


def dot_image(size, color):
    c = size / 2
    return overlay(size, color, lambda px, py: _clamp(size / 2 - math.hypot(px - c, py - c) + 0.5), "dot")


class _ShapeBase:
    """A black, borderless window drawn from a per-pixel alpha mask, so its curves blend into the desktop."""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.key = None
        self.visible = False

    def show(self, x, y, w, h, coverage):
        """Draw coverage(px, py) (pixel centres, 0..1) as black at (x, y). Re-renders only when the shape changes."""
        if w <= 0 or h <= 0:
            return self.hide()
        key = (w, h, coverage.key)
        if key != self.key:
            self.key = key
            self._render(w, h, render_alpha(w, h, coverage))
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        if not self.visible:
            self.win.deiconify()
            self.visible = True
        self._present(x, y, w, h)

    def hide(self):
        if self.visible:
            self.win.withdraw()
            self.visible = False


class MacShape(_ShapeBase):
    """macOS: Tk can make a window's background see-through, and a PNG's alpha then shows the desktop."""

    def __init__(self, root):
        super().__init__(root)
        self.win.attributes("-transparent", True)
        self.win.configure(bg="systemTransparent")
        self.canvas = tk.Canvas(self.win, bg="systemTransparent", highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0)
        self.image = None
        self.win.withdraw()

    def _render(self, w, h, alpha):
        self.image = tk.PhotoImage(data=base64.b64encode(black_png(w, h, alpha)))
        self.canvas.delete("all")
        self.canvas.config(width=w, height=h)
        self.canvas.create_image(0, 0, image=self.image, anchor="nw")

    def _present(self, x, y, w, h):
        pass


class WinShape(_ShapeBase):
    """Windows: a layered window given its pixels, alpha and all, through UpdateLayeredWindow."""

    def __init__(self, root):
        super().__init__(root)
        self.win.configure(bg=BG)
        self.win.withdraw()
        self.win.update_idletasks()
        self.hwnd = int(self.win.wm_frame(), 16)
        u, _, _ = _gdi_api()
        ex = u.GetWindowLongW(self.hwnd, -20)
        u.SetWindowLongW(self.hwnd, -20, (ex | 0x80000 | 0x80 | 0x08000000) & ~0x40000)  # LAYERED|TOOLWINDOW|NOACTIVATE
        self.pixels = None

    def _render(self, w, h, alpha):
        buf = bytearray(w * h * 4)  # premultiplied BGRA; black means only alpha is set
        buf[3::4] = alpha
        self.pixels = buf

    def _present(self, x, y, w, h):
        u, g, wt = _gdi_api()
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmi = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bmp = g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            if not bmp or not bits:
                return
            ctypes.memmove(bits, bytes(self.pixels), len(self.pixels))
            old = g.SelectObject(mem, bmp)
            blend = _BlendFunction(0, 0, 255, 1)  # AC_SRC_OVER with per-pixel alpha
            u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
                                  mem, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
            g.SelectObject(mem, old)
        finally:
            if bmp:
                g.DeleteObject(bmp)
            g.DeleteDC(mem)
            u.ReleaseDC(None, screen)


Shape = WinShape if IS_WIN else MacShape


def shape(fn, *key):
    """Tag a coverage function with a key describing it, so Shape knows when it must redraw."""
    fn.key = key
    return fn


# ---------- the app ----------

class App:
    def __init__(self, root, providers):
        self.root = root
        self.providers = providers
        self.by_key = {p.key: p for p in providers}
        self.cfg = load_config()
        # Windows measures in physical pixels (scale by DPI); macOS already works in points, Retina included.
        self.s = 1.0 if IS_MAC else root.winfo_fpixels("1i") / 96
        self.sw, self.sh = root.winfo_screenwidth(), root.winfo_screenheight()

        self.f_pct = self.font(13, bold=True)
        self.f_title = self.font(13, bold=True)
        self.f_label = self.font(12)
        self.f_small = self.font(11)
        self.f_mono = self.font(15, bold=True)

        self.col_w = self.px(62)
        self.strip_w = self.px(5)
        self.radius = self.px(20)     # the column's rounded corners on the open side
        self.ear = self.px(22)        # the flare into the screen edge
        self.card_radius = self.px(16)
        self.horn = self.px(18)       # the card's curved tail towards the column
        self.ring = self.px(40)
        self.row_h = self.ring + self.px(30)
        self.pad_top = self.px(22)
        self.pad_bottom = self.px(10)
        self.card_rect = None

        self.expanded = not self.cfg["autohide"]
        self.card_key = None
        self.peek_key, self.peek_until = None, 0
        self.away_since = None
        self.drag = None
        self.last_draw = 0
        self.results = queue.Queue()
        self.wake = threading.Event()
        self.ring_inner = self.ring / 2 - 0.5 - max(3.0, self.ring * 0.11)
        self.anim_items = {}  # provider key -> (canvas item, "working" | "waiting")

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        # The straight middle of each shape is an ordinary window holding a canvas; the curved ends above and
        # below it are Shapes. Canvases are placed so their y=0 is the top of the whole shape.
        self.col = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
        self.col.place(x=0, y=0)
        self.col.bind("<ButtonPress-1>", self.on_press)
        self.col.bind("<B1-Motion>", self.on_drag)
        self.col.bind("<ButtonRelease-1>", self.on_release)
        self.bind_menu(self.col)

        self.col_top, self.col_bottom = Shape(root), Shape(root)
        self.card, self.card_canvas = self.popup()
        self.bind_menu(self.card_canvas)
        self.card_top, self.card_bottom = Shape(root), Shape(root)

        self.autohide_var = tk.BooleanVar(value=self.cfg["autohide"])
        self.left_var = tk.BooleanVar(value=self.cfg["edge"] == "left")
        self.sounds_var = tk.BooleanVar(value=self.cfg["sounds"])
        self.menu = None
        self.dialog = None
        self.build_menu()

        self.draw_column()
        root.update_idletasks()
        win32_style(root, rounded=False)
        win32_style(self.card, rounded=False)
        self.card.withdraw()

        threading.Thread(target=self.worker, daemon=True).start()
        root.after(TICK_MS, self.tick)
        root.after(ANIM_MS, self.animate)

    def px(self, v):
        return round(v * self.s)

    def shown(self):
        """The providers with a ring: the ones whose AI is set up on this PC."""
        return [p for p in self.providers if p.visible]

    def build_menu(self):
        """The right-click menu. Rebuilt when AIs are added or removed."""
        if self.menu:
            self.menu.destroy()
        menu = self.menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Refresh now", command=self.wake.set)
        menu.add_checkbutton(label="Auto-hide", variable=self.autohide_var, command=self.toggle_autohide)
        menu.add_checkbutton(label="Dock on left edge", variable=self.left_var, command=self.toggle_edge)
        menu.add_checkbutton(label="Sound when an AI finishes or needs you", variable=self.sounds_var,
                             command=self.toggle_sounds)
        if IS_WIN or IS_MAC:
            self.autostart_var = tk.BooleanVar(value=autostart_enabled())
            menu.add_checkbutton(label="Start with Windows" if IS_WIN else "Start at login",
                                 variable=self.autostart_var, command=self.toggle_autostart)
        menu.add_separator()
        self.setting_vars = []
        for p in self.providers:
            options = getattr(p.module, "SETTINGS", None)
            if not isinstance(options, dict):
                continue
            chosen = self.cfg["provider_settings"].setdefault(p.key, {})
            for name, (label, default) in options.items():
                chosen.setdefault(name, bool(default))
                var = tk.BooleanVar(value=chosen[name])
                self.setting_vars.append(var)
                menu.add_checkbutton(label=f"{p.name}: {label}", variable=var,
                                     command=lambda p=p, name=name, var=var: self.set_provider_setting(p, name, var))
            p.module.settings = dict(chosen)
        menu.add_separator()
        menu.add_command(label="Add an AI…", command=lambda: self.open_dialog(None))
        custom = [p for p in self.providers if p.custom]
        if custom:
            edit = tk.Menu(menu, tearoff=False)
            for p in custom:
                edit.add_command(label=f"{p.name}…", command=lambda p=p: self.open_dialog(p))
            menu.add_cascade(label="Edit an AI", menu=edit)
        menu.add_command(label="Open providers folder", command=lambda: open_folder(PROVIDERS_DIR))
        menu.add_separator()
        menu.add_command(label="Quit", command=self.root.destroy)

    # --- AIs added from the menu ---

    def open_dialog(self, p):
        if self.dialog and self.dialog.win.winfo_exists():
            self.dialog.win.destroy()
        self.hide_card()
        entry = p.module.entry if p else None
        self.dialog = custom_ais.AIDialog(self.root, entry, self.save_custom, self.remove_custom if p else None)

    def save_custom(self, entry):
        entries = custom_ais.load_entries(CUSTOM_PATH)
        for i, e in enumerate(entries):
            if e["id"] == entry["id"]:
                entries[i] = entry
                break
        else:
            entries.append(entry)
        custom_ais.save_entries(CUSTOM_PATH, entries)
        self.reload_custom(entries)

    def remove_custom(self, entry):
        entries = [e for e in custom_ais.load_entries(CUSTOM_PATH) if e["id"] != entry["id"]]
        custom_ais.save_entries(CUSTOM_PATH, entries)
        self.reload_custom(entries)

    def reload_custom(self, entries):
        keep = [p for p in self.providers if not p.custom]
        added = [Provider(f"custom-{e['id']}", custom_ais.CustomAI(e, 60 + i)) for i, e in enumerate(entries)]
        self.providers = sorted(keep + added, key=lambda p: (p.order, p.key))
        self.by_key = {p.key: p for p in self.providers}
        self.build_menu()
        self.draw_column()
        self.wake.set()  # read the new ones now

    def font(self, size, bold=False):
        """A UI font sized in pixels (negative size), so it matches the layout on either system."""
        if IS_WIN:
            return tkfont.Font(family="Segoe UI Semibold" if bold else "Segoe UI", size=-self.px(size))
        return tkfont.Font(family="Helvetica Neue", size=-self.px(size), weight="bold" if bold else "normal")

    def bind_menu(self, widget):
        widget.bind("<Button-3>", self.show_menu)
        if IS_MAC:  # older Tk on macOS reports the right button as 2; Ctrl-click is the usual alternative
            widget.bind("<Button-2>", self.show_menu)
            widget.bind("<Control-Button-1>", self.show_menu)

    def popup(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0, bd=0)
        canvas.place(x=0, y=0)
        return win, canvas

    # --- data ---

    def worker(self):
        """Usage every POLL_SECS (or on a refresh click), each AI on its own thread so a slow one (a web request,
        Antigravity's process search) never holds up the others. Activity runs on its own loop every ACTIVITY_SECS."""
        threading.Thread(target=self.activity_worker, daemon=True).start()
        reading = set()  # keys whose read is still running
        lock = threading.Lock()

        def read_one(p):
            try:
                available = p.check_available()
                if available != p.visible:
                    self.results.put(("available", p.key, available, None))
                if available and hasattr(p.module, "read"):
                    try:
                        self.results.put(("usage", p.key, normalize(p.module.read()), None))
                    except Exception as e:
                        self.results.put(("usage", p.key, None, (str(e).strip() or type(e).__name__)[:200]))
            finally:
                with lock:
                    reading.discard(p.key)

        while True:
            for p in list(self.providers):  # a copy: AIs can be added or removed meanwhile
                with lock:
                    if p.module is None or p.key in reading:
                        continue
                    reading.add(p.key)
                threading.Thread(target=read_one, args=(p,), daemon=True).start()
            self.wake.wait(POLL_SECS)
            self.wake.clear()

    def activity_worker(self):
        while True:
            for p in list(self.providers):
                if p.module is not None and p.visible and hasattr(p.module, "activity"):
                    try:
                        self.results.put(("activity", p.key, normalize_sessions(p.module.activity()), None))
                    except Exception:
                        pass  # keep the last known sessions rather than flicker them away
            time.sleep(ACTIVITY_SECS)

    def apply_results(self):
        changed = False
        now = now_ms()
        while True:
            try:
                kind, key, data, err = self.results.get_nowait()
            except queue.Empty:
                return changed
            p = self.by_key.get(key)
            if p is None:  # removed since it was read
                continue
            if kind == "available":
                p.visible = data
                if not data and self.card_key == key:
                    self.hide_card()
                changed = True
                continue
            if kind == "activity":
                if data != p.sessions:
                    self.activity_changed(p, data, now)
                    changed = True
                p.seen_activity = True
                continue
            before = p.headline(now) if p.data else None
            if data:
                p.data = data
            p.error = err
            after = p.headline(now)
            if before is not None and after is not None and any(before < m <= after for m in ALERT_MARKS):
                self.peek_key, self.peek_until = key, now + PEEK_MS
            changed = True

    def activity_changed(self, p, sessions, now):
        """Peek the card (and chime) when a session finishes its work or stops to ask you something.
        A session that vanishes mid-turn was closed, not finished, so it is not announced."""
        before = {s["name"]: s["state"] for s in p.sessions}
        p.sessions = sessions
        if not p.seen_activity:
            return
        event = None
        for s in sessions:
            was = before.get(s["name"])
            if s["state"] == "waiting" and was != "waiting":
                event = "waiting"
                break
            if s["state"] == "idle" and was == "working":
                event = "done"
        if event:
            self.peek_key, self.peek_until = p.key, now + PEEK_MS
            if self.cfg["sounds"]:
                play_sound(event)

    # --- geometry ---

    def right(self):
        return self.cfg["edge"] != "left"

    def column_rect(self):
        """The column's body on screen (x, y, w, h), not counting the flares above and below it."""
        h = self.pad_top + max(1, len(self.shown())) * self.row_h + self.pad_bottom
        w = self.col_w if self.expanded else self.strip_w
        y = int(min(max(self.cfg["offset"], self.ear + self.px(4)), self.sh - h - self.ear - self.px(4)))
        x = self.sw - w if self.right() else 0
        return x, y, w, h

    def place_column(self):
        x, y, w, h = self.column_rect()
        if not self.expanded:
            self.col_top.hide()
            self.col_bottom.hide()
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.col.place(x=0, y=0)
            return
        r, e = self.radius, self.ear
        flip = not self.right()
        cap_h = e + r
        cap = column_cap(w, e, r)
        self.col_top.show(x, y - e, w, cap_h, shape(mirrored(cap, w, cap_h, flip_x=flip), "col", w, e, r, flip, 0))
        self.col_bottom.show(x, y + h - r, w, cap_h,
                             shape(mirrored(cap, w, cap_h, flip_x=flip, flip_y=True), "col", w, e, r, flip, 1))
        self.root.geometry(f"{w}x{h - 2 * r}+{x}+{y + r}")
        self.col.place(x=0, y=-r)

    def row_at(self, px_, py_):
        x, y, w, h = self.column_rect()
        if not (x <= px_ < x + w and y <= py_ < y + h) or not self.expanded:
            return None
        i = int((py_ - y - self.pad_top + self.px(7)) // self.row_h)
        shown = self.shown()
        return shown[i] if 0 <= i < len(shown) else None

    # --- drawing ---

    def icon_image(self, p, size):
        if p.icon in GLYPHS:
            return glyph_image(size, p.icon)
        if isinstance(p.icon, str) and p.icon.lower().endswith(".png"):
            path = os.path.join(PROVIDERS_DIR, p.icon)  # an absolute path stays as it is
            drop_grey = bool(getattr(p.module, "ICON_DROP_GREY", False))
            key = ("png", path, size, drop_grey)
            if key not in _cache:
                try:
                    _cache[key] = png_icon(path, size, drop_grey)
                except (OSError, ValueError, zlib.error, tk.TclError):
                    _cache[key] = None  # unreadable: fall back to the letter, and don't try again every redraw
            return _cache[key]
        return None

    def draw_icon(self, canvas, p, cx, cy, size):
        img = self.icon_image(p, size)
        if img:
            canvas.create_image(cx, cy, image=img)
        else:
            canvas.create_text(cx, cy, text=(p.name[:1] or "?").upper(), fill=TEXT, font=self.f_mono)

    def draw_column(self):
        self.place_column()
        x, y, w, h = self.column_rect()
        c = self.col
        c.delete("all")
        c.config(width=w, height=h)
        self.last_draw = now_ms()
        if not self.expanded:
            return
        now = now_ms()
        cx = self.col_w / 2
        self.anim_items = {}
        for i, p in enumerate(self.shown()):
            top = self.pad_top + i * self.row_h
            cy = top + self.ring / 2
            pct = p.headline(now)
            state = p.state()
            c.create_image(cx, cy, image=ring_image(self.ring, (pct or 0) / 100, level_color(pct)))
            self.draw_icon(c, p, cx, cy, self.px(18))
            if state:  # see-through, so it goes over the icon without hiding it
                self.anim_items[p.key] = (c.create_image(cx, cy, image=self.anim_frame(state)), state)
            c.create_text(cx, top + self.ring + self.px(12), text=fmt_pct(pct),
                          fill=TEXT if pct is not None else DIM, font=self.f_pct)

    def anim_frame(self, state):
        t = time.time()
        if state == "reply":  # your turn: a steady amber ring, calmer than the pulse that means "needs you now"
            return inner_ring(self.ring, self.ring_inner, BLUE)
        if state == "working":
            return spinner_frame(self.ring, self.ring_inner, int(t / SPIN_SECS * SPIN_FRAMES) % SPIN_FRAMES)
        return pulse_frame(self.ring, self.ring_inner, int(t / PULSE_SECS * PULSE_FRAMES) % PULSE_FRAMES)

    def animate(self):
        if self.expanded:
            for item, state in self.anim_items.values():
                self.col.itemconfigure(item, image=self.anim_frame(state))
        self.root.after(ANIM_MS, self.animate)

    def draw_sessions(self, c, sessions, x, y, width, limit=5):
        """One line per live session: a coloured dot, its name, and what it's doing."""
        px = self.px
        c.create_line(x, y - px(8), x + width, y - px(8), fill=TRACK)
        c.create_text(x, y, text="Sessions", fill=DIM, font=self.f_small, anchor="nw")
        y += px(20)
        for s in sessions[:limit]:
            if s["state"] == "waiting":
                color, status = AMBER, "Needs you" + (f" · {s['detail']}" if s.get("detail") else "")
            elif s["state"] == "working":
                color, status = TEXT, "Working…"
            else:
                color, status = BLUE, "Waiting for your reply"
            c.create_image(x + px(4), y + px(8), image=dot_image(px(7), color))
            c.create_text(x + width, y + px(8), text=status, fill=color, font=self.f_small, anchor="e")
            room = width - px(20) - self.f_small.measure(status)
            c.create_text(x + px(16), y + px(8), text=self.fit(s["name"], self.f_label, room),
                          fill=TEXT, font=self.f_label, anchor="w")
            y += px(22)
        if len(sessions) > limit:
            c.create_text(x + px(16), y, text=f"+{len(sessions) - limit} more", fill=DIM, font=self.f_small, anchor="nw")
            y += px(20)
        return y + px(10)

    @staticmethod
    def fit(text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def show_card(self, p):
        self.card_key = p.key
        self.draw_card()  # position first: a window shown before it has one lands at 0,0
        self.card.deiconify()
        self.card.lift()

    def hide_card(self):
        self.card_key = None
        self.card_rect = None
        self.card.withdraw()
        self.card_top.hide()
        self.card_bottom.hide()

    def draw_card(self):
        p = self.by_key.get(self.card_key)
        if not p:
            return
        c, px, now = self.card_canvas, self.px, now_ms()
        c.delete("all")
        w = px(300)
        pad = px(16)
        inner = w - 2 * pad
        r = self.card_radius
        y = r + px(2)

        self.draw_icon(c, p, pad + px(9), y + px(9), px(18))
        c.create_text(pad + px(26), y + px(9), text=f"{p.name} Usage", fill=TEXT, font=self.f_title, anchor="w")
        y += px(30)

        windows = p.data["windows"] if p.data else []
        if not windows:
            c.create_text(pad, y, text=p.error or "Waiting for the first reading…", fill=DIM,
                          font=self.f_label, anchor="nw", width=inner)
            y += px(40)
        for win in windows:
            used, reset, rolled = effective(win, now)
            reset_text = fmt_reset(reset, rolled, win["estimated"], now)
            room = inner - self.f_small.measure(reset_text) - px(10)
            c.create_text(pad, y, text=self.fit(win["label"], self.f_label, room), fill=TEXT, font=self.f_label,
                          anchor="nw")
            c.create_text(w - pad, y + px(1), text=reset_text, fill=DIM, font=self.f_small, anchor="ne")
            y += px(20)
            c.create_image(pad, y, image=bar_image(inner, px(6), (used or 0) / 100, level_color(used)), anchor="nw")
            y += px(11)
            c.create_text(pad, y, text=f"{fmt_pct(used)} Used", fill=DIM, font=self.f_small, anchor="nw")
            y += px(26)

        if p.sessions:
            y = self.draw_sessions(c, p.sessions, pad, y, inner)

        foot = []
        if p.data:
            foot.append(fmt_updated(p.data["updated"], now))
        if p.data and p.data.get("note"):
            foot.append(str(p.data["note"]))
        if p.error and windows:
            foot.append(p.error)
        if foot:
            item = c.create_text(pad, y - px(4), text=" · ".join(foot), fill=DIM, font=self.f_small,
                                 anchor="nw", width=inner)
            y = c.bbox(item)[3] + r
        else:
            y += px(4)

        h = max(y, 2 * r + px(10))
        c.config(width=w, height=h)
        x0, y0, cw, _ = self.column_rect()
        shown = self.shown()
        i = shown.index(p) if p in shown else 0
        horn = self.horn
        top = y0 + self.pad_top + i * self.row_h + px(4)  # the tail's tip sits level with the ring's top
        top = int(min(max(top, horn + px(8)), self.sh - h - px(8)))
        gap = px(8)
        left = x0 - gap - w if self.right() else x0 + cw + gap
        flip = not self.right()
        cap_h = horn + r
        self.card_top.show(left, top - horn, w, cap_h,
                           shape(mirrored(card_cap(w, horn, r), w, cap_h, flip_x=flip), "cardtop", w, horn, r, flip))
        self.card_bottom.show(left, top + h - r, w, r,
                              shape(mirrored(card_base(w, r), w, r, flip_y=True), "cardbase", w, r))
        self.card.geometry(f"{w}x{h - 2 * r}+{left}+{top + r}")
        c.place(x=0, y=-r)
        self.card_rect = (left, top - horn, w, h + horn)

    # --- interaction ---

    def tick(self):
        now = now_ms()
        if self.apply_results() or now - self.last_draw > REDRAW_MS:
            self.draw_column()
            if self.card_key:
                self.draw_card()

        mx, my = self.root.winfo_pointerxy()
        x, y, w, h = self.column_rect()
        in_col = x <= mx < x + w and y - self.ear <= my < y + h + self.ear
        cr = self.card_rect
        in_card = bool(self.card_key and cr) and cr[0] <= mx < cr[0] + cr[2] and cr[1] <= my < cr[1] + cr[3]
        peeking = now < self.peek_until

        if not self.expanded and (in_col or peeking):
            self.expanded = True
            self.draw_column()

        target = None
        if not self.drag:
            row = self.row_at(mx, my)
            if row:
                target = row
            elif in_card:
                target = self.by_key[self.card_key]
            elif peeking:
                target = self.by_key.get(self.peek_key)

        if target:
            self.away_since = None
            if target.key != self.card_key:
                self.show_card(target)
        elif not self.drag:
            if self.away_since is None:
                self.away_since = now
            elif now - self.away_since >= HIDE_DELAY_MS:
                if self.card_key:
                    self.hide_card()
                if self.cfg["autohide"] and self.expanded and not in_col:
                    self.expanded = False
                    self.draw_column()
        self.root.after(TICK_MS, self.tick)

    def on_press(self, e):
        self.drag = {"y": e.y_root, "offset": self.column_rect()[1], "moved": False}

    def on_drag(self, e):
        if not self.drag:
            return
        dy = e.y_root - self.drag["y"]
        if abs(dy) > self.px(4):
            self.drag["moved"] = True
            if self.card_key:
                self.hide_card()
        if self.drag["moved"]:
            self.cfg["offset"] = self.drag["offset"] + dy
            self.place_column()

    def on_release(self, e):
        moved = self.drag and self.drag["moved"]
        self.drag = None
        if moved:
            self.cfg["offset"] = self.column_rect()[1]
            save_config(self.cfg)
        else:
            p = self.row_at(e.x_root, e.y_root)
            if p and p.custom and p.module.MANUAL:
                self.open_dialog(p)  # its numbers are yours to type, so a click opens them
            else:
                self.wake.set()  # a click refreshes

    def show_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def toggle_autohide(self):
        self.cfg["autohide"] = self.autohide_var.get()
        self.expanded = not self.cfg["autohide"]
        save_config(self.cfg)
        self.draw_column()

    def set_provider_setting(self, p, name, var):
        self.cfg["provider_settings"].setdefault(p.key, {})[name] = var.get()
        p.module.settings = dict(self.cfg["provider_settings"][p.key])
        save_config(self.cfg)
        self.wake.set()  # read again now with the new setting

    def toggle_autostart(self):
        try:
            set_autostart(self.autostart_var.get())
        except OSError:
            pass
        self.autostart_var.set(autostart_enabled())  # show what actually took effect

    def toggle_sounds(self):
        self.cfg["sounds"] = self.sounds_var.get()
        save_config(self.cfg)

    def toggle_edge(self):
        self.cfg["edge"] = "left" if self.left_var.get() else "right"
        save_config(self.cfg)
        self.hide_card()
        self.draw_column()


def main():
    if not single_instance():
        return
    win32_dpi_aware()
    root = tk.Tk()
    root.title("Quota Rail")
    providers = demo_providers() if "--demo" in sys.argv else load_providers()
    App(root, providers)
    root.mainloop()


if __name__ == "__main__":
    main()
