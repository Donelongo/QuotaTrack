"""Codex (ChatGPT plan): reads the rate limits Codex itself logs after every turn.

Files: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, used by both the Codex app and the CLI.
Each turn records {"rate_limits": {"primary": {...}, "secondary": {...}}} with exact percentages and reset
times. No token, no network: the figures are as of your last Codex message, so use from elsewhere since then
(ChatGPT, Codex cloud tasks) isn't counted until your next one.

Which log is newest is decided by the timestamps inside the logs, not by the files' modified times: on Windows
a log Codex still has open keeps an old modified time, and resumed threads append to logs from earlier days.

Live mode (off unless you turn it on from QuotaTrack's right-click menu): asks chatgpt.com for your usage right
now, the same figures the Codex app's "Usage remaining" menu shows. It reads your Codex login from
~/.codex/auth.json (access_token and account_id only; it never refreshes or writes the file) and sends it to
https://chatgpt.com and nowhere else: redirects are refused, so the token can't be forwarded. It asks at most
every 5 minutes, backs off if rate-limited, and falls back to the logs if the request fails.

Activity: a session is working between its "task_started" and "task_complete" events, and waiting on you
after an approval or input request. Sessions with nothing logged in the last 20 minutes aren't listed.
"""

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAME = "Codex"
ORDER = 20
ICON = "codex"

# Shown as switches in QuotaTrack's right-click menu; QuotaTrack puts the chosen values in `settings` before each read.
SETTINGS = {"live": ("Live usage from chatgpt.com (uses your Codex login)", False)}
settings = {}

LIVE_URL = "https://chatgpt.com/backend-api/wham/usage"
LIVE_EVERY = 300        # seconds between live requests
LIVE_MIN_GAP = 60       # even a refresh click waits this long, so clicks can't trip the rate limit
LIVE_BACKOFF_CAP = 900

HOME = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
SESSIONS = os.path.join(HOME, "sessions")
TAIL_BYTES = 256 * 1024
CANDIDATES = 12   # logs most recently opened (by modified time, which is when Codex opened them)
ACTIVE_SECS = 20 * 60

WORKING = {"task_started"}
DONE = {"task_complete", "turn_aborted"}
WAITING = {"exec_approval_request", "apply_patch_approval_request", "request_user_input", "elicitation_request"}


def _candidate_logs():
    files = []
    for root, _, names in os.walk(SESSIONS):
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(root, name)
                try:
                    files.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    files.sort(reverse=True)
    return [p for _, p in files[:CANDIDATES]]


def _tail(path, size=TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - size))
        return f.read().splitlines()


def _stamp(entry):
    try:
        return dt.datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _entries(path):
    """The log's last lines, newest first, as parsed JSON."""
    for line in reversed(_tail(path)):
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _find(obj, key):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), dict):
            return obj[key]
        for v in obj.values():
            found = _find(v, key)
            if found:
                return found
    return None


def _label(minutes):
    minutes = round(minutes) if isinstance(minutes, (int, float)) else None
    if minutes == 300:
        return "Current session"
    if minutes == 10080:
        return "Weekly"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}-day window"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour window"
    return "Usage window"


# ---------- live mode ----------

class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would copy the Authorization header onto a redirect, so a redirect is treated as a failure."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)
_live = {"last_try": 0.0, "not_before": 0.0, "backoff": 0, "result": None}


def _credential():
    try:
        with open(os.path.join(HOME, "auth.json"), encoding="utf-8") as f:
            tokens = json.load(f).get("tokens") or {}
    except (OSError, ValueError):
        raise RuntimeError("Codex sign-in not found. Open Codex and sign in with ChatGPT.")
    token, account = tokens.get("access_token"), tokens.get("account_id")
    if not isinstance(token, str) or not isinstance(account, str) or not token.strip() or not account.strip():
        raise RuntimeError("Codex isn't signed in with ChatGPT.")
    return token.strip(), account.strip()


def _fetch_live():
    if not LIVE_URL.startswith("https://chatgpt.com/"):
        raise RuntimeError("The live address must be on https://chatgpt.com, so your login isn't sent there.")
    token, account = _credential()
    request = urllib.request.Request(LIVE_URL, headers={
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-Id": account,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "quotatrack",
    })
    try:
        with _opener.open(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Codex sign-in expired. Open Codex once to refresh it.")
        if e.code == 429:
            retry = e.headers.get("Retry-After", "")
            _live["backoff"] += 1
            wait = min(60 * 2 ** (_live["backoff"] - 1), LIVE_BACKOFF_CAP)
            _live["not_before"] = time.time() + max(wait, int(retry) if retry.isdigit() else 0)
            raise RuntimeError("chatgpt.com is rate-limiting usage checks; trying again later.")
        if 300 <= e.code < 400:
            raise RuntimeError("chatgpt.com answered with a redirect, which isn't followed.")
        raise RuntimeError(f"chatgpt.com answered {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Couldn't reach chatgpt.com.")
    except ValueError:
        raise RuntimeError("chatgpt.com sent a reply that isn't usage data.")


def _live_windows(reply):
    now = time.time()
    windows = []
    limits = reply.get("rate_limit") if isinstance(reply, dict) else None
    for key in ("primary_window", "secondary_window"):
        w = (limits or {}).get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        seconds = w.get("limit_window_seconds")
        reset = w.get("reset_at")
        if reset is None and isinstance(w.get("reset_after_seconds"), (int, float)):
            reset = now + w["reset_after_seconds"]
        windows.append({"label": _label(seconds / 60 if isinstance(seconds, (int, float)) else None),
                        "used": w["used_percent"], "resets_at": reset})
    if not windows:
        raise RuntimeError("chatgpt.com's reply had no usage windows.")
    return windows


def _read_live():
    """The live reading, asked for at most every LIVE_EVERY seconds; in between, the last one is reused."""
    now = time.time()
    cached = _live["result"]
    due = now - _live["last_try"] >= (LIVE_EVERY if cached else LIVE_MIN_GAP)
    if not due or now < _live["not_before"]:
        if cached:
            return cached
        raise RuntimeError("Waiting to ask chatgpt.com again.")
    _live["last_try"] = now
    windows = _live_windows(_fetch_live())
    _live["backoff"] = 0
    _live["result"] = {"windows": windows, "updated": now, "note": "Live from chatgpt.com"}
    return _live["result"]


# ---------- reading ----------

def read():
    live_error = None
    if settings.get("live"):
        try:
            return _read_live()
        except RuntimeError as e:
            live_error = str(e)
    logged = _read_logs()
    if live_error:
        logged["note"] = f"Live usage unavailable: {live_error} Showing your last logged figures."
    return logged


def _read_logs():
    if not os.path.isdir(SESSIONS):
        raise RuntimeError("Codex not found. Use the Codex app or CLI once.")
    newest = None  # (timestamp, rate_limits)
    for path in _candidate_logs():
        for entry in _entries(path):
            limits = _find(entry, "rate_limits")
            if limits and (limits.get("primary") or limits.get("secondary")):
                stamp = _stamp(entry)
                if stamp and (newest is None or stamp > newest[0]):
                    newest = (stamp, limits)
                break
    if not newest:
        raise RuntimeError("No Codex usage logged yet. Send Codex a message.")
    stamp, limits = newest
    windows = []
    for key in ("primary", "secondary"):
        w = limits.get(key)
        if isinstance(w, dict) and w.get("used_percent") is not None:
            windows.append({"label": _label(w.get("window_minutes")), "used": w["used_percent"],
                            "resets_at": w.get("resets_at")})
    return {"windows": windows, "updated": stamp, "note": "As of your last Codex message"}


def _session_meta(path):
    """The folder Codex is working in and which Codex it is ("Codex Desktop", the editor extension or the CLI),
    from the session_meta line that opens every log."""
    try:
        with open(path, "rb") as f:
            meta = json.loads(f.readline()).get("payload") or {}
    except (OSError, ValueError):
        return "Codex", None
    cwd = meta.get("cwd")
    name = (os.path.basename(cwd.rstrip("\\/")) or cwd) if cwd else "Codex"
    return name, meta.get("originator")


# The Codex app from the Microsoft Store: starting it again brings the open one forward
WIN_APP = r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"


def open_app(session):
    """A click on a Codex session: bring forward the Codex it's running in."""
    import _focus
    origin = str(session.get("app") or "").lower()
    if "desktop" in origin:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", WIN_APP])
            return True
        return _focus.focus_exe(("Codex",))
    if "vscode" in origin:
        return _focus.focus_exe(("Code.exe", "Code"))
    return _focus.focus_exe(("codex.exe", "codex"))


def activity():
    if not os.path.isdir(SESSIONS):
        return []
    now = time.time()
    sessions = []
    for path in _candidate_logs():
        last_seen, state = None, None
        for entry in _entries(path):
            if last_seen is None:
                last_seen = _stamp(entry)
                if not last_seen or now - last_seen > ACTIVE_SECS:
                    break
            if entry.get("type") != "event_msg":
                continue
            kind = (entry.get("payload") or {}).get("type")
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
            name, origin = _session_meta(path)
            sessions.append({"name": name, "state": state, "app": origin, "since": last_seen,
                             "detail": "needs your approval" if state == "waiting" else None})
    return sessions
