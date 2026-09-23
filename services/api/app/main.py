"""MeshAgent API. REST for queries and graphs, WebSocket for the live
'memory forming' stream. Thin: all data comes from the gateway."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, audit, auth, devices, paths
from .auth import AuthError, Forbidden, Principal
from .gateway import NotFound, Stale, get_gateway
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
    WhyOut,
)

app = FastAPI(title="MeshAgent API", version=__version__)

_origins = os.environ.get("MESHAGENT_CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
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
    if not who.analyst:
        raise Forbidden("this view belongs to the security office")
    return who


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

#: Registered machines. Beside the audit log and the run index rather than in
#: the hypergraph: who is allowed to write to memory is not itself a belief
#: the memory holds, and `forget` must not be able to reach it.
device_store = devices.load(paths.base_dir())


def note(who: Principal, action: str, target: str, detail: str = "") -> None:
    """Record something a person did. Never lets a logging failure take down
    the request it is describing -- but a failure to log is itself worth
    knowing, so it is re-raised in tests via MESHAGENT_STRICT_AUDIT."""
    try:
        audit_log.record(
            actor=who.subject, actor_name=who.name, role=who.role,
            verified=who.verified, action=action, target=target, detail=detail)
    except OSError:
        if os.environ.get("MESHAGENT_STRICT_AUDIT"):
            raise


def _web_url() -> str:
    """Where this deployment's web app is. One place, because the CLI prints
    it and the setup screen generates commands against it, and the two
    disagreeing sends a developer to a port nothing is listening on."""
    return os.environ.get("MESHAGENT_WEB_URL", "http://localhost:5173")


def _checkout() -> str:
    """The repository root, as this process sees it.

    The hooks shipped under adapters/ have to be invoked by absolute path: the
    editor running them has the DEVELOPER'S repository as its working
    directory, not this one, so a relative path resolves to a file that is not
    there and the hook fails silently. This is the only party that knows where
    the adapters actually are -- and it still does not know the editor is on
    this filesystem, which is why it is reported rather than asserted."""
    return str(Path(__file__).resolve().parents[3])


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
              role=who.role, verified=who.verified)


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
        verify_url=_web_url() + "/devices",
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
    """A developer's own machines; every machine for the security office,
    whose job includes knowing what is allowed to write."""
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
                         who: Principal = Depends(analyst)) -> ApplyReceipt:
    """Apply a recommendation and return the receipt. The receipt says whether
    governed memory actually changed, so nothing is claimed that did not."""
    receipt = gateway.apply_recommendation(rec_id)
    note(who, "recommendation.apply", rec_id, receipt.note[:160])
    return receipt


@app.get("/api/runs/{run_id}/graph", response_model=GraphPayload)
def run_graph(run_id: str, who: Principal = Depends(caller)) -> GraphPayload:
    _may_read(run_id, who)
    return gateway.run_graph(run_id)


# -- runs ---------------------------------------------------------------------

@app.get("/api/runs", response_model=list[RunSummary])
def runs(who: Principal = Depends(caller)) -> list[RunSummary]:
    """Oldest first. A developer's own runs; every run for the analyst."""
    return gateway.runs(owner=None if who.analyst else who.subject)


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
    cert = gateway.run_forget(
        run_id, req.node, req.reason, actor=who.subject,
        expected_version=request.headers.get("if-match"),
        idempotency_key=request.headers.get("idempotency-key"))
    note(who, "run.forget", f"{run_id}:{req.node}",
         f"purged {cert.purged_count} edge(s): {req.reason}")
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
def ingest_scan(run_id: str, sarif: dict[str, Any],
                who: Principal = Depends(caller)) -> ScanOut:
    """Accept a SARIF document from an external scanner.

    Reachability is the claim this product should least like to make on its
    own authority, so this is the preferred way it gets made: Semgrep or
    CodeQL traced the flow, and MeshAgent records who said so."""
    _may_read(run_id, who)
    got = gateway.ingest_scan(run_id, sarif)
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

    # identified before the handshake is accepted, so an unauthenticated
    # caller is refused outright rather than connected and then told
    try:
        who = identify(token, dev_user, dev_role)
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
