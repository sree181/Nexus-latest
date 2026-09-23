"""Wire models for the Analyst and CISO production journeys."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["critical", "high", "medium", "low", "unknown"]
CaseState = Literal[
    "open", "triaged", "investigating", "remediation", "resolved", "closed",
]


class OriginOut(BaseModel):
    data_origin: Literal["live", "sample", "mixed"]
    source_time: int
    seeded_count: int = 0
    coverage: dict[str, Any]


class CreateCaseRequest(BaseModel):
    finding_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    severity: Severity
    rationale: str = Field(min_length=1, max_length=4_096)


class AssignCaseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    assignee: str = Field(min_length=1, max_length=256)
    assignee_name: str = Field(min_length=1, max_length=256)
    sla_due_at: int | None = None


class TransitionCaseRequest(BaseModel):
    expected_version: int = Field(ge=1)
    to_state: Literal[
        "triaged", "investigating", "remediation", "resolved", "closed", "reopened"
    ]
    disposition: str | None = Field(default=None, max_length=256)
    rationale: str = Field(min_length=1, max_length=4_096)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class CaseEventOut(BaseModel):
    id: str
    case_id: str
    actor: str
    actor_name: str
    action: str
    from_state: str | None
    to_state: str | None
    rationale: str
    evidence_ids: list[str]
    at: int
    correlation_id: str


class CaseOut(BaseModel):
    id: str
    finding_id: str
    run_id: str
    title: str
    severity: Severity
    state: CaseState
    assignee: str | None = None
    assignee_name: str | None = None
    sla_due_at: int | None = None
    disposition: str | None = None
    version: int
    created_at: int
    updated_at: int
    created_by: str
    origin: str
    overdue: bool = False
    priority: int
    priority_reasons: list[str]
    events: list[CaseEventOut] = []


class CaseListOut(BaseModel):
    cases: list[CaseOut]
    total: int
    origin: OriginOut


class CreatePolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=512)
    severity_threshold: Severity
    denied_licenses: list[str] = Field(default_factory=list, max_length=100)
    block_on_unknown: bool = True
    rationale: str = Field(min_length=1, max_length=4_096)


class PolicyVersionOut(BaseModel):
    policy_id: str
    version: int
    severity_threshold: Severity
    denied_licenses: list[str]
    block_on_unknown: bool
    rationale: str
    created_at: int
    created_by: str


class PolicyOut(BaseModel):
    id: str
    name: str
    scope: str
    status: str
    active_version: int
    created_at: int
    updated_at: int
    created_by: str
    current: PolicyVersionOut


class CreateExceptionRequest(BaseModel):
    policy_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=512)
    rationale: str = Field(min_length=1, max_length=4_096)
    compensating_controls: str = Field(min_length=1, max_length=4_096)
    owner: str = Field(min_length=1, max_length=256)
    expires_at: int


class ExceptionOut(BaseModel):
    id: str
    policy_id: str
    scope: str
    rationale: str
    compensating_controls: str
    owner: str
    expires_at: int
    status: str
    version: int
    requested_by: str
    approved_by: str | None = None
    created_at: int
    updated_at: int


class ApprovalOut(BaseModel):
    id: str
    kind: str
    resource_id: str
    requester: str
    requester_name: str
    status: str
    rationale: str
    approver: str | None = None
    decision_rationale: str | None = None
    version: int
    expires_at: int
    created_at: int
    decided_at: int | None = None


class ApprovalDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    rationale: str = Field(min_length=1, max_length=4_096)


class CreateRemediationRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    owner: str = Field(min_length=1, max_length=256)
    due_at: int
    target_revision: str | None = Field(default=None, max_length=256)


class RemediationOut(BaseModel):
    id: str
    case_id: str
    title: str
    owner: str
    due_at: int
    status: str
    target_revision: str | None = None
    evidence_ids: list[str]
    version: int
    created_by: str
    created_at: int
    updated_at: int


class ReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    period_start: int
    period_end: int

    @model_validator(mode="after")
    def _ordered_period(self) -> "ReportRequest":
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        return self


class ReportOut(BaseModel):
    id: str
    title: str
    period_start: int
    period_end: int
    status: str
    requested_by: str
    digest: str
    created_at: int
    manifest: dict[str, Any] | None = None


class CisoOverviewOut(BaseModel):
    fleet: dict[str, Any]
    workflow: dict[str, int]
    trends: list[dict[str, Any]]
    data_health: dict[str, Any]
    origin: OriginOut
