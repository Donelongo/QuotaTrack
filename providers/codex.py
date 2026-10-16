"""Codex (ChatGPT plan): reads the rate limits Codex itself logs after every turn.

Files: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, used by both the Codex app and the CLI.
Each turn records {"rate_limits": {"primary": {...}, "secondary": {...}}} with exact percentages and reset
times. No token, no network: the figures are as of your last Codex turn.

Activity: a session is working between its "task_started" and "task_complete" events, and waiting on you
after an approval or input request. Only sessions whose log changed in the last 20 minutes are listed.
"""

import datetime as dt
import json
import os
import time

NAME = "Codex"
ORDER = 20
ICON = "codex"

HOME = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
SESSIONS = os.path.join(HOME, "sessions")
TAIL_BYTES = 512 * 1024
DAY_FOLDERS = 14  # newest day folders to look through
FILES = 6         # newest session files to look through
ACTIVE_SECS = 20 * 60

WORKING = {"task_started"}
DONE = {"task_complete", "turn_aborted"}
WAITING = {"exec_approval_request", "apply_patch_approval_request", "request_user_input", "elicitation_request"}


def _newest_logs(day_folders=DAY_FOLDERS):
    days = []
    for root, dirs, _ in os.walk(SESSIONS):
        depth = os.path.relpath(root, SESSIONS).count(os.sep)
        if depth == 2:  # YYYY/MM/DD
            days.append(root)
            dirs[:] = []
    days.sort(reverse=True)
    files = []
    for day in days[:day_folders]:
        for name in os.listdir(day):
            if name.endswith(".jsonl"):
                path = os.path.join(day, name)
                files.append((os.path.getmtime(path), path))
    files.sort(reverse=True)
    return [p for _, p in files[:FILES]]


def _find(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), dict):
            return obj[key]
        for v in obj.values():
            found = _find(v, key)
            if found:
                return found
    return None


def _tail(path, size=TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - size))
        return f.read().splitlines()


def _last_rate_limits(path):
    for line in reversed(_tail(path)):
        if b'"rate_limits"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        limits = _find(entry, "rate_limits")
        if limits and (limits.get("primary") or limits.get("secondary")):
            return limits, entry.get("timestamp")
    return None, None


def _label(minutes):
    if minutes == 300:
        return "Current session"
    if minutes == 10080:
        return "Weekly"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}-day window"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour window"
    return "Usage window"


def read():
    if not os.path.isdir(SESSIONS):
        raise RuntimeError("Codex not found. Use the Codex app or CLI once.")
    for path in _newest_logs():
        limits, stamp = _last_rate_limits(path)
        if not limits:
            continue
        windows = []
        for key in ("primary", "secondary"):
            w = limits.get(key)
            if isinstance(w, dict) and w.get("used_percent") is not None:
                windows.append({
                    "label": _label(w.get("window_minutes")),
                    "used": w["used_percent"],
                    "resets_at": w.get("resets_at"),
                })
        updated = None
        if stamp:
            try:
                updated = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return {"windows": windows, "updated": updated or os.path.getmtime(path),
                "note": "As of your last Codex message"}
    raise RuntimeError("No Codex usage logged yet. Send Codex a message.")


def _session_name(path):
    """The folder Codex is working in, from the session_meta line that opens every log."""
    try:
        with open(path, "rb") as f:
            meta = json.loads(f.readline())
        cwd = (meta.get("payload") or {}).get("cwd")
        if cwd:
            return os.path.basename(cwd.rstrip("\\/")) or cwd
    except (OSError, ValueError):
        pass
    return "Codex"


def activity():
    if not os.path.isdir(SESSIONS):
        return []
    now = time.time()
    sessions = []
    for path in _newest_logs(day_folders=3):
        if now - os.path.getmtime(path) > ACTIVE_SECS:
            continue
        state = None
        for line in reversed(_tail(path, 256 * 1024)):
            if b'"event_msg"' not in line:
                continue
            try:
                kind = (json.loads(line).get("payload") or {}).get("type")
            except ValueError:
                continue
            if kind in WORKING:
                state = "working"
            elif kind in DONE:
                state = "idle"
            elif kind in WAITING:
                state = "waiting"
            else:
                continue
            break
        if state:
            sessions.append({"name": _session_name(path), "state": state,
                             "detail": "needs your approval" if state == "waiting" else None})
    return sessions
