"""
hypermeshdb.ingest.strategies.base
====================================
Abstract base class every ingestion strategy must implement.

A *strategy* is a reusable, parameterised algorithm that transforms
source data (Excel files, existing tables, …) into a set of hyperedges
and inserts them into a named HyperMesh table.

Each strategy self-describes its parameters so the UI can render a
dynamic form without any hard-coded knowledge.
"""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class ParamSpec:
    """Descriptor for a single configurable parameter."""
    name:         str
    label:        str
    type:         str          # "string" | "number" | "integer" | "boolean" | "file_list"
    default:      Any
    description:  str = ""
    required:     bool = False
    min:          Optional[float] = None
    max:          Optional[float] = None
    options:      list[str] = field(default_factory=list)   # for "enum" type


@dataclass
class StrategyResult:
    table:           str
    entities:        int
    hyperedges:      int
    errors:          int
    formations:      list[str]
    entity_map_path: str
    elapsed_s:       float
    notes:           list[str] = field(default_factory=list)


ProgressCallback = Callable[[float, str], None]   # (0.0–1.0, message)


class IngestStrategy(ABC):
    """
    Base class for all ingestion strategies.

    Subclasses must define:
        name        — machine-readable identifier (snake_case)
        label       — human-readable title shown in the UI
        description — one-sentence explanation for strategy cards
        category    — grouping tag ("patent", "generic", "network", …)
        param_specs — ordered list of :class:`ParamSpec` objects

    And implement:
        run(config, conn, db_dir, progress_cb) -> StrategyResult
    """

    name:        str = ""
    label:       str = ""
    description: str = ""
    category:    str = "generic"
    param_specs: list[ParamSpec] = []

    # Maximum members per hyperedge (mirrors HM_MAX_MEMBERS in C)
    MAX_MEMBERS: int = 64

    # ─── helpers ──────────────────────────────────────────────────────────────

    def schema(self) -> dict:
        """Return JSON-serialisable description for the UI form builder."""
        return {
            "name":        self.name,
            "label":       self.label,
            "description": self.description,
            "category":    self.category,
            "params": [
                {
                    "name":        p.name,
                    "label":       p.label,
                    "type":        p.type,
                    "default":     p.default,
                    "description": p.description,
                    "required":    p.required,
                    **({"min": p.min} if p.min is not None else {}),
                    **({"max": p.max} if p.max is not None else {}),
                    **({"options": p.options} if p.options else {}),
                }
                for p in self.param_specs
            ],
        }

    def split_hedge(self, h: dict) -> list[dict]:
        """Split a hyperedge that exceeds MAX_MEMBERS into valid chunks."""
        if len(h["members"]) <= self.MAX_MEMBERS:
            return [h]
        anchor    = h["members"][0]
        rest      = h["members"][1:]
        chunk_sz  = self.MAX_MEMBERS - 1
        return [
            {**h, "members": [anchor] + rest[i : i + chunk_sz]}
            for i in range(0, len(rest), chunk_sz)
        ]

    def drop_table_directory(self, db_dir: str, table: str) -> None:
        """
        Physically remove the entire table directory (WAL + compacted files).

        The SDK's DROP HYPEREDGE TABLE only clears the WAL, leaving
        compacted ``hyperedges.bin`` / FMI files in place, which causes
        double-counting on the next compact.  Deleting the directory is the
        only safe way to start from a clean slate.
        """
        table_dir = Path(db_dir) / table.upper()
        if table_dir.exists():
            shutil.rmtree(str(table_dir))

    def write_entity_map(self, entity_meta: dict[int, dict], db_dir: str, table: str) -> str:
        """Persist the entity map JSON and return its path."""
        path = Path(db_dir) / f"{table}_entity_map.json"
        payload = {str(eid): meta for eid, meta in entity_meta.items()}
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        return str(path)

    # ─── abstract ─────────────────────────────────────────────────────────────

    @abstractmethod
    def run(
        self,
        config:      dict,
        conn:        Any,
        db_dir:      str,
        progress_cb: ProgressCallback = lambda p, m: None,
    ) -> StrategyResult:
        """
        Execute the strategy.

        Parameters
        ----------
        config       : validated parameter dict (keys = ParamSpec.name)
        conn         : open hypermeshdb.Connection instance
        db_dir       : absolute path to the DB directory
        progress_cb  : callable(fraction: float, message: str)
        """
