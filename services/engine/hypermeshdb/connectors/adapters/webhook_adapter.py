"""
Webhook-queue source adapter.

The webhook receiver enqueues pushed payloads as newline-delimited JSON at
``{db_dir}/connectors/{id}_queue.jsonl``.  This adapter streams that queue
file as IR.  Queue rotation (archiving the processed file) stays in the
connector's ``sync()`` so it only happens after a successful build+emit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from hypermesh_ingest.adapters.base import BaseAdapter, IRRecord
from hypermesh_ingest.spec.schema import MappingSpec

from ._records import prebuilt_to_ir, records_to_ir


class WebhookQueueAdapter(BaseAdapter):
    def __init__(
        self,
        *,
        queue_path: str | Path,
        settings: dict,
        spec: MappingSpec,
        target_table: str,
        limit: int = 50_000,
    ) -> None:
        super().__init__(spec)
        self._queue_path = Path(queue_path)
        self._settings = settings or {}
        self._target = target_table
        self._limit = limit

    @property
    def source_id(self) -> str:
        return f"webhook://{self._target}"

    def _read_queue(self) -> list[dict]:
        if not self._queue_path.exists():
            return []
        records: list[dict] = []
        with self._queue_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
                if len(records) >= self._limit:
                    break
        return records

    def stream(self) -> Iterator[IRRecord]:
        records = self._read_queue()
        if not records:
            return
        # Pre-built (members list already present) vs free-form JSON.
        if isinstance(records[0].get("members"), list):
            yield from prebuilt_to_ir(
                records, target_table=self._target, source_id=self.source_id, spec=self.spec,
            )
            return

        # Free-form: auto-detect entity columns via the shared loader.
        from hypermeshdb.ingest.generic import infer_schema

        override = self._settings.get("ingest_config") or {}
        schema = infer_schema(records, sample_n=min(200, len(records)))
        entity_cols = override.get("entity_columns") or schema.suggested_entity_cols
        ts_col = override.get("ts_column", schema.suggested_ts_col or "")
        etypes = override.get("entity_types") or {
            c.name: c.entity_type for c in schema.columns if c.role == "entity"
        }
        yield from records_to_ir(
            records,
            entity_cols=entity_cols,
            ts_col=ts_col or None,
            target_table=self._target,
            source_id=self.source_id,
            spec=self.spec,
            entity_types=etypes,
        )
