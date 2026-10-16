"""Shared by providers that ask a service for live usage with a sign-in already on this PC.

The rules every one of them follows:
- the sign-in is only read, never refreshed or written; renewing it is its own app's job
- it's sent only to the one address the provider names (checked before every request), and redirects are
  refused, because urllib would otherwise copy the Authorization header or cookie to wherever they point
- requests are spaced out (every 5 minutes by default) and back off when the service rate-limits
- nothing about the sign-in appears in an error message
"""

import json
import time
import urllib.error
import urllib.request


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def get_json(url, headers, allowed_prefix, service, expired_message):
    """GET url as JSON. url must start with allowed_prefix (an https:// origin and path)."""
    if not url.startswith(allowed_prefix):
        raise RuntimeError(f"The live address must be on {allowed_prefix}, so your sign-in isn't sent there.")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "quota-rail",
                                                   **headers})
    try:
        with _opener.open(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(expired_message)
        if e.code == 429:
            raise RateLimited(f"{service} is rate-limiting usage checks; trying again later.",
                              e.headers.get("Retry-After", ""))
        if 300 <= e.code < 400:
            raise RuntimeError(f"{service} answered with a redirect, which isn't followed.")
        raise RuntimeError(f"{service} answered {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(f"Couldn't reach {service}.")
    except ValueError:
        raise RuntimeError(f"{service} sent a reply that isn't usage data.")


class RateLimited(RuntimeError):
    def __init__(self, message, retry_after=""):
        super().__init__(message)
        self.retry_after = int(retry_after) if str(retry_after).isdigit() else 0


class Throttle:
    """Calls fetch() at most every `every` seconds, reusing the last result in between."""

    def __init__(self, every=300, min_gap=60, backoff_cap=900):
        self.every, self.min_gap, self.cap = every, min_gap, backoff_cap
        self.last_try, self.not_before, self.backoff, self.result = 0.0, 0.0, 0, None

    def get(self, fetch, waiting_message):
        now = time.time()
        if now < self.not_before or now - self.last_try < (self.every if self.result else self.min_gap):
            if self.result:
                return self.result
            raise RuntimeError(waiting_message)
        self.last_try = now
        try:
            self.result = fetch()
        except RateLimited as e:
            self.backoff += 1
            self.not_before = now + max(min(60 * 2 ** (self.backoff - 1), self.cap), e.retry_after)
            raise
        self.backoff = 0
        return self.result
