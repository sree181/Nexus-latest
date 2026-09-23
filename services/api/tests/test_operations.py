from __future__ import annotations

import json
import os

from app.models import DeletionCertificate, PurgedEdge
from app.operations import OperationJournal, OperationRecord, PlannedEdge, load


def record(key: str = "request-1") -> OperationRecord:
    return OperationRecord(
        idempotency_key=key,
        kind="forget",
        run_id="run-1",
        node="source:untrusted",
        root="01JROOT0000000000000000000",
        reason="remove untrusted source",
        actor="analyst@example.com",
        expected_version="abc123",
        planned=[
            PlannedEdge(
                ulid="01JROOT0000000000000000000",
                entity="source:untrusted",
                content_sha="a" * 64,
            )
        ],
    )


def certificate() -> DeletionCertificate:
    return DeletionCertificate(
        run_id="run-1",
        node="source:untrusted",
        root="01JROOT0000000000000000000",
        reason="remove untrusted source",
        actor="analyst@example.com",
        issued_at=100,
        purged_count=1,
        classes_pruned=[],
        retained_hash=DeletionCertificate.digest(["a" * 64]),
        purged=[
            PurgedEdge(
                ulid="01JROOT0000000000000000000",
                entity="source:untrusted",
                content_sha_retained="a" * 64,
            )
        ],
    )


def test_prepared_operation_survives_restart(tmp_path):
    journal = OperationJournal(str(tmp_path))
    journal.prepare(record())

    reopened = load(str(tmp_path))

    operation = reopened.get("request-1")
    assert operation is not None
    assert operation.state == "prepared"
    assert operation.planned[0].content_sha == "a" * 64
    assert reopened.incomplete() == [operation]


def test_committed_certificate_is_restart_safe(tmp_path):
    journal = OperationJournal(str(tmp_path))
    journal.prepare(record())
    journal.transition("request-1", "mutating")
    journal.transition("request-1", "committed", certificate=certificate())

    reopened = load(str(tmp_path))

    operation = reopened.get("request-1")
    assert operation is not None
    assert operation.state == "committed"
    assert operation.certificate == certificate()
    assert reopened.incomplete() == []


def test_prepare_is_idempotent_and_does_not_replace_the_first_request(tmp_path):
    journal = OperationJournal(str(tmp_path))
    first = journal.prepare(record())
    replay = record()
    replay.reason = "different request"

    assert journal.prepare(replay) is first
    assert journal.get("request-1").reason == "remove untrusted source"  # type: ignore[union-attr]


def test_journal_is_private_and_versioned(tmp_path):
    journal = OperationJournal(str(tmp_path))
    journal.prepare(record())

    assert os.stat(journal.path).st_mode & 0o777 == 0o600
    with open(journal.path) as handle:
        body = json.load(handle)
    assert body["version"] == 1


def test_unknown_future_version_fails_closed_to_an_empty_journal(tmp_path):
    journal = OperationJournal(str(tmp_path))
    journal.prepare(record())
    with open(journal.path) as handle:
        body = json.load(handle)
    body["version"] = 999
    with open(journal.path, "w") as handle:
        json.dump(body, handle)

    assert load(str(tmp_path)).operations == {}
