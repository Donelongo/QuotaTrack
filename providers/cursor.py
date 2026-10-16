"""Cursor: how much of your plan's included usage you've used this billing cycle.

Live mode (off unless you turn it on in Quota Rail's menu) reads the sign-in the Cursor editor keeps in its own
settings database (state.vscdb: cursorAuth/accessToken, read only) and sends it only to
https://cursor.com/api/usage-summary, the figures behind Cursor's own "Included usage · N% used". See _live.py
for the rules every live provider follows.

The ring appears once Cursor has been installed and signed in on this PC.
"""

import base64
import datetime as dt
import json
import os
import sqlite3
import sys
import time

import _live

NAME = "Cursor"
ORDER = 25

SETTINGS = {"live": ("Live usage from cursor.com (uses your Cursor sign-in)", False)}
settings = {}

if sys.platform == "win32":
    DB = os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User", "globalStorage", "state.vscdb")
    _LOGO = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "cursor", "resources", "app",
                         "resources", "win32", "code_150x150.png")
else:
    DB = os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage/state.vscdb")
    _LOGO = ""
ICON = _LOGO if os.path.isfile(_LOGO) else None  # Cursor's own logo, from its install
ICON_DROP_DARK = True  # its tile is a grey cube on a dark square: keep the cube

URL = "https://cursor.com/api/usage-summary"
_throttle = _live.Throttle()


def available():
    return os.path.isfile(DB)


def _item(con, key):
    row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
    return row[0].decode() if row and isinstance(row[0], bytes) else (row[0] if row else None)


def _cookie():
    """WorkosCursorSessionToken=<user id>::<access token>, the session cookie Cursor's dashboard uses."""
    con = None
    for mode in ("mode=ro", "immutable=1"):  # immutable once Cursor has exited and its -shm file is gone
        try:
            con = sqlite3.connect(f"file:{DB}?{mode}", uri=True)
            token = _item(con, "cursorAuth/accessToken")
            user = _item(con, "cursorAuth/stripeMembershipAuthId")
            break
        except sqlite3.Error:
            token = user = None
        finally:
            if con:
                con.close()
    if not token:
        raise RuntimeError("Cursor isn't signed in. Open Cursor and sign in.")
    if not user:  # older installs don't store it separately; it's the token's subject
        try:
            part = token.split(".")[1]
            sub = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("sub", "")
            user = sub.split("|")[-1]
        except (IndexError, ValueError):
            user = None
    if not user:
        raise RuntimeError("Cursor's sign-in is incomplete. Open Cursor and sign in again.")
    return f"WorkosCursorSessionToken={user}::{token}"


def _reset(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fetch():
    reply = _live.get_json(URL, {"Cookie": _cookie()}, "https://cursor.com/api/", "cursor.com",
                           "Cursor's sign-in has expired. Open Cursor to renew it.")
    reset = _reset(reply.get("billingCycleEnd"))
    usage = reply.get("individualUsage") or {}
    plan = usage.get("plan") or {}
    windows = []
    if isinstance(plan.get("totalPercentUsed"), (int, float)):
        windows.append({"label": "Included usage", "used": plan["totalPercentUsed"], "resets_at": reset})
    if isinstance(plan.get("apiPercentUsed"), (int, float)) and plan["apiPercentUsed"] > 0:
        windows.append({"label": "API usage", "used": plan["apiPercentUsed"], "resets_at": reset})
    on_demand = usage.get("onDemand") or {}
    if on_demand.get("enabled") and isinstance(on_demand.get("limit"), (int, float)) and on_demand["limit"] > 0:
        windows.append({"label": "On-demand", "used": (on_demand.get("used") or 0) / on_demand["limit"] * 100,
                        "resets_at": reset})
    if not windows:
        plan_name = reply.get("membershipType") or "your"
        if reply.get("isUnlimited"):
            raise RuntimeError(f"Unlimited on the {plan_name} plan, so there's nothing to measure.")
        raise RuntimeError("Cursor reported no usage limits for this account.")
    return {"windows": windows, "updated": time.time(), "note": "Live from cursor.com"}


def read():
    if not settings.get("live"):
        raise RuntimeError("Turn on Cursor's live usage in Quota Rail's menu to see it.")
    return _throttle.get(_fetch, "Waiting to ask cursor.com again.")
