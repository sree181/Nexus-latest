"""
rag/symbolic/store.py — durable rule store.

Rules live in a JSON registry sidecar (``{db_dir}/rag_rules.json``), the same
pattern HyperMesh already uses for ``rag_models.json`` and ingest provenance.
Every mutation re-validates the *whole* ruleset for stratifiability, so the
store can never hold a ruleset the reasoner would reject.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from .reasoner import StratificationError, stratify
from .schema import Rule, RuleValidationError, parse_rule

_REGISTRY_NAME = "rag_rules.json"


class RuleStore:
    def __init__(self, db_dir: str = "data") -> None:
        self._db_dir = db_dir or "data"
        self._path = os.path.join(self._db_dir, _REGISTRY_NAME)

    @property
    def path(self) -> str:
        return self._path

    # ── persistence ────────────────────────────────────────────────────────────
    def _load_raw(self) -> list[dict[str, Any]]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            return []
        rules = data.get("rules", []) if isinstance(data, dict) else []
        return [r for r in rules if isinstance(r, dict)]

    def _save_raw(self, rules: list[dict[str, Any]]) -> None:
        os.makedirs(self._db_dir, exist_ok=True)
        payload = {"version": 1, "rules": rules}
        fd, tmp = tempfile.mkstemp(dir=self._db_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── validation ─────────────────────────────────────────────────────────────
    def validate(self, rule_dict: dict[str, Any]) -> dict[str, Any]:
        """Validate a single rule in the context of the current ruleset.
        Returns {ok, errors, stratum?}."""
        try:
            rule = parse_rule(rule_dict)
        except RuleValidationError as exc:
            return {"ok": False, "errors": [str(exc)]}

        others = [parse_rule(r) for r in self._load_raw() if r.get("id") != rule.id]
        try:
            strata = stratify(others + [rule])
        except StratificationError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "errors": [], "stratum": strata.get(rule.head.pred, 0)}

    # ── CRUD ───────────────────────────────────────────────────────────────────
    def upsert(self, rule_dict: dict[str, Any]) -> dict[str, Any]:
        rule = parse_rule(rule_dict)                # raises RuleValidationError
        rows = self._load_raw()
        others = [parse_rule(r) for r in rows if r.get("id") != rule.id]
        try:
            strata = stratify(others + [rule])      # raises StratificationError
        except StratificationError as exc:
            raise RuleValidationError(str(exc)) from exc

        new_rows = [r for r in rows if r.get("id") != rule.id]
        new_rows.append(rule.as_dict())
        self._save_raw(new_rows)
        out = rule.as_dict()
        out["stratum"] = strata.get(rule.head.pred, 0)
        return out

    def get(self, rule_id: str) -> dict[str, Any] | None:
        for r in self._load_raw():
            if r.get("id") == rule_id:
                return r
        return None

    def list(self) -> list[dict[str, Any]]:
        out = []
        for r in self._load_raw():
            head = r.get("then", r.get("head", {})) or {}
            out.append({
                "id": r.get("id"),
                "name": r.get("name", ""),
                "enabled": bool(r.get("enabled", True)),
                "priority": int(r.get("priority", 0)),
                "version": int(r.get("version", 1)),
                "head": head.get("pred"),
                "body_size": len(r.get("if", r.get("body", []))),
            })
        return out

    def delete(self, rule_id: str) -> bool:
        rows = self._load_raw()
        new_rows = [r for r in rows if r.get("id") != rule_id]
        if len(new_rows) == len(rows):
            return False
        self._save_raw(new_rows)
        return True

    # ── parsed views (for the reasoner) ──────────────────────────────────────────
    def all_rules(self) -> list[Rule]:
        return [parse_rule(r) for r in self._load_raw()]

    def enabled_rules(self) -> list[Rule]:
        return [r for r in self.all_rules() if r.enabled]
