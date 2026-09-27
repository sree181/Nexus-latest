import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { ConflictRecovery, IntegrityRef, StateFrame, isVersionConflict } from "../components/WorkflowVisual";
import { Field, MutationMessage, StatusBadge, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api, type GovernanceEvidence, type PolicyException, type RenewExceptionInput, type RevokeExceptionInput } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";
import "./ciso-exception.css";

function EvidenceLink({ value }: { value: string }) {
  if (value.startsWith("review:")) return <Link to="/analyst/reviews/$requestId" params={{ requestId: value.slice(7) }} className="figma3-exception-evidence-link">{value}</Link>;
  if (value.startsWith("case:")) return <Link to="/analyst/cases/$caseId" params={{ caseId: value.slice(5) }} className="figma3-exception-evidence-link">{value}</Link>;
  return <span className="figma3-exception-evidence-link is-static">{value}</span>;
}

function remainingLabel(expiresAt: number, expired: boolean): string {
  if (expired) return "Expired";
  const seconds = Math.max(0, expiresAt - Math.floor(Date.now() / 1000));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days}d ${hours}h remaining` : `${hours}h remaining`;
}

function currentJourney(item: PolicyException): FigmaJourneyStage {
  if (item.status === "approved" && !item.expired) return "remediation";
  if (["rejected", "expired", "revoked", "superseded"].includes(item.status)) return "verified";
  return "ciso_decision";
}

function journeyOverrides(item: PolicyException) {
  const terminal = ["rejected", "expired", "revoked", "superseded"].includes(item.status);
  return {
    ciso_decision: item.decided_at ? "complete" : "current",
    remediation: item.status === "approved" && !item.expired ? "current" : terminal ? "complete" : "pending",
    verified: terminal ? "complete" : "pending",
  } as const;
}

function actionCopy(item: PolicyException, ownRequest: boolean, canRenew: boolean, canRevoke: boolean) {
  if (item.status === "pending") {
    return {
      responsibility: "Send this request through the decision desk.",
      why: "The request is not active until an independent CISO records a decision.",
      current: "Waiting for an independent decision",
      currentDetail: "The exception is not active until an authorized CISO records a decision.",
    };
  }
  if (ownRequest && (canRenew || canRevoke)) {
    return {
      responsibility: "A different authorized person must act.",
      why: "Requester separation applies to renewal and revocation as well as the initial decision.",
      current: "Requester separation applies",
      currentDetail: "Another authorized person must renew or revoke this exception.",
    };
  }
  if (canRenew || canRevoke) {
    return {
      responsibility: "Keep the exception bounded and current.",
      why: item.status === "approved" ? "Renewal creates a new request; revocation ends this exception and records the rationale." : "Lifecycle and evidence remain available.",
      current: "Keep this exception bounded",
      currentDetail: "Renewal creates a new request. Revocation ends this record.",
    };
  }
  return {
    responsibility: "This record is read-only.",
    why: "Terminal history and evidence remain available.",
    current: "This record is read-only",
    currentDetail: "Lifecycle and evidence remain available below.",
  };
}

function ExceptionHeading({ item, mobile = false }: { item: PolicyException; mobile?: boolean }) {
  return (
    <header className={mobile ? "figma3-exception-mobile-heading" : "figma3-exception-heading"}>
      <div className="figma3-exception-meta">
        <code>{item.id}</code>
        <StatusBadge status={item.status} />
        {item.renewal_number > 0 ? <span className="figma3-exception-renewal">Renewal {item.renewal_number}</span> : null}
      </div>
      {!mobile ? <h1>Policy exception</h1> : null}
      <p className="figma3-exception-scope" title={item.scope}>{item.scope}</p>
      {!mobile ? <Link to="/ciso/policies/$policyId" params={{ policyId: item.policy_id }} className="figma3-exception-policy-link">Policy {item.policy_id} · v{item.policy_version}</Link> : null}
    </header>
  );
}

function DecisionReceipt({ item }: { item: PolicyException }) {
  const active = item.status === "approved" && !item.expired;
  const terminal = ["rejected", "expired", "revoked", "superseded"].includes(item.status);
  return (
    <>
      {active ? (
        <section className="figma3-exception-status-banner" aria-label="Active approval">
          <span className="figma3-exception-status-icon" aria-hidden="true">✓</span>
          <div>
            <p>Time-bounded approval is active</p>
            <strong>{remainingLabel(item.expires_at, item.expired)}</strong>
            <span>Scope and controls remain fixed to this record.</span>
          </div>
        </section>
      ) : null}
      <section className={`figma3-exception-receipt ${active ? "is-active" : "is-terminal"}`}>
        <header>
          <span><StatusBadge status={item.status} /><strong>{active ? "Time-bounded approval is active" : `Exception is ${item.status.replaceAll("_", " ")}`}</strong></span>
          <small>{remainingLabel(item.expires_at, item.expired)}</small>
        </header>
        <div className="figma3-exception-receipt-grid">
          <div><small>Scope</small><strong title={item.scope}>{item.scope}</strong></div>
          <div><small>Policy</small><strong>{item.policy_id} · v{item.policy_version}</strong></div>
          <div><small>Requested</small><strong>{timestamp(item.created_at)}</strong></div>
          <div><small>Expires</small><strong>{timestamp(item.expires_at)}</strong></div>
        </div>
        {item.decision_rationale ? (
          <blockquote>
            <p className="figma3-kicker">Decision rationale</p>
            <span>{item.decision_rationale}</span>
            <small>{item.approved_by_name ?? "Recorded approver"}{item.decided_at ? ` · ${timestamp(item.decided_at)}` : ""}</small>
          </blockquote>
        ) : null}
        {item.revocation_rationale ? (
          <blockquote className="is-risk"><p className="figma3-kicker">Revocation rationale</p><span>{item.revocation_rationale}</span></blockquote>
        ) : null}
        {!active && !terminal && !item.decision_rationale ? <p className="figma3-exception-receipt-note">The recorded decision rationale will appear when an independent decision is returned.</p> : null}
      </section>
    </>
  );
}

function CurrentAction({ item, ownRequest, canRenew, canRevoke, onRenew, onRevoke }: {
  item: PolicyException;
  ownRequest: boolean;
  canRenew: boolean;
  canRevoke: boolean;
  onRenew: () => void;
  onRevoke: () => void;
}) {
  const copy = actionCopy(item, ownRequest, canRenew, canRevoke);
  return (
    <section className="figma3-exception-current-action">
      <div>
        <p className="figma3-kicker">Current action</p>
        <strong>{copy.current}</strong>
        <span>{copy.currentDetail}</span>
      </div>
      {!ownRequest && (canRenew || canRevoke) ? (
        <div className="figma3-exception-inline-actions">
          {canRenew ? <button type="button" className="figma3-primary" onClick={onRenew}>Request renewal</button> : null}
          {canRevoke ? <button type="button" className="figma3-danger" onClick={onRevoke}>Revoke</button> : null}
        </div>
      ) : null}
    </section>
  );
}

function EvidenceBody({ item, evidence }: { item: PolicyException; evidence: ReturnType<typeof useQuery<GovernanceEvidence>> }) {
  return (
    <div className="figma3-exception-evidence-body">
      <div className="figma3-exception-evidence-ids">
        {item.evidence_ids.length ? item.evidence_ids.map((value) => <EvidenceLink key={value} value={value} />) : <p>No linked review or case was submitted.</p>}
      </div>
      {evidence.isPending ? <StateFrame kind="loading" title="Projecting decision evidence" detail="The workflow record is already available." /> : null}
      {evidence.isError ? <StateFrame kind="error" title="Native evidence is not ready" detail="The workflow record remains available. Retry the evidence projection when the service is ready." action={<button type="button" className="figma3-secondary" onClick={() => void evidence.refetch()}>Try again</button>} /> : null}
      {evidence.data ? <FigmaEvidenceGraph graph={evidence.data.graph} label="Exception decision evidence relationship map" height={184} /> : null}
    </div>
  );
}

function ExceptionDisclosures({ item, evidence }: { item: PolicyException; evidence: ReturnType<typeof useQuery<GovernanceEvidence>> }) {
  return (
    <section className="figma3-exception-disclosures" aria-label="Exception record details">
      <details>
        <summary>Evidence <span>{item.evidence_ids.length}</span></summary>
        <EvidenceBody item={item} evidence={evidence} />
      </details>
      <details>
        <summary>Request and controls</summary>
        <dl className="figma3-exception-brief"><div><dt>Why it was requested</dt><dd>{item.rationale}</dd></div><div><dt>Submitted compensating controls</dt><dd>{item.compensating_controls}</dd></div></dl>
      </details>
      <details>
        <summary>People and decision</summary>
        <dl className="figma3-exception-brief"><div><dt>Owner</dt><dd>{item.owner_name || item.owner}</dd></div><div><dt>Requested by</dt><dd>{item.requested_by_name}</dd></div><div><dt>Decided by</dt><dd>{item.approved_by_name ?? "Waiting"}</dd></div></dl>
      </details>
      {item.predecessor_exception_id || item.superseded_by_exception_id ? <details><summary>Renewal chain</summary><div className="figma3-exception-lineage">{item.predecessor_exception_id ? <IntegrityRef label="Predecessor" value={item.predecessor_exception_id} /> : null}{item.superseded_by_exception_id ? <IntegrityRef label="Superseded by" value={item.superseded_by_exception_id} /> : null}</div></details> : null}
      <details>
        <summary>Lifecycle history <span>{item.events.length}</span></summary>
        <div>{item.events.length ? <ol className="figma3-exception-events">{[...item.events].reverse().map((event) => <li key={event.id}><span aria-hidden="true" /><div><strong>{event.action.replace(/^exception\./, "").replace(/_/g, " ")}</strong><small>{event.actor_name} · {event.rationale}</small></div><time>{timestamp(event.at)}</time></li>)}</ol> : <p>No lifecycle events were returned.</p>}</div>
      </details>
      <details>
        <summary>Evidence integrity and digests</summary>
        <div className="figma3-exception-integrity"><IntegrityRef label="Exception" value={item.id} /><IntegrityRef label="Policy digest" value={item.policy_digest} /><IntegrityRef label="Request digest" value={item.request_digest} /></div>
      </details>
    </section>
  );
}

type MutationControls<TInput> = {
  mutate: (input: TInput) => void;
  isPending: boolean;
};

function ExceptionActions({ item, ownRequest, canRenew, canRevoke, mode, setMode, renew, revoke, mutationError, reload, onRenew, onRevoke }: {
  item: PolicyException;
  ownRequest: boolean;
  canRenew: boolean;
  canRevoke: boolean;
  mode: "renew" | "revoke" | null;
  setMode: (mode: "renew" | "revoke" | null) => void;
  renew: MutationControls<RenewExceptionInput>;
  revoke: MutationControls<RevokeExceptionInput>;
  mutationError: unknown;
  reload: () => void;
  onRenew: () => void;
  onRevoke: () => void;
}) {
  const basicActions = !ownRequest && !mode && (canRenew || canRevoke) ? <div className="figma3-exception-dock-buttons">{canRenew ? <button type="button" className="figma3-primary" onClick={onRenew}>Request renewal</button> : null}{canRevoke ? <button type="button" className="figma3-danger" onClick={onRevoke}>Revoke</button> : null}</div> : null;
  return (
    <>
      <ConflictRecovery error={mutationError} onReload={reload} />
      {item.status === "pending" ? <Link to="/ciso/approvals" className="figma3-primary">Open decision desk</Link> : null}
      {ownRequest && (canRenew || canRevoke) ? <p className="figma3-exception-separation">You requested this exception. Another authorized person must renew or revoke it.</p> : null}
      {!ownRequest && mode === "renew" && canRenew ? (
        <form className="figma3-exception-action-form" onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          renew.mutate({
            expected_version: item.version,
            rationale: String(form.get("rationale") ?? "").trim(),
            compensating_controls: String(form.get("controls") ?? "").trim(),
            owner: String(form.get("owner") ?? "").trim(),
            owner_name: String(form.get("owner_name") ?? "").trim(),
            evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean),
            expires_at: dateInputToEpoch(form.get("expires_at")),
          });
        }}>
          <p className="figma3-kicker">New independent request</p>
          <p className="figma3-exception-form-note">A renewal is a new request and requires an independent decision. The predecessor relationship is recorded by the service.</p>
          <Field label="Owner"><TextInput name="owner" defaultValue={item.owner} required /></Field>
          <Field label="Owner name"><TextInput name="owner_name" defaultValue={item.owner_name} /></Field>
          <Field label="New expiry"><TextInput name="expires_at" type="date" min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} defaultValue={epochToDateInput(item.expires_at + 7 * 86400)} required /></Field>
          <Field label="Reason"><TextArea name="rationale" required /></Field>
          <Field label="Controls"><TextArea name="controls" defaultValue={item.compensating_controls} required /></Field>
          <Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" defaultValue={item.evidence_ids.join(", ")} /></Field>
          <div className="figma3-exception-form-buttons"><button type="submit" className="figma3-primary" disabled={renew.isPending}>{renew.isPending ? "Submitting…" : "Request renewal"}</button><button type="button" className="figma3-secondary" onClick={() => setMode(null)}>Cancel</button></div>
        </form>
      ) : null}
      {!ownRequest && mode === "revoke" && canRevoke ? (
        <form className="figma3-exception-action-form" onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          revoke.mutate({
            expected_version: item.version,
            rationale: String(form.get("rationale") ?? "").trim(),
            evidence_ids: String(form.get("evidence_ids") ?? "").split(",").map((value) => value.trim()).filter(Boolean),
          });
        }}>
          <p className="figma3-kicker">Record revocation</p>
          <p className="figma3-exception-form-note">Revocation ends this exception and records the supplied rationale and evidence IDs.</p>
          <Field label="Reason"><TextArea name="rationale" required /></Field>
          <Field label="Evidence IDs" hint="Comma separated"><TextInput name="evidence_ids" /></Field>
          <div className="figma3-exception-form-buttons"><button type="submit" className="figma3-danger" disabled={revoke.isPending}>{revoke.isPending ? "Revoking…" : "Confirm revocation"}</button><button type="button" className="figma3-secondary" onClick={() => setMode(null)}>Cancel</button></div>
        </form>
      ) : null}
      {basicActions}
      {!isVersionConflict(mutationError) ? <MutationMessage error={mutationError} success={null} /> : null}
      <div className="figma3-exception-dock-integrity"><IntegrityRef label="Exception" value={item.id} /><IntegrityRef label="Policy digest" value={item.policy_digest} /><IntegrityRef label="Request digest" value={item.request_digest} /></div>
    </>
  );
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
  const evidence = useQuery<GovernanceEvidence>({
    queryKey: ["governance-evidence", "exception", exceptionId],
    queryFn: () => api.exceptionEvidence(exceptionId),
    enabled: Boolean(item?.id),
    retry: false,
  });
  const ownRequest = item?.requested_by === me?.subject;
  const canRenew = Boolean(item?.status === "approved" && !item.expired && hasCapability("exception.renew"));
  const canRevoke = Boolean(item?.status === "approved" && !item.expired && hasCapability("exception.revoke"));
  const mutationError = renew.error ?? revoke.error;
  const reload = () => { renew.reset(); revoke.reset(); setMessage(null); setMode(null); void query.refetch(); };
  const openAction = (next: "renew" | "revoke") => {
    setMode(next);
    window.requestAnimationFrame(() => {
      const mobile = window.matchMedia("(max-width: 768px)").matches;
      document.getElementById(mobile ? "figma3-exception-mobile-actions" : "figma3-exception-desktop-actions")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  };

  if (query.isPending) {
    return <main className="figma3-page figma3-exception-page"><div className="figma3-exception-state"><StateFrame kind="loading" title="Loading exception" detail="Reading the decision record and native evidence." /></div></main>;
  }
  if (query.isError) {
    return <main className="figma3-page figma3-exception-page"><div className="figma3-exception-state"><StateFrame kind="error" title="This exception could not be opened" detail={query.error instanceof Error ? query.error.message : "The record is unavailable."} action={<button type="button" className="figma3-secondary" onClick={() => void query.refetch()}>Try again</button>} /></div></main>;
  }
  if (!item) {
    return <main className="figma3-page figma3-exception-page"><div className="figma3-exception-state"><StateFrame kind="empty" title="No exception record was returned" detail="The requested decision record is unavailable." /></div></main>;
  }

  const copy = actionCopy(item, ownRequest, canRenew, canRevoke);
  const actionProps = { item, ownRequest, canRenew, canRevoke, mode, setMode, renew, revoke, mutationError, reload, onRenew: () => openAction("renew"), onRevoke: () => openAction("revoke") };
  const approvedBy = item.approved_by_name ?? "No recorded decision yet";

  return (
    <main className="figma3-page figma3-exception-page">
      <div className="figma3-exception-desktop figma3-desktop-only">
        <section className="figma3-exception-main" aria-label="Exception decision record">
          <article className="figma3-exception-body">
            <Link to="/ciso/approvals" className="figma3-exception-back-link">← Decision desk</Link>
            <ExceptionHeading item={item} />
            <section className="figma3-exception-journey-card"><p className="figma3-kicker">Governance journey</p><FigmaJourney current={currentJourney(item)} overrides={journeyOverrides(item)} label="Exception governance journey" /></section>
            <DecisionReceipt item={item} />
            <CurrentAction item={item} ownRequest={ownRequest} canRenew={canRenew} canRevoke={canRevoke} onRenew={() => openAction("renew")} onRevoke={() => openAction("revoke")} />
            {message ? <div className="figma3-exception-receipt" role="status"><strong>Recorded</strong><span>{message}</span></div> : null}
            <ExceptionDisclosures item={item} evidence={evidence} />
          </article>
        </section>
        <FigmaResponsibilityDock
          className="figma3-exception-dock"
          responsibility={copy.responsibility}
          why={copy.why}
          deadline={timestamp(item.expires_at)}
          overdue={item.expired}
          blockingCondition={item.status === "approved" && !item.expired ? "The exception remains bounded to the recorded scope, policy version, and controls." : undefined}
        >
          <div id="figma3-exception-desktop-actions"><ExceptionActions {...actionProps} /></div>
          <div className="figma3-exception-approved-by"><p className="figma3-kicker">Decision record</p><strong>{approvedBy}</strong>{item.decided_at ? <small>{timestamp(item.decided_at)}</small> : null}</div>
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-exception-mobile figma3-mobile-only">
        <header className="figma3-exception-mobile-bar">
          <Link to="/ciso/approvals" aria-label="Back to decision desk">←</Link>
          <div><strong>{item.id}</strong><small>{item.status.replaceAll("_", " ")} · {remainingLabel(item.expires_at, item.expired)}</small></div>
        </header>
        <section className="figma3-exception-mobile-scroll" aria-label="Exception decision record">
          <ExceptionHeading item={item} mobile />
          <DecisionReceipt item={item} />
          <section className="figma3-exception-mobile-journey"><p className="figma3-kicker">Governance journey</p><FigmaJourney current={currentJourney(item)} overrides={journeyOverrides(item)} vertical label="Exception governance journey" /></section>
          <CurrentAction item={item} ownRequest={ownRequest} canRenew={canRenew} canRevoke={canRevoke} onRenew={() => openAction("renew")} onRevoke={() => openAction("revoke")} />
          {message ? <div className="figma3-exception-receipt" role="status"><strong>Recorded</strong><span>{message}</span></div> : null}
          <section id="figma3-exception-mobile-actions" className="figma3-exception-mobile-actions"><ExceptionActions {...actionProps} /></section>
          <ExceptionDisclosures item={item} evidence={evidence} />
        </section>
        {!mode && !ownRequest && (canRenew || canRevoke) ? <FigmaMobileDock deadline={remainingLabel(item.expires_at, item.expired)}><>{canRenew ? <button type="button" className="figma3-primary" onClick={() => openAction("renew")}>Request renewal</button> : null}{canRevoke ? <button type="button" className="figma3-danger" onClick={() => openAction("revoke")}>Revoke</button> : null}</></FigmaMobileDock> : null}
        {!mode && item.status === "pending" ? <FigmaMobileDock deadline={timestamp(item.expires_at)}><Link to="/ciso/approvals" className="figma3-primary">Open decision desk</Link></FigmaMobileDock> : null}
      </div>
    </main>
  );
}
