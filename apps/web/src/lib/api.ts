import type { GraphPayload, NodeKind, Severity } from "@meshagent/graph";

import { authHeaders } from "./auth";

/** Mirrors services/api/app/models.py. For a bigger app, generate this client
 *  from the API's /openapi.json instead of hand-maintaining it. */
export interface FleetOverview {
  agents_active: number;
  memories_governed: number;
  exploitable_findings: number;
  deletion_certificates: number;
  /** The companion to exploitable_findings. Few proven findings beside many
   *  unassessed ones is not a healthy fleet, just an unexamined one. */
  unassessed_findings: number;
  runs_unscanned: number;
  runs_with_code: number;
}

export interface AgentHit {
  agent: string;
  name: string;
  status: "exploitable" | "present" | "clean";
}

export interface QueryResult {
  query: string;
  graph: GraphPayload;
  hits: AgentHit[];
  /** How the query was actually read. A result list under a search box reads
   *  as an answer to it, so an unmatched query has to say so. */
  interpreted: string | null;
}

export interface Recommendation {
  id: string;
  title: string;
  detail: string;
  kind: "HIGH" | "CRITICAL" | "POLICY" | "REVIEW" | "LICENSE";
  agents: number;
  blast_radius: Record<string, number>;
  /** the fleet agents applying it would touch */
  agent_ids: string[];
}

// -- runs --
export type MemoryOrigin = "USER" | "AGENT" | "EXTERNAL";
export type MemoryStatus =
  | "VERIFIED"
  | "UNVERIFIED"
  | "USER_STATED"
  | "QUARANTINED";
export type RunStatus = "recording" | "complete" | "failed";

export interface RunSummary {
  id: string;
  task: string;
  status: RunStatus;
  memory_count: number;
  findings: number;
  created_at: number;
  /** true when the run is curated sample data, not engine-recorded memory */
  sample: boolean;
  /** true when the run's memory is MeshAgent's reference build rather than an
   *  execution of `task`. The governance over it is real; the match to the
   *  task is not, and the screens have to say so. */
  reference_build: boolean;
  /** the model that built this run, or null when none was involved */
  model: string | null;
  /** The developer whose agent produced this memory: the identity provider's
   *  stable subject, which is what the analyst's queue routes on. Null on
   *  memory that predates any identity, such as the seeded reference build. */
  owner: string | null;
  owner_name: string | null;
  /** Memory this deployment publishes as a reference build: it belongs to
   *  nobody and every caller may read it. An explicit flag, never inferred
   *  from a null owner — a run can end up ownerless for reasons that are
   *  nobody's decision to publish. */
  seeded: boolean;
}

// -- the deployment itself --
/** Whether a write to this deployment is kept. Sample mode answers a recorder
 *  post with a success receipt for a batch it threw away, so `note` says that
 *  in words and is rendered verbatim. */
export interface GatewayMode {
  engine: boolean;
  persists: boolean;
  note: string;
}

/** What this deployment is, answered before anyone has been identified — the
 *  banner saying a recording will be discarded is most needed exactly where
 *  nothing can answer `/api/me`. */
export interface HealthOut {
  status: string;
  version: string;
  gateway: string;
  mode: GatewayMode;
  /** False when no MESHAGENT_DB_DIR is set: memory, device tokens and the
   *  audit log all die with the API process. */
  durable: boolean;
  identity_provider: boolean;
  /** Where this deployment's web app is, the same value the CLI prints. */
  web_url: string;
  /** The repository root as the API PROCESS sees it. Not a claim about the
   *  caller's filesystem: nothing here can know the editor being configured
   *  runs on the same host, so any screen offering it must let it be edited. */
  checkout: string;
}

/** Who the API believes the caller is. `verified` is false when no identity
 *  provider is configured and the caller simply asserted this, which the UI
 *  must show rather than present as a login. */
export interface AuditEntry {
  at: number;
  actor: string;
  actor_name: string;
  role: string;
  /** False when no identity provider was configured and the actor was
   *  merely asserted. */
  verified: boolean;
  action: string;
  target: string;
  detail: string;
  digest: string;
}

export interface AuditOut {
  entries: AuditEntry[];
  /** True when every entry still commits to the one before it. */
  intact: boolean;
  broken_at: number | null;
  /** What the log does not record. Shown verbatim so its silence is not
   *  mistaken for evidence that nothing happened. */
  covers: string;
  durable: boolean;
}

export interface Me {
  subject: string;
  name: string;
  email: string;
  role: "developer" | "analyst";
  verified: boolean;
}

/** The newest run's id. Runs come back oldest first, so the seeded run is at
 *  the front; screens that mean "the run I just recorded" want the back. */
export function latestRun(runs: RunSummary[] | undefined): string | undefined {
  return runs && runs.length > 0 ? runs[runs.length - 1].id : undefined;
}

/** One 'memory forming' event on the run WebSocket: a memory hyperedge that
 *  now exists, with the envelope the write gate gave it. */
export interface MemoryEvent {
  step: string;
  detail: string;
  origin: MemoryOrigin;
  status: MemoryStatus;
  /** the entities the hyperedge spans */
  members: string[];
  /** the engine's id for it, when engine-recorded */
  ulid: string | null;
  /** what the write gate did with this write */
  gate: string;
}

/** One frame on the run WebSocket. */
export interface RunStreamEvent {
  type: "memory" | "notice" | "done" | "error";
  memory: MemoryEvent | null;
  /** the run's final state, on `done` */
  run: RunSummary | null;
  /** notice or error text */
  detail: string | null;
}

// -- provenance: why / forget --
export interface EvidenceNode {
  ulid: string;
  entity: string | null;
  kind: NodeKind;
  statement: string | null;
  origin: MemoryOrigin;
  status: MemoryStatus;
  source: string;
  /** derivation relation to the node below it in the chain */
  via: string | null;
  tombstoned: boolean;
}

export interface WhyOut {
  run_id: string;
  node: string;
  /** nearest first: the node, then what it was derived from */
  chain: EvidenceNode[];
  sample: boolean;
}

export interface PurgedEdge {
  ulid: string;
  entity: string | null;
  content_sha_retained: string;
}

export interface RewindMemory {
  ulid: string;
  entity: string | null;
  kind: NodeKind;
  statement: string | null;
  origin: MemoryOrigin;
  status: MemoryStatus;
  /** Held then, forgotten since. The store can prove it existed and can no
   *  longer say what it said — if rewind could reproduce forgotten content,
   *  every deletion certificate would be worthless. */
  redacted: boolean;
}

/** What one run's memory held at a past instant, reconstructed from validity
 *  intervals and tombstone history rather than from a snapshot. */
export interface RewindOut {
  run_id: string;
  at: number;
  memories: RewindMemory[];
  /** Instants when memory actually changed: every write, and every deletion
   *  certificate. Stops that meant something, rather than a bare continuum. */
  milestones: number[];
  held: number;
  redacted: number;
  now: number;
  sample: boolean;
}

/** One memory a forget would destroy, carrying the statement itself while it
 *  is still there to read. A count cannot be argued with; a sentence can. */
export interface DoomedEdge {
  ulid: string;
  entity: string | null;
  statement: string | null;
}

/** What a forget would destroy, computed without destroying it. `version` is
 *  the state it was computed against, and handing it back on the POST is what
 *  stops the reader agreeing to one deletion and getting another. */
export interface ForgetPreview {
  run_id: string;
  node: string;
  root: string;
  version: string;
  purged_count: number;
  classes_pruned: string[];
  doomed: DoomedEdge[];
  warnings: string[];
  sample: boolean;
}

export interface DeletionCertificate {
  run_id: string;
  node: string;
  root: string;
  reason: string;
  actor: string;
  issued_at: number;
  purged_count: number;
  classes_pruned: string[];
  retained_hash: string;
  purged: PurgedEdge[];
  sample: boolean;
}

// -- security: present vs exploitable --
/** Three states, not two. A boolean collapses "an analyser traced this and
 *  nothing reaches it" into "nobody has looked", which are opposite claims. */
export type Reachability = "reachable" | "not-reachable" | "not-assessed";

export interface ScanOut {
  tool: string;
  at: number;
  modules: string[];
  results: number;
  reachable: number;
  /** The document was read and nothing was written. Uploading a scan is the
   *  one place a developer hands this product evidence, so a receipt that
   *  cannot say the evidence was discarded is worse than no receipt. */
  sample: boolean;
}

export interface Finding {
  sink: string;
  owner: string | null;
  cwe: string | null;
  cwe_title: string | null;
  capability: string | null;
  severity: Severity | null;
  present: boolean;
  /** derived from `reachability`; true only where a path was traced */
  exploitable: boolean;
  reachability: Reachability;
  /** which analyser decided: "semgrep", "codeql", "builtin", or null */
  asserted_by: string | null;
  /** an analyser that read this and did not agree. Kept, not resolved away. */
  disputed_by: string | null;
  rule: string | null;
  entry: string | null;
  path: string[];
}

export interface FindingsOut {
  run_id: string;
  present: number;
  exploitable: number;
  findings: Finding[];
  sample: boolean;
  /** how many code records the scanner read. Zero findings with scanned > 0
   *  means the code is clean; zero with scanned === 0 means there was no code. */
  scanned: number;
  reachable: number;
  not_reachable: number;
  /** The number that matters when no scanner has run. A low exploitable
   *  count beside a high not_assessed count is not good news. */
  not_assessed: number;
  scans: ScanOut[];
}

// -- supply chain --
export interface SbomEntry {
  package: string;
  version: string;
  license: string;
  cves: string[];
  severity: Severity | null;
  /** advisory feed: "osv" is real, "sample" is curated */
  feed: string | null;
}

export interface SbomOut {
  run_id: string;
  entries: SbomEntry[];
  sample: boolean;
}

/** One module of source as the agent submitted it. Every class, finding and
 *  package on the other screens is an assertion about this text. */
export interface CodeModule {
  name: string;
  code: string;
  classes: string[];
}

export interface CodeOut {
  run_id: string;
  modules: CodeModule[];
  sample: boolean;
}

// -- the recorder: agents MeshAgent does not own --
//
// Everything above assumes the agent that wrote the code was MeshAgent's own
// build loop. Almost no developer uses that loop; they use Claude Code,
// Cursor, Copilot. This is the protocol those agents report through, kept as
// one typed vocabulary so an adapter is a translator and nothing more.

export interface SessionEvent {
  type: "session";
  agent: string;
  task: string;
  /** The developer closed the session. Until this arrives the run is still
   *  recording, because a run called complete while its agent is mid-edit
   *  puts a partial picture under a finished heading. */
  ends?: boolean;
  at?: number | null;
}

export interface DecisionEvent {
  type: "decision";
  id: string;
  statement: string;
  at?: number | null;
}

export interface CodeEvent {
  type: "code";
  module: string;
  code: string;
  /** The DecisionEvent this carries out. Absent is the normal case for a
   *  hook, which sees the write and not the reason; it is recorded as
   *  explicitly unexplained rather than invented. */
  because?: string | null;
  at?: number | null;
}

export interface PackageEvent {
  type: "package";
  package: string;
  version: string;
  license?: string;
  at?: number | null;
}

export interface ToolEvent {
  type: "tool";
  name: string;
  detail?: string;
  at?: number | null;
}

export type RecorderEvent =
  | SessionEvent
  | DecisionEvent
  | CodeEvent
  | PackageEvent
  | ToolEvent;

export interface RecorderBatch {
  agent: string;
  /** The adapter's own session handle, mapping an editor session onto a run
   *  across many posts. */
  session: string;
  events: RecorderEvent[];
}

/** What the deployment did with a batch. Deliberately not a bare 200: an
 *  adapter that cannot tell whether its events landed will report silence as
 *  compliance. */
export interface RecorderReceipt {
  run_id: string;
  session: string;
  opened: boolean;
  recorded: number;
  /** Code recorded with no stated reason — the honest denominator for how
   *  much of what our agents write anyone can explain. */
  unexplained: number;
  /** Code this build could decompose. The walk is Python-only, so anything
   *  else is stored and readable but not analysed. */
  analysed: number;
  refused: string[];
  /** Nothing landed: this deployment has no engine. */
  sample: boolean;
}

/** One developer's recorded code, and how much of it states a reason. */
export interface CoverageRow {
  owner: string | null;
  owner_name: string | null;
  agents: string[];
  modules: number;
  explained: number;
  unexplained: number;
  /** Whether everything counted here arrived under a proven identity. One
   *  unproven run makes the whole row unproven: a name is either something
   *  the security office can act on or it is not. */
  attributed: boolean;
}

/** How much of the code MeshAgent holds has a reason anyone can read — the
 *  adoption number, and the one that keeps the rest of the product honest. A
 *  fleet reporting no exploitable findings over mostly unexplained code is
 *  not a governed fleet, and nothing else on the screen would say so. */
export interface CoverageOut {
  modules: number;
  explained: number;
  by_developer: CoverageRow[];
  /** Modules MeshAgent's own loop wrote. It is made to state a decision
   *  before writing, so counting it alongside external agents would flatter
   *  the number for a reason unrelated to how the estate is governed. */
  self_recorded: number;
  sample: boolean;
}

/** What applying a recommendation actually did. `changed_memory` is true only
 *  where governed memory really changed; work that lives outside MeshAgent is
 *  recorded as an accepted decision, never reported as done. */
export interface ApplyReceipt {
  recommendation_id: string;
  title: string;
  action: string;
  changed_memory: boolean;
  agents: number;
  memories_written: number;
  certificates: DeletionCertificate[];
  note: string;
  issued_at: number;
  sample: boolean;
}

export interface CveImpact {
  cve: string;
  severity: Severity | null;
  summary: string | null;
  feed: string | null;
  versions: string[];
  packages: string[];
  classes: string[];
  decisions: string[];
  agents: string[];
  sample: boolean;
}

// -- hgviz structure analysis (mirrors app/models.py) --
export interface HgVertex {
  id: string;
  kind: string;
  structure: string; // "block:0" | "bridge:1" | "branch:2"
}
export interface Hyperedge {
  id: string;
  members: string[];
  structure: string;
}
export interface HypergraphOut {
  vertices: HgVertex[];
  edges: Hyperedge[];
}
export interface BlockOut {
  id: string;
  primal: number;
  dual: number;
  b1: number;
  eta: number;
  forbidden: number;
}
export interface BridgeOut {
  id: string;
  members: string[];
  recommendation: string;
}
export interface ForbiddenOut {
  kind: string;
  edges: string[];
  shared: string[];
}
export interface DecompositionOut {
  b0: number;
  b1: number;
  entanglement: number;
  blocks: BlockOut[];
  bridges: BridgeOut[];
  branches: number;
  forbidden: ForbiddenOut[];
}
export interface ScaleOut {
  scale: number;
  label: string;
  b0: number;
  b1: number;
  graph: HypergraphOut;
}

export interface GateAdvisory {
  id: string;
  severity: "critical" | "high" | "medium" | "low" | "unknown";
  summary: string;
  cwe: string | null;
}

/** Whether an agent may install a package, and everything the answer rests on.
 *
 *  Four-valued, and `unknown` is the one that matters: a gate that could not
 *  reach its feeds and answered "allow" would manufacture the impression of a
 *  check that never happened. Treat `unknown` as "nobody looked". */
export interface GateDecision {
  package: string;
  version: string;
  verdict: "allow" | "warn" | "block" | "unknown";
  reasons: string[];
  advisories: GateAdvisory[];
  worst: "critical" | "high" | "medium" | "low" | "unknown" | null;
  unavailable: string | null;
  policy: string;
  /** Fleet agents already on this exact version. Context, not permission —
   *  widely used and vulnerable is the worst case here, not the best. */
  fleet_agents: number;
  sample: boolean;
}

/** A machine registered to record as somebody — a laptop running an editor
 *  hook, which has no browser and so cannot sign in the way this UI does. */
export interface DeviceOut {
  id: string;
  label: string;
  subject: string;
  name: string;
  role: "developer" | "analyst";
  created_at: number;
  last_used: number;
  /** Whether the human who minted it was themselves verified. A bearer
   *  secret does not turn an asserted name into a proven one. */
  verified: boolean;
}

/** A login in progress, shown to the human before they approve it. The
 *  standing attack on every device flow is to start a pairing and talk a
 *  stranger into approving it, so this exists to be read, not skipped. */
export interface PairPending {
  user_code: string;
  label: string;
  started_at: number;
  approved: boolean;
  grants: string[];
}

/** An API failure carrying its status, so a screen can tell "nothing recorded
 *  yet" (404) apart from a real error. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fail(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      detail = String((body as { detail: unknown }).detail);
    }
  } catch {
    // no JSON body; keep the status line
  }
  throw new ApiError(res.status, detail);
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { headers: authHeaders() });
  if (!res.ok) return fail(res);
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) return fail(res);
  return res.json() as Promise<T>;
}

async function post<T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders(), ...headers },
    body: JSON.stringify(body),
  });
  if (!res.ok) return fail(res);
  return res.json() as Promise<T>;
}

export const api = {
  /** Unauthenticated, so it answers on a deployment that cannot yet say who
   *  you are — which is where the mode banner matters most. */
  health: () => get<HealthOut>("/health"),
  me: () => get<Me>("/me"),
  audit: () => get<AuditOut>("/audit"),

  checkPackage: (packageName: string, version: string) =>
    post<GateDecision>("/gate/package", { package: packageName, version }),

  devices: () => get<DeviceOut[]>("/devices"),
  revokeDevice: (id: string) =>
    del<DeviceOut>(`/devices/${encodeURIComponent(id)}`),
  pendingPair: (userCode: string) =>
    get<PairPending>(`/devices/pending/${encodeURIComponent(userCode)}`),
  approvePair: (userCode: string) =>
    post<PairPending>("/devices/approve", { user_code: userCode }),

  fleetOverview: () => get<FleetOverview>("/fleet/overview"),
  fleetCoverage: () => get<CoverageOut>("/fleet/coverage"),
  fleetQuery: (query: string) => post<QueryResult>("/fleet/query", { query }),
  recommendations: () => get<Recommendation[]>("/recommendations"),
  applyRecommendation: (id: string) =>
    post<ApplyReceipt>(`/recommendations/${encodeURIComponent(id)}/apply`, {}),

  runs: () => get<RunSummary[]>("/runs"),
  createRun: (task: string) => post<RunSummary>("/runs", { task }),

  runGraph: (runId: string) => get<GraphPayload>(`/runs/${runId}/graph`),
  runWhy: (runId: string, node: string) =>
    get<WhyOut>(`/runs/${runId}/why?node=${encodeURIComponent(node)}`),
  runRewind: (runId: string, at: number) =>
    get<RewindOut>(`/runs/${runId}/rewind?at=${at}`),

  runForgetPreview: (runId: string, node: string) =>
    get<ForgetPreview>(
      `/runs/${runId}/forget/preview?node=${encodeURIComponent(node)}`,
    ),
  /** `preview` is not optional by accident. Passing the version it was
   *  computed against is what makes the API refuse a deletion the reader
   *  never actually saw, and the idempotency key stops a double-submit
   *  purging a second closure under the first one's confirmation. */
  runForget: (runId: string, preview: ForgetPreview, reason?: string) =>
    post<DeletionCertificate>(
      `/runs/${runId}/forget`,
      reason === undefined
        ? { node: preview.node }
        : { node: preview.node, reason },
      {
        "if-match": preview.version,
        "idempotency-key": crypto.randomUUID(),
      },
    ),

  runFindings: (runId: string) => get<FindingsOut>(`/runs/${runId}/findings`),
  ingestScan: (runId: string, sarif: unknown) =>
    post<ScanOut>(`/runs/${runId}/scan`, sarif),
  runFinding: (runId: string, sink: string) =>
    get<Finding>(`/runs/${runId}/findings/${encodeURIComponent(sink)}`),

  recordEvents: (batch: RecorderBatch) =>
    post<RecorderReceipt>("/recorder", batch),

  runCode: (runId: string) => get<CodeOut>(`/runs/${runId}/code`),
  runSbom: (runId: string) => get<SbomOut>(`/runs/${runId}/sbom`),
  cveImpact: (cve: string) =>
    get<CveImpact>(`/cve/${encodeURIComponent(cve)}/impact`),

  runHypergraph: (runId: string) => get<HypergraphOut>(`/runs/${runId}/hypergraph`),
  runDecomposition: (runId: string) => get<DecompositionOut>(`/runs/${runId}/decomposition`),
  runScales: (runId: string) => get<ScaleOut[]>(`/runs/${runId}/scales`),
  fleetDecomposition: () => get<DecompositionOut>("/fleet/decomposition"),
};
