#!/usr/bin/env python3
"""Shared per-domain politeness rate limiter (max 1 request per 2s per domain)."""

import threading
import time
import urllib.parse


def extract_domain(url_or_domain):
    """Normalize a URL or hostname to a lowercase domain string."""
    raw = str(url_or_domain or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname or ""
    else:
        host = raw.split("/")[0].split(":")[0]
    return host.rstrip(".").lower()


class DomainRateLimiter:
    """Thread-safe per-domain rate limiter enforcing minimum spacing between requests."""

    def __init__(self, min_interval_s=2.0, clock=None, sleep_fn=None):
        self.min_interval_s = float(min_interval_s)
        self._clock = clock or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self._lock = threading.Lock()
        self._last_request = {}

    def wait(self, url_or_domain):
        """Block if needed so requests to the same domain are at least min_interval_s apart."""
        domain = extract_domain(url_or_domain)
        if not domain or self.min_interval_s <= 0:
            return 0.0

        with self._lock:
            now = float(self._clock())
            prev = self._last_request.get(domain)
            if prev is None:
                self._last_request[domain] = now
                return 0.0
            earliest = prev + self.min_interval_s
            delay = max(0.0, earliest - now)
            self._last_request[domain] = now + delay

        if delay > 0:
            self._sleep(delay)
        return delay

    def reset(self):
        """Clear recorded request timestamps (for tests)."""
        with self._lock:
            self._last_request.clear()


limiter = DomainRateLimiter(min_interval_s=2.0)
