"""Template for adding another AI to Notch.

1. Copy this file to providers/<name>.py (no leading underscore, or it is skipped).
2. Fill in read() so it returns how much of each limit you have used.
3. Restart Notch.

read() runs every 30 seconds on a background thread, so it may read files or call an API.
If it can't get the figures, raise an exception with a short message; the card shows it.
A provider file is ordinary Python run on your PC, so only add ones you wrote or have read.
"""

NAME = "My AI"   # shown in the hover card title
ORDER = 50       # position in the column; lower is higher up (Claude is 10, Codex 20)
ICON = None      # None shows the first letter of NAME; or a PNG file in this folder, e.g. "myai.png"


def read():
    return {
        "windows": [
            # used: percent of the limit used, 0-100
            # resets_at: when it resets, as epoch seconds or milliseconds (None if unknown)
            # estimated: True shows the reset time with a "~"
            {"label": "Current session", "used": 42, "resets_at": None},
            {"label": "Weekly", "used": 10, "resets_at": None},
        ],
        "updated": None,  # when these figures were true (epoch s/ms); None means now
        "note": None,     # optional one-line note at the bottom of the card
    }


# Optional: delete this function if your AI has no way to tell whether it is busy.
# It runs every 2 seconds, so keep it quick (read a small file, don't call a slow API).
def activity():
    return [
        # state: "working" spins an arc in the ring, "waiting" pulses it amber, "idle" just lists it
        # {"name": "my-project", "state": "working", "detail": None},
        # {"name": "other-project", "state": "waiting", "detail": "needs your approval"},
    ]
