"""Wire contracts. These Pydantic models are the single source of truth for the
API shape and mirror packages/graph/src/types.ts on the frontend. Keep the two
in sync (or generate the TS client from /openapi.json)."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

NodeKind = Literal[
    "source", "decision", "class", "package", "version",
    "license", "sink", "cwe", "cve", "entry", "agent",
    "capability", "other",
]
Plane = Literal["provenance", "security", "supply", "belief", "time"]
Severity = Literal["critical", "high", "medium", "low", "unknown"]

# The provenance envelope every memory hyperedge carries, as it appears on the
# wire. Mirrors hypermeshdb.agentmem Origin/Status by name.
Origin = Literal["USER", "AGENT", "EXTERNAL"]
MemoryStatus = Literal["VERIFIED", "UNVERIFIED", "USER_STATED", "QUARANTINED"]


class GraphNode(BaseModel):
    id: str
    kind: NodeKind
    label: str
    planes: list[Plane] = []
    owners: list[str] = []
    exploitable: bool = False
    severity: Severity | None = None
    tombstoned: bool = False


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    rel: str


class Relation(BaseModel):
    """One memory hyperedge, as it was actually recorded.

    Memory here is n-ary: a security finding is a single fact spanning
    {class, sink, capability, weakness}, written once, with one ULID and one
    provenance, and forgotten as a unit. `GraphEdge` cannot say that -- it
    splits that one fact into three pairwise lines and loses which lines
    belonged together, which is precisely the structure the rest of this
    system reasons about. Relations carry the fact; edges stay the drawing."""
    id: str                     # the engine ULID, so it can be traced
    kind: str                   # source | decision | class | finding | ...
    members: list[str]          # every entity it spans, not just two
    label: str
    tombstoned: bool = False


class GraphPayload(BaseModel):
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    #: Empty where a payload is assembled rather than recorded -- the fleet
    #: query draws agents onto versions, which is a traversal, not a memory.
    relations: list[Relation] = []


class QueryRequest(BaseModel):
    query: str


class AgentHit(BaseModel):
    agent: str
    name: str
    status: Literal["exploitable", "present", "clean"]


class QueryResult(BaseModel):
    query: str
    graph: GraphPayload
    hits: list[AgentHit]
    # How the query was actually read. Shown verbatim, because a result list
    # under a search box reads as an answer to what was typed, and when the
    # query matched nothing the screen has to say so rather than quietly
    # showing everything.
    interpreted: str | None = None


class Recommendation(BaseModel):
    id: str
    title: str
    detail: str
    kind: Literal["HIGH", "CRITICAL", "POLICY", "REVIEW", "LICENSE"]
    agents: int
    blast_radius: dict[str, int] = {}
    agent_ids: list[str] = []   # the fleet agents applying it would touch


class FleetOverview(BaseModel):
    agents_active: int
    memories_governed: int
    exploitable_findings: int
    deletion_certificates: int
    # The fleet-wide companion to exploitable_findings. Without it a small
    # number of proven findings reads as a healthy fleet, when it may only
    # mean almost nothing has been scanned.
    unassessed_findings: int = 0
    # Runs no external scanner has read at all, out of runs holding code.
    runs_unscanned: int = 0
    runs_with_code: int = 0


class MemoryEvent(BaseModel):
    """One 'memory forming' event on the run WebSocket: a memory hyperedge
    that now exists, with the envelope the write gate gave it."""
    step: str
    detail: str
    origin: Origin
    status: MemoryStatus
    members: list[str] = []     # the entities the hyperedge spans
    ulid: str | None = None     # the engine's id for it, when engine-recorded
    gate: str = ""              # what the write gate did with this write

    @staticmethod
    def gate_note(origin: Origin, status: MemoryStatus) -> str:
        """The write-gate rule that produced this envelope. Derived from the
        envelope the gate actually issued, so it describes a decision that was
        made rather than restating the policy."""
        if status == "QUARANTINED":
            return ("quarantined: instruction-shaped external content, "
                    "kept for audit and barred from recall")
        if origin == "EXTERNAL":
            return ("external origin: the gate caps it at unverified, "
                    "so it is data and never instructions")
        if origin == "USER":
            return "user-stated: admitted as the user's own claim"
        if status == "VERIFIED":
            return ("agent output: admitted verified, and barred from "
                    "claiming user-stated")
        return "agent conclusion: unverified until evidence verifies it"


# -- runs ---------------------------------------------------------------------

RunStatus = Literal["recording", "complete", "failed"]


class RunSummary(BaseModel):
    """One agent run. `sample` is the honesty flag: true when the run is
    curated sample data rather than engine-recorded memory."""
    id: str
    task: str
    status: RunStatus
    memory_count: int           # governed memory hyperedges written by the run
    findings: int               # security findings recorded against it
    created_at: int             # epoch seconds
    sample: bool = False
    # True when the run's memory is MeshAgent's reference build rather than an
    # execution of `task`. No model is wired up, so instead of pretending to
    # carry out the task the engine records a known build and governs that for
    # real. The governance is genuine; its correspondence to `task` is not, and
    # every screen over this run has to say so.
    reference_build: bool = False
    # the model that built this run, when one did. Null means no model was
    # involved: either the run is seeded, or none is configured.
    model: str | None = None
    # The developer whose agent produced this memory. `owner` is the identity
    # provider's stable subject and is what the analyst's queue routes on;
    # `owner_name` is only for display. Null on memory that predates any
    # identity, such as the seeded reference build -- the analyst has to be
    # able to see that nobody owns it rather than have someone assumed.
    owner: str | None = None
    owner_name: str | None = None
    # Memory this deployment publishes as a reference build: it belongs to
    # nobody and every caller may read it.
    #
    # An explicit flag rather than an inference from `owner` being null. In a
    # long-lived deployment an ownerless run could arise some other way -- a
    # developer removed from the identity provider, memory imported from
    # elsewhere -- and reading that as publishable would hand one developer's
    # work to the whole company without anybody saying so.
    seeded: bool = False


class CreateRunRequest(BaseModel):
    # A task is persisted into governed memory and audit detail; bounding it
    # protects both stores from a single oversized API request.
    task: str = Field(max_length=4_096)


class AuditEntry(BaseModel):
    """One thing a person did to governed memory."""
    at: int
    actor: str
    actor_name: str
    role: str
    # False when no identity provider was configured and the actor was
    # merely asserted. An audit line must carry how far it can be relied on.
    verified: bool
    action: str
    target: str
    detail: str = ""
    digest: str


class AuditOut(BaseModel):
    """The action log, newest last, with the state of its hash chain."""
    entries: list[AuditEntry] = []
    # True when every entry still commits to the one before it. False means
    # the file has been edited or truncated since it was written.
    intact: bool = True
    # Index of the first entry that broke the chain, when one did.
    broken_at: int | None = None
    # What the log does NOT record, stated on the wire so a reader cannot
    # mistake its silence for evidence that nothing happened.
    covers: str
    durable: bool


class Me(BaseModel):
    """Who the API believes the caller is.

    `verified` is the honesty flag: false when no identity provider is
    configured and the caller simply asserted this, which every screen has
    to surface rather than present as a login."""
    subject: str
    name: str
    email: str
    role: Literal["developer", "analyst"]
    verified: bool


# -- the deployment itself ----------------------------------------------------

class GatewayMode(BaseModel):
    """Whether a write to this deployment is kept.

    Sample mode answers a recorder post with a success receipt for a batch it
    threw away. Nothing about the response says so, so the flags are here and
    `note` says it in words -- shown verbatim, because a reader who is only
    handed two booleans has to be told somewhere what losing their recording
    looks like."""
    engine: bool
    persists: bool
    note: str


class HealthOut(BaseModel):
    """What this deployment is, before anyone has been identified.

    Answered unauthenticated, which is why it carries no memory and names no
    person: a screen has to be able to say "nothing you send here is kept"
    before it knows who is asking, and in a deployment that cannot answer
    /api/me at all."""
    status: str
    version: str
    gateway: str                # the class serving this, named for support
    mode: GatewayMode
    # False when no MESHAGENT_DB_DIR is set: memory, device tokens and the
    # audit log all die with this process.
    durable: bool
    # Whether an OIDC issuer is configured. False means every identity here
    # was asserted by its own client.
    identity_provider: bool
    # Where this deployment's web app is, the same value the CLI is told to
    # print. A developer following a pairing code to the wrong port is the
    # most common way `meshagent login` is abandoned half-done.
    web_url: str
    # The repository root as the API PROCESS sees it, for interpolating into
    # hook configurations. It is not a claim about the caller's filesystem --
    # nothing here can know the editor being configured runs on this host --
    # so every screen offering it has to let the developer correct it.
    checkout: str


class RunStreamEvent(BaseModel):
    """One frame on the run WebSocket. `memory` carries a write that landed,
    `notice` explains something about the stream itself, `done` closes it with
    the run's final state, `error` reports why there is nothing to stream."""
    type: Literal["memory", "notice", "done", "error"]
    memory: MemoryEvent | None = None
    run: RunSummary | None = None
    detail: str | None = None


# -- provenance: why / forget -------------------------------------------------

class EvidenceNode(BaseModel):
    """One link in a why-chain, as the engine's `why` verb returns it."""
    ulid: str
    entity: str | None          # the entity the memory is about, when it has one
    kind: NodeKind              # entity kind, for the provenance column
    statement: str | None
    origin: Origin
    status: MemoryStatus
    source: str                 # the source locator the envelope carries
    via: str | None             # derivation relation to the node below it
    tombstoned: bool = False


class WhyOut(BaseModel):
    """The evidence chain behind one node, nearest first."""
    run_id: str
    node: str
    chain: list[EvidenceNode] = []
    sample: bool = False


class ForgetRequest(BaseModel):
    node: str = Field(max_length=512)
    reason: str = Field(default="operator forget", max_length=2_048)


class RewindMemory(BaseModel):
    """One memory as it stood at the instant asked about."""
    ulid: str
    entity: str | None
    kind: NodeKind
    statement: str | None
    origin: Origin
    status: MemoryStatus
    #: Held then, forgotten since. The tombstone kept the hash and destroyed
    #: the payload, so the store can still prove this memory existed and can
    #: no longer say what it said. Rewind must not pretend otherwise: if a
    #: reconstruction could reproduce forgotten content, every deletion
    #: certificate this system has ever issued would be worthless.
    redacted: bool = False


class RewindOut(BaseModel):
    """What one run's memory held at a past instant.

    Reconstructed from validity intervals and the supersession and tombstone
    history, not from a snapshot -- there is no snapshot, and inventing one
    would be a second copy of memory that `forget` could not reach."""
    run_id: str
    at: int                     # the instant asked about, epoch seconds
    memories: list[RewindMemory] = []
    #: Instants at which this run's memory actually changed: every write, and
    #: every deletion certificate issued against it. Offered so the UI can
    #: stop at moments that meant something rather than sweeping a continuum
    #: where almost every point looks like its neighbour.
    milestones: list[int] = []
    held: int = 0               # memories current at `at`
    redacted: int = 0           # of those, how many can no longer be read
    now: int = 0                # memories current today, for comparison
    sample: bool = False


class DoomedEdge(BaseModel):
    """One memory a forget would destroy, named before it is gone.

    Deliberately not a `PurgedEdge`: that model carries the retained hash a
    tombstone leaves behind, which does not exist yet. What a preview owes the
    reader is the opposite -- the statement itself, while it is still there to
    read, so the decision is made on the content rather than on a count."""
    ulid: str
    entity: str | None
    statement: str | None


class ForgetPreview(BaseModel):
    """What a forget would destroy, computed without destroying it.

    Forget is the one verb here that cannot be undone, and the closure is the
    part nobody can eyeball: the operator picks one poisoned source and the
    derivation walk takes everything downstream of it. Running it to find out
    how much that is would be the same as not asking.

    `version` is the state this closure was computed against. Handing it back
    on the POST is what makes the preview binding rather than decorative -- if
    the memory moved in between, what the reader agreed to destroy is not what
    would be destroyed."""
    run_id: str
    node: str
    root: str                   # ULID the forget would start from
    version: str                # pass back as If-Match to hold the API to this
    purged_count: int
    classes_pruned: list[str] = []
    doomed: list[DoomedEdge] = []
    warnings: list[str] = []
    sample: bool = False


class PurgedEdge(BaseModel):
    ulid: str
    entity: str | None
    content_sha_retained: str   # the payload is gone; its hash is kept


class DeletionCertificate(BaseModel):
    """Issued by the engine's forget verb: proof of what the derivation
    closure purged and that the audit chain survives it."""
    run_id: str
    node: str
    root: str                   # ULID of the memory the forget started from
    reason: str
    actor: str
    issued_at: int              # epoch seconds
    purged_count: int
    classes_pruned: list[str] = []
    retained_hash: str          # digest over the retained per-edge hashes
    purged: list[PurgedEdge] = []
    sample: bool = False

    @staticmethod
    def digest(shas: list[str]) -> str:
        """One hash over the per-edge hashes a forget retained. The payloads
        are gone; this commits to the audit chain that covered them."""
        return hashlib.sha256("".join(sorted(shas)).encode()).hexdigest()


# -- security: present vs exploitable -----------------------------------------

#: Whether untrusted input actually reaches a dangerous call.
#:
#: Three states, not two. A boolean collapses "an analyser traced this and
#: nothing reaches it" into "no analyser has ever looked", and those are
#: opposite claims. Reporting the second as though it were the first is the
#: most dangerous thing a security view can do, so the absence of evidence
#: gets a name of its own.
Reachability = Literal["reachable", "not-reachable", "not-assessed"]


class Finding(BaseModel):
    """A dangerous call site. `present` is always true (the call is there);
    reachability is asserted only as far as some analyser actually went."""
    sink: str
    owner: str | None           # the class the call site sits in
    cwe: str | None
    cwe_title: str | None
    capability: str | None
    severity: Severity | None
    present: bool = True
    reachability: Reachability = "not-assessed"
    # Which analyser decided: "semgrep", "codeql", "builtin", or None when
    # nobody has. Two analysers can disagree, and a reader who cannot see
    # who spoke cannot weigh the claim.
    asserted_by: str | None = None
    # Set when a second analyser reached the opposite conclusion. Kept rather
    # than resolved away: two tools disagreeing about whether input reaches a
    # deserializer is the most interesting thing on the screen.
    disputed_by: str | None = None
    rule: str | None = None     # the scanner rule that proved reachability
    entry: str | None = None    # untrusted entry point of the taint path
    path: list[str] = []        # entry -> ... -> sink

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exploitable(self) -> bool:
        """Proven reachable. Derived rather than stored so it cannot drift
        from `reachability`; it always meant exactly this."""
        return self.reachability == "reachable"


class ScanOut(BaseModel):
    """One external scanner run recorded against this code."""
    tool: str
    at: int
    modules: list[str] = []     # what it examined
    results: int = 0
    reachable: int = 0
    # The document was read and nothing was written. Uploading a scan is the
    # one place a developer hands this product evidence, and a receipt that
    # cannot say the evidence was discarded is worse than no receipt.
    sample: bool = False


class FindingsOut(BaseModel):
    run_id: str
    present: int
    exploitable: int            # == reachable; kept for existing callers
    findings: list[Finding] = []
    sample: bool = False
    # How many code records the scanner actually read. Zero findings means two
    # very different things -- nothing was written, or what was written is
    # clean -- and a screen cannot tell them apart without this.
    scanned: int = 0
    reachable: int = 0
    not_reachable: int = 0
    # The headline number when no scanner has run. A low exploitable count
    # against a high not_assessed count is not good news, and the screen has
    # to be able to say so.
    not_assessed: int = 0
    scans: list[ScanOut] = []


# -- supply chain: SBOM and CVE blast radius ----------------------------------

class SbomEntry(BaseModel):
    package: str
    version: str
    license: str
    cves: list[str] = []
    severity: Severity | None = None
    feed: str | None = None     # advisory feed: "osv" real, "sample" curated


class SbomOut(BaseModel):
    run_id: str
    entries: list[SbomEntry] = []
    sample: bool = False


# -- the code itself ----------------------------------------------------------

class CodeModule(BaseModel):
    """One module of source as the agent submitted it, kept verbatim. Every
    class, finding and package on the other screens is an assertion about this
    text, so it is here to be read rather than taken on trust."""
    name: str
    code: str
    classes: list[str] = []


class CodeOut(BaseModel):
    run_id: str
    modules: list[CodeModule] = []
    sample: bool = False


# -- the recorder: agents MeshAgent does not own ------------------------------
#
# Everything above assumes the agent that wrote the code was MeshAgent's own
# build loop. Almost no developer uses that loop; they use Claude Code, Cursor,
# Copilot. This is the protocol those agents report through, so that governed
# memory describes the estate as it really is rather than the slice this
# product happens to run itself.
#
# It is deliberately one typed vocabulary rather than one shape per editor.
# An adapter is then a translator and nothing more, and the engine never
# learns which editor it is talking to.


class SessionEvent(BaseModel):
    """An agent started working on something. Opens the run the rest of the
    batch is recorded against."""
    type: Literal["session"] = "session"
    agent: str = Field(max_length=128)  # "claude-code", "cursor", "copilot"
    task: str = Field(max_length=4_096)  # what the developer asked for
    # The developer closed the session. Until this arrives the run is still
    # recording, because a run reported complete while its agent is mid-edit
    # would put a partial picture under a finished heading.
    ends: bool = False
    at: int | None = None


class DecisionEvent(BaseModel):
    """The agent volunteered why it is about to do something. This is the
    part no hook can observe, and the part worth the most."""
    type: Literal["decision"] = "decision"
    id: str = Field(max_length=256)  # the adapter's handle, referenced by `because`
    statement: str = Field(max_length=4_096)
    at: int | None = None


class CodeEvent(BaseModel):
    """The agent wrote a file."""
    type: Literal["code"] = "code"
    module: str = Field(max_length=512)
    code: str = Field(max_length=200_000)
    # The DecisionEvent this carries out. Absent is the normal case for a
    # hook, which sees the write and not the reason; it is recorded as
    # explicitly unexplained rather than invented.
    because: str | None = Field(default=None, max_length=256)
    at: int | None = None


class PackageEvent(BaseModel):
    """A dependency entered the project."""
    type: Literal["package"] = "package"
    package: str = Field(max_length=256)
    version: str = Field(max_length=128)
    license: str = Field(default="unknown", max_length=256)
    at: int | None = None


class ToolEvent(BaseModel):
    """The agent invoked something: a shell command, a test run, an install."""
    type: Literal["tool"] = "tool"
    name: str = Field(max_length=256)
    detail: str = Field(default="", max_length=4_096)
    at: int | None = None


RecorderEvent = Annotated[
    SessionEvent | DecisionEvent | CodeEvent | PackageEvent | ToolEvent,
    Field(discriminator="type"),
]


class RecorderBatch(BaseModel):
    """What one adapter observed, posted together.

    Batched because the unit of interest is a coherent change, not a
    keystroke: an adapter reports at file save and session end."""
    agent: str = Field(max_length=128)
    # The adapter's own session handle. It is what maps a developer's editor
    # session onto a run across many posts, so re-posting a batch after a
    # network failure lands in the same place rather than opening a run.
    session: str = Field(max_length=256)
    events: list[RecorderEvent] = Field(default_factory=list, max_length=100)


class RecorderReceipt(BaseModel):
    """What the deployment did with a batch.

    Deliberately not a bare 200. An adapter that cannot tell whether its
    events landed will report silence as compliance."""
    run_id: str
    session: str
    opened: bool = False        # this batch opened the run
    recorded: int = 0           # events that produced governed memory
    # Code recorded with no stated reason. The honest denominator for "how
    # much of what our agents write can anyone explain".
    unexplained: int = 0
    # Code this build could decompose into classes and packages. The walk is
    # Python-only, so anything else is stored and readable but not analysed,
    # and a caller that assumed otherwise would over-read the silence.
    analysed: int = 0
    # One line per event that was understood but not written, with the reason.
    refused: list[str] = []
    sample: bool = False        # nothing landed: this deployment has no engine


class CoverageRow(BaseModel):
    """One developer's recorded code, and how much of it states a reason."""
    owner: str | None
    owner_name: str | None
    agents: list[str] = []      # the agents that wrote it, named
    modules: int = 0
    explained: int = 0
    # Whether everything counted here arrived under a proven identity. This
    # table names people against their gaps, so it has to say when the name
    # was merely asserted -- a row nobody can stand behind is not evidence of
    # anything, and acting on it would be acting on a header.
    attributed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unexplained(self) -> int:
        return self.modules - self.explained


class CoverageOut(BaseModel):
    """How much of the code MeshAgent holds has a reason anyone can read.

    The adoption number, and the one that keeps the rest of the product
    honest. Every other screen describes the code MeshAgent can see; this one
    says how much of it arrived with a stated why rather than a recorded
    absence of one. A fleet reporting no exploitable findings over mostly
    unexplained code is not a governed fleet, and nothing else on the screen
    would tell you that.

    Counted per developer rather than fleet-wide alone, because the person
    accountable for a gap is the person whose agent left it."""
    modules: int = 0
    explained: int = 0
    by_developer: list[CoverageRow] = []
    # Modules MeshAgent's own build loop wrote. It is made to state a decision
    # before writing, so it is always fully explained; counting it silently
    # alongside external agents would flatter the number for a reason that has
    # nothing to do with how the estate is actually governed.
    self_recorded: int = 0
    sample: bool = False


# -- device tokens: identity for things with no browser ------------------------

class DeviceOut(BaseModel):
    """A machine registered to record as somebody."""
    id: str
    label: str
    subject: str
    name: str
    role: Literal["developer", "analyst"]
    created_at: int
    last_used: int = 0
    # Whether the human who minted this was themselves verified. A device
    # token is never more trustworthy than the person behind it, and a
    # bearer secret does not turn an asserted name into a proven one.
    verified: bool = False


# -- the package gate: the one thing that refuses ------------------------------

class GateAdvisory(BaseModel):
    """One advisory, as the feed stated it."""
    id: str
    severity: Literal["critical", "high", "medium", "low", "unknown"]
    summary: str
    cwe: str | None = None


class GateRequest(BaseModel):
    """An agent is about to install something."""
    package: str = Field(max_length=256)
    version: str = Field(default="", max_length=128)
    # The session it belongs to, when there is one. Optional because a gate
    # check is useful before a session exists -- an agent may ask before it
    # has written anything.
    session: str | None = Field(default=None, max_length=256)


class SarifDocument(BaseModel):
    """A forward-compatible, bounded SARIF envelope.

    Scanner vendors add extension objects, so nested SARIF content remains raw
    JSON. The standard ``runs`` container and the top-level member count are
    nevertheless safe schema-level bounds: they prevent a single upload from
    fanning out into an unbounded number of scan records without rejecting
    vendor-specific result shapes.
    """

    model_config = ConfigDict(extra="allow")

    runs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _bounded_members(self) -> "SarifDocument":
        if len(self.model_dump()) > 64:
            raise ValueError("SARIF document has too many top-level members")
        return self


class GateDecision(BaseModel):
    """Whether it may, and everything the answer rests on.

    Four-valued, and `unknown` is the value that matters. A gate that cannot
    reach its feeds and answers "allow" is worse than no gate, because it
    manufactures the impression of a check. Callers must treat `unknown` as
    "nobody looked", and this deployment's policy -- stated on every
    decision -- says what happens then."""
    package: str
    version: str
    verdict: Literal["allow", "warn", "block", "unknown"]
    # Why, in full sentences, because a blocked developer reads this rather
    # than being told to file a ticket.
    reasons: list[str] = []
    advisories: list[GateAdvisory] = []
    worst: Literal["critical", "high", "medium", "low", "unknown"] | None = None
    # Why the check is incomplete, verbatim from the feed layer.
    unavailable: str | None = None
    policy: str = ""
    # How many fleet agents already hold this exact version. Context, not
    # permission: widely used and vulnerable is the worst case, not the best.
    fleet_agents: int = 0
    sample: bool = False


class PairRequest(BaseModel):
    """`meshagent login` asking to begin."""
    label: str = ""


class PairStart(BaseModel):
    """What the CLI gets back: one secret it keeps, one code it shows.

    They are separate so that reading the code off somebody's screen is not
    enough to collect their token."""
    device_code: str
    user_code: str
    expires_in: int
    # How often to poll. Told rather than guessed so the CLI does not decide
    # for itself to hammer the endpoint.
    interval: int = 2
    verify_url: str
    # What this login will grant, told to the CLI up front so it can print
    # it. A credential whose holder cannot say what it permits is one nobody
    # can reason about storing.
    grants: list[str] = []


class PairPending(BaseModel):
    """What a human is being shown before they approve.

    Enough to recognise their own login and refuse someone else's: the
    standing attack on every device flow is to start a pairing and talk a
    stranger into approving it."""
    user_code: str
    label: str
    started_at: int
    approved: bool = False
    grants: list[str] = []


class PairToken(BaseModel):
    """The CLI's answer while polling. `token` only ever arrives once."""
    status: Literal["pending", "granted"]
    token: str | None = None
    device: DeviceOut | None = None
    # Spelled out on the wire so the thing storing this credential can say
    # what it is storing, rather than the scope living only in our docs.
    grants: list[str] = []


class ApproveRequest(BaseModel):
    user_code: str


class ApplyReceipt(BaseModel):
    """What applying a recommendation actually did.

    `changed_memory` is true only where the engine really mutated governed
    memory. A recommendation whose real work lives outside MeshAgent -- bumping
    a lockfile, shipping a patch -- is recorded as an accepted decision and
    never reported as done; `note` says what is still outstanding."""
    recommendation_id: str
    title: str
    action: str                 # what was performed, in one line
    changed_memory: bool
    agents: int
    memories_written: int
    certificates: list[DeletionCertificate] = []
    note: str
    issued_at: int
    sample: bool = False


class CveImpact(BaseModel):
    """Blast radius of one advisory, tier by tier over the same graph."""
    cve: str
    severity: Severity | None
    summary: str | None
    feed: str | None
    versions: list[str] = []
    packages: list[str] = []
    classes: list[str] = []
    decisions: list[str] = []
    agents: list[str] = []
    sample: bool = False


# -- hypergraph structure analysis (hgviz) ------------------------------------

class HgVertex(BaseModel):
    id: str
    kind: str
    structure: str          # "block:0" | "bridge:1" | "branch:2"


class Hyperedge(BaseModel):
    id: str
    members: list[str]
    structure: str          # structure of the hyperedge's dual node


class HypergraphOut(BaseModel):
    """The hypergraph for polygon rendering: vertices with structure tags and
    hyperedges as member sets (drawn as polygons)."""
    vertices: list[HgVertex] = []
    edges: list[Hyperedge] = []


class BlockOut(BaseModel):
    id: str
    primal: int             # entities in the block
    dual: int               # hyperedges in the block
    b1: int                 # independent cycles
    eta: float              # entanglement index (coupling score)
    forbidden: int          # forbidden bundles inside this block


class BridgeOut(BaseModel):
    id: str
    members: list[str]
    recommendation: str     # the single-point-of-propagation cut


class ForbiddenOut(BaseModel):
    kind: str
    edges: list[str]
    shared: list[str]


class DecompositionOut(BaseModel):
    b0: int                 # connected components
    b1: int                 # independent cycles overall
    entanglement: float     # whole-graph coupling score
    blocks: list[BlockOut] = []
    bridges: list[BridgeOut] = []
    branches: int = 0
    forbidden: list[ForbiddenOut] = []


class ScaleOut(BaseModel):
    scale: int
    label: str
    b0: int
    b1: int
    graph: HypergraphOut
