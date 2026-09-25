import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { MutationMessage, StatusBadge, TextArea } from "../components/WorkflowUI";
import { api, type Approval, type ApprovalDecisionInput, type Policy, type PolicyException } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";
import { timestamp } from "../lib/format";

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

  if (approval.status !== "pending") return approval.decision_rationale ? <p className="mt-4 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-slate"><strong>Decision rationale:</strong> {approval.decision_rationale}</p> : null;
  return <div className="mt-5 border-t border-line pt-4">{ownRequest ? <p className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-sm text-ink">You submitted this request. Another CISO must decide it.</p> : decision ? <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); decide.mutate({ expected_version: approval.version, decision, rationale: rationale.trim() }); }} className="space-y-3"><label className="block"><span className="font-mono text-[10.5px] tracking-widest text-slate">{decision.toUpperCase()} RATIONALE</span><TextArea aria-label={`${decision} rationale`} value={rationale} onChange={(event) => setRationale(event.target.value)} required maxLength={4096} className="mt-1.5" /></label><div className="flex flex-wrap gap-2"><Button type="submit" variant={decision === "reject" ? "danger" : undefined} disabled={decide.isPending || !rationale.trim()}>{decide.isPending ? "Recording…" : `Confirm ${decision}`}</Button><Button type="button" variant="ghost" disabled={decide.isPending} onClick={() => { setDecision(null); decide.reset(); }}>Cancel</Button></div><MutationMessage error={decide.error} success={success} /></form> : <div className="flex flex-wrap gap-2"><Button type="button" onClick={() => setDecision("approve")}>Approve</Button><Button type="button" variant="danger" onClick={() => setDecision("reject")}>Reject</Button></div>}{!ownRequest && !decision ? <MutationMessage error={decide.error} success={success} /> : null}</div>;
}

function DecisionCard({ approval, exception, policy }: { approval: Approval; exception?: PolicyException; policy?: Policy }) {
  return <article className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]"><div className="flex flex-wrap items-start justify-between gap-3"><div className="flex flex-wrap items-center gap-2"><StatusBadge status={approval.status} /><Badge tone="neutral">{approval.kind.replace(/_/g, " ")}</Badge>{approval.expired ? <Badge tone="risk">window closed</Badge> : null}</div><span className="font-mono text-[10px] text-slate">v{approval.version}</span></div><h2 className="mt-3 font-serif text-xl font-semibold text-ink">{exception?.scope ?? approval.resource_id}</h2><p className="mt-1 text-sm text-slate">Requested by {approval.requester_name} · expires {timestamp(approval.expires_at)}</p>
    {exception ? <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-xl bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">POLICY</p><Link to="/ciso/policies/$policyId" params={{ policyId: exception.policy_id }} className="mt-1 inline-block text-sm font-medium text-accent hover:underline">{policy?.name ?? exception.policy_id} · v{exception.policy_version}</Link><p className="mt-2 break-all font-mono text-[9px] text-slate">{exception.policy_digest}</p></div><div className="rounded-xl bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">OWNER & EXPIRY</p><p className="mt-1 text-sm text-ink">{exception.owner_name || exception.owner}</p><p className="mt-1 text-xs text-slate">{timestamp(exception.expires_at)}</p></div></div> : null}
    <dl className="mt-4 space-y-3"><div><dt className="font-mono text-[10px] tracking-widest text-slate">WHY THIS IS NEEDED</dt><dd className="mt-1 text-sm leading-relaxed text-ink">{approval.rationale}</dd></div>{exception ? <div><dt className="font-mono text-[10px] tracking-widest text-slate">COMPENSATING CONTROLS</dt><dd className="mt-1 rounded-xl border border-line-2 bg-white p-3 text-sm leading-relaxed text-ink">{exception.compensating_controls}</dd></div> : null}<div><dt className="font-mono text-[10px] tracking-widest text-slate">EVIDENCE</dt><dd className="mt-2 flex flex-wrap gap-2">{approval.evidence_ids.length ? approval.evidence_ids.map((value) => <span key={value} className="rounded-md border border-line-2 bg-surface-2 px-2 py-1 font-mono text-[10px] text-slate">{value}</span>) : <span className="text-xs text-slate">No linked review or case.</span>}</dd></div><div><dt className="font-mono text-[10px] tracking-widest text-slate">REQUEST SHA-256</dt><dd className="mt-1 break-all font-mono text-[9px] text-slate">{approval.request_digest}</dd></div></dl>
    <div className="mt-4"><Link to="/ciso/exceptions/$exceptionId" params={{ exceptionId: approval.resource_id }} className="text-sm font-medium text-accent hover:underline">Open complete decision record</Link></div><ApprovalDecisionPanel approval={approval} /></article>;
}

export function CisoApprovals() {
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  const exceptions = useQuery({ queryKey: ["exceptions"], queryFn: api.exceptions });
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [view, setView] = useState<"pending" | "all">("pending");
  const items = useMemo(() => (approvals.data ?? []).filter((item) => view === "all" || item.status === "pending"), [approvals.data, view]);
  const exceptionById = new Map((exceptions.data ?? []).map((item) => [item.id, item]));
  const policyById = new Map((policies.data ?? []).map((item) => [item.id, item]));

  return <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden"><PageHeader section="CISO" title="Decision desk" meta={<span>exact request and policy context</span>} /><div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6"><section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface p-4"><div><p className="font-serif text-lg font-semibold text-ink">{(approvals.data ?? []).filter((item) => item.status === "pending").length} waiting for a decision</p><p className="mt-1 text-sm text-slate">The requester cannot decide their own request. Stale decisions are rejected by the API.</p></div><div className="inline-flex rounded-lg bg-surface-2 p-1">{(["pending", "all"] as const).map((value) => <button key={value} type="button" onClick={() => setView(value)} className={`rounded-md px-3 py-1.5 text-xs font-medium ${view === value ? "bg-white text-ink shadow-sm" : "text-slate"}`}>{value === "pending" ? "Waiting" : "All decisions"}</button>)}</div></section>{approvals.isPending || exceptions.isPending || policies.isPending ? <Loading label="Loading decision context…" /> : approvals.isError || exceptions.isError || policies.isError ? <ErrorState error={approvals.error ?? exceptions.error ?? policies.error} retry={() => { void approvals.refetch(); void exceptions.refetch(); void policies.refetch(); }} /> : items.length === 0 ? <section className="grid min-h-48 place-items-center rounded-2xl border border-dashed border-line-2 bg-surface p-8 text-center"><div><p className="font-serif text-lg font-semibold text-ink">Nothing is waiting</p><p className="mt-1 text-sm text-slate">New exception requests will appear here with their full decision context.</p></div></section> : <div className="grid gap-4 2xl:grid-cols-2">{items.map((approval) => { const exception = exceptionById.get(approval.resource_id); return <DecisionCard key={approval.id} approval={approval} exception={exception} policy={exception ? policyById.get(exception.policy_id) : undefined} />; })}</div>}</div></main>;
}
