"""Durable enterprise recorder enrollment and trust control plane."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import dpop
from .auth import AuthError, Principal
from .recorder_models import (
    EnrollmentPending,
    EnrollmentRequest,
    EnrollmentStart,
    HeartbeatReceipt,
    HeartbeatRequest,
    RecorderConfigPayload,
    RecorderDetail,
    RecorderHistoryEntry,
    RecorderList,
    RecorderOnboardingStatus,
    RecorderSummary,
    RecorderToken,
    RecorderTrustReceipt,
    SignedRecorderConfig,
)

PAIRING_TTL = 600
MAX_PENDING_ENROLLMENTS = 1000
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"


class RecorderError(Exception):
    pass


class Missing(RecorderError):
    pass


class VersionConflict(RecorderError):
    pass


class Conflict(RecorderError):
    pass


class TrustDenied(AuthError):
    pass


def _canonical(value: Any) -> str:
    return dpop.canonical_json(value).decode()


def _digest(value: Any) -> str:
    return hashlib.sha256(dpop.canonical_json(value)).hexdigest()


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> int:
    return int(time.time())


def _user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class Store:
    def __init__(
        self,
        base: str,
        authority: dpop.SigningAuthority | None = None,
        deployment_id: str | None = None,
    ) -> None:
        self.base = base
        Path(base).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(base) / "recorder-control.sqlite3")
        self.authority = authority or dpop.SigningAuthority.load(base)
        self.deployment_id = (
            deployment_id
            if deployment_id is not None
            else os.environ.get("MESHAGENT_DEPLOYMENT_ID", "").strip()
        )
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self._db.close()

    def _migrate(self) -> None:
        root = Path(__file__).with_name("recorder_migrations")
        with self._lock:
            for path in sorted(root.glob("*.sql")):
                self._db.executescript(path.read_text())
            columns = {
                row[1] for row in self._db.execute(
                    "PRAGMA table_info(recorder_devices)"
                ).fetchall()
            }
            if "reported_config_version" not in columns:
                self._db.execute(
                    "ALTER TABLE recorder_devices ADD COLUMN "
                    "reported_config_version INTEGER NOT NULL DEFAULT 0"
                )

    def _transaction(self):
        return _Transaction(self._db, self._lock)

    def start_enrollment(self, request: EnrollmentRequest, verify_url: str) -> EnrollmentStart:
        body = request.model_dump(mode="json")
        if self.deployment_id and body["deployment"] != self.deployment_id:
            raise Conflict("recorder enrollment targets another deployment")
        public = body["public_jwk"]
        key_thumbprint = dpop.thumbprint(public)
        enrollment_id = f"enr_{secrets.token_hex(12)}"
        device_code = secrets.token_urlsafe(32)
        started = _now()
        with self._transaction() as db:
            db.execute(
                "DELETE FROM recorder_enrollments WHERE expires_at < ? AND claimed_at=0",
                (started,),
            )
            pending = db.execute(
                "SELECT COUNT(*) FROM recorder_enrollments WHERE claimed_at=0"
            ).fetchone()[0]
            if pending >= MAX_PENDING_ENROLLMENTS:
                raise Conflict("enterprise enrollment capacity is temporarily full")
            for _ in range(8):
                user_code = _user_code()
                try:
                    db.execute(
                        """INSERT INTO recorder_enrollments(
                          id,user_code,device_code_hash,request_digest,request_json,
                          started_at,expires_at
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            enrollment_id, user_code, _secret_digest(device_code),
                            _digest(body), _canonical(body), started, started + PAIRING_TTL,
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RecorderError("could not allocate enrollment code")
        return EnrollmentStart(
            enrollment_id=enrollment_id,
            device_code=device_code,
            user_code=user_code,
            expires_in=PAIRING_TTL,
            verify_url=f"{verify_url}?code={user_code}",
        )

    def pending(self, user_code: str) -> EnrollmentPending:
        row = self._db.execute(
            "SELECT * FROM recorder_enrollments WHERE user_code=?",
            (user_code.strip().upper(),),
        ).fetchone()
        if row is None or row["expires_at"] < _now():
            raise Missing("no active enterprise enrollment with that code")
        request = json.loads(row["request_json"])
        return EnrollmentPending(
            enrollment_id=row["id"], user_code=row["user_code"],
            label=request["label"], deployment=request["deployment"],
            platform=request["platform"], platform_version=request["platform_version"],
            architecture=request["architecture"], recorder_version=request["recorder_version"],
            key_thumbprint=dpop.thumbprint(request["public_jwk"]),
            attestation_format=request["attestation_format"],
            approved=bool(row["approved_at"]), started_at=row["started_at"],
        )

    def approve(self, user_code: str, who: Principal) -> EnrollmentPending:
        if who.device:
            raise AuthError("a recorder credential cannot approve enrollment")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM recorder_enrollments WHERE user_code=?",
                (user_code.strip().upper(),),
            ).fetchone()
            if row is None or row["expires_at"] < _now():
                raise Missing("no active enterprise enrollment with that code")
            if row["approved_at"]:
                raise Conflict("enterprise enrollment was already approved")
            db.execute(
                """UPDATE recorder_enrollments SET approved_at=?, approved_subject=?,
                approved_name=?, approved_email=?, approved_verified=? WHERE id=?""",
                (_now(), who.subject, who.name, who.email, int(who.verified), row["id"]),
            )
        return self.pending(user_code)

    def enrollment_for_device_code(self, device_code: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM recorder_enrollments WHERE device_code_hash=?",
            (_secret_digest(device_code),),
        ).fetchone()
        if row is None or row["expires_at"] < _now():
            raise AuthError("enterprise enrollment rejected")
        if not row["approved_at"]:
            raise Conflict("enterprise enrollment is waiting for approval")
        if row["claimed_at"]:
            raise AuthError("enterprise enrollment was already claimed")
        return row

    def claim(
        self,
        device_code: str,
        *,
        proof: str,
        method: str,
        target: str,
    ) -> RecorderToken:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM recorder_enrollments WHERE device_code_hash=?",
                (_secret_digest(device_code),),
            ).fetchone()
            if row is None or row["expires_at"] < _now() or row["claimed_at"]:
                raise AuthError("enterprise enrollment rejected")
            if not row["approved_at"]:
                raise Conflict("enterprise enrollment is waiting for approval")
            request = json.loads(row["request_json"])
            wanted = dpop.thumbprint(request["public_jwk"])
            verified = dpop.verify_proof(
                proof, method=method, target=target, access_token=None,
                expected_thumbprint=wanted,
            )
            self._consume_proof_tx(db, verified, method, target)
            now = _now()
            device_id = f"rec_{secrets.token_hex(12)}"
            db.execute(
                """INSERT INTO recorder_devices(
                  id,label,credential_kind,subject,name,email,verified,deployment,
                  platform,platform_version,architecture,recorder_version,
                  public_jwk_json,key_thumbprint,attestation_format,trust_state,
                  version,config_version,enrolled_at,approved_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    device_id, request["label"], "dpop", row["approved_subject"],
                    row["approved_name"], row["approved_email"], row["approved_verified"],
                    request["deployment"], request["platform"], request["platform_version"],
                    request["architecture"], request["recorder_version"],
                    _canonical(request["public_jwk"]), wanted,
                    request["attestation_format"], "active", 1, 1, now,
                    row["approved_at"], now,
                ),
            )
            db.execute(
                "UPDATE recorder_enrollments SET claimed_at=?,device_id=? WHERE id=?",
                (now, device_id, row["id"]),
            )
        return self.issue_token(device_id)

    def issue_token(self, device_id: str) -> RecorderToken:
        row = self._device_row(device_id)
        if row["credential_kind"] != "dpop" or row["trust_state"] != "active":
            raise TrustDenied("recorder trust does not permit token issuance")
        token = self.authority.mint_access_token(
            device_id=row["id"], subject=row["subject"], name=row["name"],
            email=row["email"], verified=bool(row["verified"]),
            key_thumbprint=row["key_thumbprint"],
        )
        return RecorderToken(
            access_token=token, device_id=row["id"], trust_state=row["trust_state"],
            key_thumbprint=row["key_thumbprint"],
            config_signing_jwk=self.authority.public,
            config_signing_kid=self.authority.kid,
        )

    def refresh_token(
        self, device_id: str, *, proof: str, method: str, target: str,
    ) -> RecorderToken:
        row = self._device_row(device_id)
        if row["credential_kind"] != "dpop" or row["trust_state"] != "active":
            raise TrustDenied("recorder trust does not permit token issuance")
        verified = dpop.verify_proof(
            proof, method=method, target=target, access_token=None,
            expected_thumbprint=row["key_thumbprint"],
        )
        with self._transaction() as db:
            self._consume_proof_tx(db, verified, method, target)
        return self.issue_token(device_id)

    def authenticate(
        self, *, token: str, proof: str, method: str, target: str,
    ) -> Principal:
        claims = self.authority.verify_access_token(token)
        device_id = str(claims.get("device_id", ""))
        row = self._device_row(device_id)
        if row["credential_kind"] != "dpop" or row["trust_state"] != "active":
            raise TrustDenied("recorder trust does not permit delivery")
        if claims.get("sub") != row["subject"]:
            raise AuthError("recorder credential rejected")
        cnf = claims.get("cnf")
        wanted = row["key_thumbprint"]
        if not isinstance(cnf, dict) or cnf.get("jkt") != wanted:
            raise AuthError("recorder credential rejected")
        verified = dpop.verify_proof(
            proof, method=method, target=target, access_token=token,
            expected_thumbprint=wanted,
        )
        with self._transaction() as db:
            self._consume_proof_tx(db, verified, method, target)
        return Principal(
            subject=row["subject"], name=row["name"], email=row["email"],
            role="developer", verified=bool(row["verified"]), device=row["id"],
        )

    def _consume_proof_tx(
        self, db: sqlite3.Connection, proof: dpop.VerifiedProof,
        method: str, target: str,
    ) -> None:
        now = _now()
        db.execute("DELETE FROM recorder_dpop_replay WHERE expires_at < ?", (now,))
        try:
            db.execute(
                "INSERT INTO recorder_dpop_replay(jti,key_thumbprint,method,target,expires_at) VALUES(?,?,?,?,?)",
                (proof.jti, proof.key_thumbprint, method.upper(), dpop.normalize_target(target), now + 300),
            )
        except sqlite3.IntegrityError as exc:
            raise AuthError("recorder proof rejected") from exc

    def signed_config(self, device_id: str) -> SignedRecorderConfig:
        with self._transaction() as db:
            now = _now()
            row = db.execute(
                "SELECT * FROM recorder_devices WHERE id=?", (device_id,),
            ).fetchone()
            if row is None:
                raise Missing("unknown recorder")
            if row["trust_state"] != "active":
                raise TrustDenied("recorder trust does not permit configuration")
            existing = db.execute(
                "SELECT * FROM recorder_configurations WHERE device_id=? AND version=?",
                (row["id"], row["config_version"]),
            ).fetchone()
            if existing is not None and existing["expires_at"] > now + 300:
                return SignedRecorderConfig(
                    payload=RecorderConfigPayload.model_validate_json(existing["payload_json"]),
                    digest_sha256=existing["digest_sha256"],
                    signature=existing["signature"],
                    kid=existing["kid"],
                )
            config_version = row["config_version"]
            if existing is not None:
                config_version += 1
                db.execute(
                    "UPDATE recorder_devices SET config_version=?,updated_at=? WHERE id=?",
                    (config_version, now, row["id"]),
                )
            payload = RecorderConfigPayload(
                version=config_version, device_id=row["id"],
                deployment=row["deployment"], trust_state=row["trust_state"],
                issued_at=now, expires_at=now + 86400,
                heartbeat_interval_seconds=300, max_queue_batches=200,
                max_queue_bytes=5 * 1024 * 1024,
            )
            dumped = payload.model_dump(mode="json")
            digest = _digest(dumped)
            signature = self.authority.sign_configuration(dumped)
            db.execute(
                """INSERT INTO recorder_configurations(
                  device_id,version,payload_json,digest_sha256,signature,kid,issued_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (row["id"], config_version, _canonical(dumped), digest,
                 signature, self.authority.kid, now, now + 86400),
            )
            db.execute(
                "UPDATE recorder_devices SET config_digest=?,updated_at=? WHERE id=?",
                (digest, now, row["id"]),
            )
        return SignedRecorderConfig(
            payload=payload, digest_sha256=digest, signature=signature,
            kid=self.authority.kid,
        )

    def heartbeat(self, device_id: str, request: HeartbeatRequest) -> HeartbeatReceipt:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM recorder_devices WHERE id=?", (device_id,)).fetchone()
            if row is None:
                raise Missing("unknown recorder")
            if row["trust_state"] != "active":
                raise TrustDenied("recorder trust does not permit heartbeat")
            if request.config_version > row["config_version"]:
                raise Conflict("recorder reports an unknown future configuration version")
            now = _now()
            db.execute(
                """UPDATE recorder_devices SET recorder_version=?,platform=?,
                platform_version=?,architecture=?,last_seen_at=?,queue_batches=?,
                queue_bytes=?,oldest_queued_age_seconds=?,adapter_state=?,
                delivery_state=?,reported_config_version=?,updated_at=? WHERE id=?""",
                (
                    request.recorder_version, request.platform, request.platform_version,
                    request.architecture, now, request.queue_batches, request.queue_bytes,
                    request.oldest_queued_age_seconds, request.adapter_state,
                    request.delivery_state, request.config_version, now, device_id,
                ),
            )
        return HeartbeatReceipt(
            device_id=device_id, accepted_at=now, trust_state="active",
            version=row["version"], next_heartbeat_seconds=300,
        )

    def list(self) -> RecorderList:
        rows = self._db.execute(
            "SELECT * FROM recorder_devices ORDER BY trust_state!='active' DESC,last_seen_at ASC,enrolled_at DESC"
        ).fetchall()
        recorders = [self._summary(row) for row in rows]
        return RecorderList(
            recorders=recorders, total=len(recorders),
            action_required=sum(
                r.trust_state != "active"
                or r.delivery_state in ("blocked", "offline")
                or r.config_status == "stale"
                for r in recorders
            ),
        )

    def detail(self, device_id: str) -> RecorderDetail:
        row = self._device_row(device_id)
        history_rows = self._db.execute(
            "SELECT * FROM recorder_receipts WHERE device_id=? ORDER BY created_at DESC LIMIT 200",
            (device_id,),
        ).fetchall()
        summary = self._summary(row).model_dump()
        return RecorderDetail(
            **summary, email=row["email"], platform_version=row["platform_version"],
            architecture=row["architecture"], approved_at=row["approved_at"],
            history=[self._history(item) for item in history_rows],
        )

    def onboarding(self, identity_provider: bool) -> RecorderOnboardingStatus:
        rows = self.list().recorders
        legacy = sum(r.credential_kind == "legacy_bearer" for r in rows)
        deadlines = [r.legacy_retire_at for r in rows if r.legacy_retire_at]
        return RecorderOnboardingStatus(
            identity_provider=identity_provider, signing_ready=True, enrolled=len(rows),
            active=sum(r.trust_state == "active" for r in rows),
            attention=sum(r.trust_state != "active" for r in rows), legacy=legacy,
            legacy_retire_at=min(deadlines) if deadlines else None,
        )

    def transition(
        self, *, device_id: str, action: str, expected_version: int,
        reason: str, actor: Principal, idempotency_key: str, correlation_id: str,
    ) -> RecorderTrustReceipt:
        if action not in ("quarantine", "revoke"):
            raise Conflict("unsupported recorder trust action")
        key = idempotency_key.strip()
        if not (8 <= len(key) <= 128):
            raise Conflict("Idempotency-Key must be between 8 and 128 characters")
        request_body = {
            "device_id": device_id, "action": action,
            "expected_version": expected_version, "reason": reason,
        }
        request_digest = _digest(request_body)
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM recorder_receipts WHERE actor=? AND action=? AND idempotency_key=?",
                (actor.subject, action, key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise Conflict("Idempotency-Key was already used for another request")
                return RecorderTrustReceipt(**json.loads(existing["response_json"]))
            row = db.execute("SELECT * FROM recorder_devices WHERE id=?", (device_id,)).fetchone()
            if row is None:
                raise Missing("unknown recorder")
            if row["version"] != expected_version:
                raise VersionConflict("recorder changed; reload before deciding")
            prior = row["trust_state"]
            if action == "quarantine":
                if prior != "active":
                    raise Conflict("only an active recorder can be quarantined")
                next_state = "quarantined"
            else:
                if prior not in ("active", "quarantined"):
                    raise Conflict("recorder is already revoked")
                next_state = "revoked"
            now = _now()
            next_version = expected_version + 1
            db.execute(
                "UPDATE recorder_devices SET trust_state=?,version=?,updated_at=? WHERE id=?",
                (next_state, next_version, now, device_id),
            )
            receipt = RecorderTrustReceipt(
                receipt_id=f"rrc_{secrets.token_hex(12)}", device_id=device_id,
                action=action, actor=actor.subject, actor_name=actor.name,
                prior_state=prior, next_state=next_state,
                resulting_version=next_version, reason=reason, created_at=now,
                correlation_id=correlation_id, idempotency_key=key,
            )
            db.execute(
                """INSERT INTO recorder_receipts(
                  receipt_id,device_id,action,actor,actor_name,prior_state,next_state,
                  resulting_version,reason,created_at,correlation_id,idempotency_key,
                  request_digest,response_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt.receipt_id, device_id, action, actor.subject, actor.name,
                    prior, next_state, next_version, reason, now, correlation_id,
                    key, request_digest, _canonical(receipt.model_dump(mode="json")),
                ),
            )
        return receipt

    def import_legacy(self, legacy_devices: Iterable[Any]) -> None:
        retire_raw = os.environ.get("MESHAGENT_LEGACY_RECORDER_RETIRE_AT", "").strip()
        try:
            retire_at = int(retire_raw) if retire_raw else None
        except ValueError:
            retire_at = None
        now = _now()
        with self._transaction() as db:
            for device in legacy_devices:
                state = "active" if device.active else "revoked"
                db.execute(
                    """INSERT INTO recorder_devices(
                      id,label,credential_kind,subject,name,email,verified,deployment,
                      platform,platform_version,architecture,recorder_version,
                      public_jwk_json,key_thumbprint,attestation_format,trust_state,
                      version,config_version,enrolled_at,approved_at,last_seen_at,
                      legacy_retire_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      label=excluded.label,subject=excluded.subject,name=excluded.name,
                      email=excluded.email,verified=excluded.verified,
                      trust_state=excluded.trust_state,last_seen_at=excluded.last_seen_at,
                      legacy_retire_at=excluded.legacy_retire_at,updated_at=excluded.updated_at
                    WHERE recorder_devices.credential_kind='legacy_bearer'""",
                    (
                        device.id, device.label, "legacy_bearer", device.subject,
                        device.name, device.email, int(device.verified), "legacy",
                        "unknown", "unknown", "unknown", "legacy", None, None,
                        None, state, 1, 1, device.created_at, device.created_at,
                        device.last_used, retire_at, now,
                    ),
                )

    def trust_state(self, device_id: str) -> str:
        return self._device_row(device_id)["trust_state"]

    def _device_row(self, device_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM recorder_devices WHERE id=?", (device_id,)
        ).fetchone()
        if row is None:
            raise Missing("unknown recorder")
        return row

    @staticmethod
    def _summary(row: sqlite3.Row) -> RecorderSummary:
        reported_config_version = row["reported_config_version"]
        config_status = (
            "unknown" if reported_config_version == 0
            else "current" if reported_config_version == row["config_version"]
            else "stale"
        )
        return RecorderSummary(
            id=row["id"], label=row["label"], credential_kind=row["credential_kind"],
            subject=row["subject"], name=row["name"], verified=bool(row["verified"]),
            deployment=row["deployment"], platform=row["platform"],
            recorder_version=row["recorder_version"], key_thumbprint=row["key_thumbprint"],
            attestation_format=row["attestation_format"], trust_state=row["trust_state"],
            version=row["version"], config_version=row["config_version"],
            reported_config_version=reported_config_version,
            config_status=config_status,
            config_digest=row["config_digest"], enrolled_at=row["enrolled_at"],
            last_seen_at=row["last_seen_at"], queue_batches=row["queue_batches"],
            queue_bytes=row["queue_bytes"],
            oldest_queued_age_seconds=row["oldest_queued_age_seconds"],
            adapter_state=row["adapter_state"], delivery_state=row["delivery_state"],
            legacy_retire_at=row["legacy_retire_at"],
        )

    @staticmethod
    def _history(row: sqlite3.Row) -> RecorderHistoryEntry:
        return RecorderHistoryEntry(
            receipt_id=row["receipt_id"], action=row["action"], actor=row["actor"],
            actor_name=row["actor_name"], prior_state=row["prior_state"],
            next_state=row["next_state"], resulting_version=row["resulting_version"],
            reason=row["reason"], created_at=row["created_at"],
            correlation_id=row["correlation_id"],
        )


class _Transaction:
    def __init__(self, db: sqlite3.Connection, lock: threading.RLock) -> None:
        self.db = db
        self.lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.lock.release()


def load(base: str) -> Store:
    return Store(base)
