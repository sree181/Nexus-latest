"""
connectors/base.py — Abstract connector framework for HyperMesh DB.

All upstream data-source connectors inherit from ConnectorBase and implement
three methods: test_connection(), sync(), and preview_schema().

Connector types
---------------
  mde         Microsoft Defender for Endpoint (OAuth2 + REST API)
  s3          Amazon S3 (boto3)
  azure_blob  Azure Blob Storage (azure-storage-blob)
  splunk      Splunk Enterprise / Splunk Cloud (search API)
  elastic     Elasticsearch / OpenSearch (REST query DSL)
  webhook     Inbound HTTP push receiver (FastAPI endpoint)
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# ConnectorConfig — persisted configuration for one connector instance
# ─────────────────────────────────────────────────────────────────────────────

CONNECTOR_TYPES = ("mde", "s3", "azure_blob", "splunk", "elastic", "webhook")

STATUS_IDLE    = "idle"
STATUS_RUNNING = "running"
STATUS_OK      = "ok"
STATUS_ERROR   = "error"


@dataclass
class ConnectorConfig:
    """Persisted configuration for a single upstream connector."""

    id:           str   = field(default_factory=lambda: str(uuid.uuid4()))
    name:         str   = ""
    type:         str   = "webhook"          # one of CONNECTOR_TYPES
    enabled:      bool  = True
    target_table: str   = ""                 # HyperMesh table to ingest into

    # Type-specific secrets (stored on disk; masked in API responses)
    credentials:  dict  = field(default_factory=dict)

    # Type-specific non-secret settings
    settings:     dict  = field(default_factory=dict)

    # Sync history (updated after every run)
    created_at:        str        = field(default_factory=lambda: _now())
    last_sync_at:      str | None = None
    last_sync_status:  str | None = None      # ok | error
    last_sync_count:   int | None = None
    last_sync_elapsed: float | None = None
    last_error:        str | None = None
    total_synced:      int        = 0

    def as_dict(self, mask_secrets: bool = True) -> dict[str, Any]:
        d = asdict(self)
        if mask_secrets and d.get("credentials"):
            d["credentials"] = {k: "***" for k in d["credentials"]}
        return d

    def as_public_dict(self) -> dict[str, Any]:
        """Safe for API responses — secrets masked."""
        return self.as_dict(mask_secrets=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# SyncResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    inserted:  int
    skipped:   int
    errors:    int
    elapsed_s: float
    message:   str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "inserted":  self.inserted,
            "skipped":   self.skipped,
            "errors":    self.errors,
            "elapsed_s": round(self.elapsed_s, 3),
            "message":   self.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ConnectorBase — abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class ConnectorBase(ABC):
    """
    Abstract base for all HyperMesh upstream connectors.

    Subclasses implement:
      test_connection() → dict   {ok, message, latency_ms}
      sync(limit)       → SyncResult
      preview_schema()  → dict   {columns, sample_rows}
    """

    def __init__(self, config: ConnectorConfig, db: Any) -> None:
        self._cfg = config
        self._db  = db

    @property
    def config(self) -> ConnectorConfig:
        return self._cfg

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """
        Verify credentials and reachability.

        Returns
        -------
        dict with keys:
          ok          : bool
          message     : str
          latency_ms  : float
        """

    @abstractmethod
    def sync(self, limit: int = 10_000) -> SyncResult:
        """
        Pull records from the upstream source and insert them as hyperedges.

        Parameters
        ----------
        limit : int
            Maximum number of records to fetch in one sync run.

        Returns
        -------
        SyncResult
        """

    @abstractmethod
    def preview_schema(self, sample_n: int = 20) -> dict[str, Any]:
        """
        Fetch a small sample and return column names + representative rows.

        Returns
        -------
        dict with keys:
          columns     : list[str]
          sample_rows : list[dict]
          total_est   : int | None   (estimated total records available)
        """

    # ── Engine-backed sync (shared by all connectors) ──────────────────────

    def _db_dir(self) -> str:
        import os
        return (
            getattr(self._db, "_db_dir", None)
            or os.environ.get("HMDB_DIR", "/tmp/hypermesh_db")
        )

    def _state_path(self, suffix: str):
        from pathlib import Path
        d = Path(self._db_dir()) / "connectors"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self._cfg.id}_{suffix}"

    def read_cursor(self) -> float:
        """Watermark of the last successful sync (0.0 if none)."""
        p = self._state_path("cursor.json")
        if p.exists():
            try:
                return float(json.loads(p.read_text(encoding="utf-8")).get("cursor", 0.0))
            except Exception:
                return 0.0
        return 0.0

    def _write_cursor(self, cursor: float) -> None:
        try:
            self._state_path("cursor.json").write_text(
                json.dumps({"cursor": cursor}), encoding="utf-8"
            )
        except Exception:
            pass

    def run_engine_sync(self, adapter, *, mode: str = "append") -> "SyncResult":
        """
        Drive the ``hypermesh_ingest`` pipeline for a connector source
        adapter and write the result into the live DB.

        ``adapter.stream() -> build() -> emit_to_connection()`` — giving the
        connector provenance, PII policy, the entity resolver and quarantine.
        Node ids stay stable across syncs via a persisted resolver map; the
        adapter's ``cursor`` (if any) is persisted as the next watermark.
        """
        import tempfile
        import time

        from hypermesh_ingest.builder import AbortThresholdExceeded, build
        from hypermesh_ingest.emitter import emit_to_connection
        from hypermesh_ingest.quarantine import DeadLetterStore
        from hypermesh_ingest.resolver import ResolverStore

        t0 = time.perf_counter()
        spec = adapter.spec
        resolver_path = self._state_path("resolver.parquet")
        resolver = ResolverStore(
            prior_map_path=resolver_path if resolver_path.exists() else None
        )

        with tempfile.TemporaryDirectory() as qdir:
            quarantine = DeadLetterStore(output_dir=qdir)
            try:
                result = build(
                    ir_source=adapter.stream(),
                    spec=spec,
                    resolver=resolver,
                    quarantine=quarantine,
                )
            except AbortThresholdExceeded as exc:
                quarantine.close()
                return SyncResult(0, 0, 1, time.perf_counter() - t0, f"Aborted: {exc}")
            except Exception as exc:
                try:
                    quarantine.close()
                except Exception:
                    pass
                return SyncResult(0, 0, 1, time.perf_counter() - t0, f"Build failed: {exc}")
            quarantine.close()

        if result.hyperedges_out == 0:
            return SyncResult(0, 0, result.quarantined, time.perf_counter() - t0,
                              "No new records to sync")

        emit = emit_to_connection(
            result, resolver, spec, self._db, mode=mode, entity_map_dir=self._db_dir()
        )
        try:
            resolver.save(resolver_path)
        except Exception:
            pass

        cursor = getattr(adapter, "cursor", None)
        if cursor is not None:
            self._write_cursor(float(cursor))

        return SyncResult(
            inserted=emit.inserted,
            skipped=emit.skipped,
            errors=emit.errors + result.quarantined,
            elapsed_s=time.perf_counter() - t0,
            message=f"Synced {emit.inserted} hyperedges into "
                    f"{', '.join(emit.tables) or self._cfg.target_table}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def build_connector(config: ConnectorConfig, db: Any) -> ConnectorBase:
    """Instantiate the right connector subclass from a config object."""
    from hypermeshdb.connectors.mde     import MDEConnector
    from hypermeshdb.connectors.s3      import S3Connector
    from hypermeshdb.connectors.webhook import WebhookConnector

    mapping = {
        "mde":      MDEConnector,
        "s3":       S3Connector,
        "azure_blob": S3Connector,   # S3-compatible interface via endpoint_url
        "webhook":  WebhookConnector,
    }

    cls = mapping.get(config.type)
    if cls is None:
        raise ValueError(f"Unknown connector type: {config.type!r}. Supported: {list(mapping)}")
    return cls(config, db)
