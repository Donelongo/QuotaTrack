"""GitHub Copilot: your monthly premium requests, chat and completions quotas.

Live mode (off unless you turn it on in QuotaTrack's menu) uses the GitHub sign-in already on this PC: GH_TOKEN or
GITHUB_TOKEN if set, else the GitHub CLI's (its hosts.yml, or `gh auth token` when the CLI keeps it in the
system's credential store). It's read only and sent only to https://api.github.com/copilot_internal/user, the
quota panel GitHub's own editors show. See _live.py for the rules every live provider follows.

The ring appears once the GitHub CLI is signed in (gh auth login), or a token is set in the environment.
"""

import datetime as dt
import os
import subprocess
import sys
import time

import _live

NAME = "Copilot"
ORDER = 27


def _installed_logo():
    """Copilot's logo as VS Code ships it (its built-in Copilot extension), newest install first, so no copy of it
    lives in this project. It's dark lines on white, so ICON_MONO redraws it as white lines for the black rail."""
    import glob
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code"),
             os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft VS Code"),
             "/Applications/Visual Studio Code.app/Contents/Resources"]
    found = []
    for root in roots:
        for pattern in ("*/resources/app/extensions/copilot/assets/copilot.png",
                        "resources/app/extensions/copilot/assets/copilot.png", "app/extensions/copilot/assets/copilot.png"):
            found += glob.glob(os.path.join(root, pattern))
    return max(found, key=os.path.getmtime) if found else None


ICON = _installed_logo()  # None (VS Code not installed) shows "C"
ICON_MONO = True

SETTINGS = {"live": ("Live usage from GitHub (uses your GitHub CLI sign-in)", False)}
settings = {}
REQUIRES = "live"  # nothing to show without it, so no ring until the switch is on

URL = "https://api.github.com/copilot_internal/user"
LABELS = {"premium_interactions": "Premium requests", "chat": "Chat", "completions": "Completions"}
_throttle = _live.Throttle()


def _config_dirs():
    dirs = []
    if os.environ.get("GH_CONFIG_DIR"):
        dirs.append(os.environ["GH_CONFIG_DIR"])
    if os.environ.get("XDG_CONFIG_HOME"):
        dirs.append(os.path.join(os.environ["XDG_CONFIG_HOME"], "gh"))
    if sys.platform == "win32":
        dirs.append(os.path.join(os.environ.get("APPDATA", ""), "GitHub CLI"))
    dirs.append(os.path.expanduser("~/.config/gh"))
    return dirs


def _hosts():
    for d in _config_dirs():
        path = os.path.join(d, "hosts.yml")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    return None


def available():
    return bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or
                (_hosts() or "").find("github.com:") >= 0)


def _token():
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    in_block = False  # oauth_token under the github.com: block of hosts.yml
    for line in (_hosts() or "").splitlines():
        if line and not line[0].isspace():
            in_block = line.strip().rstrip(":") == "github.com"
        elif in_block and line.strip().startswith("oauth_token:"):
            value = line.split(":", 1)[1].strip().strip("'\"")
            if value:
                return value
    try:  # the CLI keeps the token in the credential store when it can; only it can hand it over
        out = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL,
                             creationflags=0x08000000 if sys.platform == "win32" else 0)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise RuntimeError("GitHub isn't signed in here. Run gh auth login.")


def _reset(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _fetch():
    reply = _live.get_json(URL, {"Authorization": f"Bearer {_token()}", "X-GitHub-Api-Version": "2022-11-28"},
                           "https://api.github.com/copilot_internal/", "GitHub",
                           "GitHub's sign-in has expired or can't see Copilot. Run gh auth login again.")
    reset = _reset(reply.get("quota_reset_date"))
    snapshots = reply.get("quota_snapshots") or {}
    order = list(LABELS) + [k for k in snapshots if k not in LABELS]
    windows = []
    for key in order:
        q = snapshots.get(key)
        if not isinstance(q, dict) or q.get("unlimited"):
            continue
        entitlement = q.get("entitlement")
        if not isinstance(entitlement, (int, float)) or entitlement <= 0:
            continue
        used = q.get("used")
        if not isinstance(used, (int, float)) and isinstance(q.get("remaining"), (int, float)):
            used = entitlement - q["remaining"]
        if isinstance(used, (int, float)):
            windows.append({"label": LABELS.get(key, key.replace("_", " ").capitalize()),
                            "used": used / entitlement * 100, "resets_at": reset})
    if not windows:
        raise RuntimeError("This GitHub account has no metered Copilot quotas.")
    return {"windows": windows, "updated": time.time(), "note": "Live from GitHub"}


def read():
    if not settings.get("live"):
        raise RuntimeError("Turn on Copilot's live usage in QuotaTrack's menu to see it.")
    return _throttle.get(_fetch, "Waiting to ask GitHub again.")
