"""Wire models for the Analyst and CISO production journeys."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .developer_session_models import PolicyAdvisory
from .models import GraphPayload

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


# -- Developer attention -> Analyst review ------------------------------------

ReviewState = Literal[
    "waiting", "changes_requested", "exception_approved", "not_approved",
    "false_positive", "escalated", "verified",
]
ReviewKind = Literal[
    "security_guidance", "safe_version", "exception", "false_positive",
]


class AttentionItemOut(BaseModel):
    id: str
    session_id: str
    policy_evaluation_id: str
    run_id: str | None = None
    repository_id: str
    repository_name: str
    package: str
    version: str
    ecosystem: Literal["PyPI", "npm"]
    verdict: Literal["allow", "warn", "block", "unknown"]
    worst: Severity | None = None
    reasons: list[str] = Field(default_factory=list)
    advisories: list[PolicyAdvisory] = Field(default_factory=list)
    unavailable: str | None = None
    code_entities: list[str] = Field(default_factory=list)
    suggested_version: str | None = None
    checked_at_ms: int
    review_request_id: str | None = None
    review_status: ReviewState | None = None
    priority: int
    priority_reasons: list[str] = Field(default_factory=list)


class AttentionListOut(BaseModel):
    items: list[AttentionItemOut] = Field(default_factory=list)
    total: int


class CreateReviewRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    policy_evaluation_id: str = Field(min_length=1, max_length=128)
    kind: ReviewKind
    rationale: str = Field(min_length=1, max_length=4_096)


class ReviewDecisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    decision: Literal[
        "request_changes", "approve_exception", "reject", "false_positive",
        "escalate",
    ]
    rationale: str = Field(min_length=1, max_length=4_096)
    recommended_version: str | None = Field(default=None, max_length=128)
    expires_at: int | None = None

    @model_validator(mode="after")
    def _decision_requirements(self) -> "ReviewDecisionRequest":
        if self.decision == "approve_exception" and not self.expires_at:
            raise ValueError("exception approval requires expires_at")
        if self.decision == "request_changes" and not self.recommended_version:
            raise ValueError("requested changes require a recommended_version")
        return self


class ReviewEventOut(BaseModel):
    id: str
    request_id: str
    actor: str
    actor_name: str
    actor_role: str
    action: str
    from_state: str | None = None
    to_state: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    at: int


class ReviewRequestOut(BaseModel):
    id: str
    owner_subject: str
    owner_name: str
    session_id: str
    run_id: str | None = None
    repository_id: str
    repository_name: str
    policy_evaluation_id: str
    package: str
    version: str
    ecosystem: Literal["PyPI", "npm"]
    verdict: Literal["allow", "warn", "block", "unknown"]
    severity: Severity
    advisories: list[PolicyAdvisory] = Field(default_factory=list)
    code_entities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    kind: ReviewKind
    rationale: str
    state: ReviewState
    analyst_subject: str | None = None
    analyst_name: str | None = None
    assignee: str | None = None
    assignee_name: str | None = None
    sla_due_at: int | None = None
    overdue: bool = False
    escalated_case_id: str | None = None
    decision_rationale: str | None = None
    recommended_version: str | None = None
    exception_expires_at: int | None = None
    verification_evidence_id: str | None = None
    evidence_digest: str | None = None
    evidence_root_ulid: str | None = None
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    version_counter: int
    created_at: int
    updated_at: int
    priority: int
    priority_reasons: list[str] = Field(default_factory=list)
    events: list[ReviewEventOut] = Field(default_factory=list)


class ReviewRequestListOut(BaseModel):
    requests: list[ReviewRequestOut] = Field(default_factory=list)
    total: int


class ReviewGraphOut(BaseModel):
    request_id: str
    perspective: Literal["developer", "analyst", "ciso"]
    evidence_root_ulid: str | None = None
    evidence_digest: str | None = None
    graph: GraphPayload
    note: str


# -- Priority 4: Analyst operations ------------------------------------------

WorkKind = Literal["review", "case"]


class AssignReviewRequest(BaseModel):
    expected_version: int = Field(ge=1)
    assignee: str = Field(min_length=1, max_length=256)
    assignee_name: str = Field(min_length=1, max_length=256)
    sla_due_at: int | None = None


class EscalateReviewRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=512)
    rationale: str = Field(min_length=1, max_length=4_096)
    assignee: str | None = Field(default=None, max_length=256)
    assignee_name: str | None = Field(default=None, max_length=256)
    sla_due_at: int | None = None


class WorkItemOut(BaseModel):
    id: str
    kind: WorkKind
    title: str
    subtitle: str
    severity: Severity
    state: str
    priority: int
    priority_reasons: list[str] = Field(default_factory=list)
    version: int = Field(ge=1)
    assignee: str | None = None
    assignee_name: str | None = None
    sla_due_at: int | None = None
    overdue: bool = False
    repository_name: str | None = None
    developer_name: str | None = None
    route: str
    created_at: int
    updated_at: int


class WorkQueueOut(BaseModel):
    items: list[WorkItemOut] = Field(default_factory=list)
    total: int
    counts: dict[str, int] = Field(default_factory=dict)


class BulkWorkRef(BaseModel):
    kind: WorkKind
    id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)


class BulkAssignRequest(BaseModel):
    items: list[BulkWorkRef] = Field(min_length=1, max_length=100)
    assignee: str = Field(min_length=1, max_length=256)
    assignee_name: str = Field(min_length=1, max_length=256)
    sla_due_at: int | None = None


class BulkWorkResult(BaseModel):
    kind: WorkKind
    id: str
    ok: bool
    error: str | None = None


class BulkWorkReceipt(BaseModel):
    results: list[BulkWorkResult]
    succeeded: int
    failed: int


class WorkCommentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_096)
    mentions: list[str] = Field(default_factory=list, max_length=20)


class WorkCommentOut(BaseModel):
    id: str
    resource_kind: WorkKind
    resource_id: str
    actor: str
    actor_name: str
    actor_role: str
    message: str
    mentions: list[str] = Field(default_factory=list)
    created_at: int


class WorkActivityOut(BaseModel):
    id: str
    resource_kind: WorkKind
    resource_id: str
    actor: str
    actor_name: str
    action: str
    message: str
    at: int
    route: str


class WorkActivityListOut(BaseModel):
    items: list[WorkActivityOut] = Field(default_factory=list)
    total: int


class SavedViewRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    filters: dict[str, str] = Field(default_factory=dict)


class SavedViewOut(BaseModel):
    id: str
    owner_subject: str
    name: str
    filters: dict[str, str]
    created_at: int
    updated_at: int


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    message: str
    resource_kind: WorkKind
    resource_id: str
    route: str
    created_at: int
    not_before: int
    read: bool = False


class NotificationListOut(BaseModel):
    notifications: list[NotificationOut] = Field(default_factory=list)
    total: int
    unread: int
