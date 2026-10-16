"""Antigravity: your Gemini model quotas, asked of Antigravity itself while it's open.

Nothing leaves your PC. While Antigravity runs, its built-in language server listens on 127.0.0.1 and answers
RetrieveUserQuotaSummary, the same call its own quota panel makes. Notch finds that process (its command line
carries the --csrf_token the server expects), finds its local port, and asks there; the certificate check is
relaxed for 127.0.0.1 only, because that server signs its own. No Google login is read or used.

Antigravity reports what *remains*; Notch shows what's used. When Antigravity is closed, the last reading is
kept (in antigravity.cache.json next to this file) and shown with its time.
"""

import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

NAME = "Antigravity"
ORDER = 40
ICON = None  # shows "A"

STATE_DIRS = [os.path.join(os.path.expanduser("~"), ".gemini", d) for d in ("antigravity", "antigravity-ide")]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "antigravity.cache.json")
SERVICE = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
EVERY = 120  # seconds between asks; each one makes Antigravity refresh its quota, so don't hammer it

_state = {"last_try": 0.0, "endpoint": None, "result": None}


def available():
    """Show a ring once Antigravity has been used on this PC."""
    return any(os.path.isdir(d) for d in STATE_DIRS) or os.path.isfile(CACHE)


def _run(args):
    """Run a small system command without flashing a console window."""
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10, creationflags=flags,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _flag(cmdline, flag):
    parts = cmdline.split()
    if flag in parts and parts.index(flag) + 1 < len(parts):
        return parts[parts.index(flag) + 1].strip('"')
    return None


def _discover():
    """(csrf token, [ports]) of Antigravity's running language server, or None if it isn't running."""
    if sys.platform == "win32":
        if "language_server" not in _run(["tasklist", "/FI", "IMAGENAME eq language_server*", "/NH"]).lower():
            return None
        table = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"Name LIKE '%language_server%'\" | "
                      "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"])
        rows = [l.split("\t", 1) for l in table.splitlines() if "\t" in l]
    else:
        rows = [l.strip().split(None, 1) for l in _run(["ps", "-Ao", "pid,command"]).splitlines()]
    for row in rows:
        if len(row) != 2:
            continue
        pid, cmdline = row[0].strip(), row[1]
        # Windsurf and other Codeium-based editors run a language server too; only Antigravity's will do
        if "language_server" not in cmdline or "antigravity" not in cmdline.lower():
            continue
        csrf = _flag(cmdline, "--csrf_token")
        ports = _ports(pid) if csrf and pid.isdigit() else []
        if ports:
            return csrf, ports
    return None


def _ports(pid):
    ports = set()
    if sys.platform == "win32":
        for line in _run(["netstat", "-ano", "-p", "TCP"]).splitlines():
            cols = line.split()
            if len(cols) >= 5 and cols[3] == "LISTENING" and cols[4] == pid and cols[1].startswith("127.0.0.1:"):
                ports.add(int(cols[1].rsplit(":", 1)[1]))
    else:
        for line in _run(["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN"]).splitlines():
            addr = next((w for w in reversed(line.split()) if ":" in w), "")
            port = addr.rsplit(":", 1)[-1]
            if port.isdigit():
                ports.add(int(port))
    return sorted(ports)


def _ask(csrf, ports):
    loopback_only = ssl.create_default_context()
    loopback_only.check_hostname = False
    loopback_only.verify_mode = ssl.CERT_NONE  # Antigravity's own self-signed certificate, on 127.0.0.1 only
    for port in ports:  # it opens two ports and only one answers this
        request = urllib.request.Request(
            f"https://127.0.0.1:{port}{SERVICE}", data=b'{"forceRefresh":true}', method="POST",
            headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf})
        try:
            with urllib.request.urlopen(request, timeout=8, context=loopback_only) as response:
                windows = _windows(json.load(response))
            if windows:
                return windows
        except (OSError, ValueError):
            continue
    return None


def _reset(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _label(group, bucket_id):
    lane = str(bucket_id).lower()
    if "weekly" in lane:
        kind = "Weekly"
    elif any(k in lane for k in ("5h", "five hour", "hourly")):
        kind = "5-hour"
    else:
        kind = None
    if group and kind:
        return f"{group} · {kind}"
    return group or kind or "Quota"


def _windows(reply):
    windows = []
    groups = ((reply or {}).get("response") or {}).get("groups") or []
    for group in groups:
        name = group.get("displayName")
        for bucket in group.get("buckets") or []:
            remaining = bucket.get("remainingFraction")
            if not isinstance(remaining, (int, float)) or not 0 <= remaining <= 1:
                continue
            bucket_id = bucket.get("bucketId") or bucket.get("displayName") or ""
            windows.append({"label": _label(name, bucket_id), "used": (1 - remaining) * 100,
                            "resets_at": _reset(bucket.get("resetTime")),
                            "_order": 0 if "5-hour" in _label(None, bucket_id) else 1})
    windows.sort(key=lambda w: w.pop("_order"))  # 5-hour before weekly, as you'd read them
    return windows


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read():
    now = time.time()
    if now - _state["last_try"] >= EVERY or not _state["result"]:
        _state["last_try"] = now
        # Finding the process is slow (Windows' process query takes seconds), so the last address found is tried
        # first and the search only runs again when it stops answering, e.g. after Antigravity restarts.
        found = _state["endpoint"]
        windows = _ask(*found) if found else None
        if not windows:
            found = _state["endpoint"] = _discover()
            windows = _ask(*found) if found else None
        if windows:
            _state["result"] = {"windows": windows, "updated": now, "note": "From Antigravity on this PC"}
            try:
                with open(CACHE, "w", encoding="utf-8") as f:
                    json.dump(_state["result"], f)
            except OSError:
                pass
        else:
            cached = _state["result"] or _load_cache()
            if not cached:
                raise RuntimeError("Open Antigravity once so Notch can read its quota.")
            note = "Antigravity is closed, so this is its last reading." if not found else \
                "Antigravity didn't answer, so this is its last reading."
            _state["result"] = dict(cached, note=note)
    return _state["result"]
