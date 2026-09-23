"""
Opaque cursor pagination for REST read endpoints (P1.3).

A cursor is an opaque, URL-safe token that encodes the next offset into an
ordered result set together with a *scope* fingerprint of the originating query
(table + bounds + filters). The scope binding means a client cannot accidentally
(or maliciously) reuse a cursor minted for one query against a different one —
``decode_cursor`` raises :class:`InvalidCursor` if the scope does not match.

The token is intentionally not signed/encrypted: it carries no secret, only a
position. It is base64url(JSON) so it stays compact and copy-paste safe while
remaining trivially inspectable in tests and logs.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

__all__ = ["InvalidCursor", "scope_fingerprint", "encode_cursor", "decode_cursor"]


class InvalidCursor(ValueError):
    """Raised when a cursor is malformed or was minted for a different query."""


def scope_fingerprint(*parts: object) -> str:
    """A short, stable fingerprint of the query parameters a cursor is tied to."""
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def encode_cursor(offset: int, scope: str) -> str:
    """Encode the next ``offset`` for a result set identified by ``scope``."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    payload = json.dumps({"o": int(offset), "s": scope},
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(token: str, scope: str) -> int:
    """
    Decode ``token`` and return its offset, validating it was minted for
    ``scope``. Raises :class:`InvalidCursor` on any malformed token or a
    scope mismatch.
    """
    if not token:
        raise InvalidCursor("empty cursor")
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor("malformed cursor") from exc

    if not isinstance(payload, dict) or "o" not in payload or "s" not in payload:
        raise InvalidCursor("malformed cursor")
    if payload["s"] != scope:
        raise InvalidCursor("cursor does not belong to this query")

    offset = payload["o"]
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise InvalidCursor("malformed cursor offset")
    return offset
