"""
_logging_config.py — Structured JSON logging for HyperMesh DB.

Sets up two handlers:

1. **Console handler** — JSON lines to stderr, respects ``--log-level``.
2. **Audit file handler** (optional) — append-only JSON lines to a dedicated
   file; records every DDL statement, DML statement, auth failure, and key
   management event regardless of the console log level.

Usage
-----
Call ``configure_logging()`` once at server startup (from ``hmdb serve``).
The module is safe to import without calling ``configure_logging()``; Python's
root logger will handle messages with its default configuration.

Log record format (JSON)
------------------------
::

    {
        "ts":       "2026-04-10T22:00:00.123Z",
        "level":    "INFO",
        "logger":   "hypermeshdb._api",
        "msg":      "query executed",
        "method":   "POST",
        "path":     "/v1/query",
        "status":   200,
        "latency_ms": 1.4,
        "table":    "Events",
        "strategy": "TPI_BUCKET_PUSHDOWN",
        "rows":     12,
        "key_id":   "key_a1b2c3d4",
        "remote_ip": "10.0.0.5"
    }

Audit records additionally carry ``"audit": true`` and a ``"stmt"`` field.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


# ── JSON formatter ────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """
    Render a LogRecord as a single-line JSON object.

    Extra fields can be attached by passing them as keyword arguments when
    calling ``logger.info(..., extra={"key_id": "...", "remote_ip": "..."})``
    or by using ``logger.info(..., key_id="...", remote_ip="...")``.
    """

    _SKIP = frozenset({
        "name", "msg", "args", "created", "relativeCreated",
        "thread", "threadName", "process", "processName",
        "pathname", "filename", "module", "funcName", "lineno",
        "levelno", "levelname", "msecs", "stack_info", "exc_info",
        "exc_text", "message", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        doc: dict[str, Any] = {
            "ts":     self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + "Z",
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.message,
        }
        # Exception info
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        # Any extra fields passed via ``extra={}``
        for key, val in record.__dict__.items():
            if key not in self._SKIP and not key.startswith("_"):
                doc[key] = val
        return json.dumps(doc, default=str)


# ── Audit logger ──────────────────────────────────────────────────────────────

_AUDIT_LOGGER_NAME = "hypermeshdb.audit"
audit_log = logging.getLogger(_AUDIT_LOGGER_NAME)


def log_audit(
    event: str,
    *,
    key_id:    str | None = None,
    remote_ip: str | None = None,
    table:     str | None = None,
    stmt:      str | None = None,
    **extra: Any,
) -> None:
    """
    Emit a structured audit record.  All calls go to the ``hypermeshdb.audit``
    logger regardless of the caller's logger name.
    """
    audit_log.info(
        event,
        extra={
            "audit":     True,
            "event":     event,
            "key_id":    key_id,
            "remote_ip": remote_ip,
            "table":     table,
            "stmt":      stmt,
            **extra,
        },
    )


# ── Configuration entry point ─────────────────────────────────────────────────

def configure_logging(
    level:      str       = "INFO",
    log_file:   str | None = None,
    audit_file: str | None = None,
) -> None:
    """
    Configure root and audit loggers.

    Parameters
    ----------
    level:
        Console log level (DEBUG / INFO / WARNING / ERROR).
    log_file:
        If given, write all log records to this file in addition to stderr.
    audit_file:
        If given, write audit records to this file (append mode).
        Audit records are ALWAYS written here at INFO level regardless of
        the ``level`` parameter.
    """
    fmt = _JsonFormatter()

    # ── Root logger ───────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers (e.g. basicConfig default)
    for h in root.handlers[:]:
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # ── Audit logger ──────────────────────────────────────────────────────
    audit_log.propagate = False   # don't duplicate to root
    audit_log.setLevel(logging.INFO)

    if audit_file:
        ah = logging.FileHandler(audit_file, mode="a", encoding="utf-8")
        ah.setFormatter(fmt)
        audit_log.addHandler(ah)
    else:
        # Fall back to stderr so audit events are always visible
        ac = logging.StreamHandler(sys.stderr)
        ac.setFormatter(fmt)
        audit_log.addHandler(ac)

    logging.getLogger(__name__).info(
        "logging configured",
        extra={"level": level, "log_file": log_file, "audit_file": audit_file},
    )
