"""Claude: reads the usage history the Claude desktop app writes about every 15 minutes.

File: plan-usage-history.json in the Claude desktop app's data folder
  Windows  %APPDATA%\\Claude\\
  macOS    ~/Library/Application Support/Claude/
({"fh": five-hour %, "sd": seven-day %} per sample.) No token, no network.
The file has no reset times, so they are worked out from when the figures last fell (limits reset on the hour).

Activity: every running Claude Code session (desktop app or terminal) keeps ~/.claude/sessions/<pid>.json
up to date with "status": busy / waiting (plus what it is waiting for) / idle. Only the .json files are read;
the .key files beside them hold tokens and are never opened. "idle" means the session has finished its turn.
"""

import ctypes
import json
import os
import sys
import time

NAME = "Claude"
ORDER = 10
ICON = "claude"

if sys.platform == "win32":
    _DATA = os.path.join(os.environ.get("APPDATA", ""), "Claude")
elif sys.platform == "darwin":
    _DATA = os.path.expanduser("~/Library/Application Support/Claude")
else:
    _DATA = os.path.expanduser("~/.config/Claude")
HISTORY = os.path.join(_DATA, "plan-usage-history.json")
SESSIONS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "sessions")

# What Claude Code writes in "waitingFor", in plainer words
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
    """The 5-hour window starts at the top of the hour of the first use after the last one ended."""
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
    if i > 0:
        gap = samples[i]["t"] - samples[i - 1]["t"]
        if gap <= HOUR:
            start -= gap / 2
    return start // HOUR * HOUR + FIVE_H


def read():
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
        })
    return sessions
