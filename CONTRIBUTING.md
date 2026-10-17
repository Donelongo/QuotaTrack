# Contributing to QuotaTrack

Thanks for helping out. QuotaTrack is small on purpose, so a few ground rules keep it that way.

## Reporting a bug

Open an issue with:

- your OS (Windows 10/11 or macOS version) and Python version (`python --version`)
- which AI's ring is wrong, and what it shows next to what the AI's own app shows
- a screenshot if it's a drawing problem

Please don't paste anything from `config.json`, `custom_ais.json`, or your AI apps' sign-in files. They can
hold keys.

## Testing on a Mac

macOS support is written but hasn't been tested yet. If you run it on a Mac, an issue saying what works and what
doesn't (with screenshots) is one of the most useful things you can do.

## Adding an AI

1. Copy `providers/_template.py` to `providers/<name>.py` and follow the comments in it.
2. Read only what you need. Prefer files the AI's app already keeps on the PC.
3. If it has to ask a service for usage:
   - put it behind a switch, with `SETTINGS = {"live": ...}` and `REQUIRES = "live"`
   - use `_live.get_json`, so the sign-in only goes to that service's own address and redirects are refused
   - never refresh or write another app's sign-in
4. Define `available()` so the ring only shows when the AI is installed.
5. Don't add the AI's logo to the repo. Point `ICON` at the app's installed files, or leave it out.

## Code style

- Python 3.9+, standard library only. No third-party packages.
- Match the surrounding code: short functions, plain names, and comments that say *why*.
- Try your change with `python quotatrack.pyw --demo` and with your real AIs before opening a pull request.
