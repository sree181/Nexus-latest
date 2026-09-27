import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { GraphPayload } from "@meshagent/graph";

import { SidePanel } from "../components/DeveloperVisual";
import { FigmaEvidenceGraph, FigmaJourney, FigmaMobileDock, FigmaResponsibilityDock, type FigmaJourneyStage } from "../components/FigmaWorkflowV3";
import { WorkCollaboration } from "../components/WorkCollaboration";
import { ConflictRecovery, IntegrityRef, StateFrame, isVersionConflict } from "../components/WorkflowVisual";
import { Field, MutationMessage, Select, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { ApiError, api, type AssignCaseInput, type CaseRecord, type CaseState, type CaseTransition, type CreateExceptionInput, type TransitionCaseInput } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";
import "./analyst-case.css";

const transitions: Record<CaseRecord["state"], CaseTransition[]> = {
  open: ["triaged", "investigating", "closed"],
  triaged: ["investigating", "remediation", "closed"],
  investigating: ["remediation", "resolved", "closed"],
  remediation: ["resolved", "investigating", "closed"],
  resolved: ["reopened", "closed"],
  closed: ["reopened"],
};

export function reviewRequestIdFromCaseFinding(findingId: string): string | null {
  if (!findingId.startsWith("review:")) return null;
  return findingId.slice("review:".length).trim() || null;
}

function caseJourneyState(state: CaseState): FigmaJourneyStage {
  switch (state) {
    case "open": return "detected";
    case "triaged": return "security_review";
    case "investigating": return "investigation";
    case "remediation": return "remediation";
    case "resolved":
    case "closed": return "verified";
  }
}

function journeyOverrides(item: CaseRecord): Partial<Record<FigmaJourneyStage, "complete" | "current" | "pending" | "blocked">> {
  // The case record proves a finding was detected and exposes its own lifecycle only. Do not
  // manufacture developer-action, review, or CISO-decision facts that the route does not receive.
  const unknown: Partial<Record<FigmaJourneyStage, "pending">> = {
    developer_action: "pending",
    security_review: "pending",
    ciso_decision: "pending",
    remediation: "pending",
    verified: "pending",
  };
  const current = caseJourneyState(item.state);
  delete unknown[current];
  return {
    ...unknown,
    detected: item.state === "open" ? "current" : "complete",
    ...(item.state === "closed" ? { verified: "complete" } : {}),
  };
}

function severityClass(severity: CaseRecord["severity"]): string {
  return `figma3-case-severity is-${severity}`;
}

function AssignmentForm({ item }: { item: CaseRecord }) {
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const assign = useMutation({
    mutationFn: (input: AssignCaseInput) => api.assignCase(item.id, input),
    onSuccess: (updated) => {
      setSuccess("Assignment updated.");
      client.setQueryData(["case", item.id], updated);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void client.invalidateQueries({ queryKey: ["work-queue"] });
    },
  });

  return (
    <details className="figma3-case-action-disclosure" open={!item.assignee}>
      <summary>Owner and due time</summary>
      <form onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const due = String(form.get("sla_due_at") ?? "");
        assign.mutate({
          expected_version: item.version,
          assignee: String(form.get("assignee") ?? "").trim(),
          assignee_name: String(form.get("assignee_name") ?? "").trim(),
          sla_due_at: due ? dateInputToEpoch(due) : null,
        });
      }}>
        <ConflictRecovery error={assign.error} onReload={() => { assign.reset(); setSuccess(null); void client.invalidateQueries({ queryKey: ["case", item.id] }); }} />
        <Field label="Assignee identifier"><TextInput name="assignee" required defaultValue={item.assignee ?? ""} placeholder="analyst@example.com" /></Field>
        <Field label="Assignee name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
        <Field label="SLA due date" hint="Optional; the server computes overdue state."><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
        <button type="submit" className="figma3-primary" disabled={assign.isPending}>{assign.isPending ? "Saving…" : "Save assignment"}</button>
        {!isVersionConflict(assign.error) ? <MutationMessage error={assign.error} success={success} /> : null}
      </form>
    </details>
  );
}

function TransitionForm({ item }: { item: CaseRecord }) {
  const client = useQueryClient();
  const [target, setTarget] = useState<CaseTransition>(transitions[item.state][0]);
  const [success, setSuccess] = useState<string | null>(null);
  const isResolution = target === "resolved" || target === "closed";
  const transition = useMutation({
    mutationFn: (input: TransitionCaseInput) => api.transitionCase(item.id, input),
    onSuccess: (updated) => {
      setSuccess(`Case moved to ${updated.state}.`);
      client.setQueryData(["case", item.id], updated);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void client.invalidateQueries({ queryKey: ["work-queue"] });
      void client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  return (
    <details className="figma3-case-action-disclosure" open={Boolean(item.assignee)}>
      <summary>Move the case</summary>
      <form onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const evidence = String(form.get("evidence_ids") ?? "").split(/[\n,]/).map((value) => value.trim()).filter(Boolean);
        transition.mutate({
          expected_version: item.version,
          to_state: target,
          disposition: String(form.get("disposition") ?? "").trim() || null,
          rationale: String(form.get("rationale") ?? "").trim(),
          evidence_ids: evidence,
        });
      }}>
        <ConflictRecovery error={transition.error} onReload={() => { transition.reset(); setSuccess(null); void client.invalidateQueries({ queryKey: ["case", item.id] }); }} />
        <Field label="Next state"><Select name="to_state" value={target} onChange={(event) => setTarget(event.target.value as CaseTransition)}>{transitions[item.state].map((state) => <option key={state} value={state}>{state}</option>)}</Select></Field>
        <Field label="Rationale"><TextArea name="rationale" required maxLength={4096} /></Field>
        <Field label={`Disposition${isResolution ? " (required)" : ""}`}><TextInput name="disposition" required={isResolution} maxLength={256} placeholder="verified remediated, accepted risk, false positive…" /></Field>
        <Field label={`Evidence IDs${isResolution ? " (required)" : ""}`} hint="Resolution and closure require verification evidence."><TextArea name="evidence_ids" required={isResolution} placeholder="scan:codeql:replacement-build" /></Field>
        <button type="submit" className="figma3-primary" disabled={transition.isPending}>{transition.isPending ? "Recording…" : `Move to ${target}`}</button>
        {!isVersionConflict(transition.error) ? <MutationMessage error={transition.error} success={success} /> : null}
      </form>
    </details>
  );
}

function ExceptionRequestForm({ item }: { item: CaseRecord }) {
  const client = useQueryClient();
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [success, setSuccess] = useState<string | null>(null);
  const request = useMutation({
    mutationFn: (input: CreateExceptionInput) => api.requestException(input),
    onSuccess: (approval) => {
      setSuccess(`Request ${approval.id} is waiting for an independent CISO decision.`);
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["exceptions"] });
      void client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  const defaultExpiry = epochToDateInput(Math.floor(Date.now() / 1000) + 7 * 86400);

  return (
    <details className="figma3-case-action-disclosure">
      <summary>Request a policy exception</summary>
      {policies.isPending ? <p className="figma3-case-form-note">Loading active policies…</p> : policies.isError ? <p role="alert" className="figma3-case-form-note is-error">Policies could not be loaded.</p> : policies.data.length === 0 ? <p className="figma3-case-form-note">No active policy is available.</p> : (
        <form onSubmit={(event) => {
          event.preventDefault();
          setSuccess(null);
          const form = new FormData(event.currentTarget);
          request.mutate({
            policy_id: String(form.get("policy_id") ?? ""),
            scope: String(form.get("scope") ?? "").trim(),
            rationale: String(form.get("rationale") ?? "").trim(),
            compensating_controls: String(form.get("controls") ?? "").trim(),
            owner: String(form.get("owner") ?? "").trim(),
            evidence_ids: [`case:${item.id}`],
            expires_at: dateInputToEpoch(form.get("expires_at")),
          });
        }}>
          <p className="figma3-case-form-note">This creates a time-bounded request. A CISO—not the requester—must decide it.</p>
          <Field label="Policy"><Select name="policy_id" required>{policies.data.map((policy) => <option key={policy.id} value={policy.id}>{policy.name}</option>)}</Select></Field>
          <Field label="Scope"><TextInput name="scope" required defaultValue={`case:${item.id}`} /></Field>
          <Field label="Owner"><TextInput name="owner" required defaultValue={item.assignee ?? ""} placeholder="service or team owner" /></Field>
          <Field label="Expires"><TextInput name="expires_at" type="date" required defaultValue={defaultExpiry} min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} /></Field>
          <Field label="Risk rationale"><TextArea name="rationale" required maxLength={4096} /></Field>
          <Field label="Compensating controls"><TextArea name="controls" required maxLength={4096} /></Field>
          <button type="submit" className="figma3-primary" disabled={request.isPending}>{request.isPending ? "Submitting…" : "Request exception"}</button>
          <MutationMessage error={request.error} success={success} />
        </form>
      )}
    </details>
  );
}

function CaseHistory({ item }: { item: CaseRecord }) {
  return item.events.length ? (
    <ol className="figma3-case-history">
      {[...item.events].reverse().map((event) => (
        <li key={event.id}>
          <span aria-hidden="true" />
          <div>
            <strong>{event.action.replace(/\./g, " ")}</strong>
            <small>{event.actor_name} · {event.rationale}</small>
            {event.evidence_ids.length ? <div>{event.evidence_ids.map((id) => <code key={id}>{id}</code>)}</div> : null}
          </div>
          <time>{timestamp(event.at)}</time>
        </li>
      ))}
    </ol>
  ) : <p className="figma3-case-empty-copy">No case events were returned.</p>;
}

function EvidenceDrawer({
  evidenceGraph,
  evidencePending,
  evidenceError,
  onRetry,
  onClose,
}: {
  evidenceGraph: GraphPayload | undefined;
  evidencePending: boolean;
  evidenceError: unknown;
  onRetry: () => void;
  onClose: () => void;
}) {
  return (
    <SidePanel title="Case evidence" icon="evidence" onClose={onClose}>
      <div className="figma3-case-evidence-drawer">
        {evidencePending ? <StateFrame kind="loading" title="Projecting relationships" /> : evidenceError ? (
          <StateFrame
            kind={evidenceError instanceof ApiError && evidenceError.status === 404 ? "empty" : "error"}
            title={evidenceError instanceof ApiError && evidenceError.status === 404 ? "Evidence projection is pending" : "Evidence could not be loaded"}
            detail={evidenceError instanceof Error ? evidenceError.message : undefined}
            action={<button type="button" className="figma3-secondary" onClick={onRetry}>Retry map</button>}
          />
        ) : evidenceGraph ? <FigmaEvidenceGraph graph={evidenceGraph} label="Case source evidence relationship map" height={210} /> : <StateFrame kind="empty" title="No source evidence is available" />}
      </div>
    </SidePanel>
  );
}

export function AnalystCaseDetail() {
  const { caseId } = useParams({ from: "/analyst/cases/$caseId" });
  const { hasCapability } = useIdentity();
  const canWriteCase = hasCapability("case.write");
  const canRequestException = hasCapability("exception.request");
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const detail = useQuery({ queryKey: ["case", caseId], queryFn: () => api.case(caseId) });
  const reviewId = reviewRequestIdFromCaseFinding(detail.data?.finding_id ?? "");
  const reviewEvidence = useQuery({ queryKey: ["review", reviewId, "graph"], queryFn: () => api.reviewGraph(reviewId!), enabled: Boolean(reviewId), retry: false });
  const runEvidence = useQuery({ queryKey: ["run", detail.data?.run_id, "graph"], queryFn: () => api.runGraph(detail.data!.run_id), enabled: Boolean(detail.data && !reviewId), retry: false });
  const evidenceGraph = reviewId ? reviewEvidence.data?.graph : runEvidence.data;
  const evidencePending = reviewId ? reviewEvidence.isPending : runEvidence.isPending;
  const evidenceError = reviewId ? reviewEvidence.error : runEvidence.error;
  const refetchEvidence = () => reviewId ? reviewEvidence.refetch() : runEvidence.refetch();

  if (detail.isPending) {
    return <main className="figma3-page figma3-case-page"><div className="figma3-case-state"><StateFrame kind="loading" title="Reading case" detail="Loading state, evidence links, and history." /></div></main>;
  }
  if (detail.isError || !detail.data) {
    return <main className="figma3-page figma3-case-page"><div className="figma3-case-state"><StateFrame kind="error" title="This case could not be opened" detail={detail.error instanceof Error ? detail.error.message : "The record is unavailable."} action={<Link to="/analyst/queue" className="figma3-secondary">Back to work</Link>} /></div></main>;
  }

  const item = detail.data;
  const sourceReviewId = reviewRequestIdFromCaseFinding(item.finding_id);
  const relationCount = evidenceGraph?.relations.length;
  const nodeCount = evidenceGraph?.nodes.length;
  // A writer can still reopen a closed case; exception requests remain unavailable once closed.
  const canAct = canWriteCase || (canRequestException && item.state !== "closed");
  const mobileCanJumpToActions = item.state !== "closed" && canAct;
  const responsibility = item.state === "closed"
    ? "The case is closed."
    : !item.assignee
      ? "Assign an owner and due time."
      : "Move the investigation using recorded evidence.";
  const evidenceSummary = evidencePending
    ? "Projecting relationships…"
    : evidenceError
      ? (evidenceError instanceof ApiError && evidenceError.status === 404 ? "Evidence projection is pending." : "Evidence could not be loaded.")
      : evidenceGraph
        ? `${relationCount} recorded relationships · ${nodeCount} entities`
        : "No source evidence is available.";
  const mobileAction = mobileCanJumpToActions ? (
    <a href="#figma3-case-actions" className="figma3-primary">Case actions <span aria-hidden="true">↓</span></a>
  ) : null;

  const actions = (
    <>
      <button type="button" className="figma3-secondary" onClick={() => setEvidenceOpen(true)}>View evidence <span aria-hidden="true">→</span></button>
      {canWriteCase ? <>
        <AssignmentForm key={`assign-${item.id}-${item.version}`} item={item} />
        <TransitionForm key={`transition-${item.id}-${item.version}-${item.state}`} item={item} />
      </> : null}
      {canRequestException && item.state !== "closed" ? <ExceptionRequestForm key={`exception-${item.id}-${item.version}`} item={item} /> : null}
      {!canWriteCase && !canRequestException ? <p className="figma3-case-capability-note">Your current capabilities allow case review but no workflow changes.</p> : null}
    </>
  );

  return (
    <main className="figma3-page figma3-case-page">
      <div className="figma3-case-desktop figma3-desktop-only">
        <section className="figma3-case-scroll" aria-label="Case details">
          <article className="figma3-case-narrative">
            <Link to="/analyst/queue" className="figma3-case-back">← Security operations</Link>
            <header className="figma3-case-heading">
              <div className="figma3-case-meta-row">
                <code>{item.id}</code>
                <span className={severityClass(item.severity)}>{item.severity}</span>
                <span className="figma3-case-status">{item.state.replaceAll("_", " ")}</span>
                {item.overdue ? <span className="figma3-case-overdue">Overdue</span> : null}
              </div>
              <h1>{item.title}</h1>
              <p>Case tracks finding <code>{item.finding_id}</code> from run <code>{item.run_id}</code>.</p>
              <div className="figma3-case-record-meta"><span>{item.assignee_name ?? "Unassigned"}</span><span>Updated {timestamp(item.updated_at)}</span><span>Record v{item.version}</span></div>
            </header>

            {item.priority_reasons.length ? <section className="figma3-case-risk"><p className="figma3-kicker">Risk</p><ul>{item.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></section> : null}

            <section className="figma3-case-journey-card">
              <p className="figma3-kicker">Case journey</p>
              <FigmaJourney current={caseJourneyState(item.state)} overrides={journeyOverrides(item)} label="Seven-stage case journey" />
            </section>

            <section className="figma3-case-evidence-summary">
              <div>
                <p className="figma3-kicker">Evidence readiness</p>
                <h2>{sourceReviewId ? "Submitted review snapshot" : "Current native run graph"}</h2>
                <p>{evidenceSummary}</p>
              </div>
              <button type="button" className="figma3-secondary" onClick={() => setEvidenceOpen(true)} disabled={!evidencePending && !evidenceError && !evidenceGraph}>Open evidence</button>
            </section>

            {evidenceGraph ? <section className="figma3-case-graph-preview"><FigmaEvidenceGraph graph={evidenceGraph} label="Case source evidence relationship map" height={174} /></section> : null}

            <div className="figma3-case-disclosures">
              <details>
                <summary>Source record <span>⌄</span></summary>
                <div className="figma3-case-source-grid">
                  <div><small>Source</small>{sourceReviewId ? <Link to="/analyst/reviews/$requestId" params={{ requestId: sourceReviewId }}>Open source review</Link> : <Link to="/analyst/investigate/$runId/$findingId" params={{ runId: item.run_id, findingId: item.finding_id }}>{item.finding_id}</Link>}</div>
                  <div><small>Run</small><Link to="/runs/$runId/security" params={{ runId: item.run_id }}>{item.run_id}</Link></div>
                  <div><small>Assignee</small><strong>{item.assignee_name ?? "Unassigned"}</strong></div>
                  <div><small>Disposition</small><strong>{item.disposition ?? "Not recorded"}</strong></div>
                </div>
              </details>
              <details>
                <summary>Team notes <span>⌄</span></summary>
                <div><WorkCollaboration kind="case" id={item.id} /></div>
              </details>
              <details>
                <summary>Case history <span>{item.events.length}</span></summary>
                <div><CaseHistory item={item} /></div>
              </details>
              <details>
                <summary>Record references <span>⌄</span></summary>
                <div className="figma3-case-references"><IntegrityRef label="Case" value={item.id} /><IntegrityRef label="Finding" value={item.finding_id} /><IntegrityRef label="Run" value={item.run_id} /></div>
              </details>
            </div>
          </article>
        </section>

        <div id="figma3-case-actions" className="figma3-case-dock-wrap">
          <FigmaResponsibilityDock
            responsibility={responsibility}
            why="Every assignment and state change is checked against the current server version. Resolution and closure require evidence."
            deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined}
            overdue={item.overdue}
            blockingCondition={!item.assignee && item.state !== "closed" ? "An owner is required before the investigation can be advanced." : undefined}
          >
            {actions}
          </FigmaResponsibilityDock>
        </div>
      </div>

      <div className="figma3-case-mobile figma3-mobile-only">
        <header className="figma3-case-mobile-header">
          <Link to="/analyst/queue" aria-label="Back to security operations">←</Link>
          <div><strong>{item.id}</strong><small>{item.state.replaceAll("_", " ")} · v{item.version}</small></div>
        </header>
        <div className="figma3-case-mobile-scroll">
          <h1>{item.title}</h1>
          <div className="figma3-case-mobile-badges"><span className={severityClass(item.severity)}>{item.severity}</span>{item.overdue ? <span className="figma3-case-overdue">Overdue</span> : null}</div>
          {item.priority_reasons.length ? <section className="figma3-case-risk"><p className="figma3-kicker">Risk</p><ul>{item.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></section> : null}
          <section className="figma3-case-mobile-journey"><p className="figma3-kicker">Case journey</p><FigmaJourney current={caseJourneyState(item.state)} overrides={journeyOverrides(item)} vertical label="Seven-stage case journey" /></section>
          <section className="figma3-case-mobile-evidence">
            <p className="figma3-kicker">Evidence</p>
            <p>{evidenceSummary}</p>
            {evidenceGraph ? <FigmaEvidenceGraph graph={evidenceGraph} label="Case source evidence relationship map" mobileEvidenceOnly /> : null}
            <button type="button" className="figma3-secondary" onClick={() => setEvidenceOpen(true)} disabled={!evidencePending && !evidenceError && !evidenceGraph}>View evidence</button>
          </section>
          <div className="figma3-case-disclosures">
            <details><summary>Source record <span>⌄</span></summary><div className="figma3-case-source-grid"><div><small>Source</small>{sourceReviewId ? <Link to="/analyst/reviews/$requestId" params={{ requestId: sourceReviewId }}>Open source review</Link> : <Link to="/analyst/investigate/$runId/$findingId" params={{ runId: item.run_id, findingId: item.finding_id }}>{item.finding_id}</Link>}</div><div><small>Run</small><Link to="/runs/$runId/security" params={{ runId: item.run_id }}>{item.run_id}</Link></div></div></details>
            <details><summary>Case history <span>{item.events.length}</span></summary><div><CaseHistory item={item} /></div></details>
            <details><summary>Team notes <span>⌄</span></summary><div><WorkCollaboration kind="case" id={item.id} /></div></details>
          </div>
          {canAct ? <section id="figma3-case-actions" className="figma3-case-mobile-actions"><FigmaResponsibilityDock responsibility={responsibility} why="Every assignment and state change is checked against the current server version. Resolution and closure require evidence." deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined} overdue={item.overdue}>{actions}</FigmaResponsibilityDock></section> : null}
        </div>
        <FigmaMobileDock deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined}>
          <button type="button" className="figma3-secondary" onClick={() => setEvidenceOpen(true)}>View evidence</button>
          {mobileAction}
        </FigmaMobileDock>
      </div>

      {evidenceOpen ? <EvidenceDrawer evidenceGraph={evidenceGraph} evidencePending={evidencePending} evidenceError={evidenceError} onRetry={() => void refetchEvidence()} onClose={() => setEvidenceOpen(false)} /> : null}
    </main>
  );
}
