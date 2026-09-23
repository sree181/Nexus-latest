"""
connectors/config_store.py — Persistent connector configuration storage.

Saves each ConnectorConfig as a JSON file in ``{db_dir}/connectors/``.
The store is the single source of truth for connector definitions;
the FastAPI layer reads/writes through this class.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hypermeshdb.connectors.base import ConnectorConfig


class ConnectorStore:
    """
    File-backed CRUD store for ConnectorConfig objects.

    Each connector is persisted as ``{db_dir}/connectors/{id}.json``.
    """

    def __init__(self, db_dir: str) -> None:
        self._dir = Path(db_dir) / "connectors"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def list_all(self) -> list[ConnectorConfig]:
        configs: list[ConnectorConfig] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                raw = json.loads(p.read_text("utf-8"))
                configs.append(ConnectorConfig(**raw))
            except Exception:
                pass
        return configs

    def get(self, connector_id: str) -> ConnectorConfig | None:
        p = self._dir / f"{connector_id}.json"
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text("utf-8"))
            return ConnectorConfig(**raw)
        except Exception:
            return None

    def save(self, config: ConnectorConfig) -> ConnectorConfig:
        p = self._dir / f"{config.id}.json"
        p.write_text(json.dumps(config.as_dict(mask_secrets=False), indent=2), encoding="utf-8")
        return config

    def delete(self, connector_id: str) -> bool:
        p = self._dir / f"{connector_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def update_sync_status(
        self,
        connector_id:  str,
        status:        str,
        count:         int | None = None,
        elapsed_s:     float | None = None,
        error:         str | None = None,
    ) -> None:
        """Update last_sync_* fields after a sync run."""
        from hypermeshdb.connectors.base import _now
        cfg = self.get(connector_id)
        if cfg is None:
            return
        cfg.last_sync_at     = _now()
        cfg.last_sync_status = status
        if count is not None:
            cfg.last_sync_count   = count
            cfg.total_synced     += count
        if elapsed_s is not None:
            cfg.last_sync_elapsed = round(elapsed_s, 3)
        if error is not None:
            cfg.last_error = error
        elif status == "ok":
            cfg.last_error = None
        self.save(cfg)
