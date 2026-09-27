import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { WorkCollaboration } from "../components/WorkCollaboration";
import { SidePanel } from "../components/DeveloperVisual";
import { ConflictRecovery, IntegrityRef, ResponsibilityDock, StateFrame, WorkflowJourney, isVersionConflict, type JourneyItem } from "../components/WorkflowVisual";
import { Field, MutationMessage, Select, SeverityBadge, StatusBadge, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { ApiError, api, type AssignCaseInput, type CaseRecord, type CaseState, type CaseTransition, type CreateExceptionInput, type TransitionCaseInput } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

const transitions: Record<CaseRecord["state"], CaseTransition[]> = {
  open: ["triaged", "investigating", "closed"],
  triaged: ["investigating", "remediation", "closed"],
  investigating: ["remediation", "resolved", "closed"],
  remediation: ["resolved", "investigating", "closed"],
  resolved: ["reopened", "closed"],
  closed: ["reopened"],
};

const caseStages: CaseState[] = ["open", "triaged", "investigating", "remediation", "resolved", "closed"];

export function reviewRequestIdFromCaseFinding(findingId: string): string | null {
  if (!findingId.startsWith("review:")) return null;
  return findingId.slice("review:".length).trim() || null;
}

function caseJourney(item: CaseRecord): JourneyItem[] {
  const visited = new Set(item.events.map((event) => event.to_state).filter(Boolean));
  visited.add(item.state);
  return caseStages.map((state) => ({
    id: state,
    label: state.replaceAll("_", " "),
    detail: state === item.state ? `Current · v${item.version}` : visited.has(state) ? "Recorded" : "Not reached",
    state: state === item.state ? "current" : visited.has(state) ? "complete" : "pending",
  }));
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
    <details className="workflow-action-disclosure" open={!item.assignee}>
      <summary>Owner and due time</summary>
      <form onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const due = String(form.get("sla_due_at") ?? "");
        assign.mutate({ expected_version: item.version, assignee: String(form.get("assignee") ?? "").trim(), assignee_name: String(form.get("assignee_name") ?? "").trim(), sla_due_at: due ? dateInputToEpoch(due) : null });
      }}>
        <ConflictRecovery error={assign.error} onReload={() => { assign.reset(); setSuccess(null); void client.invalidateQueries({ queryKey: ["case", item.id] }); }} />
        <Field label="Assignee identifier"><TextInput name="assignee" required defaultValue={item.assignee ?? ""} placeholder="analyst@example.com" /></Field>
        <Field label="Assignee name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
        <Field label="SLA due date" hint="Optional; the server computes overdue state."><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
        <Button type="submit" disabled={assign.isPending}>{assign.isPending ? "Saving…" : "Save assignment"}</Button>
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
    <details className="workflow-action-disclosure" open={Boolean(item.assignee)}>
      <summary>Move the case</summary>
      <form onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const evidence = String(form.get("evidence_ids") ?? "").split(/[\n,]/).map((value) => value.trim()).filter(Boolean);
        transition.mutate({ expected_version: item.version, to_state: target, disposition: String(form.get("disposition") ?? "").trim() || null, rationale: String(form.get("rationale") ?? "").trim(), evidence_ids: evidence });
      }}>
        <ConflictRecovery error={transition.error} onReload={() => { transition.reset(); setSuccess(null); void client.invalidateQueries({ queryKey: ["case", item.id] }); }} />
        <Field label="Next state"><Select name="to_state" value={target} onChange={(event) => setTarget(event.target.value as CaseTransition)}>{transitions[item.state].map((state) => <option key={state} value={state}>{state}</option>)}</Select></Field>
        <Field label="Rationale"><TextArea name="rationale" required maxLength={4096} /></Field>
        <Field label={`Disposition${isResolution ? " (required)" : ""}`}><TextInput name="disposition" required={isResolution} maxLength={256} placeholder="verified remediated, accepted risk, false positive…" /></Field>
        <Field label={`Evidence IDs${isResolution ? " (required)" : ""}`} hint="Resolution and closure require verification evidence."><TextArea name="evidence_ids" required={isResolution} placeholder="scan:codeql:replacement-build" /></Field>
        <Button type="submit" disabled={transition.isPending}>{transition.isPending ? "Recording…" : `Move to ${target}`}</Button>
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
    <details className="workflow-action-disclosure">
      <summary>Request a policy exception</summary>
      {policies.isPending ? <p className="m-3 text-sm text-slate">Loading active policies…</p> : policies.isError ? <p role="alert" className="m-3 text-sm text-risk">Policies could not be loaded.</p> : policies.data.length === 0 ? <p className="m-3 text-sm text-slate">No active policy is available.</p> : (
        <form onSubmit={(event) => {
          event.preventDefault();
          setSuccess(null);
          const form = new FormData(event.currentTarget);
          request.mutate({ policy_id: String(form.get("policy_id") ?? ""), scope: String(form.get("scope") ?? "").trim(), rationale: String(form.get("rationale") ?? "").trim(), compensating_controls: String(form.get("controls") ?? "").trim(), owner: String(form.get("owner") ?? "").trim(), evidence_ids: [`case:${item.id}`], expires_at: dateInputToEpoch(form.get("expires_at")) });
        }}>
          <p className="text-xs leading-relaxed text-slate">This creates a time-bounded request. A CISO—not the requester—must decide it.</p>
          <Field label="Policy"><Select name="policy_id" required>{policies.data.map((policy) => <option key={policy.id} value={policy.id}>{policy.name}</option>)}</Select></Field>
          <Field label="Scope"><TextInput name="scope" required defaultValue={`case:${item.id}`} /></Field>
          <Field label="Owner"><TextInput name="owner" required defaultValue={item.assignee ?? ""} placeholder="service or team owner" /></Field>
          <Field label="Expires"><TextInput name="expires_at" type="date" required defaultValue={defaultExpiry} min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} /></Field>
          <Field label="Risk rationale"><TextArea name="rationale" required maxLength={4096} /></Field>
          <Field label="Compensating controls"><TextArea name="controls" required maxLength={4096} /></Field>
          <Button type="submit" disabled={request.isPending}>{request.isPending ? "Submitting…" : "Request exception"}</Button>
          <MutationMessage error={request.error} success={success} />
        </form>
      )}
    </details>
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

  return (
    <main className="workflow-page analyst-case-page">
      {detail.isPending ? <div className="workflow-scroll"><StateFrame kind="loading" title="Reading case" detail="Loading state, evidence links, and history." /></div> : detail.isError || !detail.data ? <div className="workflow-scroll"><StateFrame kind="error" title="This case could not be opened" detail={detail.error instanceof Error ? detail.error.message : "The record is unavailable."} action={<Link to="/analyst/queue" className="workflow-secondary-action">Back to work</Link>} /></div> : (() => {
        const item = detail.data;
        const sourceReviewId = reviewRequestIdFromCaseFinding(item.finding_id);
        const relationCount = evidenceGraph?.relations.length;
        const nodeCount = evidenceGraph?.nodes.length;
        return (
          <div className="analyst-case-shell">
            <section className="analyst-case-scroll">
              <article className="analyst-case-narrative">
                <Link to="/analyst/queue" className="focus-back-link">← Security operations</Link>
                <header className="analyst-case-heading">
                  <div className="analyst-review-meta"><span>{item.id}</span><SeverityBadge severity={item.severity} /><StatusBadge status={item.state} />{item.overdue ? <StatusBadge status="overdue" /> : null}</div>
                  <h1>{item.title}</h1>
                  <p>Case tracks finding <code>{item.finding_id}</code> from run <code>{item.run_id}</code>.</p>
                  <div className="analyst-case-meta"><span>{item.assignee_name ?? "Unassigned"}</span><span>Updated {timestamp(item.updated_at)}</span><span>Record v{item.version}</span></div>
                </header>

                {item.priority_reasons.length ? <section className="analyst-case-risk"><p className="workflow-eyebrow">Risk</p><ul>{item.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></section> : null}

                <section className="focus-journey-card"><p className="workflow-eyebrow">Case lifecycle</p><WorkflowJourney items={caseJourney(item)} label="Case lifecycle" /></section>

                <section className="case-evidence-entry">
                  <div><p className="workflow-eyebrow">Evidence</p><h2>{sourceReviewId ? "Submitted review snapshot" : "Current native run graph"}</h2><p>{evidencePending ? "Projecting relationships…" : evidenceError ? (evidenceError instanceof ApiError && evidenceError.status === 404 ? "Evidence projection is pending." : "Evidence could not be loaded.") : evidenceGraph ? `${relationCount} recorded relationships · ${nodeCount} entities` : "No source evidence is available."}</p></div>
                  <button type="button" className="workflow-secondary-action" onClick={() => setEvidenceOpen(true)} disabled={!evidencePending && !evidenceError && !evidenceGraph}>Open evidence</button>
                </section>

                <div className="focus-disclosures analyst-case-disclosures">
                  <details><summary>Source record</summary><div><div className="case-source-grid"><div><span>Source</span>{sourceReviewId ? <Link to="/analyst/reviews/$requestId" params={{ requestId: sourceReviewId }}>Open source review</Link> : <Link to="/analyst/investigate/$runId/$findingId" params={{ runId: item.run_id, findingId: item.finding_id }}>{item.finding_id}</Link>}</div><div><span>Run</span><Link to="/runs/$runId/security" params={{ runId: item.run_id }}>{item.run_id}</Link></div><div><span>Assignee</span><strong>{item.assignee_name ?? "Unassigned"}</strong></div><div><span>Disposition</span><strong>{item.disposition ?? "Not recorded"}</strong></div></div></div></details>
                  <details><summary>Team notes</summary><div><WorkCollaboration kind="case" id={item.id} /></div></details>
                  <details><summary>Case history <span>{item.events.length}</span></summary><div>{item.events.length ? <ol className="workflow-list">{[...item.events].reverse().map((event) => <li key={event.id} className="workflow-list-row"><span className="workflow-update-dot" aria-hidden="true" /><span className="workflow-list-row-main"><strong>{event.action.replace(/\./g, " ")}</strong><small>{event.actor_name} · {event.rationale}</small>{event.evidence_ids.length ? <span className="case-evidence-ids">{event.evidence_ids.map((id) => <code key={id}>{id}</code>)}</span> : null}</span><span className="font-mono text-[10px] text-slate">{timestamp(event.at)}</span></li>)}</ol> : <p>No case events were returned.</p>}</div></details>
                  <details><summary>Record references</summary><div className="grid gap-2"><IntegrityRef label="Case" value={item.id} /><IntegrityRef label="Finding" value={item.finding_id} /><IntegrityRef label="Run" value={item.run_id} /></div></details>
                </div>
              </article>
            </section>

            <div id="case-actions" className="analyst-case-dock"><ResponsibilityDock responsibility={item.state === "closed" ? "The case is closed." : !item.assignee ? "Assign an owner and due time." : "Move the investigation using recorded evidence."} why="Every assignment and state change is checked against the current server version. Resolution and closure require evidence." deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined} overdue={item.overdue}>
              <button type="button" className="workflow-secondary-action" onClick={() => setEvidenceOpen(true)}>View evidence</button>
              {canWriteCase ? <><AssignmentForm key={`assign-${item.id}-${item.version}`} item={item} /><TransitionForm key={`transition-${item.id}-${item.version}-${item.state}`} item={item} /></> : null}
              {canRequestException && item.state !== "closed" ? <ExceptionRequestForm key={`exception-${item.id}-${item.version}`} item={item} /> : null}
              {!canWriteCase && !canRequestException ? <p className="text-xs leading-relaxed text-slate">Your current capabilities allow case review but no workflow changes.</p> : null}
            </ResponsibilityDock></div>
            {item.state !== "closed" && (canWriteCase || canRequestException) ? <a href="#case-actions" className="analyst-mobile-action">Case actions</a> : null}
            {evidenceOpen ? <SidePanel title="Case evidence" icon="evidence" onClose={() => setEvidenceOpen(false)}>{evidencePending ? <StateFrame kind="loading" title="Projecting relationships" /> : evidenceError ? <StateFrame kind={evidenceError instanceof ApiError && evidenceError.status === 404 ? "empty" : "error"} title={evidenceError instanceof ApiError && evidenceError.status === 404 ? "Evidence projection is pending" : "Evidence could not be loaded"} detail={evidenceError instanceof Error ? evidenceError.message : undefined} action={<button type="button" className="workflow-secondary-action" onClick={() => void refetchEvidence()}>Retry map</button>} /> : evidenceGraph ? <ReviewEvidenceGraph graph={evidenceGraph} label="Case source evidence relationship map" /> : <StateFrame kind="empty" title="No source evidence is available" />}</SidePanel> : null}
          </div>
        );
      })()}
    </main>
  );
}
