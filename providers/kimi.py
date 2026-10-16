"""Kimi Code: the 5-hour and weekly limits of your Kimi Code plan.

Kimi Code keeps no usage figures on your PC, so this needs live mode (off unless you turn it on from Quota Rail's
right-click menu). It then reads the login the Kimi Code CLI saved in ~/.kimi-code/credentials/kimi-code.json
(access_token only; it never refreshes or writes the file) and sends it to https://api.kimi.com and nowhere
else: redirects are refused, so the token can't be forwarded. It asks at most every 5 minutes.

The ring only appears once you've signed in to Kimi Code (run `kimi` and log in). Kimi's access tokens last
15 minutes and the CLI renews them when it runs, so if Kimi Code hasn't run lately the card asks you to.
"""

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

NAME = "Kimi"
ORDER = 30
ICON = None  # shows "K"

SETTINGS = {"live": ("Live usage from api.kimi.com (uses your Kimi Code login)", False)}
settings = {}

HOME = os.environ.get("KIMI_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".kimi-code")
CREDENTIALS = os.path.join(HOME, "credentials", "kimi-code.json")
LIVE_URL = "https://api.kimi.com/coding/v1/usages"
LIVE_EVERY = 300
LIVE_MIN_GAP = 60
LIVE_BACKOFF_CAP = 900


def available():
    """Only show a ring once Kimi Code is signed in on this PC."""
    return os.path.isfile(CREDENTIALS)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would copy the Authorization header onto a redirect, so a redirect is treated as a failure."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)
_live = {"last_try": 0.0, "not_before": 0.0, "backoff": 0, "result": None}


def _token():
    try:
        with open(CREDENTIALS, encoding="utf-8") as f:
            creds = json.load(f)
    except (OSError, ValueError):
        raise RuntimeError("Kimi Code isn't signed in. Run kimi and log in.")
    token, expires = creds.get("access_token"), creds.get("expires_at")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Kimi Code isn't signed in. Run kimi and log in.")
    if isinstance(expires, (int, float)) and expires <= time.time():
        raise RuntimeError("Kimi Code's sign-in has expired. Run kimi once to renew it.")
    return token.strip()


def _fetch():
    if not LIVE_URL.startswith("https://api.kimi.com/"):
        raise RuntimeError("The live address must be on https://api.kimi.com, so your login isn't sent there.")
    request = urllib.request.Request(LIVE_URL, headers={
        "Authorization": f"Bearer {_token()}", "Accept": "application/json", "User-Agent": "quota-rail"})
    try:
        with _opener.open(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Kimi Code's sign-in has expired. Run kimi once to renew it.")
        if e.code == 429:
            _live["backoff"] += 1
            _live["not_before"] = time.time() + min(60 * 2 ** (_live["backoff"] - 1), LIVE_BACKOFF_CAP)
            raise RuntimeError("api.kimi.com is rate-limiting usage checks; trying again later.")
        raise RuntimeError(f"api.kimi.com answered {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Couldn't reach api.kimi.com.")
    except ValueError:
        raise RuntimeError("api.kimi.com sent a reply that isn't usage data.")


def _count(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reset(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _row(label, detail):
    """Counts arrive as decimal strings. Without "used", a limit and "remaining" still pin it down."""
    limit, used, remaining = _count(detail.get("limit")), _count(detail.get("used")), _count(detail.get("remaining"))
    if used is None and limit and remaining is not None:
        used = limit - remaining
    if used is None or not limit:
        return None
    return {"label": label, "used": used / limit * 100, "resets_at": _reset(detail.get("resetTime"))}


def _window_label(window):
    """Only the windows Kimi meters today get a name: the 5-hour one and the weekly one."""
    if not isinstance(window, dict):
        return None
    duration, unit = _count(window.get("duration")), window.get("timeUnit")
    if (unit == "TIME_UNIT_MINUTE" and duration == 300) or (unit == "TIME_UNIT_HOUR" and duration == 5):
        return "Current session"
    if unit == "TIME_UNIT_WEEK" and duration == 1:
        return "Weekly"
    return None


def _windows(reply):
    windows = []
    for entry in reply.get("limits") or []:
        label = _window_label(entry.get("window")) if isinstance(entry, dict) else None
        row = label and isinstance(entry.get("detail"), dict) and _row(label, entry["detail"])
        if row:
            windows.append(row)
    summary = reply.get("usage")  # the account summary is the weekly window
    if isinstance(summary, dict) and not any(w["label"] == "Weekly" for w in windows):
        row = _row("Weekly", summary)
        if row:
            windows.append(row)
    if not windows:
        raise RuntimeError("This Kimi account has no Kimi Code usage limits.")
    return windows


def read():
    if not settings.get("live"):
        raise RuntimeError("Kimi keeps no usage on your PC. Turn on \"Kimi: Live usage\" in the right-click menu.")
    now = time.time()
    cached = _live["result"]
    if now < _live["not_before"] or now - _live["last_try"] < (LIVE_EVERY if cached else LIVE_MIN_GAP):
        if cached:
            return cached
        raise RuntimeError("Waiting to ask api.kimi.com again.")
    _live["last_try"] = now
    _live["result"] = {"windows": _windows(_fetch()), "updated": now, "note": "Live from api.kimi.com"}
    _live["backoff"] = 0
    return _live["result"]
