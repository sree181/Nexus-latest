"""
rag/symbolic/schema.py — Rule schema, parsing & safety validation.

A rule is a safe (range-restricted) Horn clause with stratified
negation-as-failure, authored as JSON:

    {
      "id": "rule:coordinated_threat",
      "name": "Coordinated threat escalation",
      "version": 1, "priority": 10, "enabled": true,
      "if": [
        {"pred": "formation",   "args": ["?E", "CONVERGENCE"]},
        {"pred": "member",      "args": ["?E", "?A"]},
        {"pred": "member_type", "args": ["?A", "drone"]},
        {"pred": "weight",      "args": ["?E", "?W"]},
        {"compare": ["?W", ">=", 0.8]},
        {"not": {"pred": "cleared", "args": ["?A"]}}
      ],
      "then": {"pred": "coordinated_threat", "args": ["?E"], "confidence": 0.9}
    }

Variables are ``?``-prefixed strings (SPARQL style); everything else (numbers,
unprefixed strings) is a constant. This makes constants like the formation
label ``"CONVERGENCE"`` unambiguous.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Extensional (base) predicates produced from retrieved hyperedges. A rule head
# may NOT redefine these — they are the observed facts the engine reasons from.
EDB_PREDS: frozenset[str] = frozenset(
    {"hyperedge", "formation", "member", "member_type", "weight", "at"}
)

_VAR_RE = re.compile(r"^\?[A-Za-z_][A-Za-z0-9_]*$")
_PRED_RE = re.compile(r"^[a-z_][A-Za-z0-9_]*$")
_CMP_OPS = frozenset({">=", "<=", ">", "<", "==", "!="})


class RuleValidationError(ValueError):
    """Raised when a rule is malformed or unsafe (not range-restricted)."""


def is_var(x: Any) -> bool:
    return isinstance(x, str) and x.startswith("?") and bool(_VAR_RE.match(x))


def _vars_in(args: tuple[Any, ...]) -> set[str]:
    return {a for a in args if is_var(a)}


@dataclass(frozen=True)
class Atom:
    """A positive literal: ``pred(arg, ...)`` with vars and/or constants."""
    pred: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class Compare:
    """An arithmetic/relational guard over bound terms: ``lhs op rhs``."""
    lhs: Any
    op: str
    rhs: Any


@dataclass(frozen=True)
class Neg:
    """Negation-as-failure over a positive atom."""
    atom: Atom


@dataclass
class Head:
    pred: str
    args: tuple[Any, ...]
    confidence: float = 1.0


@dataclass
class Rule:
    id: str
    head: Head
    body: list[Any] = field(default_factory=list)        # Atom | Compare | Neg
    name: str = ""
    version: int = 1
    priority: int = 0
    enabled: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)

    # ── Derived views ────────────────────────────────────────────────────────
    @property
    def positives(self) -> list[Atom]:
        return [b for b in self.body if isinstance(b, Atom)]

    @property
    def compares(self) -> list[Compare]:
        return [b for b in self.body if isinstance(b, Compare)]

    @property
    def negations(self) -> list[Neg]:
        return [b for b in self.body if isinstance(b, Neg)]

    @property
    def body_pos_preds(self) -> set[str]:
        return {a.pred for a in self.positives}

    @property
    def body_neg_preds(self) -> set[str]:
        return {n.atom.pred for n in self.negations}

    def as_dict(self) -> dict[str, Any]:
        body: list[dict[str, Any]] = []
        for b in self.body:
            if isinstance(b, Atom):
                body.append({"pred": b.pred, "args": list(b.args)})
            elif isinstance(b, Compare):
                body.append({"compare": [b.lhs, b.op, b.rhs]})
            elif isinstance(b, Neg):
                body.append({"not": {"pred": b.atom.pred, "args": list(b.atom.args)}})
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "priority": self.priority,
            "enabled": self.enabled,
            "if": body,
            "then": {"pred": self.head.pred, "args": list(self.head.args),
                     "confidence": self.head.confidence},
            "provenance": self.provenance,
        }


# ── Parsing ─────────────────────────────────────────────────────────────────

def _parse_atom(d: Any, where: str) -> Atom:
    if not isinstance(d, dict) or "pred" not in d:
        raise RuleValidationError(f"{where}: expected an atom with a 'pred' field")
    pred = d["pred"]
    if not isinstance(pred, str) or not _PRED_RE.match(pred):
        raise RuleValidationError(f"{where}: invalid predicate name {pred!r} (use snake_case)")
    args = d.get("args", [])
    if not isinstance(args, list):
        raise RuleValidationError(f"{where}: 'args' must be a list")
    return Atom(pred=pred, args=tuple(args))


def parse_rule(d: dict[str, Any]) -> Rule:
    """Parse + validate a rule dict. Raises RuleValidationError if unsafe."""
    if not isinstance(d, dict):
        raise RuleValidationError("rule must be a JSON object")

    rid = d.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise RuleValidationError("rule 'id' is required and must be a non-empty string")

    raw_body = d.get("if", d.get("body"))
    if not isinstance(raw_body, list) or not raw_body:
        raise RuleValidationError(f"{rid}: 'if' must be a non-empty list of body literals")

    raw_head = d.get("then", d.get("head"))
    if not isinstance(raw_head, dict):
        raise RuleValidationError(f"{rid}: 'then' (head) is required")

    body: list[Any] = []
    for i, lit in enumerate(raw_body):
        where = f"{rid}.if[{i}]"
        if not isinstance(lit, dict):
            raise RuleValidationError(f"{where}: body literal must be an object")
        if "compare" in lit:
            cmp = lit["compare"]
            if not (isinstance(cmp, list) and len(cmp) == 3):
                raise RuleValidationError(f"{where}: 'compare' must be [lhs, op, rhs]")
            lhs, op, rhs = cmp
            if op not in _CMP_OPS:
                raise RuleValidationError(f"{where}: compare op {op!r} not in {sorted(_CMP_OPS)}")
            body.append(Compare(lhs=lhs, op=op, rhs=rhs))
        elif "not" in lit:
            body.append(Neg(atom=_parse_atom(lit["not"], f"{where}.not")))
        else:
            atom = _parse_atom(lit, where)
            if atom.pred in EDB_PREDS or True:  # positive atoms may match any pred
                body.append(atom)

    head_pred = raw_head.get("pred")
    if not isinstance(head_pred, str) or not _PRED_RE.match(head_pred):
        raise RuleValidationError(f"{rid}: head 'pred' invalid (use snake_case)")
    if head_pred in EDB_PREDS:
        raise RuleValidationError(
            f"{rid}: head predicate {head_pred!r} is a reserved base predicate "
            f"(one of {sorted(EDB_PREDS)}); a rule may only derive new predicates"
        )
    head_args = raw_head.get("args", [])
    if not isinstance(head_args, list):
        raise RuleValidationError(f"{rid}: head 'args' must be a list")
    try:
        head_conf = float(raw_head.get("confidence", 1.0))
    except (TypeError, ValueError):
        raise RuleValidationError(f"{rid}: head 'confidence' must be a number")
    if not (0.0 <= head_conf <= 1.0):
        raise RuleValidationError(f"{rid}: head 'confidence' must be in [0, 1]")

    rule = Rule(
        id=rid,
        head=Head(pred=head_pred, args=tuple(head_args), confidence=head_conf),
        body=body,
        name=str(d.get("name", "")),
        version=int(d.get("version", 1)),
        priority=int(d.get("priority", 0)),
        enabled=bool(d.get("enabled", True)),
        provenance=dict(d.get("provenance", {})) if isinstance(d.get("provenance"), dict) else {},
    )
    _check_safety(rule)
    return rule


def _check_safety(rule: Rule) -> None:
    """Range-restriction: every head/compare/negation var must be bound by a
    positive body atom. Guarantees a finite, well-defined derivation."""
    pos_vars: set[str] = set()
    for a in rule.positives:
        pos_vars |= _vars_in(a.args)

    if not rule.positives:
        raise RuleValidationError(f"{rule.id}: body needs at least one positive atom")

    head_vars = _vars_in(rule.head.args)
    unbound_head = head_vars - pos_vars
    if unbound_head:
        raise RuleValidationError(
            f"{rule.id}: head variables {sorted(unbound_head)} not bound by any "
            f"positive body atom (unsafe rule)"
        )

    for c in rule.compares:
        for term in (c.lhs, c.rhs):
            if is_var(term) and term not in pos_vars:
                raise RuleValidationError(
                    f"{rule.id}: compare variable {term!r} not bound by a positive atom"
                )

    for n in rule.negations:
        nv = _vars_in(n.atom.args)
        unbound = nv - pos_vars
        if unbound:
            raise RuleValidationError(
                f"{rule.id}: negated variables {sorted(unbound)} not bound by a "
                f"positive atom (unsafe negation)"
            )
