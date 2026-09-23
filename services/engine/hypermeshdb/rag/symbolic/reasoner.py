"""
rag/symbolic/reasoner.py — stratified forward-chaining reasoner.

Pure, deterministic, LLM-free. Given a base ``FactBase`` (from retrieved
hyperedges) and a set of rules, it derives new facts to a fixpoint, stratum by
stratum, honouring negation-as-failure. Termination is guaranteed: there are no
function symbols, the Herbrand base is finite, and derivation is monotone.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .facts import Fact, FactBase
from .schema import EDB_PREDS, Atom, Compare, Neg, Rule, is_var


class StratificationError(ValueError):
    """Raised when a ruleset cannot be stratified (negation through recursion)."""


# ── Stratification ─────────────────────────────────────────────────────────────

def stratify(rules: list[Rule]) -> dict[str, int]:
    """Assign each predicate a stratum so that a predicate that appears under
    ``not`` is fully computed in a strictly lower stratum. EDB preds are 0.

    Raises StratificationError on a negative cycle.
    """
    preds: set[str] = set(EDB_PREDS)
    for r in rules:
        preds.add(r.head.pred)
        preds |= r.body_pos_preds | r.body_neg_preds

    stratum: dict[str, int] = {p: 0 for p in preds}

    # Relax constraints: head >= body_pos ; head > body_neg.
    n = len(preds) + 1
    for _ in range(n):
        changed = False
        for r in rules:
            h = r.head.pred
            for q in r.body_pos_preds:
                if stratum[h] < stratum[q]:
                    stratum[h] = stratum[q]
                    changed = True
            for q in r.body_neg_preds:
                if stratum[h] <= stratum[q]:
                    stratum[h] = stratum[q] + 1
                    changed = True
        if not changed:
            break
    else:
        # Did not converge within n passes → negative cycle.
        raise StratificationError(
            "ruleset is not stratifiable: negation through recursion detected"
        )

    # Final verification of the strict (negative) constraints.
    for r in rules:
        h = r.head.pred
        for q in r.body_neg_preds:
            if not stratum[h] > stratum[q]:
                raise StratificationError(
                    f"rule {r.id!r}: predicate {q!r} is negated but cannot be placed "
                    f"in a lower stratum than {h!r} (negation through recursion)"
                )
    return stratum


# ── Body matching ───────────────────────────────────────────────────────────────

def _unify(pattern: tuple[Any, ...], ground: tuple[Any, ...],
           binding: dict[str, Any]) -> dict[str, Any] | None:
    if len(pattern) != len(ground):
        return None
    nb = dict(binding)
    for p, g in zip(pattern, ground):
        if is_var(p):
            if p in nb:
                if nb[p] != g:
                    return None
            else:
                nb[p] = g
        elif p != g:
            return None
    return nb


def _resolve(term: Any, binding: dict[str, Any]) -> Any:
    return binding.get(term, term) if is_var(term) else term


def _eval_compare(cmp: Compare, binding: dict[str, Any]) -> bool:
    lhs = _resolve(cmp.lhs, binding)
    rhs = _resolve(cmp.rhs, binding)
    op = cmp.op
    try:
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op == ">=":
            return lhs >= rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == "<":
            return lhs < rhs
    except TypeError:
        return False
    return False


def _match_positives(
    positives: list[Atom],
    fb: FactBase,
    binding: dict[str, Any],
    support: list[Fact],
) -> Iterator[tuple[dict[str, Any], list[Fact]]]:
    if not positives:
        yield binding, support
        return
    atom, rest = positives[0], positives[1:]
    for fact in fb.by_pred(atom.pred):
        nb = _unify(atom.args, fact.args, binding)
        if nb is not None:
            yield from _match_positives(rest, fb, nb, support + [fact])


def match_body(rule: Rule, fb: FactBase) -> Iterator[tuple[dict[str, Any], list[Fact]]]:
    """Yield (binding, support_facts) for every way the body is satisfied."""
    for binding, support in _match_positives(rule.positives, fb, {}, []):
        if not all(_eval_compare(c, binding) for c in rule.compares):
            continue
        # negation-as-failure: the grounded negated atom must NOT be present.
        ok = True
        for neg in rule.negations:
            ground = tuple(_resolve(a, binding) for a in neg.atom.args)
            if any(f.args == ground for f in fb.by_pred(neg.atom.pred)):
                ok = False
                break
        if ok:
            yield binding, support


# ── Reasoner ─────────────────────────────────────────────────────────────────

@dataclass
class ReasonResult:
    fact_base: FactBase
    derived: list[Fact] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)
    iterations: int = 0
    strata: dict[str, int] = field(default_factory=dict)
    timed_out: bool = False


class SymbolicReasoner:
    """Stratified forward chaining over a fact base."""

    def __init__(self, max_iterations: int = 64, deadline_ms: float | None = None):
        self._max_it = max_iterations
        self._deadline_ms = deadline_ms

    def run(self, fact_base: FactBase, rules: list[Rule]) -> ReasonResult:
        enabled = [r for r in rules if r.enabled]
        strata = stratify(enabled)
        # Group rules by the stratum of their head predicate.
        by_stratum: dict[int, list[Rule]] = {}
        for r in enabled:
            by_stratum.setdefault(strata[r.head.pred], []).append(r)
        for s in by_stratum:
            by_stratum[s].sort(key=lambda r: (-r.priority, r.id))

        t0 = time.perf_counter()
        fired: list[str] = []
        derived: list[Fact] = []
        total_it = 0
        timed_out = False

        for s in sorted(by_stratum):
            if s == 0 and not by_stratum.get(0):
                continue
            it = 0
            changed = True
            while changed and it < self._max_it:
                if self._deadline_ms is not None and \
                        (time.perf_counter() - t0) * 1000 >= self._deadline_ms:
                    timed_out = True
                    break
                changed = False
                it += 1
                total_it += 1
                for rule in by_stratum[s]:
                    for binding, support in list(match_body(rule, fact_base)):
                        head_args = tuple(_resolve(a, binding) for a in rule.head.args)
                        head_fact = Fact(rule.head.pred, head_args)
                        if head_fact in fact_base:
                            continue
                        prem_conf = [fact_base.meta(f).confidence for f in support]
                        conf = rule.head.confidence * (min(prem_conf) if prem_conf else 1.0)
                        if fact_base.add_derived(head_fact, support, rule.id, conf):
                            changed = True
                            derived.append(head_fact)
                            if rule.id not in fired:
                                fired.append(rule.id)
            if timed_out:
                break

        return ReasonResult(
            fact_base=fact_base, derived=derived, rules_fired=fired,
            iterations=total_it, strata=strata, timed_out=timed_out,
        )
