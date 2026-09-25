import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { GovernanceEvidencePanel } from "../components/GovernanceEvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, StatusBadge, TextArea, TextInput, epochToDateInput, dateInputToEpoch } from "../components/WorkflowUI";
import { api, type RenewExceptionInput, type RevokeExceptionInput } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

function EvidenceLink({ value }: { value: string }) {
  if (value.startsWith("review:")) return <Link to="/analyst/reviews/$requestId" params={{ requestId: value.slice(7) }} className="rounded-md border border-line-2 bg-surface-2 px-2 py-1 font-mono text-[10px] text-accent hover:underline">{value}</Link>;
  if (value.startsWith("case:")) return <Link to="/analyst/cases/$caseId" params={{ caseId: value.slice(5) }} className="rounded-md border border-line-2 bg-surface-2 px-2 py-1 font-mono text-[10px] text-accent hover:underline">{value}</Link>;
  return <span className="rounded-md border border-line-2 bg-surface-2 px-2 py-1 font-mono text-[10px] text-slate">{value}</span>;
}

export function CisoExceptionDetail() {
  const { exceptionId } = useParams({ strict: false }) as { exceptionId: string };
  const { me } = useIdentity();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["exception", exceptionId], queryFn: () => api.exception(exceptionId) });
  const [mode, setMode] = useState<"renew" | "revoke" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const renew = useMutation({
    mutationFn: (input: RenewExceptionInput) => api.renewException(exceptionId, input),
    onSuccess: (approval) => {
      setMessage(`Renewal request ${approval.id} sent for independent decision.`);
      setMode(null);
      void client.invalidateQueries({ queryKey: ["exception", exceptionId] });
      void client.invalidateQueries({ queryKey: ["exceptions"] });
      void client.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (input: RevokeExceptionInput) => api.revokeException(exceptionId, input),
    onSuccess: (updated) => {
      setMessage("Exception revoked.");
      setMode(null);
      client.setQueryData(["exception", exceptionId], updated);
      void client.invalidateQueries({ queryKey: ["exceptions"] });
      void client.invalidateQueries({ queryKey: ["governance-evidence", "exception", exceptionId] });
    },
  });
  const item = query.data;
  const ownRequest = item?.requested_by === me?.subject;
  const canRenew = item?.status === "approved" && !item.expired;
  const canRevoke = item?.status === "approved" && !item.expired;

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO / Exceptions" title={item?.scope ?? "Exception"} meta={<Link to="/ciso/policies" className="text-accent hover:underline">Back to policy control</Link>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        {query.isPending ? <Loading label="Loading exception…" /> : query.isError || !item ? <ErrorState error={query.error ?? new Error("Exception unavailable")} retry={() => void query.refetch()} /> : <>
          <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
            <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><StatusBadge status={item.status} />{item.renewal_number > 0 ? <Badge tone="neutral">Renewal {item.renewal_number}</Badge> : null}</div><p className="mt-3 font-mono text-xs text-slate">{item.id}</p></div><div className="text-right"><p className="font-mono text-[10px] tracking-widest text-slate">EXPIRES</p><p className="mt-1 text-sm font-medium text-ink">{timestamp(item.expires_at)}</p></div></div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><div><p className="font-mono text-[10px] tracking-widest text-slate">OWNER</p><p className="mt-1 text-sm text-ink">{item.owner_name || item.owner}</p></div><div><p className="font-mono text-[10px] tracking-widest text-slate">REQUESTED BY</p><p className="mt-1 text-sm text-ink">{item.requested_by_name}</p></div><div><p className="font-mono text-[10px] tracking-widest text-slate">POLICY VERSION</p><Link to="/ciso/policies/$policyId" params={{ policyId: item.policy_id }} className="mt-1 inline-block text-sm text-accent hover:underline">Version {item.policy_version}</Link></div><div><p className="font-mono text-[10px] tracking-widest text-slate">DECIDED BY</p><p className="mt-1 text-sm text-ink">{item.approved_by_name ?? "Waiting"}</p></div></div>
          </section>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,.8fr)]">
            <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Decision record</h2><dl className="mt-4 space-y-4"><div><dt className="font-mono text-[10px] tracking-widest text-slate">WHY IT WAS REQUESTED</dt><dd className="mt-1 text-sm leading-relaxed text-ink">{item.rationale}</dd></div><div><dt className="font-mono text-[10px] tracking-widest text-slate">COMPENSATING CONTROLS</dt><dd className="mt-1 rounded-xl bg-surface-2 p-3 text-sm leading-relaxed text-ink">{item.compensating_controls}</dd></div>{item.decision_rationale ? <div><dt className="font-mono text-[10px] tracking-widest text-slate">DECISION RATIONALE</dt><dd className="mt-1 text-sm leading-relaxed text-ink">{item.decision_rationale}</dd></div> : null}<div><dt className="font-mono text-[10px] tracking-widest text-slate">SUBMITTED EVIDENCE</dt><dd className="mt-2 flex flex-wrap gap-2">{item.evidence_ids.length ? item.evidence_ids.map((value) => <EvidenceLink key={value} value={value} />) : <span className="text-sm text-slate">No linked review or case was submitted.</span>}</dd></div><div><dt className="font-mono text-[10px] tracking-widest text-slate">BOUND DIGESTS</dt><dd className="mt-2 space-y-2"><p className="break-all font-mono text-[10px] text-slate">Policy {item.policy_digest}</p><p className="break-all font-mono text-[10px] text-slate">Request {item.request_digest}</p></dd></div></dl></section>

            <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Next action</h2>{item.status === "pending" ? <div className="mt-3"><p className="text-sm text-slate">This request is waiting for an independent decision.</p><Link to="/ciso/approvals" className="mt-3 inline-flex rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white">Open decision desk</Link></div> : ownRequest && (canRenew || canRevoke) ? <p className="mt-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-sm text-ink">You requested this exception. Another authorized person must renew or revoke it.</p> : !canRenew && !canRevoke ? <p className="mt-3 text-sm text-slate">This exception is in a terminal state. Its history remains available below.</p> : mode === "renew" ? <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); setMessage(null); const form = new FormData(event.currentTarget); renew.mutate({ expected_version: item.version, rationale: String(form.get("rationale") ?? "").trim(), compensating_controls: String(form.get("controls") ?? "").trim(), owner: String(form.get("owner") ?? "").trim(), owner_name: String(form.get("owner_name") ?? "").trim(), evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean), expires_at: dateInputToEpoch(form.get("expires_at")) }); }}><Field label="Owner"><TextInput name="owner" defaultValue={item.owner} required /></Field><Field label="Owner name"><TextInput name="owner_name" defaultValue={item.owner_name} /></Field><Field label="New expiry"><TextInput name="expires_at" type="date" min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} defaultValue={epochToDateInput(item.expires_at + 7 * 86400)} required /></Field><Field label="Reason"><TextArea name="rationale" required /></Field><Field label="Controls"><TextArea name="controls" defaultValue={item.compensating_controls} required /></Field><Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" defaultValue={item.evidence_ids.join(", ")} /></Field><div className="flex gap-2"><Button type="submit" disabled={renew.isPending}>Request renewal</Button><Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button></div></form> : mode === "revoke" ? <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); setMessage(null); const form = new FormData(event.currentTarget); revoke.mutate({ expected_version: item.version, rationale: String(form.get("rationale") ?? "").trim(), evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean) }); }}><Field label="Reason"><TextArea name="rationale" required /></Field><Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" /></Field><div className="flex gap-2"><Button type="submit" variant="danger" disabled={revoke.isPending}>Confirm revocation</Button><Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button></div></form> : <div className="mt-4 flex flex-wrap gap-2"><Button type="button" disabled={ownRequest} onClick={() => setMode("renew")}>Request renewal</Button><Button type="button" variant="danger" disabled={ownRequest} onClick={() => setMode("revoke")}>Revoke</Button></div>}<MutationMessage error={renew.error ?? revoke.error} success={message} /></section>
          </div>

          <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Lifecycle</h2><ol className="mt-4 grid gap-3 lg:grid-cols-2">{[...item.events].reverse().map((event) => <li key={event.id} className="rounded-xl border border-line-2 bg-white p-4"><div className="flex items-center justify-between gap-2"><StatusBadge status={event.to_state} /><span className="font-mono text-[10px] text-slate">{timestamp(event.at)}</span></div><p className="mt-2 text-sm font-medium text-ink">{event.action.replace(/^exception\./, "").replace(/_/g, " ")}</p><p className="mt-1 text-xs leading-relaxed text-slate">{event.actor_name} · {event.rationale}</p></li>)}</ol></section>
          <GovernanceEvidencePanel kind="exception" id={item.id} />
        </>}
      </div>
    </main>
  );
}
