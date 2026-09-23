"""
_http.py - shared HTTP plumbing for the sync and async remote clients.

Centralises configuration resolution (env vars), authentication headers, the
retry/backoff policy, and mapping of transport/HTTP errors onto the HyperMesh
exception taxonomy. Both :class:`hypermeshdb.Client` and
:class:`hypermeshdb.AsyncClient` share this logic.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field

from ._types import AuthError, ConnectionError, HyperMeshError, TimeoutError

logger = logging.getLogger("hypermesh.client")

DEFAULT_TIMEOUT = 30.0
ENV_URL = "HYPERMESH_URL"
ENV_API_KEY = "HYPERMESH_API_KEY"

# Status codes worth retrying (transient server / rate-limit conditions).
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential-backoff-with-jitter retry policy."""

    retries: int = 2
    backoff_factor: float = 0.25
    max_backoff: float = 10.0
    retry_status: frozenset[int] = field(default_factory=lambda: RETRYABLE_STATUS)

    def backoff(self, attempt: int) -> float:
        """Seconds to sleep before *attempt* (1-based), with full jitter."""
        base = min(self.max_backoff, self.backoff_factor * (2 ** (attempt - 1)))
        return random.uniform(0, base)


def resolve_base_url(url: str | None) -> str:
    """Resolve the server base URL from the argument or ``HYPERMESH_URL``."""
    resolved = url or os.environ.get(ENV_URL)
    if not resolved:
        raise ConnectionError(
            f"No server URL provided. Pass a URL or set the {ENV_URL} environment variable."
        )
    return resolved.rstrip("/")


def resolve_api_key(api_key: str | None) -> str | None:
    """Resolve the API key from the argument or ``HYPERMESH_API_KEY``."""
    return api_key if api_key is not None else os.environ.get(ENV_API_KEY)


def build_headers(api_key: str | None, extra: dict[str, str] | None) -> dict[str, str]:
    """Build default request headers (auth + user-agent + custom)."""
    headers: dict[str, str] = {"User-Agent": "hypermesh-client"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def map_status_error(status: int, detail: str) -> HyperMeshError:
    """Map an HTTP error response to the appropriate exception type."""
    if status in (401, 403):
        return AuthError(f"Authentication failed ({status}): {detail}")
    if status in (408, 504):
        return TimeoutError(f"Server timeout ({status}): {detail}")
    return HyperMeshError(f"Server {status}: {detail}")


def map_transport_error(exc: Exception, where: str) -> HyperMeshError:
    """Map an httpx transport exception to the appropriate exception type."""
    name = type(exc).__name__
    if "Timeout" in name:
        return TimeoutError(f"{where} timed out: {exc}")
    if "Connect" in name or "Transport" in name or "Network" in name:
        return ConnectionError(f"{where} failed to connect: {exc}")
    return HyperMeshError(f"{where} failed: {exc}")


def extract_detail(response: object) -> str:
    """Best-effort extraction of a server error detail from a response."""
    try:
        body = response.json()  # type: ignore[attr-defined]
        if isinstance(body, dict):
            return str(body.get("detail", body))
        return str(body)
    except Exception:
        try:
            return str(response.text)  # type: ignore[attr-defined]
        except Exception:
            return "<no detail>"
