"""MeshAgent API. REST for queries and graphs, WebSocket for the live
'memory forming' stream. Thin: all data comes from the gateway."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from . import (
    __version__,
    audit,
    auth,
    browser_sessions,
    control_plane,
    developer_sessions,
    devices,
    paths,
    review_service,
    session_projection,
)
from .auth import AuthError, Forbidden, Principal
from .gateway import Conflict, NotFound, Stale, get_gateway
from .models import (
    ApplyReceipt,
    ApproveRequest,
    AuditEntry,
    AuditOut,
    CodeOut,
    CoverageOut,
    CreateRunRequest,
    CveImpact,
    DecompositionOut,
    DeletionCertificate,
    DeviceOut,
    Finding,
    FindingsOut,
    FleetOverview,
    ForgetPreview,
    ForgetRequest,
    GateDecision,
    GateRequest,
    GraphPayload,
    HealthOut,
    HypergraphOut,
    Me,
    PairPending,
    PairRequest,
    PairStart,
    PairToken,
    QueryRequest,
    QueryResult,
    RecorderBatch,
    RecorderReceipt,
    Recommendation,
    RewindOut,
    RunStreamEvent,
    RunSummary,
    SbomOut,
    ScaleOut,
    ScanOut,
    SarifDocument,
    WhyOut,
)
from .developer_session_models import (
    ActivityBatchRequest,
    ActivityIngestReceipt,
    ActivityListOut,
    DeveloperSessionOut,
    PolicyEvaluationListOut,
    SessionListOut,
    SessionStartRequest,
)
from .workflow_models import (
    ApprovalDecisionRequest,
    ApprovalOut,
    AssignCaseRequest,
    AttentionListOut,
    CaseListOut,
    CaseOut,
    CisoOverviewOut,
    CreateCaseRequest,
    CreateExceptionRequest,
    CreatePolicyRequest,
    CreateRemediationRequest,
    CreateReviewRequest,
    ExceptionOut,
    OriginOut,
    PolicyOut,
    RemediationOut,
    ReportOut,
    ReportRequest,
    ReviewDecisionRequest,
    ReviewGraphOut,
    ReviewRequestListOut,
    ReviewRequestOut,
    TransitionCaseRequest,
)

logger = logging.getLogger(__name__)

def validate_startup() -> None:
    """Reject incomplete production configuration before serving traffic."""
    environment = auth.environment()  # validates the closed MESHAGENT_ENV set
    if environment != "production":
        return

    cfg = auth.config()
    parsed = urlparse(_web_url())
    problems: list[str] = []
    if os.environ.get("MESHAGENT_ENGINE", "").strip() != "1":
        problems.append("MESHAGENT_ENGINE=1")
    if not cfg.issuer:
        problems.append("MESHAGENT_OIDC_ISSUER")
    if not cfg.audience:
        problems.append("MESHAGENT_OIDC_AUDIENCE")
    if not os.environ.get("MESHAGENT_OIDC_CLIENT_ID", "").strip():
        problems.append("MESHAGENT_OIDC_CLIENT_ID")
    if not cfg.analyst_groups:
        problems.append("MESHAGENT_ANALYST_GROUPS")
    if not cfg.ciso_groups:
        problems.append("MESHAGENT_CISO_GROUPS")
    if set(cfg.analyst_groups) & set(cfg.ciso_groups):
        problems.append("MESHAGENT role groups (Analyst and CISO must not overlap)")
    db_raw = os.environ.get("MESHAGENT_DB_DIR", "").strip()
    if not db_raw:
        problems.append("MESHAGENT_DB_DIR")
    else:
        db_path = Path(db_raw).expanduser()
        temp_path = Path(tempfile.gettempdir()).resolve()
        try:
            resolved_db = db_path.resolve()
        except OSError:
            resolved_db = db_path.absolute()
        if not db_path.is_absolute() or (
            resolved_db == temp_path or temp_path in resolved_db.parents
        ):
            problems.append("MESHAGENT_DB_DIR (must be absolute and non-temporary)")
    if not audit.strict_enabled():
        problems.append("MESHAGENT_STRICT_AUDIT")
    if parsed.scheme != "https" or not parsed.netloc:
        problems.append("MESHAGENT_WEB_URL (must be an HTTPS URL)")
    cors = [
        origin.strip()
        for origin in os.environ.get("MESHAGENT_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    web_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    if (
        not cors
        or "*" in cors
        or any(urlparse(origin).scheme != "https" for origin in cors)
        or web_origin not in cors
    ):
        problems.append(
            "MESHAGENT_CORS_ORIGINS (must contain the exact HTTPS web origin)"
        )
    if problems:
        raise RuntimeError(
            "production startup requires: " + ", ".join(problems))


@asynccontextmanager
async def _lifespan(_: FastAPI):
    validate_startup()
    developer_session_store.reset_interrupted_projections()
    await asyncio.to_thread(_reconcile_developer_session_projections)
    projection_task = asyncio.create_task(
        _projection_reconciler(), name="developer-session-projection-reconciler"
    )
    try:
        yield
    finally:
        projection_task.cancel()
        with suppress(asyncio.CancelledError):
            await projection_task


app = FastAPI(title="MeshAgent API", version=__version__, lifespan=_lifespan)

_origins = os.environ.get(
    "MESHAGENT_CORS_ORIGINS", "http://localhost:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

gateway = get_gateway()


@app.exception_handler(NotFound)
def _not_found(_: Request, exc: NotFound) -> JSONResponse:
    """The gateways raise NotFound so neither has to know about HTTP."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AuthError)
def _unauthenticated(_: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": str(exc)},
                        headers={"WWW-Authenticate": "Bearer"})


@app.exception_handler(Forbidden)
def _forbidden(_: Request, exc: Forbidden) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(Stale)
def _stale(_: Request, exc: Stale) -> JSONResponse:
    """412, the precondition the caller stated did not hold. Nothing was
    destroyed, which is the only outcome worth reporting here."""
    return JSONResponse(status_code=412, content={"detail": str(exc)})


@app.exception_handler(Conflict)
def _conflict(_: Request, exc: Conflict) -> JSONResponse:
    """The key exists, but it identifies a different request."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(control_plane.Missing)
def _workflow_missing(_: Request, exc: control_plane.Missing) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(control_plane.VersionConflict)
def _workflow_version(_: Request, exc: control_plane.VersionConflict) -> JSONResponse:
    return JSONResponse(status_code=412, content={"detail": str(exc)})


@app.exception_handler(control_plane.SeparationConflict)
def _workflow_separation(
    _: Request, exc: control_plane.SeparationConflict
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(control_plane.StoreError)
def _workflow_conflict(_: Request, exc: control_plane.StoreError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(developer_sessions.MissingSession)
@app.exception_handler(developer_sessions.SessionAccessDenied)
def _developer_session_missing(
    _: Request, exc: developer_sessions.SessionStoreError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(developer_sessions.SequenceConflict)
def _developer_session_sequence(
    _: Request, exc: developer_sessions.SequenceConflict
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "code": "sequence_conflict",
            "expected_sequence": exc.expected,
            "received_sequence": exc.received,
        },
    )


@app.exception_handler(developer_sessions.ReplayConflict)
@app.exception_handler(developer_sessions.LifecycleConflict)
def _developer_session_conflict(
    _: Request, exc: developer_sessions.SessionStoreError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# -- who is asking ------------------------------------------------------------

_verifier: auth.Verifier | None = None


def _bearer(header: str | None) -> str:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError("this deployment requires an access token")
    return header.split(" ", 1)[1].strip()


def _optional_bearer(header: str | None) -> str | None:
    """The token if one is there, without insisting on it. The insisting
    happens in `identify`, which knows whether a provider is configured."""
    if not header or not header.lower().startswith("bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _expected_browser_origin() -> str:
    parsed = urlparse(_web_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def _require_same_origin(origin: str | None) -> None:
    """Protect opaque-cookie sessions from cross-site request forgery.

    Bearer and local-development-header clients do not use ambient credentials
    and therefore do not pass through this check.
    """
    expected = _expected_browser_origin()
    if not origin or not secrets.compare_digest(origin.rstrip("/"), expected):
        raise Forbidden("browser session request has an invalid origin")


def identify(token: str | None, dev_user: str | None,
             dev_role: str | None) -> Principal:
    """The caller, from an OIDC token when a provider is configured.

    With no provider this falls back to the identity the client asserts,
    marked unverified. The two paths never mix: once an issuer is set the
    development headers are not read at all, so a deployment cannot be
    talked out of authenticating by sending one."""
    global _verifier
    cfg = auth.config()
    if not cfg.enabled:
        if not auth.allows_local_asserted_identities():
            raise AuthError("local asserted identities are disabled in production")
        return auth.local_principal(dev_user, dev_role)
    if _verifier is None:
        _verifier = auth.Verifier(cfg)
    return _verifier.verify(token or "")


def machine(request: Request) -> Principal | None:
    """The device behind this request, if it is a machine rather than a person.

    Checked before anything else and in every mode, including local: a
    deployment with no identity provider still wants a hook's writes
    attributed to the developer who ran `meshagent login`, rather than to
    whatever name the hook felt like putting in a header."""
    token = _optional_bearer(request.headers.get("authorization"))
    if not token or not token.startswith(devices.PREFIX):
        return None
    return device_store.verify(token)


def caller(request: Request) -> Principal:
    """FastAPI dependency: the authenticated human, or 401.

    A device token is turned away here rather than anywhere further in. This
    is the whole containment story for a credential that sits in a file on a
    laptop: it reaches the two routes that opt into `recorder` below, and
    every other route in this file refuses it without either side having to
    remember to check."""
    if (dev := machine(request)) is not None:
        raise Forbidden(
            f"{dev.name}'s device token may only record; sign in to do this")
    cfg = auth.config()
    if cfg.enabled:
        session = browser_session_store.resolve(
            request.cookies.get(browser_sessions.session_cookie_name()))
        if session is not None:
            if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                _require_same_origin(request.headers.get("origin"))
            return session.principal
    token = _bearer(request.headers.get("authorization")) if cfg.enabled else None
    return identify(token,
                    request.headers.get(auth.DEV_USER),
                    request.headers.get(auth.DEV_ROLE))


def recorder(request: Request) -> Principal:
    """A caller allowed to write memory: a registered device, or a person.

    People are still permitted because the built-in loop and the tests post
    here as themselves. What a person cannot do is the reverse."""
    return machine(request) or caller(request)


def analyst(who: Principal = Depends(caller)) -> Principal:
    """Only the security office. The fleet views read across every
    developer's memory, which is precisely what a developer must not see of
    their colleagues."""
    if not who.has("fleet.read"):
        raise Forbidden("this view belongs to the security office")
    return who


def ciso(who: Principal = Depends(caller)) -> Principal:
    """The accountable policy and approval authority."""
    if not who.ciso:
        raise Forbidden("this action requires the CISO role")
    return who


def require_capability(capability: str):
    """Create a FastAPI dependency from the server-owned capability map."""
    def dependency(who: Principal = Depends(caller)) -> Principal:
        if not who.has(capability):
            raise Forbidden(f"this action requires {capability}")
        return who
    return dependency


def _may_read(run_id: str, who: Principal) -> None:
    """A developer may only reach their own runs, and the seeded one.

    A seeded run is memory this deployment publishes as a reference build: it
    belongs to nobody and is the same for everybody. Gated on the flag and
    never on `owner` being null, because a run can end up ownerless for
    reasons that are nobody's decision to publish.

    404 rather than 403 for someone else's run: whether a colleague has a run
    by that id is itself something a developer should not learn here."""
    if who.analyst:
        return
    run = gateway.run(run_id)
    if run.seeded:
        return
    if run.owner != who.subject:
        # refusals are logged: someone walking run ids should leave a trail,
        # and the caller is told nothing either way
        note(who, "access.denied", f"run:{run_id}", "not the caller's run")
        raise NotFound(f"unknown run {run_id}")


# -- the audit log ------------------------------------------------------------

audit_log = audit.Log(base=paths.base_dir())

# Mutable workflow state belongs beside, but not inside, the evidence graph.
# Cases and approvals can change state; the HyperMesh relations they reference
# remain immutable evidence.
workflow_store = control_plane.load(paths.base_dir())

# Connected coding-agent sessions are operational state, not beliefs. Activity
# is committed here before ordered projection into governed HyperMesh memory.
developer_session_store = developer_sessions.load(paths.base_dir())


def _reconcile_developer_session_projections() -> None:
    """Retry durable projection work without requiring another editor event."""
    for session in developer_session_store.recoverable_sessions():
        principal = Principal(
            subject=session.owner_subject,
            name=session.owner_name,
            email=session.owner_subject,
            role="developer",
            verified=session.verified,
            device=session.device_id,
        )
        try:
            session_projection.project_pending(
                developer_session_store, gateway, session, principal,
            )
        except Exception:
            logger.exception(
                "developer session projection reconciliation failed",
                extra={"developer_session_id": session.id},
            )


async def _projection_reconciler() -> None:
    try:
        interval = float(os.environ.get(
            "MESHAGENT_PROJECTION_RETRY_SECONDS", "5.0"
        ))
    except ValueError:
        interval = 5.0
    interval = min(max(interval, 0.05), 300.0)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_reconcile_developer_session_projections)
        except Exception:
            logger.exception("developer session projection scan failed")

# OIDC transactions and browser sessions are server-owned. Only hashes of the
# opaque cookie values are retained; access tokens never enter browser storage.
browser_session_store = browser_sessions.Store(paths.base_dir())

#: Registered machines. Beside the audit log and the run index rather than in
#: the hypergraph: who is allowed to write to memory is not itself a belief
#: the memory holds, and `forget` must not be able to reach it.
device_store = devices.load(paths.base_dir())


def note(who: Principal, action: str, target: str, detail: str = "",
         *, require_commit: bool = False) -> None:
    """Record something a person did.

    Routine audit failures do not take down a request unless strict auditing is
    enabled. Destructive operations pass ``require_commit`` and log before the
    mutation, so a failed append/fsync prevents the destructive gateway call.
    """
    try:
        audit_log.record(
            actor=who.subject, actor_name=who.name, role=who.role,
            verified=who.verified, action=action, target=target, detail=detail)
    except OSError:
        if require_commit or audit.strict_enabled():
            raise


def _web_url() -> str:
    """Where this deployment's web app is. One place, because the CLI prints
    it and the setup screen generates commands against it, and the two
    disagreeing sends a developer to a port nothing is listening on."""
    return os.environ.get("MESHAGENT_WEB_URL", "http://localhost:5173")


def _cookie(response: JSONResponse | RedirectResponse, name: str, value: str,
            *, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, httponly=True,
        secure=auth.is_production(), samesite="lax", path="/",
    )


def _delete_cookie(response: JSONResponse | RedirectResponse, name: str) -> None:
    response.delete_cookie(
        name, path="/", httponly=True,
        secure=auth.is_production(), samesite="lax",
    )


@app.get("/auth/login", include_in_schema=False)
def browser_login(return_to: str = "/") -> RedirectResponse:
    """Begin Authorization Code + PKCE without exposing tokens to JavaScript."""
    location, transaction = browser_sessions.authorization_url(
        browser_session_store, return_to)
    response = RedirectResponse(location, status_code=302)
    _cookie(response, browser_sessions.transaction_cookie_name(), transaction,
            max_age=browser_sessions.TRANSACTION_TTL)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth/callback", include_in_schema=False)
def browser_callback(request: Request, code: str | None = None,
                     state: str | None = None,
                     error: str | None = None) -> RedirectResponse:
    """Consume a one-time OIDC transaction and establish an application session."""
    transaction = request.cookies.get(browser_sessions.transaction_cookie_name())
    if error:
        try:
            browser_session_store.consume(transaction, state)
        except AuthError:
            pass
        response = RedirectResponse("/?auth_error=provider", status_code=303)
        _delete_cookie(response, browser_sessions.transaction_cookie_name())
        response.headers["Cache-Control"] = "no-store"
        return response

    global _verifier
    try:
        if _verifier is None:
            _verifier = auth.Verifier(auth.config())
        session, expires_at, return_to = browser_sessions.exchange_code(
            browser_session_store, transaction=transaction, state=state,
            code=code, verifier=_verifier)
    except AuthError:
        response = RedirectResponse("/?auth_error=failed", status_code=303)
        _delete_cookie(response, browser_sessions.transaction_cookie_name())
        response.headers["Cache-Control"] = "no-store"
        return response

    response = RedirectResponse(return_to, status_code=303)
    _cookie(response, browser_sessions.session_cookie_name(), session,
            max_age=max(1, expires_at - int(time.time())))
    _delete_cookie(response, browser_sessions.transaction_cookie_name())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth/session", include_in_schema=False)
def browser_session(request: Request) -> JSONResponse:
    session = browser_session_store.resolve(
        request.cookies.get(browser_sessions.session_cookie_name()))
    if session is None:
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "expires_at": None,
                     "reauth_required": True},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        content={"authenticated": True, "expires_at": session.expires_at,
                 "reauth_required": False},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/auth/logout", include_in_schema=False)
def browser_logout(request: Request) -> JSONResponse:
    _require_same_origin(request.headers.get("origin"))
    browser_session_store.revoke(
        request.cookies.get(browser_sessions.session_cookie_name()))
    response = JSONResponse(
        content={"signed_out": True}, headers={"Cache-Control": "no-store"})
    _delete_cookie(response, browser_sessions.session_cookie_name())
    return response


def _checkout() -> str:
    """The repository root, as this process sees it.

    The hooks shipped under adapters/ have to be invoked by absolute path: the
    editor running them has the DEVELOPER'S repository as its working
    directory, not this one, so a relative path resolves to a file that is not
    there and the hook fails silently. This is the only party that knows where
    the adapters actually are -- and it still does not know the editor is on
    this filesystem, which is why it is reported rather than asserted."""
    configured = os.environ.get("MESHAGENT_CHECKOUT", "").strip()
    if configured:
        return configured
    here = Path(__file__).resolve()
    return str(next(
        (parent for parent in here.parents if (parent / "adapters").is_dir()),
        Path.cwd(),
    ))


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    """What this deployment is and whether it keeps anything.

    Unauthenticated, and has to stay that way: the banner saying a recording
    will be discarded is most needed exactly where nothing can answer
    /api/me."""
    return HealthOut(
        status="ok", version=__version__, gateway=type(gateway).__name__,
        mode=gateway.mode(), durable=paths.durable(),
        identity_provider=auth.config().enabled,
        web_url=_web_url(), checkout=_checkout(),
    )


@app.get("/api/me", response_model=Me)
def me(who: Principal = Depends(caller)) -> Me:
    """Who the API believes the caller is, and whether it checked."""
    return Me(subject=who.subject, name=who.name, email=who.email,
              role=who.role, primary_role=who.role,
              capabilities=sorted(who.capabilities), verified=who.verified)


# -- device tokens: logging in a thing that has no browser --------------------

def _device_out(d: devices.Device) -> DeviceOut:
    return DeviceOut(id=d.id, label=d.label, subject=d.subject, name=d.name,
                     role=d.role, created_at=d.created_at,
                     last_used=d.last_used, verified=d.verified)


@app.post("/api/devices/pair", response_model=PairStart)
def pair_start(req: PairRequest) -> PairStart:
    """Begin a login from a shell. Deliberately unauthenticated: the whole
    problem is that the thing asking has no way to authenticate yet.

    This grants nothing on its own. It hands out a code that is worthless
    until a signed-in human approves it, and expires shortly if nobody
    does."""
    device_code, user_code = device_store.start(req.label)
    return PairStart(
        device_code=device_code, user_code=user_code,
        expires_in=devices.PAIRING_TTL, grants=list(devices.SCOPE),
        verify_url=_web_url() + "/developer/connections/devices",
    )


@app.get("/api/devices/pending/{user_code}", response_model=PairPending)
def pair_pending(user_code: str, _: Principal = Depends(caller)) -> PairPending:
    """What the human is about to approve, so they can recognise whether it
    is their own login. Shown before the approve button, not after."""
    try:
        p = device_store.pending(user_code)
    except AuthError as exc:
        raise NotFound(str(exc)) from exc
    return PairPending(user_code=p.user_code, label=p.label,
                       started_at=p.started_at, approved=p.approved,
                       grants=list(devices.SCOPE))


@app.post("/api/devices/approve", response_model=PairPending)
def pair_approve(req: ApproveRequest,
                 who: Principal = Depends(caller)) -> PairPending:
    """A signed-in human binds a waiting device to themselves.

    Audited, and audited as the person rather than the machine, because
    this is the moment a credential that can write memory in their name
    comes into existence."""
    p = device_store.approve(req.user_code, who)
    note(who, "device.approve", p.user_code, p.label)
    return PairPending(user_code=p.user_code, label=p.label,
                       started_at=p.started_at, approved=True,
                       grants=list(devices.SCOPE))


@app.post("/api/devices/token", response_model=PairToken)
def pair_token(req: dict[str, str]) -> PairToken:
    """The CLI polling for its token. Unauthenticated, and safe to be: the
    device code it carries is the only secret involved, and it is single
    use."""
    got = device_store.claim(str(req.get("device_code", "")))
    if got is None:
        return PairToken(status="pending", grants=list(devices.SCOPE))
    device, token = got
    note(device.principal(), "device.mint", device.id, device.label)
    return PairToken(status="granted", token=token,
                     device=_device_out(device), grants=list(devices.SCOPE))


@app.get("/api/devices", response_model=list[DeviceOut])
def list_devices(who: Principal = Depends(caller)) -> list[DeviceOut]:
    """A person's own machines; every machine only for the CISO role."""
    return [_device_out(d) for d in device_store.owned_by(who)]


@app.delete("/api/devices/{device_id}", response_model=DeviceOut)
def revoke_device(device_id: str, who: Principal = Depends(caller)) -> DeviceOut:
    """Retire a machine. Takes effect on the next request it makes; there is
    no token lifetime to wait out, which is the point of keeping the hashes
    here rather than issuing self-contained JWTs."""
    try:
        device = device_store.revoke(device_id, who)
    except AuthError as exc:
        raise NotFound(str(exc)) from exc
    note(who, "device.revoke", device.id, device.label)
    return _device_out(device)


#: Said on the wire rather than only in a docstring: a reader must not take
#: the log's silence as evidence that nothing happened.
AUDIT_COVERS = (
    "Deletions, run creations and applied recommendations, plus refused "
    "attempts to reach another developer's run, blocked installs, installs "
    "that went through unchecked, and every device registered or revoked. "
    "Reads are not recorded yet, so this log shows what was changed, not "
    "what was seen."
)


@app.get("/api/audit", response_model=AuditOut)
def audit_trail(limit: int = 200, _: Principal = Depends(analyst)) -> AuditOut:
    """The action log. Every entry commits to the digest of the one before
    it, so `intact` says whether the file still hangs together."""
    broken = audit_log.verify()
    return AuditOut(
        entries=[AuditEntry(**{k: v for k, v in vars(e).items()
                               if k != "prev"})
                 for e in audit_log.read(limit=limit)],
        intact=broken is None,
        broken_at=broken,
        covers=AUDIT_COVERS,
        durable=paths.durable(),
    )


@app.get("/api/fleet/overview", response_model=FleetOverview)
def fleet_overview(_: Principal = Depends(analyst)) -> FleetOverview:
    return gateway.fleet_overview()


@app.post("/api/fleet/query", response_model=QueryResult)
def fleet_query(req: QueryRequest,
                _: Principal = Depends(analyst)) -> QueryResult:
    return gateway.fleet_query(req.query)


@app.get("/api/recommendations", response_model=list[Recommendation])
def recommendations(_: Principal = Depends(analyst)) -> list[Recommendation]:
    return gateway.recommendations()


@app.post("/api/recommendations/{rec_id}/apply", response_model=ApplyReceipt)
def apply_recommendation(rec_id: str,
                         who: Principal = Depends(ciso)) -> ApplyReceipt:
    """Apply a recommendation and return the receipt. The receipt says whether
    governed memory actually changed, so nothing is claimed that did not."""
    # Cut recommendations delete governed memory. Audit before invoking the
    # gateway so a failed durable commit leaves the recommendation untouched.
    note(who, "recommendation.apply", rec_id, "requested", require_commit=True)
    receipt = gateway.apply_recommendation(rec_id, actor=who.subject)
    return receipt


# -- Analyst casework and CISO governance -------------------------------------

def _correlation(request: Request) -> str:
    supplied = request.headers.get("x-correlation-id", "").strip()
    return supplied[:128] if supplied else f"req_{secrets.token_hex(8)}"


def _origin() -> dict[str, Any]:
    runs = gateway.runs(owner=None)
    seeded = sum(bool(run.seeded or run.sample) for run in runs)
    live = len(runs) - seeded
    if live and seeded:
        kind = "mixed"
    elif live:
        kind = "live"
    else:
        kind = "sample"
    overview = gateway.fleet_overview()
    return control_plane.Origin(
        data_origin=kind,
        source_time=max((run.created_at for run in runs), default=0),
        seeded_count=seeded,
        coverage_known=max(0, live - overview.runs_unscanned),
        coverage_unknown=overview.runs_unscanned,
        coverage_basis="authenticated, non-seeded runs observed by MeshAgent",
    ).wire()


@app.get("/api/cases", response_model=CaseListOut)
def list_cases(
    state: str | None = None,
    assignee: str | None = None,
    _: Principal = Depends(require_capability("case.read")),
) -> CaseListOut:
    rows = workflow_store.list_cases(state=state, assignee=assignee)
    return CaseListOut(cases=rows, total=len(rows), origin=OriginOut(**_origin()))


@app.post("/api/cases", response_model=CaseOut, status_code=201)
def create_case(
    req: CreateCaseRequest,
    request: Request,
    who: Principal = Depends(require_capability("case.write")),
) -> CaseOut:
    # A case cannot be created around an identifier that the evidence gateway
    # does not know. This keeps the mutable workflow anchored to immutable data.
    gateway.run_finding(req.run_id, req.finding_id)
    evidence_run = gateway.run(req.run_id)
    note(
        who, "case.create.request", f"{req.run_id}:{req.finding_id}",
        req.rationale, require_commit=True,
    )
    case = workflow_store.create_case(
        finding_id=req.finding_id,
        run_id=req.run_id,
        title=req.title,
        severity=req.severity,
        rationale=req.rationale,
        actor=who.subject,
        actor_name=who.name,
        correlation_id=_correlation(request),
        origin="sample" if evidence_run.sample or evidence_run.seeded else "live",
    )
    return CaseOut(**case)


@app.get("/api/cases/{case_id}", response_model=CaseOut)
def get_case(
    case_id: str,
    _: Principal = Depends(require_capability("case.read")),
) -> CaseOut:
    return CaseOut(**workflow_store.case(case_id))


@app.post("/api/cases/{case_id}/assign", response_model=CaseOut)
def assign_case(
    case_id: str,
    req: AssignCaseRequest,
    request: Request,
    who: Principal = Depends(require_capability("case.write")),
) -> CaseOut:
    note(
        who, "case.assign.request", case_id, req.assignee,
        require_commit=True,
    )
    case = workflow_store.assign_case(
        case_id,
        expected_version=req.expected_version,
        assignee=req.assignee,
        assignee_name=req.assignee_name,
        sla_due_at=req.sla_due_at,
        actor=who.subject,
        actor_name=who.name,
        correlation_id=_correlation(request),
    )
    return CaseOut(**case)


@app.post("/api/cases/{case_id}/transition", response_model=CaseOut)
def transition_case(
    case_id: str,
    req: TransitionCaseRequest,
    request: Request,
    who: Principal = Depends(require_capability("case.write")),
) -> CaseOut:
    note(
        who, "case.transition.request", case_id, req.to_state,
        require_commit=True,
    )
    case = workflow_store.transition_case(
        case_id,
        expected_version=req.expected_version,
        to_state=req.to_state,
        disposition=req.disposition,
        rationale=req.rationale,
        evidence_ids=req.evidence_ids,
        actor=who.subject,
        actor_name=who.name,
        correlation_id=_correlation(request),
    )
    return CaseOut(**case)


@app.get("/api/governance/overview", response_model=CisoOverviewOut)
def governance_overview(
    _: Principal = Depends(ciso),
) -> CisoOverviewOut:
    fleet = gateway.fleet_overview().model_dump()
    counts = workflow_store.counts()
    workflow_store.capture_posture(
        {**fleet, **counts}, origin=_origin()["data_origin"]
    )
    data_health = {
        "durable": paths.durable(),
        "engine": gateway.mode().engine,
        "persists": gateway.mode().persists,
        "audit_intact": audit_log.verify() is None,
        "identity_verified": auth.config().enabled,
    }
    return CisoOverviewOut(
        fleet=fleet,
        workflow=counts,
        trends=workflow_store.posture_trend(),
        data_health=data_health,
        origin=OriginOut(**_origin()),
    )


@app.get("/api/policies", response_model=list[PolicyOut])
def policies(_: Principal = Depends(require_capability("policy.read"))) -> list[PolicyOut]:
    return [PolicyOut(**row) for row in workflow_store.policies()]


@app.post("/api/policies", response_model=PolicyOut, status_code=201)
def create_policy(
    req: CreatePolicyRequest,
    who: Principal = Depends(require_capability("policy.write")),
) -> PolicyOut:
    note(
        who, "policy.create.request", "policy:new", req.scope,
        require_commit=True,
    )
    policy = workflow_store.create_policy(
        name=req.name,
        scope=req.scope,
        severity_threshold=req.severity_threshold,
        denied_licenses=req.denied_licenses,
        block_on_unknown=req.block_on_unknown,
        rationale=req.rationale,
        actor=who.subject,
    )
    return PolicyOut(**policy)


@app.get("/api/exceptions", response_model=list[ExceptionOut])
def exceptions(
    _: Principal = Depends(require_capability("exception.read")),
) -> list[ExceptionOut]:
    return [ExceptionOut(**row) for row in workflow_store.exceptions()]


@app.post("/api/exceptions", response_model=ApprovalOut, status_code=201)
def request_exception(
    req: CreateExceptionRequest,
    who: Principal = Depends(require_capability("exception.request")),
) -> ApprovalOut:
    note(
        who, "exception.request", f"policy:{req.policy_id}", req.scope,
        require_commit=True,
    )
    exception, approval = workflow_store.create_exception(
        policy_id=req.policy_id,
        scope=req.scope,
        rationale=req.rationale,
        controls=req.compensating_controls,
        owner=req.owner,
        expires_at=req.expires_at,
        actor=who.subject,
        actor_name=who.name,
    )
    return ApprovalOut(**approval)


@app.get("/api/approvals", response_model=list[ApprovalOut])
def approvals(
    status: str | None = None,
    _: Principal = Depends(require_capability("exception.approve")),
) -> list[ApprovalOut]:
    return [ApprovalOut(**row) for row in workflow_store.approvals(status)]


@app.post("/api/approvals/{approval_id}/decision", response_model=ApprovalOut)
def decide_approval(
    approval_id: str,
    req: ApprovalDecisionRequest,
    who: Principal = Depends(require_capability("exception.approve")),
) -> ApprovalOut:
    note(
        who, f"approval.{req.decision}.request", approval_id,
        req.rationale, require_commit=True,
    )
    approval = workflow_store.decide_approval(
        approval_id,
        expected_version=req.expected_version,
        decision=req.decision,
        rationale=req.rationale,
        actor=who.subject,
    )
    return ApprovalOut(**approval)


@app.get("/api/remediations", response_model=list[RemediationOut])
def remediations(
    _: Principal = Depends(require_capability("remediation.write")),
) -> list[RemediationOut]:
    return [RemediationOut(**row) for row in workflow_store.remediations()]


@app.post("/api/remediations", response_model=RemediationOut, status_code=201)
def create_remediation(
    req: CreateRemediationRequest,
    who: Principal = Depends(require_capability("remediation.write")),
) -> RemediationOut:
    note(
        who, "remediation.create.request", f"case:{req.case_id}",
        req.owner, require_commit=True,
    )
    remediation = workflow_store.create_remediation(
        case_id=req.case_id,
        title=req.title,
        owner=req.owner,
        due_at=req.due_at,
        target_revision=req.target_revision,
        actor=who.subject,
    )
    return RemediationOut(**remediation)


@app.get("/api/reports", response_model=list[ReportOut])
def reports(
    _: Principal = Depends(require_capability("report.generate")),
) -> list[ReportOut]:
    return [ReportOut(**row) for row in workflow_store.reports()]


@app.post("/api/reports", response_model=ReportOut, status_code=201)
def create_report(
    req: ReportRequest,
    who: Principal = Depends(require_capability("report.generate")),
) -> ReportOut:
    if not gateway.mode().persists:
        raise control_plane.StoreError(
            "reports require the durable engine; sample data is excluded"
        )
    live_runs = [
        run for run in gateway.runs(owner=None)
        if not run.sample and not run.seeded
        and req.period_start <= run.created_at <= req.period_end
    ]
    if not live_runs:
        raise control_plane.StoreError(
            "no authenticated live runs exist in the requested report period"
        )
    finding_count, reachable, not_assessed = 0, 0, 0
    for run in live_runs:
        findings = gateway.run_findings(run.id)
        finding_count += findings.present
        reachable += findings.reachable
        not_assessed += findings.not_assessed
    manifest = {
        "data_origin": "live",
        "period": {"start": req.period_start, "end": req.period_end},
        "coverage": {
            "authenticated_runs": len(live_runs),
            "basis": "non-sample, non-seeded runs inside the requested period",
        },
        "findings": {
            "present": finding_count,
            "reachable": reachable,
            "not_assessed": not_assessed,
        },
        "workflow": workflow_store.counts(),
        "audit_intact": audit_log.verify() is None,
        "run_ids": [run.id for run in live_runs],
    }
    note(
        who, "report.generate.request", "report:new", req.title,
        require_commit=True,
    )
    report = workflow_store.create_report(
        title=req.title,
        period_start=req.period_start,
        period_end=req.period_end,
        requested_by=who.subject,
        manifest=manifest,
    )
    return ReportOut(**report)


@app.get("/api/reports/{report_id}", response_model=ReportOut)
def report(
    report_id: str,
    _: Principal = Depends(require_capability("report.generate")),
) -> ReportOut:
    return ReportOut(**workflow_store.report(report_id))


@app.get("/api/runs/{run_id}/graph", response_model=GraphPayload)
def run_graph(run_id: str, who: Principal = Depends(caller)) -> GraphPayload:
    _may_read(run_id, who)
    return gateway.run_graph(run_id)


# -- runs ---------------------------------------------------------------------

@app.get("/api/runs", response_model=list[RunSummary])
def runs(who: Principal = Depends(caller)) -> list[RunSummary]:
    """Oldest first. A developer's own runs; every run for the analyst."""
    return gateway.runs(owner=None if who.analyst else who.subject)


@app.get("/api/runs/{run_id}", response_model=RunSummary)
def run_summary(run_id: str, who: Principal = Depends(caller)) -> RunSummary:
    """Return one run without weakening the existing owner boundary."""
    _may_read(run_id, who)
    return gateway.run(run_id)


@app.post("/api/runs", response_model=RunSummary, status_code=201)
def create_run(req: CreateRunRequest,
               who: Principal = Depends(caller)) -> RunSummary:
    """Accept a task. The engine records it as the run's first governed
    memory (USER origin) and returns the run id."""
    task = req.task.strip()
    if not task:
        raise HTTPException(status_code=422, detail="task must not be empty")
    run = gateway.create_run(task, owner=who.subject, owner_name=who.name)
    note(who, "run.create", run.id, task[:160])
    return run


# -- the recorder: agents this product does not own ---------------------------

@app.post(
    "/api/v1/developer/sessions",
    response_model=DeveloperSessionOut,
    status_code=201,
)
def start_developer_session(
    req: SessionStartRequest,
    who: Principal = Depends(recorder),
) -> DeveloperSessionOut:
    """Open one authenticated Cursor or Claude Code work session.

    The client supplies a collision-safe opaque session id and stable source
    event id. Exact retries return the same session; divergent reuse is a 409.
    The opening activity commits before its ordered HyperMesh projection.
    """
    session, replayed = developer_session_store.create(req, who)
    session_projection.project_pending(
        developer_session_store, gateway, session, who,
    )
    current = developer_session_store.get(session.id, who)
    note(
        who,
        "developer.session.replay" if replayed else "developer.session.start",
        current.id,
        f"{current.adapter} {current.repository.id}",
    )
    return current


@app.get("/api/v1/developer/sessions", response_model=SessionListOut)
def list_developer_sessions(
    limit: int = 100,
    who: Principal = Depends(caller),
) -> SessionListOut:
    """List only the authenticated developer's connected-agent sessions."""
    return developer_session_store.list(who, limit=max(1, min(limit, 200)))


@app.get(
    "/api/v1/developer/sessions/{session_id}",
    response_model=DeveloperSessionOut,
)
def developer_session(
    session_id: str,
    who: Principal = Depends(caller),
) -> DeveloperSessionOut:
    return developer_session_store.get(session_id, who)


@app.post(
    "/api/v1/developer/sessions/{session_id}/events",
    response_model=ActivityIngestReceipt,
)
def ingest_developer_activity(
    session_id: str,
    batch: ActivityBatchRequest,
    who: Principal = Depends(recorder),
) -> ActivityIngestReceipt:
    """Append a contiguous activity batch and project it in session order.

    SQLite uniqueness constraints make event retries safe. The receipt's
    acknowledged sequence is the durable append boundary; projection state is
    independently visible on the event history for operational reconciliation.
    """
    _, accepted, duplicates = developer_session_store.ingest(
        session_id, batch, who,
    )
    session = developer_session_store.get(session_id, who)
    projected, refused, run_id = session_projection.project_pending(
        developer_session_store, gateway, session, who,
    )
    current = developer_session_store.get(session_id, who)
    note(
        who,
        "developer.activity.ingest",
        session_id,
        f"accepted={accepted} duplicate={duplicates} projected={projected}",
    )
    return ActivityIngestReceipt(
        session=current,
        accepted=accepted,
        duplicates=duplicates,
        projected=projected,
        refused=refused,
        acknowledged_through=current.last_acked_sequence,
        next_sequence=current.next_sequence,
        run_id=run_id or current.run_id,
    )


@app.get(
    "/api/v1/developer/sessions/{session_id}/events",
    response_model=ActivityListOut,
)
def developer_activity(
    session_id: str,
    after_sequence: int = 0,
    limit: int = 200,
    who: Principal = Depends(caller),
) -> ActivityListOut:
    return developer_session_store.events(
        session_id,
        who,
        after_sequence=max(0, after_sequence),
        limit=max(1, min(limit, 500)),
    )


@app.get(
    "/api/v1/developer/sessions/{session_id}/policy-evaluations",
    response_model=PolicyEvaluationListOut,
)
def developer_policy_evaluations(
    session_id: str,
    limit: int = 200,
    who: Principal = Depends(caller),
) -> PolicyEvaluationListOut:
    return developer_session_store.policy_evaluations(
        session_id, who, limit=max(1, min(limit, 500)),
    )


@app.get("/api/v1/developer/attention", response_model=AttentionListOut)
def developer_attention(
    limit: int = 200,
    who: Principal = Depends(require_capability("review.own")),
) -> AttentionListOut:
    """Actionable security signals from only the caller's connected sessions."""
    return review_service.attention_items(
        developer_session_store, gateway, workflow_store, who,
        limit=max(1, min(limit, 500)),
    )


@app.get("/api/v1/developer/review-requests", response_model=ReviewRequestListOut)
def developer_review_requests(
    who: Principal = Depends(require_capability("review.own")),
) -> ReviewRequestListOut:
    values = workflow_store.list_review_requests(owner_subject=who.subject)
    return ReviewRequestListOut(requests=values, total=len(values))


@app.post(
    "/api/v1/developer/review-requests",
    response_model=ReviewRequestOut,
    status_code=201,
)
def create_developer_review_request(
    req: CreateReviewRequest,
    who: Principal = Depends(require_capability("review.own")),
) -> ReviewRequestOut:
    session = developer_session_store.get(req.session_id, who)
    evaluation = developer_session_store.policy_evaluation(
        req.session_id, req.policy_evaluation_id, who,
    )
    snapshot = {
        "session_id": session.id, "run_id": session.run_id,
        "repository_id": session.repository.id,
        "repository_name": session.repository.name,
        "policy_evaluation_id": evaluation.id,
        "package": evaluation.package, "version": evaluation.version,
        "ecosystem": evaluation.ecosystem, "verdict": evaluation.verdict,
        "severity": evaluation.worst or "unknown",
        "advisories": [item.model_dump(mode="json") for item in evaluation.advisories],
        "reasons": evaluation.reasons,
        "code_entities": review_service.linked_code_entities(
            developer_session_store, gateway, session.id, who,
            session.run_id, evaluation.package,
        ),
    }
    created = workflow_store.create_review_request(
        snapshot=snapshot, kind=req.kind, rationale=req.rationale,
        actor=who.subject, actor_name=who.name,
    )
    note(who, "review.request", created["id"], f"{evaluation.package}@{evaluation.version}")
    return ReviewRequestOut.model_validate(created)


@app.get(
    "/api/v1/developer/review-requests/{request_id}",
    response_model=ReviewRequestOut,
)
def developer_review_request(
    request_id: str,
    who: Principal = Depends(require_capability("review.own")),
) -> ReviewRequestOut:
    return ReviewRequestOut.model_validate(
        workflow_store.review_request(request_id, owner_subject=who.subject),
    )


@app.get(
    "/api/v1/developer/review-requests/{request_id}/graph",
    response_model=ReviewGraphOut,
)
def developer_review_graph(
    request_id: str,
    who: Principal = Depends(require_capability("review.own")),
) -> ReviewGraphOut:
    review = workflow_store.review_request(request_id, owner_subject=who.subject)
    return review_service.request_graph(review, "developer")


@app.get("/api/reviews", response_model=ReviewRequestListOut)
def analyst_review_requests(
    state: str | None = None,
    _: Principal = Depends(require_capability("review.read")),
) -> ReviewRequestListOut:
    values = workflow_store.list_review_requests(state=state)
    return ReviewRequestListOut(requests=values, total=len(values))


@app.get("/api/reviews/{request_id}", response_model=ReviewRequestOut)
def analyst_review_request(
    request_id: str,
    _: Principal = Depends(require_capability("review.read")),
) -> ReviewRequestOut:
    return ReviewRequestOut.model_validate(workflow_store.review_request(request_id))


@app.post("/api/reviews/{request_id}/decision", response_model=ReviewRequestOut)
def decide_review_request(
    request_id: str, req: ReviewDecisionRequest,
    who: Principal = Depends(require_capability("review.write")),
) -> ReviewRequestOut:
    note(who, "review.decision.requested", request_id, req.decision, require_commit=True)
    value = workflow_store.decide_review_request(
        request_id, expected_version=req.expected_version,
        decision=req.decision, rationale=req.rationale,
        recommended_version=req.recommended_version,
        expires_at=req.expires_at, actor=who.subject,
        actor_name=who.name, actor_role=who.role,
    )
    return ReviewRequestOut.model_validate(value)


@app.get("/api/reviews/{request_id}/graph", response_model=ReviewGraphOut)
def analyst_review_graph(
    request_id: str,
    who: Principal = Depends(require_capability("review.read")),
) -> ReviewGraphOut:
    review = workflow_store.review_request(request_id)
    return review_service.request_graph(review, "ciso" if who.ciso else "analyst")

@app.get("/api/fleet/coverage", response_model=CoverageOut)
def fleet_coverage(_: Principal = Depends(analyst)) -> CoverageOut:
    """How much of the recorded code states a reason, per developer.

    The security office's view, and named per developer, so it is theirs to
    read rather than a developer's."""
    return gateway.fleet_coverage()

@app.post("/api/gate/package", response_model=GateDecision)
def check_package(req: GateRequest,
                  who: Principal = Depends(recorder)) -> GateDecision:
    """Whether an agent may install this package at this version.

    Reachable by a device token, because the thing asking is a pre-tool hook
    in somebody's editor and the answer is worthless if it arrives after the
    install. It decides nothing about anyone else's runs, so it is inside
    the recording scope.

    Every refusal is audited. A gate whose blocks are invisible cannot be
    argued with, and a policy nobody can see the effects of is one that gets
    quietly disabled."""
    decision = gateway.check_package(req)
    if decision.verdict in ("block", "unknown"):
        note(who, f"gate.{decision.verdict}",
             f"{req.package}@{req.version or 'unpinned'}",
             decision.reasons[0][:160] if decision.reasons else "")
    if decision.verdict == "allow" and req.session:
        try:
            session = developer_session_store.get(req.session, who)
        except developer_sessions.SessionStoreError:
            session = None
        if session is not None:
            evidence_id = f"gate:{req.session}:{req.ecosystem}:{req.package}:{req.version}"
            verified = workflow_store.verify_package_reviews(
                owner_subject=who.subject, repository_id=session.repository.id,
                ecosystem=req.ecosystem, package=req.package,
                version=req.version, evidence_id=evidence_id,
            )
            for request_id in verified:
                note(who, "review.verified", request_id, evidence_id)
    return decision


@app.post("/api/recorder", response_model=RecorderReceipt)
def record_events(batch: RecorderBatch,
                  who: Principal = Depends(recorder)) -> RecorderReceipt:
    """Accept what an external agent -- Claude Code, Cursor, Copilot -- did.

    Not run-scoped, because the adapter has no reason to know run ids: it
    names its own session and the gateway maps that onto a run. The caller's
    identity owns everything the batch writes, which is also what stops one
    developer's session id colliding into another's run."""
    got = gateway.record_events(batch, owner=who.subject, owner_name=who.name,
                                attributed=who.verified)
    note(who, "recorder.ingest", got.run_id,
         f"{batch.agent}: {got.recorded} event(s), {got.unexplained} "
         f"unexplained, {len(got.refused)} refused"
         + (f" (device {who.device})" if who.device else ""))
    return got


# -- provenance: why and forget -----------------------------------------------

@app.get("/api/runs/{run_id}/why", response_model=WhyOut)
def run_why(run_id: str, node: str,
            who: Principal = Depends(caller)) -> WhyOut:
    """The evidence chain behind one node, from the engine's `why` verb."""
    _may_read(run_id, who)
    return gateway.run_why(run_id, node)


@app.get("/api/runs/{run_id}/rewind", response_model=RewindOut)
def run_rewind(run_id: str, at: int,
               who: Principal = Depends(caller)) -> RewindOut:
    """What this run's memory held at epoch-second *at*.

    Memory forgotten since comes back listed and unreadable. That is the whole
    contract: rewind may prove a memory was there, and may not say what it
    said, because a reconstruction that could would quietly repeal every
    deletion certificate the system has issued."""
    _may_read(run_id, who)
    return gateway.run_rewind(run_id, at)


@app.get("/api/runs/{run_id}/forget/preview", response_model=ForgetPreview)
def run_forget_preview(run_id: str, node: str,
                       who: Principal = Depends(caller)) -> ForgetPreview:
    """What forgetting *node* would destroy, without destroying it.

    Reads only, and is not audited for that reason: asking what a deletion
    would cost is not a step towards it, and logging it as one would make the
    log worse at answering who actually destroyed something."""
    _may_read(run_id, who)
    return gateway.forget_preview(run_id, node)


@app.post("/api/runs/{run_id}/forget", response_model=DeletionCertificate)
def run_forget(run_id: str, req: ForgetRequest, request: Request,
               who: Principal = Depends(caller)) -> DeletionCertificate:
    """Forget a node and its derivation closure, and return the deletion
    certificate the engine's forget verb issues.

    The destructive verb, so this is where identity matters most: memory is
    evidence, one developer must not be able to destroy another's, and the
    certificate has to name whoever did.

    `If-Match` carries the version from the preview. It is optional on the
    wire because the engine's own seeding and the recommendation path forget
    without a human ever reading a preview; when a human did read one, sending
    it back is what stops them confirming one closure and purging another."""
    _may_read(run_id, who)
    expected_version = request.headers.get("if-match")
    idempotency_key = request.headers.get("idempotency-key")
    if expected_version is not None and len(expected_version) > 128:
        raise HTTPException(status_code=400, detail="If-Match is too long")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
    if auth.is_production() and not expected_version:
        raise HTTPException(status_code=428,
                            detail="If-Match is required for production forget")
    if auth.is_production() and not idempotency_key:
        raise HTTPException(status_code=428,
                            detail="Idempotency-Key is required for production forget")
    # The audit entry is a precondition for destruction, never an afterthought.
    note(who, "run.forget", f"{run_id}:{req.node}",
         f"requested: {req.reason}", require_commit=True)
    cert = gateway.run_forget(
        run_id, req.node, req.reason, actor=who.subject,
        expected_version=expected_version, idempotency_key=idempotency_key)
    return cert


# -- security: present vs exploitable -----------------------------------------

@app.get("/api/runs/{run_id}/findings", response_model=FindingsOut)
def run_findings(run_id: str, who: Principal = Depends(caller)) -> FindingsOut:
    """Every dangerous call site in the run. `exploitable` is set only where a
    taint path proves the sink is reachable."""
    _may_read(run_id, who)
    return gateway.run_findings(run_id)


@app.get("/api/runs/{run_id}/findings/{sink}", response_model=Finding)
def run_finding(run_id: str, sink: str,
                who: Principal = Depends(caller)) -> Finding:
    _may_read(run_id, who)
    return gateway.run_finding(run_id, sink)


# -- supply chain -------------------------------------------------------------

@app.post("/api/runs/{run_id}/scan", response_model=ScanOut)
def ingest_scan(run_id: str, sarif: SarifDocument,
                who: Principal = Depends(caller)) -> ScanOut:
    """Accept a SARIF document from an external scanner.

    Reachability is the claim this product should least like to make on its
    own authority, so this is the preferred way it gets made: Semgrep or
    CodeQL traced the flow, and MeshAgent records who said so."""
    _may_read(run_id, who)
    got = gateway.ingest_scan(run_id, sarif.model_dump())
    note(who, "scan.ingest", run_id,
         f"{got.tool}: {got.reachable} reachable of {got.results} result(s) "
         f"over {len(got.modules)} module(s)")
    return got


@app.get("/api/runs/{run_id}/code", response_model=CodeOut)
def run_code(run_id: str, who: Principal = Depends(caller)) -> CodeOut:
    """The source the run wrote, verbatim, so every other screen's claims can
    be read against the code that produced them."""
    _may_read(run_id, who)
    return gateway.run_code(run_id)


@app.get("/api/runs/{run_id}/sbom", response_model=SbomOut)
def run_sbom(run_id: str, who: Principal = Depends(caller)) -> SbomOut:
    _may_read(run_id, who)
    return gateway.run_sbom(run_id)


@app.get("/api/cve/{cve}/impact", response_model=CveImpact)
def cve_impact(cve: str, _: Principal = Depends(analyst)) -> CveImpact:
    """Blast radius of an advisory: versions, packages, the classes that
    import them, the decisions those rest on, and the agents affected."""
    return gateway.cve_impact(cve)


# -- structure-aware hypergraph analysis (hgviz) ------------------------------

@app.get("/api/runs/{run_id}/hypergraph", response_model=HypergraphOut)
def run_hypergraph(run_id: str,
                   who: Principal = Depends(caller)) -> HypergraphOut:
    """The hypergraph for polygon rendering: vertices tagged by structure,
    hyperedges as member sets."""
    _may_read(run_id, who)
    return gateway.run_hypergraph(run_id)


@app.get("/api/runs/{run_id}/decomposition", response_model=DecompositionOut)
def run_decomposition(run_id: str,
                      who: Principal = Depends(caller)) -> DecompositionOut:
    """Topological decomposition: blocks with entanglement scores, bridges as
    cut recommendations, branches, and forbidden clusters."""
    _may_read(run_id, who)
    return gateway.run_decomposition(run_id)


@app.get("/api/runs/{run_id}/scales", response_model=list[ScaleOut])
def run_scales(run_id: str,
               who: Principal = Depends(caller)) -> list[ScaleOut]:
    """Multi-scale simplification ladder for the scale slider."""
    _may_read(run_id, who)
    return gateway.run_scales(run_id)


@app.get("/api/fleet/decomposition", response_model=DecompositionOut)
def fleet_decomposition(_: Principal = Depends(analyst)) -> DecompositionOut:
    return gateway.fleet_decomposition()


def _stream_delay() -> float:
    """Seconds between memory frames, so the rail reads as memory forming
    rather than appearing all at once. Set to 0 in tests."""
    return float(os.environ.get("MESHAGENT_STREAM_DELAY", "0.9"))


def _drain(events: Iterator[RunStreamEvent]) -> None:
    """Let a run finish recording even if the client went away. The writes are
    the point, not the socket; a half-recorded run would be a worse artifact."""
    for _ in events:
        pass


@app.websocket("/api/runs/{run_id}/stream")
async def run_stream(ws: WebSocket, run_id: str) -> None:
    """Streams memory-forming events as the run records them. The gateway does
    the recording; this paces the frames and sends them. In engine mode every
    `memory` frame describes a hyperedge that exists by the time it arrives.

    A browser cannot put an Authorization header on a WebSocket, so the token
    arrives as the second subprotocol -- which, unlike a query parameter,
    does not end up in access logs."""
    offered = [p.strip() for p in
               ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    token, dev_user, dev_role = auth.from_subprotocols(offered)
    # a browser has to use the subprotocol, but anything else -- a test, a
    # server-side client -- can still send headers, so they remain the
    # fallback rather than being ignored
    token = token or _optional_bearer(ws.headers.get("authorization"))
    dev_user = dev_user or ws.headers.get(auth.DEV_USER)
    dev_role = dev_role or ws.headers.get(auth.DEV_ROLE)

    # Same-origin browsers carry the HttpOnly application session cookie.
    # Non-browser clients retain the documented bearer subprotocol/header path.
    browser_cookie = ws.cookies.get(browser_sessions.session_cookie_name())
    try:
        browser_session = browser_session_store.resolve(browser_cookie)
        if browser_session is not None:
            _require_same_origin(ws.headers.get("origin"))
        who = (browser_session.principal if browser_session is not None
               else identify(token, dev_user, dev_role))
    except AuthError as exc:
        await ws.close(code=1008, reason=str(exc)[:120])
        return

    await ws.accept(subprotocol=offered[0] if offered else None)
    try:
        _may_read(run_id, who)
    except NotFound as exc:
        with suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_json(
                RunStreamEvent(type="error", detail=str(exc)).model_dump())
            await ws.close()
        return
    events = gateway.run_stream(run_id)
    delay = _stream_delay()
    try:
        while True:
            if browser_session is not None and browser_session_store.resolve(
                    browser_cookie) is None:
                await ws.close(code=4401, reason="browser session expired")
                return
            frame = await asyncio.to_thread(next, events, None)
            if frame is None:
                return
            await ws.send_json(frame.model_dump())
            if frame.type == "memory" and delay:
                await asyncio.sleep(delay)
    except NotFound as exc:
        with suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_json(
                RunStreamEvent(type="error", detail=str(exc)).model_dump())
    except WebSocketDisconnect:
        return
    finally:
        await asyncio.to_thread(_drain, events)
