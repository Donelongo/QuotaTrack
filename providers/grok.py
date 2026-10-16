"""Grok (Grok CLI): your weekly Grok Build credits.

Untested: written from the Grok CLI's documented session file and endpoint, but not yet run against a real
account, since Grok CLI wasn't installed where this was built. If it misreads, the card says what went wrong.

Live mode (off unless you turn it on in Quota Rail's menu) reads the session Grok CLI saved in ~/.grok/auth.json
(only an entry issued by auth.x.ai itself; read only, and the CLI renews it) and sends it only to
https://cli-chat-proxy.grok.com/v1/billing, the figures the CLI's own /usage shows. See _live.py for the rules
every live provider follows.
"""

import datetime as dt
import json
import os
import time

import _live

NAME = "Grok"
ORDER = 45
ICON = None  # shows "G"

SETTINGS = {"live": ("Live usage from grok.com (uses your Grok CLI sign-in)", False)}
settings = {}
REQUIRES = "live"  # nothing to show without it, so no ring until the switch is on

AUTH = os.path.join(os.path.expanduser("~"), ".grok", "auth.json")
URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
_throttle = _live.Throttle()


def available():
    return os.path.isfile(AUTH)


def _token():
    try:
        with open(AUTH, encoding="utf-8") as f:
            sessions = json.load(f)
    except (OSError, ValueError):
        raise RuntimeError("Grok CLI isn't signed in. Run grok and log in.")
    for issuer, entry in (sessions.items() if isinstance(sessions, dict) else []):
        # only xAI's own sign-in; a company's identity provider token is meant for its own proxy
        if not str(issuer).startswith("https://auth.x.ai::") or not isinstance(entry, dict):
            continue
        token, expires = entry.get("key"), entry.get("expires_at")
        if isinstance(expires, (int, float)) and (expires / 1000 if expires > 1e12 else expires) <= time.time():
            raise RuntimeError("Grok CLI's sign-in has expired. Run grok once to renew it.")
        if isinstance(token, str) and token.strip():
            return token.strip()
    raise RuntimeError("Grok CLI isn't signed in with xAI. Run grok and log in.")


def _when(v):
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else v
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fetch():
    reply = _live.get_json(URL, {"Authorization": f"Bearer {_token()}", "X-XAI-Token-Auth": "xai-grok-cli"},
                           "https://cli-chat-proxy.grok.com/v1/", "grok.com",
                           "Grok CLI's sign-in has expired. Run grok once to renew it.")
    config = reply.get("config") or {}
    used = config.get("creditUsagePercent")
    if not isinstance(used, (int, float)):
        raise RuntimeError("grok.com's reply had no credit usage.")
    period = config.get("currentPeriod") or {}
    label = "Weekly credits" if "WEEK" in str(period.get("type", "")).upper() else "Credits"
    return {"windows": [{"label": label, "used": used, "resets_at": _when(period.get("end"))}],
            "updated": time.time(), "note": "Live from grok.com"}


def read():
    if not settings.get("live"):
        raise RuntimeError("Turn on Grok's live usage in Quota Rail's menu to see it.")
    return _throttle.get(_fetch, "Waiting to ask grok.com again.")
