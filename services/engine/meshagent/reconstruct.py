"""
Project:     MeshAgent
File:        meshagent/reconstruct.py
Description: Reconstruct a task run from its episode. This is the payoff
             of writing state as provenance-versioned hyperedges: given
             an episode ULID, replay exactly what the agent did, which
             tools it called with which arguments, and how it ended, with
             nothing pulled from process memory. The `why` verb builds on
             this; here it is the plain reconstruction.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypermeshdb.agentmem import Kind, MemoryStore


@dataclass
class ReconstructedRun:
    """A task run rebuilt entirely from its stored episode."""

    episode_ulid: str
    task: str
    outcome: str
    turns: int
    final_text: str | None
    tool_calls: list[dict[str, Any]]
    tools_used: list[str]
    subjects: list[str]

    def matches(self, result: Any) -> bool:
        """True when this reconstruction agrees with a live TaskResult on
        every field an audit would check."""
        live_tools = sorted({r.name for r in result.tool_results})
        return (
            self.task == result.task
            and self.outcome == result.outcome
            and self.turns == result.turns
            and self.final_text == result.final_text
            and self.tools_used == live_tools
            and len(self.tool_calls) == len(result.tool_results)
        )


def reconstruct(memory: MemoryStore, episode_ulid: str) -> ReconstructedRun:
    """Rebuild a run from its episode ULID. Raises if the ULID is not an
    episode or has been redacted (a redacted episode is auditable only as
    a tombstone, not a replay)."""
    m = memory.get(episode_ulid)
    if m is None:
        raise KeyError(f"no memory {episode_ulid}")
    if m.envelope.kind != Kind.EPISODE:
        raise ValueError(f"{episode_ulid} is not an episode")
    if m.content is None:
        raise ValueError(
            f"episode {episode_ulid} is redacted; only its tombstone remains"
        )
    p = m.content
    tools_used = sorted({c.get("tool") for c in p.get("tool_calls", [])})
    return ReconstructedRun(
        episode_ulid=episode_ulid,
        task=p["task"],
        outcome=p["outcome"],
        turns=int(p["turns"]),
        final_text=p.get("final_text"),
        tool_calls=list(p.get("tool_calls", [])),
        tools_used=[t for t in tools_used if t],
        subjects=[n for n in m.member_names if not n.startswith("edge:")],
    )
