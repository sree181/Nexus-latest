"""Strict, content-free contracts for enterprise recorder trust.

These models intentionally cannot carry prompts, source, repository names, paths,
commands, package names, sessions, or HyperMesh evidence. Platform administrators
manage device trust, not developer work.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TrustState = Literal["pending", "active", "quarantined", "revoked"]
CredentialKind = Literal["dpop", "legacy_bearer"]
Platform = Literal["darwin", "windows", "linux"]
DeliveryState = Literal["unknown", "healthy", "delayed", "blocked", "offline"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicJWK(StrictModel):
    kty: Literal["EC"]
    crv: Literal["P-256"]
    x: str = Field(min_length=43, max_length=43)
    y: str = Field(min_length=43, max_length=43)


class EnrollmentRequest(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    deployment: str = Field(min_length=1, max_length=128)
    platform: Platform
    platform_version: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=32)
    recorder_version: str = Field(min_length=1, max_length=64)
    public_jwk: PublicJWK
    attestation_format: Literal["none", "apple", "windows_tpm", "linux_tpm"] = "none"
    attestation: str | None = Field(default=None, max_length=16384)

    @model_validator(mode="after")
    def verified_attestation_only(self) -> "EnrollmentRequest":
        if self.attestation_format != "none" or self.attestation is not None:
            raise ValueError(
                "platform attestation is not accepted until a server verifier is configured"
            )
        return self


class EnrollmentStart(StrictModel):
    enrollment_id: str
    device_code: str
    user_code: str
    expires_in: int
    interval: int = 2
    verify_url: str


class EnrollmentPending(StrictModel):
    enrollment_id: str
    user_code: str
    label: str
    deployment: str
    platform: Platform
    platform_version: str
    architecture: str
    recorder_version: str
    key_thumbprint: str
    attestation_format: str
    approved: bool
    started_at: int


class EnrollmentApproveRequest(StrictModel):
    user_code: str = Field(min_length=9, max_length=9)


class RecorderTokenRequest(StrictModel):
    device_code: str | None = Field(default=None, min_length=32, max_length=256)
    device_id: str | None = Field(default=None, min_length=8, max_length=64)

    @model_validator(mode="after")
    def exactly_one_grant(self) -> "RecorderTokenRequest":
        if (self.device_code is None) == (self.device_id is None):
            raise ValueError("provide exactly one of device_code or device_id")
        return self


class RecorderToken(StrictModel):
    access_token: str
    token_type: Literal["DPoP"] = "DPoP"
    expires_in: Literal[600] = 600
    scope: Literal["recorder.write gate.check"] = "recorder.write gate.check"
    device_id: str
    trust_state: TrustState
    key_thumbprint: str
    config_signing_jwk: PublicJWK
    config_signing_kid: str


class RecorderConfigPayload(StrictModel):
    protocol: Literal["meshagent.recorder.config.v1"] = "meshagent.recorder.config.v1"
    version: int = Field(ge=1)
    device_id: str
    deployment: str
    trust_state: TrustState
    issued_at: int
    expires_at: int
    token_ttl_seconds: Literal[600] = 600
    heartbeat_interval_seconds: int = Field(ge=30, le=3600)
    max_queue_batches: int = Field(ge=1, le=10000)
    max_queue_bytes: int = Field(ge=1024, le=104857600)


class SignedRecorderConfig(StrictModel):
    payload: RecorderConfigPayload
    digest_sha256: str = Field(min_length=64, max_length=64)
    signature: str
    kid: str


class HeartbeatRequest(StrictModel):
    recorder_version: str = Field(min_length=1, max_length=64)
    platform: Platform
    platform_version: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=32)
    config_version: int = Field(ge=0)
    adapter_state: Literal["ready", "disabled", "degraded", "unknown"]
    queue_batches: int = Field(ge=0, le=10000)
    queue_bytes: int = Field(ge=0, le=104857600)
    oldest_queued_age_seconds: int = Field(ge=0, le=31536000)
    delivery_state: DeliveryState


class HeartbeatReceipt(StrictModel):
    device_id: str
    accepted_at: int
    trust_state: TrustState
    version: int
    next_heartbeat_seconds: int


class RecorderSummary(StrictModel):
    id: str
    label: str
    credential_kind: CredentialKind
    subject: str
    name: str
    verified: bool
    deployment: str
    platform: str
    recorder_version: str
    key_thumbprint: str | None
    attestation_format: str | None
    trust_state: TrustState
    version: int
    config_version: int
    reported_config_version: int
    config_status: Literal["unknown", "current", "stale"]
    config_digest: str | None
    enrolled_at: int
    last_seen_at: int
    queue_batches: int
    queue_bytes: int
    oldest_queued_age_seconds: int
    adapter_state: str
    delivery_state: DeliveryState
    legacy_retire_at: int | None


class RecorderHistoryEntry(StrictModel):
    receipt_id: str
    action: str
    actor: str
    actor_name: str
    prior_state: TrustState
    next_state: TrustState
    resulting_version: int
    reason: str
    created_at: int
    correlation_id: str


class RecorderDetail(RecorderSummary):
    email: str
    platform_version: str
    architecture: str
    approved_at: int
    history: list[RecorderHistoryEntry] = Field(default_factory=list, max_length=200)


class RecorderList(StrictModel):
    recorders: list[RecorderSummary]
    total: int
    action_required: int


class RecorderOnboardingStatus(StrictModel):
    identity_provider: bool
    signing_ready: bool
    enrolled: int
    active: int
    attention: int
    legacy: int
    legacy_retire_at: int | None


class RecorderTrustRequest(StrictModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class RecorderTrustReceipt(StrictModel):
    receipt_id: str
    device_id: str
    action: Literal["quarantine", "revoke"]
    actor: str
    actor_name: str
    prior_state: TrustState
    next_state: TrustState
    resulting_version: int
    reason: str
    created_at: int
    correlation_id: str
    idempotency_key: str


class RecorderError(StrictModel):
    detail: str
    code: str
    metadata: dict[str, Any] = Field(default_factory=dict)
