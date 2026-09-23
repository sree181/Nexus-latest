from __future__ import annotations

import pytest

engine_gateway = pytest.importorskip("app.engine_gateway")

from app import engine_seed, operations  # noqa: E402
from app.gateway import Conflict  # noqa: E402


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_ENV", "test")
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "deployment"))
    return lambda: engine_gateway.EngineGateway()


def test_idempotent_forget_returns_the_same_certificate_after_restart(deployment):
    first = deployment()
    preview = first.forget_preview(engine_seed.RUN_ID, "source:poisoned-mirror")
    issued = first.run_forget(
        engine_seed.RUN_ID,
        "source:poisoned-mirror",
        "remove poisoned source",
        actor="analyst@example.com",
        expected_version=preview.version,
        idempotency_key="forget-restart-1",
    )

    restarted = deployment()
    replayed = restarted.run_forget(
        engine_seed.RUN_ID,
        "source:poisoned-mirror",
        "remove poisoned source",
        actor="analyst@example.com",
        expected_version=preview.version,
        idempotency_key="forget-restart-1",
    )

    assert replayed == issued
    assert restarted.fleet_overview().deletion_certificates == 1


def test_reusing_an_idempotency_key_for_a_different_request_is_a_conflict(deployment):
    gateway = deployment()
    preview = gateway.forget_preview(engine_seed.RUN_ID, "source:poisoned-mirror")
    gateway.run_forget(
        engine_seed.RUN_ID,
        "source:poisoned-mirror",
        "remove poisoned source",
        actor="analyst@example.com",
        expected_version=preview.version,
        idempotency_key="forget-conflict-1",
    )

    with pytest.raises(Conflict):
        gateway.run_forget(
            engine_seed.RUN_ID,
            "source:poisoned-mirror",
            "a different reason",
            actor="analyst@example.com",
            expected_version=preview.version,
            idempotency_key="forget-conflict-1",
        )


def test_startup_finishes_a_prepared_partial_deletion(deployment):
    first = deployment()
    run = first._run(engine_seed.RUN_ID)
    preview = first.forget_preview(engine_seed.RUN_ID, "source:poisoned-mirror")
    planned: list[operations.PlannedEdge] = []
    for doomed in preview.doomed:
        memory = run.store.get(doomed.ulid)
        assert memory is not None
        planned.append(operations.PlannedEdge(
            ulid=doomed.ulid,
            entity=doomed.entity,
            content_sha=memory.envelope.content_sha,
        ))
    operation = operations.OperationRecord(
        idempotency_key="forget-interrupted-1",
        kind="forget",
        run_id=engine_seed.RUN_ID,
        node="source:poisoned-mirror",
        root=preview.root,
        reason="remove poisoned source",
        actor="analyst@example.com",
        expected_version=preview.version,
        planned=planned,
    )
    first._operations.prepare(operation)
    first._operations.transition(operation.idempotency_key, "mutating")
    # Simulate process loss after the first graph tombstone but before the
    # remaining closure, certificate, and operation commit.
    run.store.tombstone(
        planned[0].ulid,
        reason=operation.reason,
        actor=operation.actor,
    )

    restarted = deployment()

    recovered = restarted._operations.get(operation.idempotency_key)
    assert recovered is not None
    assert recovered.state == "committed"
    assert recovered.certificate is not None
    assert recovered.certificate.purged_count == len(planned)
    for edge in planned:
        memory = restarted._run(engine_seed.RUN_ID).store.get(edge.ulid)
        assert memory is not None and memory.tombstoned and memory.redacted


def test_startup_reuses_a_certificate_persisted_before_journal_commit(deployment):
    first = deployment()
    preview = first.forget_preview(engine_seed.RUN_ID, "source:poisoned-mirror")
    issued = first.run_forget(
        engine_seed.RUN_ID,
        "source:poisoned-mirror",
        "remove poisoned source",
        actor="analyst@example.com",
        expected_version=preview.version,
        idempotency_key="forget-after-certificate-1",
    )
    # Simulate a process that persisted the certificate but lost the final
    # journal transition.
    first._operations.transition("forget-after-certificate-1", "mutating")

    restarted = deployment()

    recovered = restarted._operations.get("forget-after-certificate-1")
    assert recovered is not None
    assert recovered.state == "committed"
    assert recovered.certificate == issued
    assert restarted.fleet_overview().deletion_certificates == 1
