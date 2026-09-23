"""
_deprecation.py - helpers implementing the SDK deprecation policy.

Policy
------
Public API is governed by SemVer. A symbol is first marked deprecated in a
minor release (emitting :class:`DeprecationWarning`), kept working for at least
one subsequent minor release, and only removed in a major release. Every
deprecation states the version it was deprecated in and the earliest version it
may be removed.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def deprecated(*, since: str, removal: str, alternative: str | None = None) -> Callable[[F], F]:
    """
    Mark a function/method as deprecated.

    Parameters
    ----------
    since:
        Version in which the symbol was deprecated (e.g. ``"0.2.0"``).
    removal:
        Earliest version in which it may be removed (e.g. ``"1.0.0"``).
    alternative:
        Optional recommended replacement.
    """

    def decorator(func: F) -> F:
        msg = f"{func.__qualname__} is deprecated since {since} and may be removed in {removal}."
        if alternative:
            msg += f" Use {alternative} instead."

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        wrapper.__doc__ = f"{func.__doc__ or ''}\n\n.. deprecated:: {since}\n    {msg}"
        return wrapper  # type: ignore[return-value]

    return decorator


def warn_deprecated(name: str, *, since: str, removal: str, alternative: str | None = None) -> None:
    """Emit a deprecation warning for a non-callable symbol (e.g. a module attribute)."""
    msg = f"{name} is deprecated since {since} and may be removed in {removal}."
    if alternative:
        msg += f" Use {alternative} instead."
    warnings.warn(msg, DeprecationWarning, stacklevel=2)
