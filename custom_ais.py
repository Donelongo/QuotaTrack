"""AIs you add from Quota Rail's right-click menu ("Add an AI…"), with no code.

Each one is saved in custom_ais.json next to quotarail.pyw (kept out of git, since it can hold an API key) and gets
its numbers one of three ways:
  manual  you type in how much you've used and when it resets; click its ring to update them
  web     Quota Rail fetches a web address that returns JSON (with an optional header, such as an API key)
  file    Quota Rail reads a JSON file on this PC that another tool keeps up to date
For web and file, you name the fields holding the numbers as paths into the JSON, like data.usage.percent or
limits[0].used, optionally with the field it's "out of" and the field holding the reset time.
"""

import datetime as dt
import json
import os
import re
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import filedialog, ttk

WEB_EVERY = 300   # seconds between fetches of a web address
WEB_MIN_GAP = 30
ROWS = 2          # limit windows per AI in the form


# ---------- storage ----------

def load_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f).get("ais", [])
        return [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("name")]
    except (OSError, ValueError, AttributeError):
        return []


def save_entries(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ais": entries}, f, indent=2)


# ---------- parsing ----------

_RELATIVE = re.compile(r"^(?:in\s+)?(?:(\d+)\s*d\w*)?\s*(?:(\d+)\s*h\w*)?\s*(?:(\d+)\s*m\w*)?$", re.I)


def parse_reset(text, now=None):
    """'3h', '2d 4h', 'in 45m', '18:30' (the next one), '2026-10-22' or '2026-10-22 14:00' -> epoch seconds."""
    text = (text or "").strip()
    if not text:
        return None
    now = now or time.time()
    m = _RELATIVE.match(text)
    if m and any(m.groups()):
        d, h, mins = (int(g or 0) for g in m.groups())
        return now + d * 86400 + h * 3600 + mins * 60
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    try:
        t = dt.datetime.strptime(text, "%H:%M").time()
        when = dt.datetime.combine(dt.date.fromtimestamp(now), t)
        if when.timestamp() <= now:
            when += dt.timedelta(days=1)
        return when.timestamp()
    except ValueError:
        raise ValueError(f'Quota Rail can\'t read the reset time "{text}". Try 3h, 2d 4h, 18:30 or 2026-10-22 14:00.')


def format_reset(epoch):
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M") if epoch else ""


def number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def resolve(data, path):
    """'data.limits[0].used' (or data.limits.0.used) -> the value there."""
    value = data
    for part in re.findall(r"[^.\[\]]+", path or ""):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise KeyError(path)
    return value


def reset_value(v):
    """A reset time from JSON: epoch seconds or milliseconds, or an ISO date."""
    n = number(v)
    if n is not None and n > 1e8:
        return n / 1000 if n > 1e12 else n
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def describe(data):
    """A short hint at what a JSON reply contains, to help pick the field paths."""
    if isinstance(data, dict):
        return "The reply has: " + ", ".join(list(data)[:12])
    if isinstance(data, list):
        return f"The reply is a list of {len(data)} items; start paths with [0]."
    return "The reply isn't a JSON object."


# ---------- the provider ----------

class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry your header (maybe an API key) to wherever it points, so it's refused."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def fetch(entry):
    if entry.get("source") == "file":
        try:
            with open(os.path.expanduser(entry.get("file", "")), encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            raise RuntimeError("Couldn't open the file.")
        except ValueError:
            raise RuntimeError("The file isn't JSON.")
    url = (entry.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise RuntimeError("The address must start with https:// (or http://).")
    headers = {"Accept": "application/json", "User-Agent": "quota-rail"}
    header = (entry.get("header") or "").strip()
    if header:
        if ":" not in header:
            raise RuntimeError('The header must look like "Name: value".')
        name, value = header.split(":", 1)
        headers[name.strip()] = value.strip()
    try:
        with _opener.open(urllib.request.Request(url, headers=headers), timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise RuntimeError("The address redirects somewhere else, which isn't followed.")
        raise RuntimeError(f"The address answered {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Couldn't reach the address.")
    except ValueError:
        raise RuntimeError("The address didn't send JSON.")


def windows_from(entry, data):
    windows = []
    for w in entry.get("windows", []):
        if not w.get("used_field"):
            continue
        try:
            used = number(resolve(data, w["used_field"]))
        except KeyError:
            raise RuntimeError(f'Couldn\'t find "{w["used_field"]}" in the reply. {describe(data)}')
        if used is None:
            raise RuntimeError(f'"{w["used_field"]}" isn\'t a number.')
        if w.get("limit_field"):
            try:
                limit = number(resolve(data, w["limit_field"]))
            except KeyError:
                raise RuntimeError(f'Couldn\'t find "{w["limit_field"]}" in the reply. {describe(data)}')
            if not limit:
                raise RuntimeError(f'"{w["limit_field"]}" isn\'t a number above zero.')
            used = used / limit * 100
        if w.get("counts_left"):
            used = 100 - used
        reset = None
        if w.get("reset_field"):
            try:
                reset = reset_value(resolve(data, w["reset_field"]))
            except KeyError:
                raise RuntimeError(f'Couldn\'t find "{w["reset_field"]}" in the reply. {describe(data)}')
        windows.append({"label": w.get("label") or "Usage", "used": used, "resets_at": reset})
    if not windows:
        raise RuntimeError("No usage field is set. Right-click the rail and click it under Your AIs to add one.")
    return windows


class CustomAI:
    """Looks like a provider module to Quota Rail: NAME, ORDER, ICON and read()."""

    def __init__(self, entry, order):
        self.entry = entry
        self.NAME = entry["name"]
        self.ORDER = order
        self.ICON = None
        self.MANUAL = entry.get("source") == "manual"
        self._last_try, self._result = 0.0, None

    def read(self, force=False):
        e = self.entry
        if self.MANUAL:
            windows = [{"label": w.get("label") or "Usage", "used": w["used"], "resets_at": w.get("resets_at")}
                       for w in e.get("windows", []) if number(w.get("used")) is not None]
            if not windows:
                raise RuntimeError("No numbers yet. Click the ring to type them in.")
            return {"windows": windows, "updated": e.get("updated"), "note": "Typed in by you · click the ring to update"}
        now = time.time()
        if e.get("source") == "web" and not force:
            if now - self._last_try < (WEB_EVERY if self._result else WEB_MIN_GAP):
                if self._result:
                    return self._result
                raise RuntimeError("Waiting to fetch the address again.")
            self._last_try = now
        windows = windows_from(e, fetch(e))
        where = "From your file" if e.get("source") == "file" else "From " + re.sub(r"^https?://([^/]+).*$", r"\1", e.get("url", ""))
        self._result = {"windows": windows, "updated": now, "note": where}
        return self._result


# ---------- the form ----------

class AIDialog:
    """Add or edit one AI. on_save(entry) saves it; on_remove(entry) deletes it (editing only)."""

    def __init__(self, root, entry, on_save, on_remove=None):
        self.entry = dict(entry or {})
        self.on_save, self.on_remove = on_save, on_remove
        win = self.win = tk.Toplevel(root)
        win.title(f"Edit {self.entry['name']}" if entry else "Add an AI")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        f = ttk.Frame(win, padding=16)
        f.grid(sticky="nsew")
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value=self.entry.get("name", ""))
        name_box = ttk.Entry(f, textvariable=self.name, width=36)
        name_box.grid(row=0, column=1, sticky="we", padx=(8, 0))

        ttk.Label(f, text="Where do its numbers come from?").grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 4))
        self.source = tk.StringVar(value=self.entry.get("source", "manual"))
        for i, (value, text) in enumerate([("manual", "I'll type them in myself"),
                                           ("web", "A web address that returns JSON"),
                                           ("file", "A JSON file on this PC")]):
            ttk.Radiobutton(f, text=text, value=value, variable=self.source, command=self.show_source).grid(
                row=2 + i, column=0, columnspan=2, sticky="w")

        # where to read from
        self.where = ttk.Frame(f)
        self.where.grid(row=5, column=0, columnspan=2, sticky="we", pady=(12, 0))
        self.where.columnconfigure(1, weight=1)
        self.url = tk.StringVar(value=self.entry.get("url", ""))
        self.header = tk.StringVar(value=self.entry.get("header", ""))
        self.file = tk.StringVar(value=self.entry.get("file", ""))
        self.web_widgets = [
            ttk.Label(self.where, text="Address"), ttk.Entry(self.where, textvariable=self.url),
            ttk.Label(self.where, text="Header"), ttk.Entry(self.where, textvariable=self.header),
            ttk.Label(self.where, text="Optional, e.g. Authorization: Bearer YOUR-KEY. Saved only on this PC.",
                      foreground="#666"),
        ]
        self.file_widgets = [
            ttk.Label(self.where, text="File"), ttk.Entry(self.where, textvariable=self.file),
            ttk.Button(self.where, text="Browse…", command=self.browse),
        ]

        # the limit windows
        self.table = ttk.Frame(f)
        self.table.grid(row=6, column=0, columnspan=2, sticky="we", pady=(14, 0))
        saved = self.entry.get("windows", [])
        defaults = ["Current session", "Weekly"]
        self.rows = []
        for i in range(ROWS):
            w = saved[i] if i < len(saved) else {}
            self.rows.append({
                "label": tk.StringVar(value=w.get("label", defaults[i] if i < len(defaults) else "")),
                "used": tk.StringVar(value="" if w.get("used") is None else f"{number(w['used']):g}"),
                "reset": tk.StringVar(value=format_reset(w.get("resets_at"))),
                "used_field": tk.StringVar(value=w.get("used_field", "")),
                "limit_field": tk.StringVar(value=w.get("limit_field", "")),
                "reset_field": tk.StringVar(value=w.get("reset_field", "")),
                "counts_left": tk.BooleanVar(value=bool(w.get("counts_left"))),
            })
        self.hint = ttk.Label(f, foreground="#666", wraplength=440, justify="left")
        self.hint.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.test_row = ttk.Frame(f)
        self.test_row.grid(row=8, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(self.test_row, text="Test", command=self.test).grid(row=0, column=0, sticky="w")
        self.result = ttk.Label(self.test_row, wraplength=380, justify="left")
        self.result.grid(row=0, column=1, sticky="w", padx=(10, 0))

        buttons = ttk.Frame(f)
        buttons.grid(row=9, column=0, columnspan=2, sticky="we", pady=(16, 0))
        buttons.columnconfigure(1, weight=1)
        if entry and on_remove:
            ttk.Button(buttons, text="Remove", command=self.remove).grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="Cancel", command=win.destroy).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Save", command=self.save).grid(row=0, column=3)
        self.error = ttk.Label(f, foreground="#c62828", wraplength=440, justify="left")
        self.error.grid(row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))

        win.bind("<Escape>", lambda e: win.destroy())
        self.show_source()
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        y = (win.winfo_screenheight() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        win.focus_force()
        name_box.focus_set()

    def browse(self):
        path = filedialog.askopenfilename(parent=self.win, title="Choose a JSON file",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.file.set(path)

    def show_source(self):
        source = self.source.get()
        for w in self.web_widgets + self.file_widgets:
            w.grid_remove()
        if source == "web":
            for i, w in enumerate(self.web_widgets[:4]):
                w.grid(row=i // 2, column=i % 2, sticky="we" if i % 2 else "w", padx=(8, 0) if i % 2 else 0, pady=2)
            self.web_widgets[4].grid(row=2, column=1, sticky="w", padx=(8, 0))
        elif source == "file":
            self.file_widgets[0].grid(row=0, column=0, sticky="w")
            self.file_widgets[1].grid(row=0, column=1, sticky="we", padx=(8, 0))
            self.file_widgets[2].grid(row=0, column=2, padx=(8, 0))
        self.where.grid() if source != "manual" else self.where.grid_remove()
        self.test_row.grid() if source != "manual" else self.test_row.grid_remove()

        for child in self.table.winfo_children():
            child.destroy()
        if source == "manual":
            heads = ["Limit", "% used", "Resets"]
            for c, h in enumerate(heads):
                ttk.Label(self.table, text=h).grid(row=0, column=c, sticky="w", padx=(0, 8))
            for r, row in enumerate(self.rows, start=1):
                ttk.Entry(self.table, textvariable=row["label"], width=18).grid(row=r, column=0, padx=(0, 8), pady=2)
                ttk.Entry(self.table, textvariable=row["used"], width=8).grid(row=r, column=1, padx=(0, 8), pady=2)
                ttk.Entry(self.table, textvariable=row["reset"], width=18).grid(row=r, column=2, pady=2)
            self.hint.config(text="Resets: like 3h, 2d 4h, 18:30 or 2026-10-22 14:00, or leave it empty. "
                                  "Leave % used empty to skip a row. Click the AI's ring any time to update.")
        else:
            heads = ["Limit", "Used field", "Out of field", "Reset field", ""]
            for c, h in enumerate(heads):
                ttk.Label(self.table, text=h).grid(row=0, column=c, sticky="w", padx=(0, 6))
            for r, row in enumerate(self.rows, start=1):
                ttk.Entry(self.table, textvariable=row["label"], width=15).grid(row=r, column=0, padx=(0, 6), pady=2)
                ttk.Entry(self.table, textvariable=row["used_field"], width=16).grid(row=r, column=1, padx=(0, 6), pady=2)
                ttk.Entry(self.table, textvariable=row["limit_field"], width=12).grid(row=r, column=2, padx=(0, 6), pady=2)
                ttk.Entry(self.table, textvariable=row["reset_field"], width=14).grid(row=r, column=3, padx=(0, 6), pady=2)
                ttk.Checkbutton(self.table, text="counts what's left", variable=row["counts_left"]).grid(row=r, column=4)
            self.hint.config(text="Fields are paths into the JSON, like data.usage.percent or limits[0].used. "
                                  "Leave \"Out of\" empty if the used field is already a percent. "
                                  "Tick \"counts what's left\" if the number is what remains. Press Test to check.")

    def build(self):
        """The entry described by the form. Raises ValueError with a message for the user."""
        name = self.name.get().strip()
        if not name:
            raise ValueError("Give the AI a name.")
        source = self.source.get()
        entry = {"id": self.entry.get("id") or f"ai{int(time.time() * 1000)}", "name": name, "source": source}
        windows = []
        for row in self.rows:
            label = row["label"].get().strip() or "Usage"
            if source == "manual":
                used = row["used"].get().strip()
                if not used:
                    continue
                pct = number(used)
                if pct is None or not 0 <= pct <= 100:
                    raise ValueError(f'"{label}": % used must be a number from 0 to 100.')
                windows.append({"label": label, "used": pct, "resets_at": parse_reset(row["reset"].get())})
            elif row["used_field"].get().strip():
                windows.append({"label": label, "used_field": row["used_field"].get().strip(),
                                "limit_field": row["limit_field"].get().strip(),
                                "reset_field": row["reset_field"].get().strip(),
                                "counts_left": row["counts_left"].get()})
        if not windows:
            raise ValueError("Fill in at least one limit." if source == "manual" else "Fill in at least one used field.")
        entry["windows"] = windows
        if source == "manual":
            entry["updated"] = time.time()
        elif source == "web":
            if not re.match(r"^https?://", self.url.get().strip(), re.I):
                raise ValueError("The address must start with https:// (or http://).")
            entry["url"], entry["header"] = self.url.get().strip(), self.header.get().strip()
        else:
            if not self.file.get().strip():
                raise ValueError("Choose the JSON file.")
            entry["file"] = self.file.get().strip()
        return entry

    def test(self):
        try:
            result = CustomAI(self.build(), 0).read(force=True)
            lines = [f"{w['label']}: {w['used']:.0f}% used" +
                     (f", resets {dt.datetime.fromtimestamp(w['resets_at']):%a %d %b %H:%M}" if w.get("resets_at") else "")
                     for w in result["windows"]]
            self.result.config(text="Works. " + "; ".join(lines), foreground="#2e7d32")
        except (ValueError, RuntimeError) as e:
            self.result.config(text=str(e), foreground="#c62828")

    def save(self):
        try:
            entry = self.build()
        except ValueError as e:
            self.error.config(text=str(e))
            return
        self.on_save(entry)
        self.win.destroy()

    def remove(self):
        self.on_remove(self.entry)
        self.win.destroy()
