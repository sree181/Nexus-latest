"""
_rate_limiter.py — Sliding window rate limiter for HyperMesh DB.

Each API key gets its own in-process sliding window.  When auth is disabled
all requests share an anonymous bucket so the server still has some DoS
protection.

Configuration
-------------
``HMDB_RATE_LIMIT_QPM``   Integer — requests allowed per key per minute.
                          Default: 1000.  Set to 0 to disable rate limiting.

Response headers (added to every request when limiting is active)
-------------------------------------------------------------------
``X-RateLimit-Limit``      Configured limit for this key.
``X-RateLimit-Remaining``  Requests left in the current window.
``X-RateLimit-Reset``      Unix timestamp (seconds) when the window resets.
``Retry-After``            Seconds to wait before retrying (only on 429).
"""
from __future__ import annotations

import collections
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class _Window:
    """Mutable sliding-window state for one key."""
    timestamps: collections.deque = field(default_factory=collections.deque)
    lock:       threading.Lock    = field(default_factory=threading.Lock)


class RateLimiter:
    """
    Thread-safe in-process sliding window rate limiter.

    All state lives in-process — no Redis or external dependency required.
    For single-server deployments this is correct.  Multi-replica deployments
    will have independent windows per replica (acceptable for v1).
    """

    def __init__(
        self,
        requests_per_window: int   = 1000,
        window_seconds:      float = 60.0,
    ) -> None:
        if requests_per_window < 0:
            raise ValueError("requests_per_window must be >= 0")
        self._rpm    = requests_per_window
        self._win    = window_seconds
        self._store: dict[str, _Window] = {}
        self._global = threading.Lock()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _window(self, key: str) -> _Window:
        with self._global:
            if key not in self._store:
                self._store[key] = _Window()
            return self._store[key]

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def limit(self) -> int:
        return self._rpm

    @property
    def disabled(self) -> bool:
        return self._rpm == 0

    def check(self, key: str) -> Tuple[bool, int, float]:
        """
        Test whether ``key`` has remaining capacity.

        Parameters
        ----------
        key:
            Opaque string identifying the caller (e.g. SHA-256 of the API key).

        Returns
        -------
        (allowed, remaining, reset_epoch)
            ``allowed``     — True if the request should proceed.
            ``remaining``   — Requests left in the current window.
            ``reset_epoch`` — Wall-clock UNIX time when the oldest slot expires.
        """
        if self._rpm == 0:
            return True, 0, 0.0

        win = self._window(key)
        now    = time.monotonic()
        cutoff = now - self._win

        with win.lock:
            while win.timestamps and win.timestamps[0] <= cutoff:
                win.timestamps.popleft()

            count = len(win.timestamps)
            if count >= self._rpm:
                reset_at = win.timestamps[0] + self._win
                return False, 0, time.time() + (reset_at - now)

            win.timestamps.append(now)
            remaining = self._rpm - count - 1
            return True, remaining, time.time() + self._win

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "RateLimiter":
        """Construct from ``HMDB_RATE_LIMIT_QPM`` environment variable."""
        rpm = int(os.environ.get("HMDB_RATE_LIMIT_QPM", "1000"))
        return cls(requests_per_window=rpm)


# ── Utility ───────────────────────────────────────────────────────────────────

def key_fingerprint(api_key: str) -> str:
    """SHA-256 hex of the plaintext key — safe to use as a rate limit bucket."""
    return hashlib.sha256(api_key.encode()).hexdigest()
