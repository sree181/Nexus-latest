import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { GovernanceEvidencePanel } from "../components/GovernanceEvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { ConflictRecovery, IntegrityRef, StateFrame, WorkflowHero, WorkflowJourney, WorkflowSection, isVersionConflict, type JourneyItem } from "../components/WorkflowVisual";
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
  const reload = () => {
    decide.reset();
    setSuccess(null);
    void client.invalidateQueries({ queryKey: ["approvals"] });
    void client.invalidateQueries({ queryKey: ["exceptions"] });
    void client.invalidateQueries({ queryKey: ["exception", approval.resource_id] });
  };

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

function DecisionDetail({ approval, exception, policy }: { approval: Approval; exception?: PolicyException; policy?: Policy }) {
  return (
    <div className="workflow-stack">
      <WorkflowHero
        eyebrow={`Decision request · ${approval.id}`}
        title={exception?.scope ?? approval.resource_id}
        description={approval.rationale}
        tone={approval.expired ? "danger" : approval.status === "pending" ? "warning" : approval.status === "approved" ? "success" : "danger"}
        status={<div className="flex flex-wrap gap-2"><StatusBadge status={approval.status} /><Badge tone="neutral">{approval.kind.replace(/_/g, " ")}</Badge>{approval.expired ? <Badge tone="risk">window closed</Badge> : null}</div>}
        meta={<><span>Requested by {approval.requester_name}</span><span>Expires {timestamp(approval.expires_at)}</span><span>Version {approval.version}</span></>}
      />

      <WorkflowSection eyebrow="Journey" title="Approval path" description="The requester and deciding principal remain separate.">
        <WorkflowJourney items={approvalJourney(approval)} label="Approval journey" />
      </WorkflowSection>

      <WorkflowSection eyebrow="Bounded scope" title="What this decision covers" description="Approval applies only to the exact policy, scope, digest, controls, and expiry shown here.">
        {exception ? <div className="decision-scope-grid"><div><span>Policy</span><Link to="/ciso/policies/$policyId" params={{ policyId: exception.policy_id }}>{policy?.name ?? exception.policy_id} · v{exception.policy_version}</Link></div><div><span>Owner</span><strong>{exception.owner_name || exception.owner}</strong></div><div><span>Expiry</span><strong>{timestamp(exception.expires_at)}</strong></div><div><span>Status</span><StatusBadge status={exception.status} /></div></div> : <StateFrame kind="empty" title="Exception context is unavailable" detail="Do not decide until the linked exception can be loaded." />}
        {exception ? <dl className="decision-brief"><div><dt>Compensating controls</dt><dd>{exception.compensating_controls}</dd></div><div><dt>Submitted evidence</dt><dd className="flex flex-wrap gap-2">{approval.evidence_ids.length ? approval.evidence_ids.map((value) => <code key={value}>{value}</code>) : "No linked review or case."}</dd></div></dl> : null}
      </WorkflowSection>

      {exception ? <GovernanceEvidencePanel kind="exception" id={exception.id} /> : null}
    </div>
  );
}

export function CisoApprovals() {
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  const exceptions = useQuery({ queryKey: ["exceptions"], queryFn: api.exceptions });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [view, setView] = useState<"pending" | "all">("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const items = useMemo(() => (approvals.data ?? []).filter((item) => view === "all" || item.status === "pending"), [approvals.data, view]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const exceptionById = new Map((exceptions.data ?? []).map((item) => [item.id, item]));
  const policyById = new Map((policies.data ?? []).map((item) => [item.id, item]));
  const selectedException = selected ? exceptionById.get(selected.resource_id) : undefined;

  const pendingCount = (approvals.data ?? []).filter((item) => item.status === "pending").length;
  const loading = approvals.isPending || exceptions.isPending || policies.isPending;
  const loadError = approvals.error ?? exceptions.error ?? policies.error;

  return (
    <main className="workflow-page">
      <PageHeader section="CISO" title="Decision desk" meta={<span>server-enforced independence</span>} />
      <div className="workflow-scroll">
        <div className="workflow-main mx-auto w-full max-w-[1500px]">
          <WorkflowHero
            eyebrow="CISO decision desk"
            title={`${pendingCount} request${pendingCount === 1 ? "" : "s"} waiting`}
            description="Decide time-bounded policy exceptions with their exact request, policy, and native evidence in view."
            tone={pendingCount ? "warning" : "success"}
            meta={<><span>Requester self-decision blocked</span><span>Stale writes rejected</span><span>Scope and expiry bound</span></>}
          />

          {loading ? <StateFrame kind="loading" title="Loading decision context" detail="Joining approvals with their policy and exception records." /> : loadError ? <StateFrame kind="error" title="Decision context could not be loaded" detail={loadError instanceof Error ? loadError.message : "One or more records are unavailable."} action={<button type="button" className="workflow-secondary-action" onClick={() => { void approvals.refetch(); void exceptions.refetch(); void policies.refetch(); }}>Try again</button>} /> : items.length === 0 ? <StateFrame kind="empty" title="Nothing is waiting" detail="New exception requests will appear here with their complete decision context." /> : (
            <div className="decision-desk-grid">
              <WorkflowSection className="flush decision-queue" eyebrow="Queue" title="Decision requests" action={<div className="inline-flex rounded-lg bg-surface-2 p-1">{(["pending", "all"] as const).map((value) => <button key={value} type="button" onClick={() => { setView(value); setSelectedId(null); }} className={`rounded-md px-3 py-1.5 text-xs font-medium ${view === value ? "bg-white text-ink shadow-sm" : "text-slate"}`}>{value === "pending" ? "Waiting" : "All"}</button>)}</div>}>
                <ol className="decision-queue-list">
                  {items.map((approval) => {
                    const exception = exceptionById.get(approval.resource_id);
                    return <li key={approval.id}><button type="button" className={(selected?.id === approval.id) ? "is-selected" : ""} aria-pressed={selected?.id === approval.id} onClick={() => setSelectedId(approval.id)}><span className="decision-queue-top"><StatusBadge status={approval.status} /><small>{timestamp(approval.expires_at)}</small></span><strong>{exception?.scope ?? approval.resource_id}</strong><span>{approval.requester_name} · {approval.kind.replaceAll("_", " ")}</span></button></li>;
                  })}
                </ol>
              </WorkflowSection>

              <div className="decision-detail-column">
                <DecisionDetail approval={selected} exception={selectedException} policy={selectedException ? policyById.get(selectedException.policy_id) : undefined} />
                <aside className="decision-action-dock">
                  <div><p className="workflow-eyebrow">Your responsibility</p><h2>{selected.status === "pending" ? "Decide only within the submitted scope." : "Review the recorded outcome."}</h2><p>{selected.status === "pending" ? "Confirm the controls and evidence, then record a rationale. The API enforces requester separation and version freshness." : "The decision remains linked to the request digest and native evidence."}</p></div>
                  <ApprovalDecisionPanel key={`${selected.id}:${selected.version}`} approval={selected} />
                  <div className="grid gap-2"><IntegrityRef label="Approval" value={selected.id} /><IntegrityRef label="Request digest" value={selected.request_digest} /></div>
                  {selectedException ? <Link to="/ciso/exceptions/$exceptionId" params={{ exceptionId: selectedException.id }} className="workflow-secondary-action text-center">Open complete record</Link> : null}
                </aside>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
