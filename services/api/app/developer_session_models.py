"""Wire contracts for connected coding-agent sessions.

The editor adapter reports observed activity. The server owns identity,
ordering, replay detection, policy history, and the link to governed evidence.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Adapter = Literal["cursor", "claude-code"]
SessionStatus = Literal["starting", "active", "ending", "completed", "failed"]
ProjectionStatus = Literal["pending", "projecting", "projected", "refused", "failed"]
ActivityType = Literal[
    "session.started",
    "prompt.submitted",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "file.changed",
    "decision.recorded",
    "package.requested",
    "package.installed",
    "policy.evaluated",
    "response.completed",
    "session.ended",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryContext(StrictModel):
    id: str = Field(pattern=r"^repo-[A-Za-z0-9._-]{1,96}$", max_length=101)
    name: str = Field(min_length=1, max_length=256)
    remote: str | None = Field(default=None, max_length=1_024)
    branch: str | None = Field(default=None, max_length=256)
    commit: str | None = Field(default=None, max_length=128)


class SessionStartRequest(StrictModel):
    id: str = Field(pattern=r"^ses_[A-Za-z0-9._-]{16,96}$", max_length=100)
    source_session_id: str = Field(min_length=1, max_length=256)
    source_event_id: str = Field(min_length=1, max_length=256)
    adapter: Adapter
    adapter_version: str = Field(default="1", max_length=64)
    repository: RepositoryContext
    task: str = Field(min_length=1, max_length=4_096)
    started_at_ms: int = Field(ge=0)
    sequence: Literal[1] = 1


class PromptPayload(StrictModel):
    prompt: str = Field(min_length=1, max_length=4_096)
    turn_id: str | None = Field(default=None, max_length=256)


class ToolPayload(StrictModel):
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    detail: str = Field(default="", max_length=4_096)
    exit_code: int | None = None


class FileChangedPayload(StrictModel):
    path: str = Field(min_length=1, max_length=1_024)
    operation: Literal["create", "update", "delete"] = "update"
    code: str | None = Field(default=None, max_length=200_000)
    because: str | None = Field(default=None, max_length=256)

    @field_validator("path")
    @classmethod
    def _repository_relative_path(cls, value: str) -> str:
        if (
            "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or re.match(r"^[A-Za-z]:/", value)
            or any(part in ("", ".", "..") for part in value.split("/"))
        ):
            raise ValueError("path must be a canonical repository-relative POSIX path")
        return value

    @model_validator(mode="after")
    def _content_for_write(self) -> "FileChangedPayload":
        if self.operation != "delete" and self.code is None:
            raise ValueError("file create/update events require code")
        return self


class DecisionPayload(StrictModel):
    decision_id: str = Field(min_length=1, max_length=256)
    statement: str = Field(min_length=1, max_length=4_096)


class PackagePayload(StrictModel):
    package: str = Field(min_length=1, max_length=256)
    version: str = Field(default="", max_length=128)
    license: str = Field(default="unknown", max_length=256)
    ecosystem: str | None = Field(default=None, max_length=64)
    command: str | None = Field(default=None, max_length=4_096)


class PolicyAdvisory(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    severity: Literal["critical", "high", "medium", "low", "unknown"]
    summary: str = Field(min_length=1, max_length=2_048)
    cwe: str | None = Field(default=None, max_length=128)
    fixed_versions: list[str] = Field(default_factory=list, max_length=20)
    references: list[str] = Field(default_factory=list, max_length=10)


class PolicyPayload(StrictModel):
    package: str = Field(min_length=1, max_length=256)
    version: str = Field(default="", max_length=128)
    ecosystem: Literal["PyPI", "npm"] = "PyPI"
    verdict: Literal["allow", "warn", "block", "unknown"]
    reasons: list[str] = Field(default_factory=list, max_length=32)
    policy: str = Field(default="", max_length=4_096)
    worst: Literal["critical", "high", "medium", "low", "unknown"] | None = None
    unavailable: str | None = Field(default=None, max_length=2_048)
    advisories: list[PolicyAdvisory] = Field(default_factory=list, max_length=100)


class ResponsePayload(StrictModel):
    turn_id: str | None = Field(default=None, max_length=256)
    summary: str = Field(default="", max_length=4_096)
    stop_reason: str | None = Field(default=None, max_length=128)


class SessionEndedPayload(StrictModel):
    reason: str = Field(default="normal", max_length=256)


class ActivityBase(StrictModel):
    event_id: str = Field(pattern=r"^evt_[A-Za-z0-9._-]{16,120}$", max_length=124)
    source_event_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=2)
    occurred_at_ms: int = Field(ge=0)


class PromptSubmitted(ActivityBase):
    type: Literal["prompt.submitted"]
    payload: PromptPayload


class ToolStarted(ActivityBase):
    type: Literal["tool.started"]
    payload: ToolPayload


class ToolCompleted(ActivityBase):
    type: Literal["tool.completed"]
    payload: ToolPayload


class ToolFailed(ActivityBase):
    type: Literal["tool.failed"]
    payload: ToolPayload


class FileChanged(ActivityBase):
    type: Literal["file.changed"]
    payload: FileChangedPayload


class DecisionRecorded(ActivityBase):
    type: Literal["decision.recorded"]
    payload: DecisionPayload


class PackageRequested(ActivityBase):
    type: Literal["package.requested"]
    payload: PackagePayload


class PackageInstalled(ActivityBase):
    type: Literal["package.installed"]
    payload: PackagePayload


class PolicyEvaluated(ActivityBase):
    type: Literal["policy.evaluated"]
    payload: PolicyPayload


class ResponseCompleted(ActivityBase):
    type: Literal["response.completed"]
    payload: ResponsePayload


class SessionEnded(ActivityBase):
    type: Literal["session.ended"]
    payload: SessionEndedPayload


ActivityEventIn = Annotated[
    PromptSubmitted
    | ToolStarted
    | ToolCompleted
    | ToolFailed
    | FileChanged
    | DecisionRecorded
    | PackageRequested
    | PackageInstalled
    | PolicyEvaluated
    | ResponseCompleted
    | SessionEnded,
    Field(discriminator="type"),
]


class ActivityBatchRequest(StrictModel):
    events: list[ActivityEventIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _strictly_ordered(self) -> "ActivityBatchRequest":
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise ValueError("events must have unique ascending sequences")
        if any(event.type == "session.ended" for event in self.events[:-1]):
            raise ValueError("session.ended must be the final event in a batch")
        encoded_bytes = sum(
            len(json.dumps(event.model_dump(mode="json"), separators=(",", ":")).encode())
            for event in self.events
        )
        if encoded_bytes > 2 * 1024 * 1024:
            raise ValueError("activity batch exceeds 2 MiB")
        return self


class DeveloperSessionOut(BaseModel):
    id: str
    owner_subject: str
    owner_name: str
    adapter: Adapter
    adapter_version: str
    source_session_id: str
    repository: RepositoryContext
    task: str
    status: SessionStatus
    started_at_ms: int
    last_seen_at_ms: int
    ended_at_ms: int | None = None
    next_sequence: int
    last_acked_sequence: int
    run_id: str | None = None
    device_id: str | None = None
    verified: bool
    failure_reason: str | None = None


class SessionListOut(BaseModel):
    sessions: list[DeveloperSessionOut] = Field(default_factory=list)
    total: int = 0


class ActivityEventOut(BaseModel):
    event_id: str
    source_event_id: str
    session_id: str
    sequence: int
    type: ActivityType
    occurred_at_ms: int
    received_at_ms: int
    payload: dict[str, Any]
    payload_sha256: str
    projection_status: ProjectionStatus
    projection_attempts: int
    projection_last_attempt_at_ms: int | None = None
    projection_next_attempt_at_ms: int | None = None
    projected_at_ms: int | None = None
    projection_error: str | None = None
    run_id: str | None = None


class ActivityListOut(BaseModel):
    events: list[ActivityEventOut] = Field(default_factory=list)
    next_after_sequence: int | None = None


class ActivityIngestReceipt(BaseModel):
    session: DeveloperSessionOut
    accepted: int = 0
    duplicates: int = 0
    projected: int = 0
    refused: list[str] = Field(default_factory=list)
    acknowledged_through: int
    next_sequence: int
    run_id: str | None = None


class PolicyEvaluationOut(BaseModel):
    id: str
    session_id: str
    activity_event_id: str
    package: str
    version: str
    ecosystem: Literal["PyPI", "npm"] = "PyPI"
    verdict: Literal["allow", "warn", "block", "unknown"]
    reasons: list[str] = Field(default_factory=list)
    advisories: list[PolicyAdvisory] = Field(default_factory=list)
    worst: Literal["critical", "high", "medium", "low", "unknown"] | None = None
    unavailable: str | None = None
    policy: str
    evaluated_at_ms: int
    owner_subject: str
    device_id: str | None = None


class PolicyEvaluationListOut(BaseModel):
    evaluations: list[PolicyEvaluationOut] = Field(default_factory=list)
    total: int = 0
