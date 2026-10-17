<div align="center">

# QuotaTrack

**See how much of your AI usage limits you've used, and whether each AI is working or waiting on you, from
a slim rail on the edge of your screen.**

[![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)](#install)
[![macOS](https://img.shields.io/badge/macOS-untested-lightgrey?logo=apple&logoColor=white)](#install)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![No dependencies](https://img.shields.io/badge/dependencies-none-22c55e)](#how-it-works)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<img src="docs/hero.gif" alt="QuotaTrack's rail on the right edge of the screen, with the Claude card open: a session asking you something, one working, and one done" width="410">

</div>

---

Claude, Codex, Copilot, Cursor and the rest each hide their limits in a different menu. QuotaTrack puts them
all in one place, always on screen:

- 🟢 **One ring per AI**: green below 50%, yellow from 50%, red from 70%.
- 🔍 **Hover a ring** to see each limit, how much of it is used, and when it resets.
- ⏳ **Working:** a white arc spins inside the ring while an AI is busy.
- 🔵 **Waiting for your reply:** a blue ring blinks when an AI has asked you something or needs your approval.
- 🖱️ **Click it** to jump straight to that chat in the Claude or Codex app, or to the terminal it runs in.
- ✅ **Done:** finished chats show "Done" for 5 minutes, then drop off the list.
- 🔔 **Alerts:** the card pops out, with an optional sound, when an AI finishes or needs you, and when a limit
  passes 80% or 100%.
- 🧩 **Add any AI** from the menu, without writing code.

It only shows the AIs you actually have installed, so it works out of the box on anyone's PC.

## Supported AIs

| AI | Usage limits | Working / waiting | Needs a switch? |
| --- | :---: | :---: | :---: |
| **Claude** (desktop app and Claude Code) | ✅ | ✅ | No |
| **Codex** (ChatGPT plan) | ✅ | ✅ | No; optional live mode |
| **Antigravity** | ✅ | — | No |
| **GitHub Copilot** | ✅ | — | Yes |
| **Cursor** | ✅ | — | Yes |
| **Kimi Code** | ✅ | — | Yes |
| **Grok CLI** *(untested)* | ✅ | — | Yes |
| **Anything else** | ✅ via [Add an AI](#add-your-own-ai) | — | — |

AIs that need a switch keep no usage figures on your PC, so QuotaTrack has to ask the service for them. It
only does that once you turn that AI's **Live usage** switch on in the right-click menu (see
[Privacy](#privacy-and-safety)).

## Install

QuotaTrack is plain Python with no packages to install.

**Windows**

1. Install [Python 3](https://www.python.org/downloads/) (3.9 or newer; tested on 3.14).
2. Download this repo (**Code → Download ZIP**, or clone it):
   ```bash
   git clone https://github.com/Donelongo/QuotaTrack.git
   ```
3. Double-click **`quotatrack.pyw`**.

**macOS** *(not tested yet; reports welcome)*

```bash
git clone https://github.com/Donelongo/QuotaTrack.git
cd QuotaTrack
python3 quotatrack.pyw
```

Use Python from [python.org](https://www.python.org/downloads/), or Homebrew's Python with
`brew install python-tk`.

**Start it automatically:** right-click the rail and turn on **Start with Windows** (**Start at login** on
macOS).

Want to look around first? Run `python quotatrack.pyw --demo` to see it with sample figures.

## Using it

| Do this | To |
| --- | --- |
| **Hover** a ring | See its limits, reset times and live sessions |
| **Click** a blinking ring | Jump to the chat that's waiting for you |
| **Click** a session in the card | Open that chat |
| **Drag** the rail | Move it up or down the edge (the position is remembered) |
| **Right-click** | Open the menu |

<img src="docs/menu.png" alt="QuotaTrack's right-click menu: Refresh now, Auto-hide, Dock on left edge, Sounds, Start with Windows, Add an AI" width="396">

The menu has switches for **Auto-hide**, **Dock on left edge**, **Sounds** and **Start with Windows**; a
**Live usage** switch for each AI that needs one; and your own AIs. It closes when your pointer leaves it.
Settings are saved in `config.json` next to the app.

## Privacy and safety

QuotaTrack is built to be safe to run:

- **Local by default.** Out of the box it only reads files your AI apps already keep on your computer. It
  makes no network requests, and it never changes another app's settings or files.
- **Live modes are opt-in, one AI at a time.** When you turn one on, QuotaTrack reads the sign-in that AI's own
  app saved, without refreshing or rewriting it. It sends that sign-in only to the service's own address, and
  refuses redirects so it can't be forwarded anywhere else. It asks at most every 5 minutes.
- **No tokens in the repo.** `config.json` and `custom_ais.json`, which can hold API keys, are git-ignored.
- **Small and readable.** No third-party packages. Each AI is one short file in [`providers/`](providers), so
  you can check exactly what it reads.

<details>
<summary><b>Exactly what each AI reads</b></summary>

| AI | Usage from | Working / waiting from |
| --- | --- | --- |
| Claude | The Claude desktop app's `plan-usage-history.json` | Claude Code's `~/.claude/sessions/*.json` (the `.key` files beside them are never opened) |
| Codex | The rate limits Codex logs in `~/.codex/sessions` after each message | `task_started` / `task_complete` events in the same logs |
| Codex, live mode | `chatgpt.com/backend-api/wham/usage`, with `access_token` and `account_id` from `~/.codex/auth.json` | — |
| Antigravity | Antigravity's own local server on `127.0.0.1`, while Antigravity is open (nothing leaves your PC) | — |
| Copilot | `api.github.com/copilot_internal/user`, with the GitHub CLI's sign-in | — |
| Cursor | `cursor.com/api/usage-summary`, with the editor's own sign-in | — |
| Kimi Code | `api.kimi.com/coding/v1/usages`, with the Kimi Code CLI's sign-in | — |
| Grok CLI | `cli-chat-proxy.grok.com/v1/billing`, with Grok CLI's sign-in | — |

</details>

## How accurate is it?

- **Codex, Copilot, Cursor, Kimi and Antigravity** show the service's own figures. In Codex's default mode they're
  as of your last Codex message. Turn on live mode for the same numbers as the Codex app's **Usage remaining** menu.
- **Claude** can be up to about 15 minutes behind. The Claude desktop app only saves your usage to disk about
  every 15 minutes, and QuotaTrack reads that file rather than your sign-in. Opening Claude's own Usage panel
  should make it save a fresh reading. Reset times are worked out from the saved readings, so they're marked with `~`.

## Add your own AI

**Without code:** right-click the rail and choose **Add an AI…**. Its numbers can come from:

- **You:** type how much you've used and when it resets (`3h`, `2d 4h`, `18:30` or `2026-10-22 14:00`). Click
  its ring to update them.
- **A web address that returns JSON:** for services with a usage API. Add a header if it needs a key, then name
  the fields that hold the numbers (`data.usage.percent`, `limits[0].used`). Press **Test** to check it.
- **A JSON file on this PC** that another tool keeps up to date.

<details>
<summary><b>With code</b></summary>

Each built-in AI is one file in [`providers/`](providers):

1. Copy `providers/_template.py` to `providers/<name>.py`.
2. Fill in `read()` to return the limit percentages. Optionally add `activity()` for working/waiting,
   `open_app()` for click-to-open, `SETTINGS` for switches in the menu, and `available()` to hide the ring
   until the AI is installed.
3. Restart QuotaTrack.

It gets its own ring automatically, with the first letter of its name or a PNG icon (`ICON = "file.png"`). A
provider is ordinary Python running on your computer, so only add ones you wrote or have read.

</details>

## How it works

- `quotatrack.pyw` is the app: the rail, rings, cards, animations and alerts, drawn with Tkinter.
- `providers/` has one file per AI, plus shared helpers for live requests (`_live.py`) and bringing a chat's
  window forward (`_focus.py`).
- `custom_ais.py` is the **Add an AI…** form.
- The curved ends of the rail and cards are separate windows with per-pixel transparency
  (`UpdateLayeredWindow` on Windows, a transparent Tk window on macOS), so they blend into whatever is behind
  them.
- Usage is read every 30 seconds and activity every 2 seconds, on background threads.

## Contributing

Bug reports, Mac test results and new providers are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). QuotaTrack is an independent project, not affiliated with or endorsed by Anthropic, OpenAI,
Google, GitHub, Anysphere, Moonshot AI or xAI. Product names belong to their owners. Logos are read from each
app's installed files on your PC and aren't included in this repo.
