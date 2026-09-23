"""
Project:     MeshAgent
File:        meshagent/skills.py
Description: The self-improving skill loop, built so a skill is promoted
             on replayed OUTCOMES, never on the model's opinion of its own
             run. That single rule is the fix for the failure mode of this
             agent class ("self-evaluation always successful"). Skills are
             SKILL hyperedges linked by SUPPORTS to the episodes they were
             induced from; every lifecycle transition is a supersession,
             so the whole history — draft, promotion, deprecation — stays
             auditable and reversible.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from hypermeshdb.agentmem import Kind, MemoryStore, Origin, Rel, Status

from .loop import TaskResult


class SkillState(str, enum.Enum):
    """The lifecycle a skill moves through. Each transition is a new
    version of the SKILL edge, so the trail is append-only."""

    DRAFT = "draft"            # induced from >= N episodes, unproven
    CANDIDATE = "candidate"    # passed replay eval on a held-out task
    PROMOTED = "promoted"      # explicitly approved for use
    DEPRECATED = "deprecated"  # failed in use or expired; never overwritten


def signature_of(tool_names: list[str]) -> str:
    """A task signature from the sorted tool set an episode used. Skills
    generalize over 'tasks that use this combination of tools'."""
    key = "+".join(sorted(set(tool_names)))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _subject(sig: str) -> str:
    return f"skill:sig:{sig}"


@dataclass
class SkillRecord:
    """A skill as read back from the graph."""

    ulid: str
    signature: str
    state: SkillState
    statement: str
    supporting_episodes: list[str]
    consecutive_failures: int
    tombstoned: bool


# A replay runner: given a held-out task, execute it (following the skill)
# and return the live TaskResult. The evaluator judges that RESULT, never
# a self-report. In production this runs AgentLoop with the skill in
# context; in tests it is deterministic.
ReplayRunner = Callable[[str], TaskResult]

# An outcome check on a replayed result: the objective test that decides
# whether the skill actually worked. Supplied by the caller, applied to
# the result — this is what keeps promotion honest.
OutcomeCheck = Callable[[TaskResult], bool]


class SkillManager:
    """Induce, evaluate, promote, and retire skills over one memory store."""

    def __init__(self, memory: MemoryStore) -> None:
        self._m = memory

    # ── induction ────────────────────────────────────────────────────────

    def induct(self, *, min_episodes: int = 3) -> list[str]:
        """Draft a skill for every tool-signature backed by at least
        *min_episodes* successful episodes and not already covered by a
        live skill. Returns the ULIDs of newly drafted skills."""
        groups: dict[str, list[str]] = {}
        for ep_ulid in self._m.find_by_subject("outcome:success"):
            m = self._m.get(ep_ulid)
            if m is None or m.content is None:
                continue
            if m.envelope.kind != Kind.EPISODE:
                continue
            tools = sorted({
                c.get("tool") for c in m.content.get("tool_calls", [])
                if c.get("tool")
            })
            if not tools:
                continue
            groups.setdefault(signature_of(tools), []).append(ep_ulid)

        drafted: list[str] = []
        for sig, eps in groups.items():
            if len(eps) < min_episodes:
                continue
            if self.current(sig) is not None:
                continue
            ulid = self._m.write(
                Kind.SKILL,
                [_subject(sig)],
                origin=Origin.AGENT,
                status=Status.UNVERIFIED,   # a draft is not yet trusted
                source="agent:skill-induction",
                payload={
                    "state": SkillState.DRAFT.value,
                    "signature": sig,
                    "statement": f"procedure for tasks using tools [{sig}]",
                    "consecutive_failures": 0,
                },
            )
            # attach the evidence this skill rests on
            self._m.derive(ulid, eps, Rel.SUPPORTS)
            drafted.append(ulid)
        return drafted

    # ── the replay-evaluation gate ───────────────────────────────────────

    def evaluate(
        self,
        skill_ulid: str,
        held_out_task: str,
        *,
        runner: ReplayRunner,
        outcome_check: OutcomeCheck,
    ) -> bool:
        """Promote DRAFT to CANDIDATE only if the skill, replayed on a
        HELD-OUT task, passes an OUTCOME check. The skill never grades
        itself: the runner produces a real result and the caller-supplied
        outcome_check is the objective test applied to it.

        Returns whether the skill passed. A pass advances it to CANDIDATE;
        a fail leaves it DRAFT (recorded, not silently retried)."""
        rec = self.get(skill_ulid)
        if rec is None:
            raise ValueError(f"unknown skill {skill_ulid}")
        if rec.state != SkillState.DRAFT:
            raise ValueError(
                f"evaluate expects a DRAFT skill, got {rec.state.value}"
            )
        result = runner(held_out_task)
        passed = bool(result.outcome == "success" and outcome_check(result))
        if passed:
            self._transition(
                skill_ulid,
                SkillState.CANDIDATE,
                note={"eval_task": held_out_task, "eval": "passed"},
            )
        else:
            self._annotate(
                skill_ulid,
                {"eval_task": held_out_task, "eval": "failed"},
            )
        return passed

    def promote(self, skill_ulid: str, *, approver: str) -> str:
        """Advance CANDIDATE to PROMOTED. Explicit human approval in v0:
        eval-only promotion is a later opt-in, never the default, so
        behaviour change is always attributable to a person."""
        rec = self.get(skill_ulid)
        if rec is None or rec.state != SkillState.CANDIDATE:
            raise ValueError("promote expects a CANDIDATE skill")
        return self._transition(
            skill_ulid, SkillState.PROMOTED, note={"approver": approver}
        )

    def record_outcome(self, skill_ulid: str, *, success: bool) -> str | None:
        """Record a promoted skill's real-use outcome. Two consecutive
        failures deprecate it (a tombstone-free supersession to DEPRECATED,
        so the history and the reason survive). Returns the new state's
        ULID when a transition happened, else None."""
        rec = self.get(skill_ulid)
        if rec is None:
            raise ValueError(f"unknown skill {skill_ulid}")
        fails = 0 if success else rec.consecutive_failures + 1
        if not success and fails >= 2:
            return self._transition(
                skill_ulid,
                SkillState.DEPRECATED,
                note={"reason": "two consecutive failures in use"},
                consecutive_failures=fails,
            )
        # record the running count without changing state
        return self._annotate(
            skill_ulid, {"last_outcome": "success" if success else "failure"},
            consecutive_failures=fails,
        )

    # ── reads ─────────────────────────────────────────────────────────────

    def get(self, skill_ulid: str) -> SkillRecord | None:
        m = self._m.get(skill_ulid)
        if m is None or m.envelope.kind != Kind.SKILL or m.content is None:
            return None
        c = m.content
        # supporting episodes are linked by SUPPORTS/DERIVED_FROM derivation
        # edges, not carried as members of the skill edge itself.
        parents = self._m.why(skill_ulid, max_depth=1).get("parents", [])
        supporting = [
            p["ulid"] for p in parents
            if p.get("rel") in ("SUPPORTS", "DERIVED_FROM")
        ]
        return SkillRecord(
            ulid=skill_ulid,
            signature=c.get("signature", ""),
            state=SkillState(c.get("state", "draft")),
            statement=c.get("statement", ""),
            supporting_episodes=supporting,
            consecutive_failures=int(c.get("consecutive_failures", 0)),
            tombstoned=m.tombstoned,
        )

    def current(self, signature: str) -> SkillRecord | None:
        """The live (non-superseded, non-tombstoned) skill for a signature."""
        latest: SkillRecord | None = None
        latest_ts = -1
        for u in self._m.find_by_subject(_subject(signature)):
            m = self._m.get(u)
            if m is None or m.tombstoned or m.superseded_by is not None:
                continue
            if m.content is None:
                continue
            if m.event_ts >= latest_ts:
                latest_ts = m.event_ts
                latest = self.get(u)
        return latest

    # ── transitions (append-only via supersession) ───────────────────────

    def _transition(
        self,
        skill_ulid: str,
        state: SkillState,
        *,
        note: dict[str, Any] | None = None,
        consecutive_failures: int | None = None,
    ) -> str:
        rec = self.get(skill_ulid)
        assert rec is not None
        payload = {
            "state": state.value,
            "signature": rec.signature,
            "statement": rec.statement,
            "consecutive_failures": (
                consecutive_failures
                if consecutive_failures is not None
                else rec.consecutive_failures
            ),
        }
        if note:
            payload["note"] = note
        return self._m.supersede(skill_ulid, payload=payload)

    def _annotate(
        self,
        skill_ulid: str,
        note: dict[str, Any],
        *,
        consecutive_failures: int | None = None,
    ) -> str:
        rec = self.get(skill_ulid)
        assert rec is not None
        payload = {
            "state": rec.state.value,
            "signature": rec.signature,
            "statement": rec.statement,
            "consecutive_failures": (
                consecutive_failures
                if consecutive_failures is not None
                else rec.consecutive_failures
            ),
            "note": note,
        }
        return self._m.supersede(skill_ulid, payload=payload)
