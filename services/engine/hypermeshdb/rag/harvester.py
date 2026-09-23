"""
rag/harvester.py — Real-Query Interaction Harvester

Automatically saves every RAG pipeline interaction to disk in fine-tune format.
Users can mark answers as good (👍) or bad (👎) via the API. Only positively-
rated examples are included in the training export.

Storage
-------
  {db_dir}/rag_training/interactions.jsonl   — all interactions (raw)
  {db_dir}/rag_training/approved.jsonl       — 👍 approved examples only
  {db_dir}/rag_training/rejected.jsonl       — 👎 rejected (for DPO training)

Each line is:
  {
    "id":          "<uuid>",
    "timestamp":   "<iso>",
    "table":       "THREATEVENTS",
    "query":       "...",
    "answer":      "...",
    "context":     "...",           # the full hyperedge context block
    "model":       "gpt-4o-mini",
    "confidence":  0.85,
    "cited_edges": [12, 47, 103],
    "rating":      null | "good" | "bad",
    "chatml":      {...}            # ready-to-use fine-tune record
  }
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class InteractionHarvester:
    """
    Saves RAG interactions to disk and manages feedback ratings.

    Parameters
    ----------
    db_dir :
        Root database directory.
    """

    def __init__(self, db_dir: str = "data") -> None:
        self._dir = Path(db_dir) / "rag_training"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._interactions_path = self._dir / "interactions.jsonl"
        self._approved_path     = self._dir / "approved.jsonl"
        self._rejected_path     = self._dir / "rejected.jsonl"

    # ── Save an interaction ───────────────────────────────────────────────────

    def save(
        self,
        *,
        table:        str,
        query:        str,
        answer:       str,
        context:      str,
        system_prompt: str,
        model:        str,
        confidence:   float,
        cited_edges:  list[int],
    ) -> str:
        """
        Persist an interaction and return its interaction_id.
        """
        interaction_id = str(uuid.uuid4())
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        chatml = {
            "messages": [
                {"role": "system",    "content": system_prompt},
                {"role": "user",      "content": f"{context}\n\n---\n\nQuestion: {query}"},
                {"role": "assistant", "content": answer},
            ]
        }

        record = {
            "id":          interaction_id,
            "timestamp":   ts,
            "table":       table,
            "query":       query,
            "answer":      answer,
            "context":     context,
            "model":       model,
            "confidence":  round(confidence, 3),
            "cited_edges": cited_edges,
            "rating":      None,
            "chatml":      chatml,
        }

        with open(self._interactions_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        log.debug("Harvested interaction %s (table=%s, conf=%.2f)", interaction_id, table, confidence)
        return interaction_id

    # ── Apply feedback ────────────────────────────────────────────────────────

    def rate(self, interaction_id: str, rating: str) -> dict[str, Any]:
        """
        Mark an interaction as 'good' or 'bad'.

        Updates the interactions file in place and appends the example
        to the appropriate output file.

        Parameters
        ----------
        interaction_id : UUID string from ``save()``.
        rating : "good" | "bad"

        Returns
        -------
        The updated interaction record.
        """
        if rating not in ("good", "bad"):
            raise ValueError("rating must be 'good' or 'bad'")

        # Read all interactions, find and update the target
        records: list[dict] = []
        target: dict | None = None

        if self._interactions_path.exists():
            with open(self._interactions_path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    if rec["id"] == interaction_id:
                        rec["rating"] = rating
                        target = rec
                    records.append(rec)

        if target is None:
            raise KeyError(f"Interaction {interaction_id!r} not found")

        # Rewrite interactions file
        with open(self._interactions_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Append to the appropriate silo
        dest = self._approved_path if rating == "good" else self._rejected_path
        with open(dest, "a", encoding="utf-8") as f:
            f.write(json.dumps(target["chatml"], ensure_ascii=False) + "\n")

        return target

    # ── Stats + export ────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return counts of interactions, approved, rejected."""
        def count_lines(path: Path) -> int:
            if not path.exists():
                return 0
            with open(path, encoding="utf-8") as f:
                return sum(1 for _ in f)

        total    = count_lines(self._interactions_path)
        approved = count_lines(self._approved_path)
        rejected = count_lines(self._rejected_path)
        unrated  = total - approved - rejected

        return {
            "total":    total,
            "approved": approved,
            "rejected": rejected,
            "unrated":  unrated,
            "approved_path": str(self._approved_path),
            "rejected_path": str(self._rejected_path),
        }

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the N most recent interactions (newest first)."""
        records: list[dict] = []
        if self._interactions_path.exists():
            with open(self._interactions_path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    # Exclude raw context (too large for UI lists)
                    rec.pop("context", None)
                    rec.pop("chatml",  None)
                    records.append(rec)
        return list(reversed(records))[-n:]

    def export_training_jsonl(self, out_path: str | None = None) -> str:
        """
        Export approved examples to a final fine-tune JSONL file.

        Merges approved human interactions with any synthetically-generated examples
        found in the same rag_training/ directory.
        """
        dest = Path(out_path) if out_path else self._dir / "final_training.jsonl"
        count = 0
        with open(dest, "w", encoding="utf-8") as f_out:
            # 1. Approved human interactions
            if self._approved_path.exists():
                with open(self._approved_path, encoding="utf-8") as f:
                    for line in f:
                        f_out.write(line)
                        count += 1
            # 2. Synthetic data from synthesizer
            for synthetic in sorted(self._dir.glob("*_training.jsonl")):
                with open(synthetic, encoding="utf-8") as f:
                    for line in f:
                        f_out.write(line)
                        count += 1

        log.info("Exported %d training examples to %s", count, dest)
        return str(dest)
