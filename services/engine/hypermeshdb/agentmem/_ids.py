"""
Project:     HyperMesh DB
File:        hypermeshdb/agentmem/_ids.py
Description: ULID generation for stable memory-edge identity. Monotonic
             within a process so identifiers sort by creation order.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import os
import threading
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """Return a 26-character ULID (48-bit ms timestamp + 80-bit entropy).

    Monotonic within the process: two calls in the same millisecond
    increment the random component, so ordering is total.
    """
    global _last_ms, _last_rand
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms:
            _last_rand += 1
        else:
            _last_ms = now_ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        rand = _last_rand & ((1 << 80) - 1)
        return _encode(now_ms & ((1 << 48) - 1), 10) + _encode(rand, 16)


def is_ulid(value: str) -> bool:
    """Cheap shape check for a ULID string."""
    return (
        isinstance(value, str)
        and len(value) == 26
        and all(c in _CROCKFORD for c in value)
    )
