import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { MutationMessage, StatusBadge, TextArea } from "../components/WorkflowUI";
import { api, type Approval, type ApprovalDecisionInput } from "../lib/api";
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
      void client.invalidateQueries({ queryKey: ["governanceOverview"] });
    },
  });
  const ownRequest = me?.subject === approval.requester;

  if (approval.status !== "pending") {
    return approval.decision_rationale ? <p className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-slate"><strong>Decision rationale:</strong> {approval.decision_rationale}</p> : null;
  }

  return (
    <div className="mt-4 border-t border-line pt-4">
      {ownRequest ? (
        <p className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-sm text-ink">You requested this exception. Separation of duties requires another CISO identity to decide it.</p>
      ) : decision ? (
        <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); decide.mutate({ expected_version: approval.version, decision, rationale: rationale.trim() }); }} className="space-y-3">
          <label className="block"><span className="font-mono text-[10.5px] tracking-widest text-slate">{decision.toUpperCase()} RATIONALE</span><TextArea aria-label={`${decision} rationale`} value={rationale} onChange={(event) => setRationale(event.target.value)} required maxLength={4096} className="mt-1.5" /></label>
          <div className="flex flex-wrap gap-2"><Button type="submit" variant={decision === "reject" ? "danger" : undefined} disabled={decide.isPending || !rationale.trim()}>{decide.isPending ? "Recording decision…" : `Confirm ${decision}`}</Button><Button type="button" variant="ghost" disabled={decide.isPending} onClick={() => { setDecision(null); decide.reset(); }}>Cancel</Button></div>
          <MutationMessage error={decide.error} success={success} />
        </form>
      ) : (
        <div className="flex flex-wrap gap-2"><Button type="button" onClick={() => setDecision("approve")}>Approve</Button><Button type="button" variant="danger" onClick={() => setDecision("reject")}>Reject</Button></div>
      )}
      {!ownRequest && !decision ? <MutationMessage error={decide.error} success={success} /> : null}
    </div>
  );
}

export function CisoApprovals() {
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO" title="Approvals" meta={<span>exception decisions</span>} />
      <Async query={approvals} label="Loading approval requests…" isEmpty={(items) => items.length === 0} emptyTitle="No approval requests" emptyDetail="Exception requests will appear here with requester, expiry, and rationale.">
        {(items) => (
          <div className="flex flex-1 flex-col gap-4 overflow-auto p-4 sm:p-6">
            <section className="rounded-xl border border-line bg-surface px-4 py-3 text-sm text-slate">Decisions are version checked and written to the action log. A requester cannot approve or reject their own request.</section>
            <ul className="grid gap-4 xl:grid-cols-2">
              {items.map((approval) => (
                <li key={approval.id} className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
                  <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><StatusBadge status={approval.status} /><span className="rounded border border-line-2 px-2 py-1 font-mono text-[11px] text-slate">{approval.kind}</span></div><span className="font-mono text-[11px] text-slate">v{approval.version}</span></div>
                  <h2 className="mt-3 font-serif text-lg font-semibold text-ink">{approval.resource_id}</h2>
                  <p className="mt-2 text-sm leading-relaxed text-ink">{approval.rationale}</p>
                  <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2"><div><dt className="font-mono tracking-widest text-slate">REQUESTER</dt><dd className="mt-1 text-ink">{approval.requester_name}<br /><span className="font-mono text-slate">{approval.requester}</span></dd></div><div><dt className="font-mono tracking-widest text-slate">EXPIRES</dt><dd className="mt-1 text-ink">{timestamp(approval.expires_at)}</dd></div>{approval.approver ? <div><dt className="font-mono tracking-widest text-slate">DECIDED BY</dt><dd className="mt-1 text-ink">{approval.approver}</dd></div> : null}</dl>
                  <ApprovalDecisionPanel approval={approval} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </Async>
    </main>
  );
}
