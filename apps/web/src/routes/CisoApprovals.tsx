import { useMemo, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { GovernanceEvidencePanel } from "../components/GovernanceEvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { ConflictRecovery, IntegrityRef, ResponsibilityDock, StateFrame, WorkflowJourney, isVersionConflict, type JourneyItem } from "../components/WorkflowVisual";
import { MutationMessage, StatusBadge, TextArea } from "../components/WorkflowUI";
import { api, type Approval, type ApprovalDecisionInput, type Policy, type PolicyException } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";
import { timestamp } from "../lib/format";

function approvalJourney(approval: Approval): JourneyItem[] {
  const decided = approval.status === "approved" || approval.status === "rejected";
  return [
    { id: "requested", label: "Requested", detail: timestamp(approval.created_at), state: "complete" },
    { id: "independence", label: "Independent decision", detail: decided ? approval.approver_name ?? "Recorded" : "Waiting", state: decided ? "complete" : "current" },
    { id: "bounded", label: "Bounded outcome", detail: approval.status, state: decided ? "complete" : approval.expired ? "blocked" : "pending" },
    { id: "expiry", label: "Expiry", detail: timestamp(approval.expires_at), state: approval.expired ? "complete" : "pending" },
  ];
}

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
  const reload = () => { decide.reset(); setSuccess(null); void client.invalidateQueries({ queryKey: ["approvals"] }); void client.invalidateQueries({ queryKey: ["exceptions"] }); void client.invalidateQueries({ queryKey: ["exception", approval.resource_id] }); };

  if (approval.status !== "pending") return approval.decision_rationale ? <div className="workflow-receipt is-success"><strong>Decision rationale</strong><span>{approval.decision_rationale}</span></div> : null;
  if (approval.expired) return <div className="workflow-receipt is-warning"><strong>Decision window closed</strong><span>The server will not accept a decision for this expired approval.</span></div>;
  return (
    <div className="approval-decision-panel">
      <ConflictRecovery error={decide.error} onReload={reload} draft={rationale.trim() || undefined} />
      {ownRequest ? <p className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-sm text-ink">You submitted this request. Another CISO must decide it.</p> : decision ? (
        <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); decide.mutate({ expected_version: approval.version, decision, rationale: rationale.trim() }); }} className="space-y-3">
          <label className="block"><span className="workflow-eyebrow">{decision} rationale</span><TextArea aria-label={`${decision} rationale`} value={rationale} onChange={(event) => setRationale(event.target.value)} required maxLength={4096} className="mt-1.5" /></label>
          <div className="flex flex-wrap gap-2"><Button type="submit" variant={decision === "reject" ? "danger" : undefined} disabled={decide.isPending || !rationale.trim()}>{decide.isPending ? "Recording…" : `Confirm ${decision}`}</Button><Button type="button" variant="ghost" disabled={decide.isPending} onClick={() => { setDecision(null); decide.reset(); }}>Cancel</Button></div>
          {!isVersionConflict(decide.error) ? <MutationMessage error={decide.error} success={success} /> : null}
        </form>
      ) : <div className="approval-choice"><button type="button" aria-label="Approve" className="workflow-primary-action" onClick={() => setDecision("approve")}>Approve within scope</button><button type="button" aria-label="Reject" className="workflow-danger-action" onClick={() => setDecision("reject")}>Reject request</button></div>}
      {!ownRequest && !decision && !isVersionConflict(decide.error) ? <MutationMessage error={decide.error} success={success} /> : null}
    </div>
  );
}

function ScopeToken({ label, children }: { label: string; children: ReactNode }) {
  return <span className="decision-scope-token"><small>{label}</small><strong>{children}</strong></span>;
}

function DecisionNarrative({ approval, exception, policy, reviewOpen, setReviewOpen }: { approval: Approval; exception?: PolicyException; policy?: Policy; reviewOpen: boolean; setReviewOpen: (value: boolean) => void }) {
  return (
    <article className="decision-narrative">
      <header className="decision-focus-heading">
        <div className="decision-focus-meta"><span>Decision request · {approval.id}</span><StatusBadge status={approval.status} /><Badge tone="neutral">{approval.kind.replace(/_/g, " ")}</Badge>{approval.expired ? <Badge tone="risk">window closed</Badge> : null}</div>
        <h1>{exception?.scope ?? approval.resource_id}</h1>
        <p>{approval.rationale}</p>
        <div className="decision-focus-submeta"><span>Requested by {approval.requester_name}</span><span>Expires {timestamp(approval.expires_at)}</span><span>Record v{approval.version}</span></div>
      </header>

      <section className="focus-journey-card"><p className="workflow-eyebrow">Approval path</p><WorkflowJourney items={approvalJourney(approval)} label="Approval journey" /></section>

      <section className="decision-boundary">
        <div className="decision-boundary-heading"><p className="workflow-eyebrow">Submitted decision bounds</p><span>The server applies the recorded decision only to this request context.</span></div>
        {exception ? (
          <div className="decision-scope-chain">
            <ScopeToken label="Policy"><Link to="/ciso/policies/$policyId" params={{ policyId: exception.policy_id }}>{policy?.name ?? exception.policy_id} · v{exception.policy_version}</Link></ScopeToken><i>→</i>
            <ScopeToken label="Scope">{exception.scope}</ScopeToken><i>→</i>
            <ScopeToken label="Owner">{exception.owner_name || exception.owner}</ScopeToken><i>→</i>
            <ScopeToken label="Controls">{exception.compensating_controls}</ScopeToken><i>→</i>
            <ScopeToken label="Expires">{timestamp(exception.expires_at)}</ScopeToken>
          </div>
        ) : <StateFrame kind="empty" title="Exception context is unavailable" detail="Do not decide until the linked exception can be loaded." />}
      </section>

      {exception ? (
        <section className="decision-structured-brief">
          <div><small>Exact scope</small><strong>{exception.scope}</strong></div><div><small>Owner</small><strong>{exception.owner_name || exception.owner}</strong></div><div><small>Decision window</small><strong>{timestamp(approval.expires_at)}</strong></div><div><small>Request digest</small><code>{approval.request_digest}</code></div>
          <div className="is-wide"><small>Submitted rationale</small><p>{approval.rationale}</p></div><div className="is-wide"><small>Compensating controls</small><p>{exception.compensating_controls}</p></div><div className="is-wide"><small>Linked evidence</small><p className="decision-evidence-ids">{approval.evidence_ids.length ? approval.evidence_ids.map((value) => <code key={value}>{value}</code>) : "No linked review or case."}</p></div>
        </section>
      ) : null}

      {exception ? <section className="decision-native-evidence"><GovernanceEvidencePanel kind="exception" id={exception.id} /></section> : null}

      <section id="decision-panel" className={`decision-review-panel ${reviewOpen ? "is-open" : ""}`}>
        <button type="button" className="decision-review-summary" aria-expanded={reviewOpen} onClick={() => setReviewOpen(!reviewOpen)}><span><small>Approval {approval.id}</small><strong>{approval.status === "pending" ? "Review and decide" : "Recorded decision"}</strong></span><span>{reviewOpen ? "−" : "+"}</span></button>
        {reviewOpen || approval.status !== "pending" ? <div className="decision-review-body"><ApprovalDecisionPanel key={`${approval.id}:${approval.version}`} approval={approval} /></div> : null}
      </section>
    </article>
  );
}

export function CisoApprovals() {
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  const exceptions = useQuery({ queryKey: ["exceptions"], queryFn: api.exceptions });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [view, setView] = useState<"pending" | "all">("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const items = useMemo(() => (approvals.data ?? []).filter((item) => view === "all" || item.status === "pending"), [approvals.data, view]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const exceptionById = new Map((exceptions.data ?? []).map((item) => [item.id, item]));
  const policyById = new Map((policies.data ?? []).map((item) => [item.id, item]));
  const selectedException = selected ? exceptionById.get(selected.resource_id) : undefined;
  const pendingCount = (approvals.data ?? []).filter((item) => item.status === "pending").length;
  const loading = approvals.isPending || exceptions.isPending || policies.isPending;
  const loadError = approvals.error ?? exceptions.error ?? policies.error;
  const { me } = useIdentity();
  const ownRequest = Boolean(selected && me?.subject === selected.requester);
  const focusDecision = () => { setReviewOpen(true); window.requestAnimationFrame(() => document.getElementById("decision-panel")?.scrollIntoView({ behavior: "smooth", block: "center" })); };

  return (
    <main className="workflow-page ciso-desk-page">
      <PageHeader section="CISO" title="Decision desk" meta={<span>server-enforced independence</span>} />
      {loading ? <div className="workflow-scroll"><StateFrame kind="loading" title="Loading decision context" detail="Joining approvals with their policy and exception records." /></div> : loadError ? <div className="workflow-scroll"><StateFrame kind="error" title="Decision context could not be loaded" detail={loadError instanceof Error ? loadError.message : "One or more records are unavailable."} action={<button type="button" className="workflow-secondary-action" onClick={() => { void approvals.refetch(); void exceptions.refetch(); void policies.refetch(); }}>Try again</button>} /></div> : items.length === 0 ? <div className="workflow-scroll"><StateFrame kind="empty" title="Nothing is waiting" detail="New exception requests will appear here with their complete decision context." /></div> : (
        <div className="ciso-decision-shell">
          <aside className="decision-queue-pane" aria-label="Decision queue">
            <header><div><p className="workflow-eyebrow">Decision queue</p><h1>{pendingCount} waiting</h1></div><span>{items.length}</span></header>
            <div className="decision-view-tabs">{(["pending", "all"] as const).map((value) => <button key={value} type="button" onClick={() => { setView(value); setSelectedId(null); setReviewOpen(false); }} aria-pressed={view === value}>{value === "pending" ? "Waiting" : "All"}</button>)}</div>
            <ol className="decision-queue-list">{items.map((approval) => { const exception = exceptionById.get(approval.resource_id); return <li key={approval.id}><button type="button" className={selected?.id === approval.id ? "is-selected" : ""} aria-pressed={selected?.id === approval.id} onClick={() => { setSelectedId(approval.id); setReviewOpen(false); }}><span className="decision-queue-top"><StatusBadge status={approval.status} /><small>{timestamp(approval.expires_at)}</small></span><strong>{exception?.scope ?? approval.resource_id}</strong><span>{approval.requester_name} · {approval.kind.replaceAll("_", " ")}</span></button></li>; })}</ol>
          </aside>
          <section className="decision-focus-scroll"><DecisionNarrative approval={selected} exception={selectedException} policy={selectedException ? policyById.get(selectedException.policy_id) : undefined} reviewOpen={reviewOpen} setReviewOpen={setReviewOpen} /></section>
          <ResponsibilityDock responsibility={selected.status === "pending" ? "Decide only within the submitted scope." : "Review the recorded outcome."} why={ownRequest ? "You submitted this request. Another CISO must decide it." : selected.status === "pending" ? "Confirm the controls and native evidence, then record a rationale. The API validates requester separation and version freshness." : "The decision remains linked to the request digest and evidence."} deadline={timestamp(selected.expires_at)} overdue={selected.expired}>
            {selected.status === "pending" && !selected.expired && !ownRequest ? <button type="button" className="workflow-primary-action" onClick={focusDecision}>Review and decide</button> : null}
            <div className="grid gap-2"><IntegrityRef label="Approval" value={selected.id} /><IntegrityRef label="Request digest" value={selected.request_digest} /></div>
            {selectedException ? <Link to="/ciso/exceptions/$exceptionId" params={{ exceptionId: selectedException.id }} className="workflow-secondary-action text-center">Open complete record</Link> : null}
          </ResponsibilityDock>
          {selected.status === "pending" && !selected.expired && !ownRequest ? <button type="button" className="ciso-mobile-action" onClick={focusDecision}>Review and decide · {timestamp(selected.expires_at)}</button> : null}
        </div>
      )}
    </main>
  );
}
