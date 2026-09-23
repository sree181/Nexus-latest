"""
Project:     MeshAgent
File:        meshagent/subagent.py
Description: Governed subagents. A subagent is another AgentLoop over the
             SAME memory store, so parent and child share one provenance
             graph and one write gate. That shared, gated substrate is
             what makes multi-agent safe here: the documented failure of
             this agent class is cross-boundary memory poisoning, where one
             agent plants a "memory" that rewrites another's behaviour.
             Here a subagent's writes pass the same gate as anyone's, so it
             cannot author a preference or a skill in the parent's mind, and
             every handoff between agents is an episode.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from dataclasses import dataclass

from hypermeshdb.agentmem import Kind, MemoryStore, Origin, Rel, Status

from .llm import LLMClient
from .loop import AgentLoop, TaskResult
from .tools import ApprovalFn, ToolRegistry, deny_all


@dataclass
class Handoff:
    """The record of a parent delegating a task to a subagent."""

    parent_task_id: str
    subagent_name: str
    subtask: str
    result_outcome: str
    parent_episode: str | None
    subagent_episode: str | None
    handoff_episode: str


class Subagent:
    """A named worker agent that shares its parent's memory and gate."""

    def __init__(
        self,
        name: str,
        *,
        llm: LLMClient,
        tools: ToolRegistry,
        memory: MemoryStore,
        approve: ApprovalFn = deny_all,
        max_turns: int = 8,
    ) -> None:
        self.name = name
        self._loop = AgentLoop(
            llm=llm, tools=tools, memory=memory, approve=approve,
            max_turns=max_turns,
        )

    def run(self, subtask: str) -> TaskResult:
        # subagent tasks are namespaced so their episodes are attributable
        return self._loop.run(
            subtask, task_id=f"subtask:{self.name}:{_slug(subtask)}"
        )


class Orchestrator:
    """A parent that delegates to named subagents over shared memory. The
    delegation itself is recorded as an episode, so a run that spans agents
    reconstructs as one traceable chain."""

    def __init__(self, memory: MemoryStore) -> None:
        self._m = memory
        self._subagents: dict[str, Subagent] = {}

    def register(self, sub: Subagent) -> None:
        if sub.name in self._subagents:
            raise ValueError(f"duplicate subagent {sub.name}")
        self._subagents[sub.name] = sub

    def delegate(
        self, subagent_name: str, subtask: str, *, parent_task_id: str
    ) -> Handoff:
        sub = self._subagents.get(subagent_name)
        if sub is None:
            raise KeyError(f"no subagent {subagent_name!r}")
        result = sub.run(subtask)
        # record the handoff as its own episode: who delegated what to whom
        handoff_ulid = self._m.write(
            Kind.EPISODE,
            [
                parent_task_id,
                f"subagent:{subagent_name}",
                f"outcome:{result.outcome}",
            ],
            origin=Origin.AGENT,
            status=Status.VERIFIED,
            source="agent:orchestrator",
            payload={
                # standard episode fields, so the handoff reconstructs like
                # any other run...
                "task": f"delegate to {subagent_name}: {subtask}",
                "task_id": parent_task_id,
                "outcome": result.outcome,
                "final_text": result.final_text,
                "turns": result.turns,
                "usage": {},
                "tool_calls": [],
                # ...plus the handoff-specific record
                "kind": "handoff",
                "subagent": subagent_name,
                "subtask": subtask,
                "subagent_episode": result.episode_ulid,
            },
        )
        # link the handoff to the subagent's own episode as its evidence
        if result.episode_ulid is not None:
            self._m.derive(handoff_ulid, [result.episode_ulid], Rel.SUPPORTS)
        return Handoff(
            parent_task_id=parent_task_id,
            subagent_name=subagent_name,
            subtask=subtask,
            result_outcome=result.outcome,
            parent_episode=None,
            subagent_episode=result.episode_ulid,
            handoff_episode=handoff_ulid,
        )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower())[:32]
