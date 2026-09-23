"""
connectors/webhook.py — Inbound HTTP push connector.

This connector is special: it does not *pull* data but instead maintains a
persistent queue of events that have been POSTed to the webhook endpoint at
``POST /v1/connectors/webhook/{table}``.

Payload formats accepted
------------------------
1. Single event (dict):
   { "members": [1, 2, 3], "ts": 1700000000, "props": {...} }

2. Batch of events (list):
   [ { "members": [...], "ts": ..., "props": {...} }, ... ]

3. Free-form JSON (auto-schema):
   { "machineId": "abc", "processName": "cmd.exe", "timestamp": "2024-01-01T..." }
   Entity columns are auto-detected and resolved via schema inference.

4. NDJSON (newline-delimited JSON) — one record per line.

Auth
----
Optional bearer token check.  Configure via:
  ConnectorConfig.credentials.webhook_secret = "my-shared-secret"
Sender must include: ``Authorization: Bearer my-shared-secret``

Queue-based sync
----------------
Incoming payloads are appended to a JSON Lines file:
  ``{db_dir}/connectors/{id}_queue.jsonl``

``sync()`` flushes the queue into the target HyperMesh table and rotates
the file.

test_connection()
-----------------
Webhooks are always "connected" by definition; test_connection() checks that
the queue file is accessible and returns the current queue depth.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from hypermeshdb.connectors.base import ConnectorBase, ConnectorConfig, SyncResult


class WebhookConnector(ConnectorBase):
    """
    Inbound HTTP push connector — receives events via POST and queues them
    for batch ingestion into HyperMesh.
    """

    # ── Queue file path ────────────────────────────────────────────────────

    def _queue_path(self) -> Path:
        db_dir = (
            getattr(self._db, "_db_dir", None)
            or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
        )
        return Path(db_dir) / "connectors" / f"{self._cfg.id}_queue.jsonl"

    # ── Ingestion helpers ──────────────────────────────────────────────────

    def _normalise_payload(self, payload: Any) -> list[dict]:
        """
        Convert any accepted payload shape into a list of raw record dicts.
        Returns records suitable for either direct hyperedge insert or
        schema-inference-based insert.
        """
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    def _is_pre_built(self, record: dict) -> bool:
        """True when the record already carries a 'members' list."""
        return isinstance(record.get("members"), list)

    # ── Public: enqueue ────────────────────────────────────────────────────

    def enqueue(self, payload: Any) -> int:
        """
        Called by the FastAPI webhook endpoint to persist incoming events.

        Parameters
        ----------
        payload : dict | list
            Parsed JSON body of the incoming HTTP request.

        Returns
        -------
        int
            Number of records enqueued.
        """
        records = self._normalise_payload(payload)
        if not records:
            return 0
        qp = self._queue_path()
        qp.parent.mkdir(parents=True, exist_ok=True)
        with qp.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return len(records)

    def queue_depth(self) -> int:
        """Return number of enqueued records awaiting sync."""
        qp = self._queue_path()
        if not qp.exists():
            return 0
        try:
            return sum(1 for _ in qp.open("r", encoding="utf-8"))
        except Exception:
            return 0

    # ── ConnectorBase interface ────────────────────────────────────────────

    def test_connection(self) -> dict[str, Any]:
        depth = self.queue_depth()
        qp    = self._queue_path()
        return {
            "ok":          True,
            "message":     f"Webhook receiver ready — {depth} record(s) queued",
            "latency_ms":  0,
            "queue_depth": depth,
            "queue_path":  str(qp),
            "endpoint":    f"/v1/connectors/webhook/{self._cfg.target_table or self._cfg.id}",
        }

    def preview_schema(self, sample_n: int = 5) -> dict[str, Any]:
        from hypermeshdb.ingest.generic import infer_schema
        qp = self._queue_path()
        if not qp.exists():
            return {"columns": [], "sample_rows": [], "total_est": 0, "error": "Queue is empty"}
        records: list[dict] = []
        with qp.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
                if len(records) >= sample_n:
                    break
        if not records:
            return {"columns": [], "sample_rows": [], "total_est": 0}
        if self._is_pre_built(records[0]):
            cols = list(records[0].keys())
            return {"columns": cols, "sample_rows": records[:5], "total_est": len(records)}
        schema = infer_schema(records, sample_n=sample_n)
        return {
            "columns":     [c.name for c in schema.columns],
            "sample_rows": records[:5],
            "total_est":   self.queue_depth(),
        }

    def sync(self, limit: int = 50_000) -> SyncResult:
        """Flush the webhook queue through the hypermesh_ingest pipeline."""
        from hypermeshdb.connectors.adapters import WebhookQueueAdapter
        from hypermeshdb.connectors.spec_bridge import connector_spec

        qp = self._queue_path()
        if not qp.exists() or qp.stat().st_size == 0:
            return SyncResult(0, 0, 0, 0.0, "Queue is empty — nothing to sync")

        spec = connector_spec(self._cfg)
        adapter = WebhookQueueAdapter(
            queue_path=qp,
            settings=self._cfg.settings,
            spec=spec,
            target_table=(self._cfg.target_table or "WEBHOOK_EVENTS").upper(),
            limit=limit,
        )
        result = self.run_engine_sync(adapter, mode="append")

        # Rotate the queue only after a clean pipeline run, so a build
        # failure leaves the queue intact for the next sync.
        if not result.message.startswith(("Build failed", "Aborted")):
            archive = qp.with_suffix(f".{int(time.time())}.jsonl.bak")
            try:
                qp.rename(archive)
            except Exception:
                try:
                    qp.write_text("", encoding="utf-8")
                except Exception:
                    pass
        return result
