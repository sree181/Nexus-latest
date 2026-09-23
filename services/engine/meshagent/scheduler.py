"""
Project:     MeshAgent
File:        meshagent/scheduler.py
Description: Scheduled automations. Because the loop is stateless between
             tasks and writes one episode per run, a schedule is just a
             due-time table plus a runner that invokes the loop. The
             governance payoff over this agent class: every scheduled run
             leaves an episode, so an unattended run that misbehaves is one
             `why` away from an explanation and one `forget` away from
             remediation. Nothing here needs a real clock — tick(now) is
             driven by the caller, so schedules are deterministic to test.
Author:      Sreehas Gopinathan
Created:     2026
Copyright:   (c) 2026 Sreehas Gopinathan
License:     Proprietary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from hypermeshdb.agentmem import MemoryStore

from .loop import TaskResult

# A runner executes one scheduled task and returns its result. In
# production this is AgentLoop.run; injected so the scheduler is testable
# and so one scheduler can drive many agents.
Runner = Callable[[str], TaskResult]


@dataclass
class Schedule:
    """A recurring task. `interval` seconds between runs from `next_run`."""

    schedule_id: str
    task: str
    interval: int
    next_run: int
    enabled: bool = True
    runs: list[str] = field(default_factory=list)  # episode ULIDs, in order


@dataclass
class RunReport:
    """What one tick did, for the operator's log."""

    fired: list[str]                    # schedule_ids that ran
    episodes: dict[str, str]            # schedule_id -> episode ULID
    outcomes: dict[str, str]            # schedule_id -> outcome


class Scheduler:
    """A minimal due-time scheduler over one memory store. State is kept in
    memory here; a deployment would persist the table, but the audit record
    of every RUN lives in the hypergraph regardless."""

    def __init__(self, memory: MemoryStore) -> None:
        self._m = memory
        self._schedules: dict[str, Schedule] = {}

    def add(
        self, schedule_id: str, task: str, *, interval: int, first_run: int
    ) -> Schedule:
        if schedule_id in self._schedules:
            raise ValueError(f"duplicate schedule {schedule_id}")
        s = Schedule(schedule_id, task, interval, first_run)
        self._schedules[schedule_id] = s
        return s

    def disable(self, schedule_id: str) -> None:
        self._schedules[schedule_id].enabled = False

    def enable(self, schedule_id: str) -> None:
        self._schedules[schedule_id].enabled = True

    def schedules(self) -> list[Schedule]:
        return list(self._schedules.values())

    def tick(self, now: int, *, runner: Runner) -> RunReport:
        """Run every enabled schedule that is due at *now*, advancing each
        past its next_run. A schedule that is overdue by several intervals
        runs once and catches its next_run up to the future, so a paused
        scheduler does not stampede on resume."""
        report = RunReport(fired=[], episodes={}, outcomes={})
        for s in self._schedules.values():
            if not s.enabled or now < s.next_run:
                continue
            result = runner(s.task)
            if result.episode_ulid is not None:
                s.runs.append(result.episode_ulid)
                report.episodes[s.schedule_id] = result.episode_ulid
            report.fired.append(s.schedule_id)
            report.outcomes[s.schedule_id] = result.outcome
            # advance next_run to the first future slot
            while s.next_run <= now:
                s.next_run += max(1, s.interval)
        return report

    def runs_of(self, schedule_id: str) -> list[str]:
        """The episode ULIDs of every run of a schedule, in order. Each is
        independently reconstructable via meshagent.reconstruct."""
        return list(self._schedules[schedule_id].runs)
