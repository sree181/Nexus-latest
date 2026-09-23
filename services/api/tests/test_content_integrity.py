from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

engine_gateway = pytest.importorskip("app.engine_gateway")

from app import engine_seed  # noqa: E402
from hypermeshdb import HyperMeshError  # noqa: E402
from hypermeshdb.agentmem import Kind, Origin, Status  # noqa: E402


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHAGENT_DB_DIR", str(tmp_path / "deployment"))
    return engine_seed.new_store("integrity-test")


def write_memory(store):
    return store.write(
        Kind.FACT,
        ["fact:one"],
        origin=Origin.AGENT,
        status=Status.VERIFIED,
        source="agent:test",
        payload={"statement": "original evidence"},
    )


def content_db(store) -> str:
    return store._content._path  # the test intentionally simulates disk tampering


def test_payload_tampering_fails_closed(store):
    ulid = write_memory(store)
    conn = sqlite3.connect(content_db(store))
    conn.execute(
        "UPDATE content SET payload = ? WHERE ulid = ?",
        (json.dumps({"statement": "rewritten evidence"}), ulid),
    )
    conn.commit()
    conn.close()

    with pytest.raises(HyperMeshError, match="payload hash differs"):
        store.get(ulid)


def test_sidecar_hash_tampering_fails_closed(store):
    ulid = write_memory(store)
    conn = sqlite3.connect(content_db(store))
    conn.execute("UPDATE content SET sha = ? WHERE ulid = ?", ("0" * 64, ulid))
    conn.commit()
    conn.close()

    with pytest.raises(HyperMeshError, match="graph and sidecar hashes differ"):
        store.get(ulid)


def test_legitimate_redaction_preserves_verifiable_hash(store):
    ulid = write_memory(store)
    before = store.get(ulid)
    store.tombstone(ulid, reason="test", actor="analyst@example.com")

    after = store.get(ulid)

    assert before is not None and after is not None
    assert after.redacted and after.tombstoned
    assert after.content is None
    assert after.envelope.content_sha == before.envelope.content_sha


def test_graph_tombstone_completes_an_interrupted_sidecar_redaction(store):
    ulid = write_memory(store)
    with patch.object(store._content, "redact", return_value=False):
        store.tombstone(ulid, reason="test", actor="analyst@example.com")
    payload, _, redacted = store._content.get(ulid)
    assert payload is not None and not redacted

    recovered = store.get(ulid)

    assert recovered is not None
    assert recovered.tombstoned and recovered.redacted
    assert recovered.content is None


def test_sidecar_redaction_without_a_graph_tombstone_fails_closed(store):
    ulid = write_memory(store)
    store._content.redact(ulid)

    with pytest.raises(HyperMeshError, match="missing without a tombstone"):
        store.get(ulid)
