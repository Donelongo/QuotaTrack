"""Claude: reads the usage history the Claude desktop app writes about every 15 minutes.

File: plan-usage-history.json in the Claude desktop app's data folder
  Windows  %APPDATA%\\Claude\\
  macOS    ~/Library/Application Support/Claude/
({"fh": five-hour %, "sd": seven-day %} per sample.) No token, no network.
The file has no reset times, so they are worked out from when the figures last rose from 0 or fell. The app
only adds a sample while it is open and checking, so the file can fall hours behind.

Live mode (off unless you turn it on in QuotaTrack's menu) asks Anthropic for the same figures Claude Code's
/usage shows, with exact reset times. That request is a usage lookup, not a message, so it uses none of your quota.
It reads Claude Code's sign-in from ~/.claude/.credentials.json (accessToken and expiresAt only) and sends it only to
https://api.anthropic.com/api/oauth/. QuotaTrack never refreshes or writes that file: once the sign-in has expired it
runs `claude auth status`, and Claude Code renews its own sign-in. It asks every minute, backs off if rate-limited,
and falls back to the desktop app's file if the request fails. See _live.py for the rules every live provider follows.

Activity: every running Claude Code session (desktop app or terminal) keeps ~/.claude/sessions/<pid>.json
up to date with "status": busy / waiting (plus what it is waiting for) / idle. Only the .json files are read;
the .key files beside them hold tokens and are never opened. "idle" means the session has finished its turn.
"""

import ctypes
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

import _live

NAME = "Claude"
ORDER = 10
ICON = "claude"

SETTINGS = {"live": ("Live usage from anthropic.com (uses your Claude Code sign-in)", False)}
settings = {}

if sys.platform == "win32":
    _DATA = os.path.join(os.environ.get("APPDATA", ""), "Claude")
elif sys.platform == "darwin":
    _DATA = os.path.expanduser("~/Library/Application Support/Claude")
else:
    _DATA = os.path.expanduser("~/.config/Claude")
HISTORY = os.path.join(_DATA, "plan-usage-history.json")
_CODE = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
SESSIONS = os.path.join(_CODE, "sessions")
CREDENTIALS = os.path.join(_CODE, ".credentials.json")
URL = "https://api.anthropic.com/api/oauth/usage"
LIVE_LABELS = {"five_hour": "Current session", "seven_day": "Weekly · all models",
               "seven_day_opus": "Weekly · Opus", "seven_day_sonnet": "Weekly · Sonnet"}
RENEW_GAP = 600  # seconds between asks to Claude Code to renew an expired sign-in
_throttle = _live.Throttle(every=60)
_renewed = [0.0]

# What Claude Code writes in "waitingFor", in plainer words
# The Claude app's id for a chat, as its claude://code/continue link accepts it
APP_SESSION_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")
WAITING_FOR = {
    "dialog open": "asking you something",
    "input needed": "needs your input",
    "sandbox request": "needs sandbox approval",
    "worker request": "needs your approval",
    "goal proposal": "proposed a goal",
}
HOUR = 3600 * 1000
FIVE_H = 5 * HOUR
WEEK = 7 * 24 * HOUR
STALE_MS = 45 * 60 * 1000


def _samples():
    with open(HISTORY, encoding="utf-8") as f:
        data = json.load(f)
    samples = [s for s in data.get("samples", [])
               if isinstance(s, dict) and isinstance(s.get("t"), (int, float)) and isinstance(s.get("u"), dict)]
    samples.sort(key=lambda s: s["t"])
    if not samples:
        return []
    org = samples[-1].get("org")
    return [s for s in samples if s.get("org") == org]


def _on_the_hour(before, after):
    """Claude's limits reset on the hour: the top of an hour between two samples, nearest their middle."""
    middle = (before + after) / 2
    nearest = round(middle / HOUR) * HOUR
    for hour in (nearest, nearest - HOUR, nearest + HOUR):
        if before < hour <= after:
            return hour
    return middle


def _weekly_reset(samples, now):
    """The weekly figure only falls at a reset, so the latest fall plus a week is the next one."""
    for i in range(len(samples) - 1, 0, -1):
        prev, cur = samples[i - 1]["u"].get("sd"), samples[i]["u"].get("sd")
        if prev is not None and cur is not None and cur < prev:
            before, after = samples[i - 1]["t"], samples[i]["t"]
            reset = (_on_the_hour(before, after) if after - before <= HOUR else after) + WEEK
            while reset < now:
                reset += WEEK
            return reset
    return None


def _session_reset(samples):
    """The 5-hour window starts with the first use after the last one ended. Under 0.5% shows as 0, so the
    window has usually begun by the last 0 reading before the figure rose: that reading is taken as the start."""
    if not samples or not samples[-1]["u"].get("fh"):
        return None
    i = len(samples) - 1
    while i > 0:
        prev, cur = samples[i - 1], samples[i]
        pv, cv = prev["u"].get("fh"), cur["u"].get("fh")
        if not pv or pv > cv or cur["t"] - prev["t"] >= FIVE_H:
            break
        i -= 1
    start = samples[i]["t"]
    if i > 0 and start - samples[i - 1]["t"] <= HOUR:
        start = samples[i - 1]["t"]
    return start + FIVE_H


# ---------- live mode ----------

def _signin():
    """(accessToken, expiresAt in ms) from Claude Code's credentials file, or (None, None)."""
    try:
        with open(CREDENTIALS, encoding="utf-8") as f:
            oauth = json.load(f).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None, None
    token, expires = oauth.get("accessToken"), oauth.get("expiresAt")
    return (token if isinstance(token, str) and token else None,
            expires if isinstance(expires, (int, float)) else None)


def _fresh(expires, margin=60):
    return expires is None or expires / 1000 > time.time() + margin


def _renew():
    """Claude Code renews its own sign-in when it starts; `claude auth status` is the lightest way to start it."""
    exe = shutil.which("claude")
    if not exe:
        return
    try:
        subprocess.run([exe, "auth", "status"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)  # CREATE_NO_WINDOW
    except (OSError, subprocess.SubprocessError):
        pass


def _token():
    token, expires = _signin()
    if token and _fresh(expires):
        return token
    if time.time() - _renewed[0] >= RENEW_GAP:
        _renewed[0] = time.time()
        _renew()
        token, expires = _signin()
        if token and _fresh(expires, 0):
            return token
    if not token:
        raise RuntimeError("Claude Code isn't signed in here. Run claude in a terminal and sign in.")
    raise RuntimeError("Claude Code's sign-in has expired. Run claude in a terminal to renew it.")


def _iso_ms(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _fetch_live():
    reply = _live.get_json(URL, {"Authorization": f"Bearer {_token()}", "anthropic-beta": "oauth-2025-04-20"},
                           "https://api.anthropic.com/api/oauth/", "Anthropic",
                           "Claude Code's sign-in was turned down. Run claude in a terminal to sign in again.")
    windows = []
    for key, label in LIVE_LABELS.items():
        w = reply.get(key)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            windows.append({"label": label, "used": w["utilization"], "resets_at": _iso_ms(w.get("resets_at"))})
    if not windows:
        raise RuntimeError("Anthropic sent no usage limits for this account.")
    return {"windows": windows, "updated": time.time(), "note": "Live from Anthropic"}


# ---------- reading ----------

def read():
    live_error = None
    if settings.get("live"):
        try:
            return _throttle.get(_fetch_live, "Waiting to ask Anthropic again.")
        except RuntimeError as e:
            live_error = str(e)
    logged = _read_history()
    if live_error:
        logged["note"] = f"Live usage unavailable: {live_error} Showing the desktop app's last saved figures."
    return logged


def _read_history():
    try:
        samples = _samples()
    except FileNotFoundError:
        raise RuntimeError("Claude desktop app not found. Open it and sign in.")
    if not samples:
        raise RuntimeError("No usage recorded yet")
    now = time.time() * 1000
    last = samples[-1]
    fh, sd = last["u"].get("fh"), last["u"].get("sd")
    s_reset, w_reset = _session_reset(samples), _weekly_reset(samples, now)
    if s_reset is not None and s_reset <= now:
        fh, s_reset = 0, None
    if w_reset is not None and last["t"] < w_reset - WEEK <= now:
        sd = 0
    stale = now - last["t"] > STALE_MS
    return {
        "windows": [
            {"label": "Current session", "used": fh, "resets_at": s_reset, "estimated": True},
            {"label": "Weekly · all models", "used": sd, "resets_at": w_reset, "estimated": True},
        ],
        "updated": last["t"],
        "note": "Claude desktop isn't running, so these may be out of date" if stale else None,
    }


def _alive(pid):
    """A session file can outlive a crash, so only count it while its process is still running."""
    if sys.platform == "win32":
        k = ctypes.WinDLL("kernel32")
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_uint32()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def open_app(session):
    """A click on a session: the Claude app opens that very chat; a terminal session brings its terminal up."""
    import _focus
    chat = str(session.get("chat") or "")
    if APP_SESSION_ID.match(chat) and _focus.open_link(f"claude://code/continue?session={chat}&source=quotatrack"):
        return True
    return bool(session.get("pid")) and _focus.focus_pid(session["pid"])


def activity():
    try:
        names = os.listdir(SESSIONS)
    except OSError:
        return []
    sessions = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS, fn), encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        pid = s.get("pid")
        if not isinstance(pid, int) or not _alive(pid):
            continue
        status = s.get("status")
        state = "working" if status == "busy" else "waiting" if status == "waiting" else "idle"
        sessions.append({
            "name": s.get("name") or os.path.basename(str(s.get("cwd", "")).rstrip("\\/")) or "Claude Code",
            "state": state,
            "detail": WAITING_FOR.get(s.get("waitingFor"), s.get("waitingFor")) if state == "waiting" else None,
            "where": "Claude app" if s.get("entrypoint") == "claude-desktop" else "Terminal",
            "pid": pid,  # a click brings forward the window above it: the Claude app, or the terminal
            "since": s.get("statusUpdatedAt"),
            "chat": s.get("hostSessionId") if s.get("entrypoint") == "claude-desktop" else None,
        })
    return sessions
