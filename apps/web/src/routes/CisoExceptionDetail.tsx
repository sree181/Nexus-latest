import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { GovernanceEvidencePanel } from "../components/GovernanceEvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { ConflictRecovery, IntegrityRef, ResponsibilityDock, StateFrame, WorkflowJourney, isVersionConflict, type JourneyItem } from "../components/WorkflowVisual";
import { Field, MutationMessage, StatusBadge, TextArea, TextInput, epochToDateInput, dateInputToEpoch } from "../components/WorkflowUI";
import { api, type PolicyException, type RenewExceptionInput, type RevokeExceptionInput } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

function EvidenceLink({ value }: { value: string }) {
  if (value.startsWith("review:")) return <Link to="/analyst/reviews/$requestId" params={{ requestId: value.slice(7) }} className="evidence-link">{value}</Link>;
  if (value.startsWith("case:")) return <Link to="/analyst/cases/$caseId" params={{ caseId: value.slice(5) }} className="evidence-link">{value}</Link>;
  return <span className="evidence-link is-static">{value}</span>;
}

function exceptionJourney(item: PolicyException): JourneyItem[] {
  const decided = Boolean(item.decided_at);
  const terminal = ["rejected", "expired", "revoked", "superseded"].includes(item.status);
  return [
    { id: "requested", label: item.renewal_number ? `Renewal ${item.renewal_number}` : "Requested", detail: timestamp(item.created_at), state: "complete" },
    { id: "decided", label: "Independent decision", detail: item.approved_by_name ?? item.status, state: decided ? "complete" : "current" },
    { id: "active", label: "Bounded period", detail: item.status === "approved" ? `Until ${timestamp(item.expires_at)}` : item.status, state: item.status === "approved" ? "current" : decided ? "complete" : "pending" },
    { id: "end", label: terminal ? item.status : "Expiry or renewal", detail: terminal ? timestamp(item.updated_at) : "Future action", state: terminal ? "complete" : "pending" },
  ];
}

function remainingLabel(expiresAt: number, expired: boolean): string {
  if (expired) return "Expired";
  const seconds = Math.max(0, expiresAt - Math.floor(Date.now() / 1000));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days}d ${hours}h remaining` : `${hours}h remaining`;
}

export function CisoExceptionDetail() {
  const { exceptionId } = useParams({ strict: false }) as { exceptionId: string };
  const { me, hasCapability } = useIdentity();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["exception", exceptionId], queryFn: () => api.exception(exceptionId) });
  const [mode, setMode] = useState<"renew" | "revoke" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const renew = useMutation({
    mutationFn: (input: RenewExceptionInput) => api.renewException(exceptionId, input),
    onSuccess: (approval) => { setMessage(`Renewal request ${approval.id} is waiting for an independent decision.`); setMode(null); void client.invalidateQueries({ queryKey: ["exception", exceptionId] }); void client.invalidateQueries({ queryKey: ["exceptions"] }); void client.invalidateQueries({ queryKey: ["approvals"] }); void client.invalidateQueries({ queryKey: ["notifications"] }); },
  });
  const revoke = useMutation({
    mutationFn: (input: RevokeExceptionInput) => api.revokeException(exceptionId, input),
    onSuccess: (updated) => { setMessage("Exception revoked."); setMode(null); client.setQueryData(["exception", exceptionId], updated); void client.invalidateQueries({ queryKey: ["exceptions"] }); void client.invalidateQueries({ queryKey: ["governance-evidence", "exception", exceptionId] }); void client.invalidateQueries({ queryKey: ["notifications"] }); },
  });
  const item = query.data;
  const ownRequest = item?.requested_by === me?.subject;
  const canRenew = Boolean(item?.status === "approved" && !item.expired && hasCapability("exception.renew"));
  const canRevoke = Boolean(item?.status === "approved" && !item.expired && hasCapability("exception.revoke"));
  const mutationError = renew.error ?? revoke.error;
  const reload = () => { renew.reset(); revoke.reset(); setMessage(null); setMode(null); void query.refetch(); };

  return (
    <main className="workflow-page ciso-exception-page">
      <PageHeader section="CISO / Exceptions" title={item?.scope ?? "Exception"} meta={<Link to="/ciso/approvals" className="text-accent hover:underline">Back to decision desk</Link>} />
      {query.isPending ? <div className="workflow-scroll"><StateFrame kind="loading" title="Loading exception" detail="Reading the decision record and native evidence." /></div> : query.isError || !item ? <div className="workflow-scroll"><StateFrame kind="error" title="This exception could not be opened" detail={query.error instanceof Error ? query.error.message : "The record is unavailable."} action={<button type="button" className="workflow-secondary-action" onClick={() => void query.refetch()}>Try again</button>} /></div> : (
        <div className="exception-detail-shell">
          <section className="exception-detail-scroll">
            <article className="exception-narrative">
              <Link to="/ciso/approvals" className="focus-back-link">← Decision desk</Link>
              <header className="exception-focus-heading">
                <div className="decision-focus-meta"><span>{item.id}</span><StatusBadge status={item.status} />{item.renewal_number > 0 ? <Badge tone="neutral">Renewal {item.renewal_number}</Badge> : null}</div>
                <h1>Policy exception</h1>
                <p>{item.scope}</p>
                <Link to="/ciso/policies/$policyId" params={{ policyId: item.policy_id }}>Policy {item.policy_id} · v{item.policy_version}</Link>
              </header>

              <section className="focus-journey-card"><p className="workflow-eyebrow">Exception lifecycle</p><WorkflowJourney items={exceptionJourney(item)} label="Exception lifecycle" /></section>

              <section className={`exception-receipt ${item.status === "approved" && !item.expired ? "is-active" : "is-terminal"}`}>
                <header><span><StatusBadge status={item.status} /><strong>{item.status === "approved" && !item.expired ? "Time-bounded approval is active" : `Exception is ${item.status.replaceAll("_", " ")}`}</strong></span><small>{remainingLabel(item.expires_at, item.expired)}</small></header>
                <div className="exception-receipt-grid"><div><small>Scope</small><strong>{item.scope}</strong></div><div><small>Policy</small><strong>{item.policy_id} · v{item.policy_version}</strong></div><div><small>Requested</small><strong>{timestamp(item.created_at)}</strong></div><div><small>Expires</small><strong>{timestamp(item.expires_at)}</strong></div></div>
                {item.decision_rationale ? <blockquote><p className="workflow-eyebrow">Decision rationale</p><span>{item.decision_rationale}</span><small>{item.approved_by_name ?? "Recorded approver"}{item.decided_at ? ` · ${timestamp(item.decided_at)}` : ""}</small></blockquote> : null}
                {item.revocation_rationale ? <blockquote className="is-risk"><p className="workflow-eyebrow">Revocation rationale</p><span>{item.revocation_rationale}</span></blockquote> : null}
              </section>

              {message ? <div className="workflow-receipt is-success" role="status"><strong>Recorded</strong><span>{message}</span></div> : null}

              <div className="focus-disclosures exception-disclosures">
                <details><summary>Evidence <span>{item.evidence_ids.length}</span></summary><div><div className="decision-evidence-ids">{item.evidence_ids.length ? item.evidence_ids.map((value) => <EvidenceLink key={value} value={value} />) : <p>No linked review or case was submitted.</p>}</div><GovernanceEvidencePanel kind="exception" id={item.id} /></div></details>
                <details><summary>Request and controls</summary><div><dl className="decision-brief"><div><dt>Why it was requested</dt><dd>{item.rationale}</dd></div><div><dt>Submitted compensating controls</dt><dd>{item.compensating_controls}</dd></div></dl></div></details>
                <details><summary>People and decision</summary><div><dl className="decision-brief"><div><dt>Owner</dt><dd>{item.owner_name || item.owner}</dd></div><div><dt>Requested by</dt><dd>{item.requested_by_name}</dd></div><div><dt>Decided by</dt><dd>{item.approved_by_name ?? "Waiting"}</dd></div></dl></div></details>
                {item.predecessor_exception_id || item.superseded_by_exception_id ? <details><summary>Renewal chain</summary><div className="exception-lineage">{item.predecessor_exception_id ? <IntegrityRef label="Predecessor" value={item.predecessor_exception_id} /> : null}{item.superseded_by_exception_id ? <IntegrityRef label="Superseded by" value={item.superseded_by_exception_id} /> : null}</div></details> : null}
                <details><summary>Lifecycle history <span>{item.events.length}</span></summary><div>{item.events.length ? <ol className="workflow-list">{[...item.events].reverse().map((event) => <li key={event.id} className="workflow-list-row"><span className="workflow-update-dot" aria-hidden="true" /><span className="workflow-list-row-main"><strong>{event.action.replace(/^exception\./, "").replace(/_/g, " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span><span className="font-mono text-[10px] text-slate">{timestamp(event.at)}</span></li>)}</ol> : <p>No lifecycle events were returned.</p>}</div></details>
                <details><summary>Evidence integrity and digests</summary><div className="grid gap-2"><IntegrityRef label="Exception" value={item.id} /><IntegrityRef label="Policy digest" value={item.policy_digest} /><IntegrityRef label="Request digest" value={item.request_digest} /></div></details>
              </div>
            </article>
          </section>

          <div id="exception-actions" className="exception-detail-dock"><ResponsibilityDock responsibility={item.status === "pending" ? "Send this request through the decision desk." : ownRequest && (canRenew || canRevoke) ? "A different authorized person must act." : canRenew || canRevoke ? "Keep the exception bounded and current." : "This record is read-only."} why={item.status === "pending" ? "The request is not active until an independent CISO records a decision." : ownRequest ? "Requester separation applies to renewal and revocation as well as the initial decision." : item.status === "approved" ? "Renewal creates a new request; revocation ends this exception and records the rationale." : "Terminal history and evidence remain available."} deadline={timestamp(item.expires_at)} overdue={item.expired}>
            <ConflictRecovery error={mutationError} onReload={reload} />
            {item.status === "pending" ? <Link to="/ciso/approvals" className="workflow-primary-action">Open decision desk</Link> : null}
            {ownRequest && (canRenew || canRevoke) ? <p className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-xs leading-relaxed text-ink">You requested this exception. Another authorized person must renew or revoke it.</p> : null}
            {!ownRequest && mode === "renew" && canRenew ? <form onSubmit={(event) => { event.preventDefault(); setMessage(null); const form = new FormData(event.currentTarget); renew.mutate({ expected_version: item.version, rationale: String(form.get("rationale") ?? "").trim(), compensating_controls: String(form.get("controls") ?? "").trim(), owner: String(form.get("owner") ?? "").trim(), owner_name: String(form.get("owner_name") ?? "").trim(), evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean), expires_at: dateInputToEpoch(form.get("expires_at")) }); }}><Field label="Owner"><TextInput name="owner" defaultValue={item.owner} required /></Field><Field label="Owner name"><TextInput name="owner_name" defaultValue={item.owner_name} /></Field><Field label="New expiry"><TextInput name="expires_at" type="date" min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} defaultValue={epochToDateInput(item.expires_at + 7 * 86400)} required /></Field><Field label="Reason"><TextArea name="rationale" required /></Field><Field label="Controls"><TextArea name="controls" defaultValue={item.compensating_controls} required /></Field><Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" defaultValue={item.evidence_ids.join(", ")} /></Field><div className="grid gap-2"><Button type="submit" disabled={renew.isPending}>{renew.isPending ? "Submitting…" : "Request renewal"}</Button><Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button></div></form> : null}
            {!ownRequest && mode === "revoke" && canRevoke ? <form onSubmit={(event) => { event.preventDefault(); setMessage(null); const form = new FormData(event.currentTarget); revoke.mutate({ expected_version: item.version, rationale: String(form.get("rationale") ?? "").trim(), evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean) }); }}><Field label="Reason"><TextArea name="rationale" required /></Field><Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" /></Field><div className="grid gap-2"><Button type="submit" variant="danger" disabled={revoke.isPending}>{revoke.isPending ? "Revoking…" : "Confirm revocation"}</Button><Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button></div></form> : null}
            {!ownRequest && !mode && (canRenew || canRevoke) ? <div className="grid gap-2">{canRenew ? <Button type="button" onClick={() => setMode("renew")}>Request renewal</Button> : null}{canRevoke ? <Button type="button" variant="danger" onClick={() => setMode("revoke")}>Revoke</Button> : null}</div> : null}
            {!isVersionConflict(mutationError) ? <MutationMessage error={mutationError} success={null} /> : null}
            <div className="grid gap-2"><IntegrityRef label="Exception" value={item.id} /><IntegrityRef label="Policy digest" value={item.policy_digest} /><IntegrityRef label="Request digest" value={item.request_digest} /></div>
          </ResponsibilityDock></div>
          {(canRenew || canRevoke) && !ownRequest ? <a href="#exception-actions" className="ciso-mobile-action">Exception actions · {remainingLabel(item.expires_at, item.expired)}</a> : null}
        </div>
      )}
    </main>
  );
}
