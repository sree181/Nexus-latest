import { useMemo, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { GraphNode } from "@meshagent/graph";

import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { ConflictRecovery, IntegrityRef, StateFrame, isVersionConflict } from "../components/WorkflowVisual";
import { MutationMessage, StatusBadge, TextArea } from "../components/WorkflowUI";
import { api, type Approval, type ApprovalDecisionInput, type GovernanceEvidence, type Policy, type PolicyException } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

import "./ciso-desk.css";

function cisoJourney(approval: Approval): { current: FigmaJourneyStage; overrides: Partial<Record<FigmaJourneyStage, "complete" | "current" | "pending" | "blocked">> } {
  const recorded = approval.status === "approved" || approval.status === "rejected";
  return {
    current: "ciso_decision",
    overrides: {
      ciso_decision: approval.expired ? "blocked" : recorded ? "complete" : "current",
    },
  };
}

function short(value: string, max = 28): string {
  return value.length > max ? `${value.slice(0, 12)}…${value.slice(-(max - 14))}` : value;
}

function ScopeToken({ label, children, tone = "slate" }: { label: string; children: ReactNode; tone?: "amber" | "teal" | "blue" | "green" | "violet" | "slate" }) {
  return <span className={`figma3-ciso-scope-token is-${tone}`}><small>{label}</small><strong>{children}</strong></span>;
}

function QueueRow({ approval, exception, selected, onSelect }: { approval: Approval; exception?: PolicyException; selected: boolean; onSelect: () => void }) {
  const urgency = approval.expired ? "expired" : approval.status === "pending" ? "pending" : approval.status;
  return (
    <button type="button" className={`figma3-ciso-queue-row is-${urgency} ${selected ? "is-selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
      <span className="figma3-ciso-queue-row-top"><code>{approval.id}</code><StatusBadge status={approval.status} /></span>
      <strong title={exception?.scope ?? approval.resource_id}>{exception?.scope ?? approval.resource_id}</strong>
      <small>{approval.requester_name} · {approval.kind.replaceAll("_", " ")}</small>
      <small className={approval.expired ? "is-overdue" : ""}>{approval.expired ? "Window closed" : `Due ${timestamp(approval.expires_at)}`}</small>
    </button>
  );
}

/** Kept exported so callers/tests retain the production route's decision surface. */
export function ApprovalDecisionPanel({ approval }: { approval: Approval }) {
  const client = useQueryClient();
  const { me } = useIdentity();
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [rationale, setRationale] = useState("");
  const [success, setSuccess] = useState<string | null>(null);
  const decide = useMutation({
    mutationFn: (input: ApprovalDecisionInput) => api.decideApproval(approval.id, input),
    onSuccess: (updated) => {
      setSuccess(`Request ${updated.status}.`);
      setDecision(null);
      setRationale("");
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["exceptions"] });
      void client.invalidateQueries({ queryKey: ["exception", approval.resource_id] });
      void client.invalidateQueries({ queryKey: ["governance-evidence", "exception", approval.resource_id] });
      void client.invalidateQueries({ queryKey: ["governanceOverview"] });
    },
  });
  const ownRequest = me?.subject === approval.requester;
  const reload = () => {
    decide.reset();
    setSuccess(null);
    void client.invalidateQueries({ queryKey: ["approvals"] });
    void client.invalidateQueries({ queryKey: ["exceptions"] });
    void client.invalidateQueries({ queryKey: ["exception", approval.resource_id] });
  };

  if (approval.status !== "pending") {
    return approval.decision_rationale ? <div className="figma3-ciso-receipt is-success"><strong>Decision rationale</strong><span>{approval.decision_rationale}</span></div> : null;
  }
  if (approval.expired) {
    return <div className="figma3-ciso-receipt is-warning"><strong>Decision window closed</strong><span>The server will not accept a decision for this expired approval.</span></div>;
  }

  return (
    <div className="figma3-ciso-decision-panel">
      <ConflictRecovery error={decide.error} onReload={reload} draft={rationale.trim() || undefined} />
      {ownRequest ? (
        <p className="figma3-ciso-owner-block" role="alert">You submitted this request. Another CISO must decide it.</p>
      ) : decision ? (
        <form
          className="figma3-ciso-decision-form"
          onSubmit={(event) => {
            event.preventDefault();
            setSuccess(null);
            decide.mutate({ expected_version: approval.version, decision, rationale: rationale.trim() });
          }}
        >
          <div className="figma3-ciso-decision-heading"><span className="figma3-kicker">Record a governed outcome</span><strong>{decision === "approve" ? "Approve within submitted scope" : "Reject this request"}</strong></div>
          <label className="figma3-ciso-rationale"><span>{decision} rationale</span><TextArea aria-label={`${decision} rationale`} value={rationale} onChange={(event) => setRationale(event.target.value)} required maxLength={4096} rows={5} /></label>
          <p className="figma3-ciso-durable-note">This action records an immutable governance decision against this request digest and its linked evidence.</p>
          <div className="figma3-ciso-decision-actions">
            <button type="submit" className={decision === "reject" ? "figma3-danger" : "figma3-primary"} disabled={decide.isPending || !rationale.trim()}>{decide.isPending ? "Recording…" : `Confirm ${decision}`}</button>
            <button type="button" className="figma3-secondary" disabled={decide.isPending} onClick={() => { setDecision(null); decide.reset(); }}>Cancel</button>
          </div>
          {!isVersionConflict(decide.error) ? <MutationMessage error={decide.error} success={success} /> : null}
        </form>
      ) : (
        <div className="figma3-ciso-choices">
          <button type="button" className="figma3-primary" aria-label="Approve" onClick={() => setDecision("approve")}>Approve within scope <span>→</span></button>
          <button type="button" className="figma3-danger" aria-label="Reject" onClick={() => setDecision("reject")}>Reject request</button>
        </div>
      )}
      {!ownRequest && !decision && !isVersionConflict(decide.error) ? <MutationMessage error={decide.error} success={success} /> : null}
    </div>
  );
}

function DecisionBounds({ exception, policy }: { exception?: PolicyException; policy?: Policy }) {
  return (
    <section className="figma3-ciso-boundary">
      <div className="figma3-ciso-section-heading"><p className="figma3-kicker">Submitted decision bounds</p><span>The server applies the recorded decision only to this request context.</span></div>
      {exception ? (
        <>
          <div className="figma3-ciso-scope-chain">
            <ScopeToken label="Policy" tone="amber"><Link to="/ciso/policies/$policyId" params={{ policyId: exception.policy_id }}>{policy?.name ?? exception.policy_id} · v{exception.policy_version}</Link></ScopeToken><i>→</i>
            <ScopeToken label="Scope" tone="blue"><code>{exception.scope}</code></ScopeToken><i>→</i>
            <ScopeToken label="Owner"><span>{exception.owner_name || exception.owner}</span></ScopeToken><i>→</i>
            <ScopeToken label="Controls" tone="green"><span>{exception.compensating_controls}</span></ScopeToken><i>→</i>
            <ScopeToken label="Expires" tone="violet"><span>{timestamp(exception.expires_at)}</span></ScopeToken>
          </div>
          <p className="figma3-ciso-boundary-note">Entities outside these submitted bounds are not covered by this decision.</p>
        </>
      ) : <StateFrame kind="empty" title="Exception context is unavailable" detail="Do not decide until the linked exception can be loaded." />}
    </section>
  );
}

function DecisionBrief({ approval, exception }: { approval: Approval; exception?: PolicyException }) {
  if (!exception) return null;
  return (
    <section className="figma3-ciso-brief">
      <p className="figma3-kicker">Structured decision brief</p>
      <div className="figma3-ciso-brief-grid">
        <div><small>Exact scope</small><code>{exception.scope}</code></div>
        <div><small>Owner</small><strong>{exception.owner_name || exception.owner}</strong></div>
        <div><small>Decision window</small><strong>{timestamp(approval.expires_at)}</strong></div>
        <div><small>Request digest</small><code title={approval.request_digest}>{short(approval.request_digest)}</code></div>
        <div className="is-wide"><small>Submitted rationale</small><p>{approval.rationale}</p></div>
        <div className="is-wide"><small>Compensating controls</small><p>{exception.compensating_controls}</p></div>
        <div className="is-wide"><small>Linked evidence</small><p className="figma3-ciso-evidence-ids">{approval.evidence_ids.length ? approval.evidence_ids.map((value) => <code key={value} title={value}>{short(value)}</code>) : "No linked review or case."}</p></div>
      </div>
    </section>
  );
}

function NativeEvidence({ exception }: { exception: PolicyException }) {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const evidence = useQuery<GovernanceEvidence>({
    queryKey: ["governance-evidence", "exception", exception.id],
    queryFn: () => api.exceptionEvidence(exception.id),
    retry: false,
  });

  return (
    <details className="figma3-ciso-evidence" open>
      <summary><span><span className="figma3-kicker">Native HyperMesh evidence</span><strong>Decision evidence relationship map</strong></span><span aria-hidden="true">⌄</span></summary>
      <div className="figma3-ciso-evidence-body">
        {evidence.isPending ? <StateFrame kind="loading" title="Projecting decision evidence" detail="The workflow record is already available." /> : evidence.isError ? (
          <StateFrame kind="error" title="Native evidence is not ready" detail="The workflow record remains available. Retry the evidence projection when the service is ready." action={<button type="button" className="figma3-secondary" onClick={() => void evidence.refetch()}>Try again</button>} />
        ) : evidence.data ? (
          <>
            <FigmaEvidenceGraph graph={evidence.data.graph} label="Exception decision evidence relationship map" selectedId={selectedNode?.id} onSelect={setSelectedNode} height={184} />
            <details className="figma3-ciso-integrity">
              <summary>Integrity references · {evidence.data.projection_count} acknowledged projection{evidence.data.projection_count === 1 ? "" : "s"}</summary>
              <dl>
                {evidence.data.native_ulids.map((ulid, index) => <div key={ulid}><dt>Native ULID</dt><dd title={ulid}>{short(ulid)}</dd><dt>Payload SHA-256</dt><dd title={evidence.data.payload_sha256[index]}>{short(evidence.data.payload_sha256[index] ?? "")}</dd></div>)}
              </dl>
            </details>
          </>
        ) : <StateFrame kind="empty" title="No native evidence is available" detail="The request context remains available for review." />}
      </div>
    </details>
  );
}

function DecisionNarrative({ approval, exception, policy, reviewOpen, setReviewOpen }: { approval: Approval; exception?: PolicyException; policy?: Policy; reviewOpen: boolean; setReviewOpen: (value: boolean) => void }) {
  const journey = cisoJourney(approval);
  return (
    <article className="figma3-ciso-body">
      <header className="figma3-ciso-heading">
        <div className="figma3-ciso-heading-meta"><span>Decision request · {approval.id}</span><StatusBadge status={approval.status} /><Badge tone="neutral">{approval.kind.replace(/_/g, " ")}</Badge>{approval.expired ? <Badge tone="risk">window closed</Badge> : null}</div>
        <h1>{approval.status === "pending" ? "Pending decision" : "Recorded decision"} <span>— {approval.id}</span></h1>
        <p>{exception?.scope ?? approval.resource_id}</p>
        <div className="figma3-ciso-submeta"><span>Requested by {approval.requester_name}</span><span>Expires {timestamp(approval.expires_at)}</span><span>Record v{approval.version}</span></div>
      </header>

      <section className="figma3-ciso-journey-card"><p className="figma3-kicker">Seven-stage journey</p><FigmaJourney current={journey.current} overrides={journey.overrides} label="Approval journey" /></section>
      <DecisionBounds exception={exception} policy={policy} />
      <DecisionBrief approval={approval} exception={exception} />

      <section id="decision-panel" className={`figma3-ciso-review ${reviewOpen ? "is-open" : ""}`}>
        <button type="button" className="figma3-ciso-review-summary" aria-expanded={reviewOpen} onClick={() => setReviewOpen(!reviewOpen)}>
          <span><small>Approval {approval.id}</small><strong>{approval.status === "pending" ? "Review and decide" : "Recorded decision"}</strong><em>{approval.expired ? "Decision window closed" : approval.status === "pending" ? "Open the governed decision form" : "View the recorded rationale"}</em></span><span aria-hidden="true">{reviewOpen ? "−" : "+"}</span>
        </button>
        {reviewOpen || approval.status !== "pending" ? <div className="figma3-ciso-review-body"><ApprovalDecisionPanel key={`${approval.id}:${approval.version}`} approval={approval} /></div> : null}
      </section>

      {exception ? <NativeEvidence exception={exception} /> : null}
    </article>
  );
}

function MobileQueue({ items, selected, exceptionById, view, setView, select }: { items: Approval[]; selected: Approval; exceptionById: Map<string, PolicyException>; view: "pending" | "all"; setView: (view: "pending" | "all") => void; select: (id: string) => void }) {
  return (
    <details className="figma3-ciso-mobile-queue">
      <summary><span><span className="figma3-kicker">Decision queue</span><strong>{items.length} visible request{items.length === 1 ? "" : "s"}</strong></span><span aria-hidden="true">⌄</span></summary>
      <div>
        <div className="figma3-ciso-view-tabs">{(["pending", "all"] as const).map((value) => <button key={value} type="button" aria-pressed={view === value} onClick={() => setView(value)}>{value === "pending" ? "Waiting" : "All"}</button>)}</div>
        <div className="figma3-ciso-mobile-queue-list">{items.map((approval) => <QueueRow key={approval.id} approval={approval} exception={exceptionById.get(approval.resource_id)} selected={selected.id === approval.id} onSelect={() => select(approval.id)} />)}</div>
      </div>
    </details>
  );
}

export function CisoApprovals() {
  // Route capability remains enforced by the /ciso/approvals router wrapper; data comes only from the server-bound APIs below.
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  const exceptions = useQuery({ queryKey: ["exceptions"], queryFn: api.exceptions });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [view, setViewState] = useState<"pending" | "all">("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const { me } = useIdentity();

  const items = useMemo(() => (approvals.data ?? []).filter((item) => view === "all" || item.status === "pending"), [approvals.data, view]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const exceptionById = new Map((exceptions.data ?? []).map((item) => [item.id, item]));
  const policyById = new Map((policies.data ?? []).map((item) => [item.id, item]));
  const selectedException = selected ? exceptionById.get(selected.resource_id) : undefined;
  const pendingCount = (approvals.data ?? []).filter((item) => item.status === "pending").length;
  const loading = approvals.isPending || exceptions.isPending || policies.isPending;
  const loadError = approvals.error ?? exceptions.error ?? policies.error;
  const ownRequest = Boolean(selected && me?.subject === selected.requester);
  const select = (id: string) => { setSelectedId(id); setReviewOpen(false); };
  const setView = (next: "pending" | "all") => { setViewState(next); setSelectedId(null); setReviewOpen(false); };
  const focusDecision = () => {
    setReviewOpen(true);
    window.requestAnimationFrame(() => document.getElementById("decision-panel")?.scrollIntoView({ behavior: "smooth", block: "center" }));
  };

  if (loading) {
    return <main className="figma3-page figma3-ciso-page"><div className="figma3-ciso-state"><StateFrame kind="loading" title="Loading decision context" detail="Joining approvals with their policy and exception records." /></div></main>;
  }
  if (loadError) {
    return <main className="figma3-page figma3-ciso-page"><div className="figma3-ciso-state"><StateFrame kind="error" title="Decision context could not be loaded" detail={loadError instanceof Error ? loadError.message : "One or more records are unavailable."} action={<button type="button" className="figma3-secondary" onClick={() => { void approvals.refetch(); void exceptions.refetch(); void policies.refetch(); }}>Try again</button>} /></div></main>;
  }
  if (!selected) {
    return <main className="figma3-page figma3-ciso-page"><div className="figma3-ciso-state"><StateFrame kind="empty" title="Nothing is waiting" detail="New exception requests will appear here with their complete decision context." /></div></main>;
  }

  const policy = selectedException ? policyById.get(selectedException.policy_id) : undefined;
  const dockActions = selected.status === "pending" && !selected.expired && !ownRequest ? <button type="button" className="figma3-primary" onClick={focusDecision}>Review and decide <span>→</span></button> : undefined;
  const dockBlocking = ownRequest ? "You submitted this request. Another CISO must decide it." : selected.expired ? "The server will not accept a decision for this expired approval." : undefined;

  return (
    <main className="figma3-page figma3-ciso-page">
      <div className="figma3-ciso-desktop figma3-desktop-only">
        <aside className="figma3-ciso-queue" aria-label="Decision queue">
          <header><div><p className="figma3-kicker">Decision queue</p><h1>{pendingCount} waiting</h1></div><span>{items.length} shown</span></header>
          <div className="figma3-ciso-view-tabs">{(["pending", "all"] as const).map((value) => <button key={value} type="button" onClick={() => setView(value)} aria-pressed={view === value}>{value === "pending" ? "Waiting" : "All"}</button>)}</div>
          <div className="figma3-ciso-queue-list" role="listbox" aria-label="Approval requests">{items.map((approval) => <QueueRow key={approval.id} approval={approval} exception={exceptionById.get(approval.resource_id)} selected={selected.id === approval.id} onSelect={() => select(approval.id)} />)}</div>
        </aside>
        <section className="figma3-ciso-main" aria-label="Selected decision"><DecisionNarrative approval={selected} exception={selectedException} policy={policy} reviewOpen={reviewOpen} setReviewOpen={setReviewOpen} /></section>
        <FigmaResponsibilityDock
          className="figma3-ciso-dock"
          responsibility={selected.status === "pending" ? "Decide only within the submitted scope." : "Review the recorded outcome."}
          why={ownRequest ? "You submitted this request. Another CISO must decide it." : selected.status === "pending" ? "Confirm the controls and native evidence, then record a rationale. The API validates requester separation and version freshness." : "The decision remains linked to the request digest and evidence."}
          actions={dockActions}
          deadline={timestamp(selected.expires_at)}
          overdue={selected.expired}
          blockingCondition={dockBlocking}
        >
          <div className="figma3-ciso-integrity-stack"><IntegrityRef label="Approval" value={selected.id} /><IntegrityRef label="Request digest" value={selected.request_digest} /></div>
          {selectedException ? <Link to="/ciso/exceptions/$exceptionId" params={{ exceptionId: selectedException.id }} className="figma3-secondary figma3-ciso-open-record">Open complete record</Link> : null}
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-ciso-mobile figma3-mobile-only">
        <div className="figma3-ciso-mobile-scroll">
          <div className="figma3-ciso-mobile-context"><strong>CISO Decision Desk</strong><small>{pendingCount} pending approval{pendingCount === 1 ? "" : "s"}</small></div>
          <DecisionNarrative approval={selected} exception={selectedException} policy={policy} reviewOpen={reviewOpen} setReviewOpen={setReviewOpen} />
          <MobileQueue items={items} selected={selected} exceptionById={exceptionById} view={view} setView={setView} select={select} />
        </div>
        {dockActions ? <FigmaMobileDock deadline={timestamp(selected.expires_at)}>{dockActions}</FigmaMobileDock> : null}
      </div>
    </main>
  );
}
