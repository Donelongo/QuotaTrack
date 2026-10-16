# AI Usage Notch

A small black notch pinned to the edge of your screen that shows how much of each AI's usage limit you've
used, and whether it's working, done, or waiting on you.

![The notch on the right edge with the Claude card open](docs/screenshot.png)

- **One ring per AI.** Green below 50%, yellow from 50%, red from 70%.
- **Hover a ring** for each limit window, how much is used, and when it resets.
- **Working:** a white arc spins inside the ring while a session is busy.
- **Waiting on you:** the ring pulses amber when a session needs your approval or input.
- **Alerts:** the card pops open (with a sound you can turn off) when a session finishes or stops to ask you
  something, and when a limit crosses 80% or 100%.

## Safe by design

The built-in providers only **read files the AI apps already write on your computer**. Notch sends nothing
over the network, never touches login tokens, and never edits another app's settings.

| AI | Usage from | Working / waiting from |
| --- | --- | --- |
| Claude | Claude desktop's `plan-usage-history.json` (reset times are estimated, shown with `~`) | Claude Code's session files in `~/.claude/sessions/*.json` |
| Codex (ChatGPT plan) | The rate limits Codex logs in `~/.codex/sessions` after each message | `task_started` / `task_complete` events in the same logs |

No third-party packages: it's plain Python and Tkinter.

## Run it

**Windows:** install [Python 3](https://www.python.org/downloads/), then double-click `notch.pyw`, or run:

```bash
pythonw notch.pyw
```

**macOS** (untested so far): use Python from python.org, or Homebrew's Python plus `brew install python-tk`:

```bash
python3 notch.pyw
```

Add `--demo` to see it with sample figures. Only one copy runs at a time.

## Using it

- **Click a ring** to refresh it now.
- **Drag** the column up or down the edge. Its position is remembered.
- **Right-click** for: Refresh now, Auto-hide, Dock on left edge, sounds on/off, Open providers folder, Quit.

Settings are saved in `config.json` next to `notch.pyw`.

## Adding another AI

Each AI is one file in [`providers/`](providers). To add one:

1. Copy `providers/_template.py` to `providers/<name>.py`.
2. Fill in `read()` to return the limit percentages, and optionally `activity()` for working/waiting.
3. Restart Notch.

It gets its own ring automatically, showing the first letter of its name, or a PNG icon you put in
`providers/`. A provider is ordinary Python running on your computer, so only add ones you wrote or have read.

## How it works

- `notch.pyw` is the app: the column, rings, hover cards, animations and alerts.
- The straight middle of the column and of the card are ordinary windows. The curved ends are separate
  windows with per-pixel transparency (`UpdateLayeredWindow` on Windows, a transparent Tk window on macOS),
  so the curves blend smoothly into whatever is behind them.
- Usage is read every 30 seconds and activity every 2 seconds, on a background thread.
