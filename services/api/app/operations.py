"""Durable operation journal for destructive governance actions.

A deletion is not one write. It changes HyperMesh records, redacts payloads,
persists a certificate, and appends audit evidence. This journal records the
operator's approved closure before the first mutation and survives process
restarts. The gateway can therefore finish an interrupted operation or return
the original certificate for a retried idempotency key.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .models import DeletionCertificate

FILE_NAME = "operations.json"
VERSION = 1
OperationState = Literal["prepared", "mutating", "committed", "failed"]


@dataclass(frozen=True)
class PlannedEdge:
    """One approved memory in a deletion closure before its payload is removed."""

    ulid: str
    entity: str | None
    content_sha: str


@dataclass
class OperationRecord:
    """Restart-safe state for one idempotent destructive operation."""

    idempotency_key: str
    kind: Literal["forget"]
    run_id: str
    node: str
    root: str
    reason: str
    actor: str
    expected_version: str
    planned: list[PlannedEdge]
    state: OperationState = "prepared"
    prepared_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))
    certificate: DeletionCertificate | None = None
    error: str | None = None

    def matches(
        self,
        *,
        run_id: str,
        node: str,
        reason: str,
        actor: str,
        expected_version: str | None = None,
    ) -> bool:
        """Whether a replay is the same request rather than key reuse."""
        return (
            self.run_id == run_id
            and self.node == node
            and self.reason == reason
            and self.actor == actor
            and (
                expected_version is None
                or self.expected_version == expected_version
            )
        )


@dataclass
class OperationJournal:
    """Small JSON journal with atomic replacement and durable directory sync."""

    base: str
    operations: dict[str, OperationRecord] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def path(self) -> str:
        return os.path.join(self.base, FILE_NAME)

    def get(self, key: str) -> OperationRecord | None:
        with self._lock:
            return self.operations.get(key)

    def prepare(self, record: OperationRecord) -> OperationRecord:
        with self._lock:
            existing = self.operations.get(record.idempotency_key)
            if existing is not None:
                return existing
            self.operations[record.idempotency_key] = record
            self.save()
            return record

    def transition(
        self,
        key: str,
        state: OperationState,
        *,
        certificate: DeletionCertificate | None = None,
        error: str | None = None,
    ) -> OperationRecord:
        with self._lock:
            record = self.operations[key]
            record.state = state
            record.updated_at = int(time.time())
            record.certificate = certificate
            record.error = error
            self.save()
            return record

    def incomplete(self) -> list[OperationRecord]:
        with self._lock:
            return [
                record
                for record in self.operations.values()
                if record.state in ("prepared", "mutating")
            ]

    def save(self) -> None:
        os.makedirs(self.base, mode=0o700, exist_ok=True)
        body = {
            "version": VERSION,
            "operations": [self._dump(record) for record in self.operations.values()],
        }
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".operations-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(body, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.base, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _dump(record: OperationRecord) -> dict[str, Any]:
        body = asdict(record)
        if record.certificate is not None:
            body["certificate"] = record.certificate.model_dump()
        return body


def load(base: str) -> OperationJournal:
    journal = OperationJournal(base=base)
    try:
        with open(journal.path) as handle:
            body: dict[str, Any] = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return journal
    if body.get("version") != VERSION:
        return journal
    for raw in body.get("operations", []):
        try:
            fields = dict(raw)
            planned = [PlannedEdge(**edge) for edge in fields.pop("planned")]
            cert_raw = fields.pop("certificate", None)
            certificate = (
                DeletionCertificate(**cert_raw) if cert_raw is not None else None
            )
            record = OperationRecord(
                **fields,
                planned=planned,
                certificate=certificate,
            )
        except (TypeError, KeyError, ValueError):
            continue
        journal.operations[record.idempotency_key] = record
    return journal
