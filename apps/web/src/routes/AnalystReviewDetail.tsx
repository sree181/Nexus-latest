import { useState, type SyntheticEvent } from "react";
import type { GraphNode } from "@meshagent/graph";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { WorkCollaboration } from "../components/WorkCollaboration";
import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { ConflictRecovery, IntegrityRef, StateFrame, isVersionConflict } from "../components/WorkflowVisual";
import { Field, MutationMessage, Select, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { ApiError, api, type ReviewRequest } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";
import "./analyst-review.css";

const decisions = [
  ["request_changes", "Ask for a safer version"],
  ["false_positive", "Mark not applicable"],
  ["reject", "Do not approve"],
] as const;

type EvidenceLens = "why" | "affected" | "outcome";
type DockSection = "owner" | "decision" | "case";
type ExpandedDockSection = DockSection | "none" | null;

function reviewJourneyStage(item: ReviewRequest): FigmaJourneyStage {
  if (["verified", "false_positive", "not_approved"].includes(item.state)) return "verified";
  if (item.state === "exception_approved") return "remediation";
  if (item.state === "escalated") return "investigation";
  if (item.state === "changes_requested") return "developer_action";
  return "security_review";
}

function LensAnswer({ item, lens, selectedNode }: { item: ReviewRequest; lens: EvidenceLens; selectedNode: GraphNode | null }) {
  return (
    <section className="figma3-analyst-answer" aria-live="polite">
      <p className="figma3-kicker">{lens === "why" ? "Why this needs a decision" : lens === "affected" ? "Recorded impact" : "Potential outcome change"}</p>
      {lens === "why" ? (
        <>
          {item.reasons.length ? <ul>{item.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>No additional reason was returned.</p>}
          {item.advisories.length ? <div className="figma3-analyst-advisories">{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</div> : <p>No published advisory details were returned with this review.</p>}
        </>
      ) : null}
      {lens === "affected" ? (
        item.code_entities.length ? <ul>{item.code_entities.map((entity) => <li key={entity}><code>{entity}</code></li>)}</ul> : <p>No linked code entities were returned in the submitted snapshot.</p>
      ) : null}
      {lens === "outcome" ? (
        <div className="figma3-analyst-outcome">
          <div><small>Recorded recommendation</small><strong>{item.recommended_version ?? "None recorded"}</strong></div>
          <div><small>Published fixed versions</small><strong>{Array.from(new Set(item.advisories.flatMap((advisory) => advisory.fixed_versions))).join(", ") || "None returned"}</strong></div>
          <div><small>Verification evidence</small><strong>{item.verification_evidence_id ?? "Not recorded"}</strong></div>
        </div>
      ) : null}
      {selectedNode ? <p className="figma3-analyst-selection"><strong>Focused entity:</strong> {selectedNode.label} <code>{selectedNode.id}</code></p> : null}
    </section>
  );
}

function ReviewActionForms({
  item,
  terminal,
  canWriteReview,
  canWriteCase,
  decision,
  setDecision,
  selectedNode,
  caseId,
  onClearSuccess,
  onAssign,
  onEscalate,
  onDecide,
  assignPending,
  escalatePending,
  decidePending,
  expanded,
  setExpanded,
}: {
  item: ReviewRequest;
  terminal: boolean;
  canWriteReview: boolean;
  canWriteCase: boolean;
  decision: (typeof decisions)[number][0];
  setDecision: (value: (typeof decisions)[number][0]) => void;
  selectedNode: GraphNode | null;
  caseId: string | null;
  onClearSuccess: () => void;
  onAssign: (input: Parameters<typeof api.assignReview>[1]) => void;
  onEscalate: (input: Parameters<typeof api.escalateReview>[1]) => void;
  onDecide: (input: Parameters<typeof api.decideReview>[1]) => void;
  assignPending: boolean;
  escalatePending: boolean;
  decidePending: boolean;
  expanded: ExpandedDockSection;
  setExpanded: (value: ExpandedDockSection) => void;
}) {
  const detailsProps = (section: DockSection, defaultOpen: boolean) => ({
    open: expanded === section || (expanded === null && defaultOpen),
    onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => {
      if (event.currentTarget.open) setExpanded(section);
      else if (expanded === section || expanded === null) setExpanded("none");
    },
  });

  return (
    <>
      {!terminal && canWriteReview ? (
        <details id="figma3-analyst-owner" className="figma3-analyst-action-disclosure" {...detailsProps("owner", !item.assignee)}>
          <summary>Owner and due time</summary>
          <form onSubmit={(event) => {
            event.preventDefault();
            onClearSuccess();
            const form = new FormData(event.currentTarget);
            const due = String(form.get("sla_due_at") ?? "");
            onAssign({
              expected_version: item.version_counter,
              assignee: String(form.get("assignee") ?? "").trim(),
              assignee_name: String(form.get("assignee_name") ?? "").trim(),
              sla_due_at: due ? dateInputToEpoch(due) : null,
            });
          }}>
            <Field label="Owner email"><TextInput name="assignee" type="email" required defaultValue={item.assignee ?? ""} /></Field>
            <Field label="Owner name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
            <Field label="Due date"><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
            <button className="figma3-primary" type="submit" disabled={assignPending}>{assignPending ? "Saving…" : "Save owner"}</button>
          </form>
        </details>
      ) : null}

      {!terminal && canWriteReview ? (
        <details id="figma3-analyst-decision" className="figma3-analyst-action-disclosure" {...detailsProps("decision", Boolean(item.assignee))}>
          <summary>Return a decision</summary>
          <form onSubmit={(event) => {
            event.preventDefault();
            onClearSuccess();
            const form = new FormData(event.currentTarget);
            onDecide({
              expected_version: item.version_counter,
              decision,
              rationale: String(form.get("rationale") ?? "").trim(),
              recommended_version: decision === "request_changes" ? String(form.get("recommended_version") ?? "").trim() : null,
              expires_at: null,
            });
          }}>
            <Field label="Decision"><Select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>{decisions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
            {decision === "request_changes" ? <Field label="Recommended version"><TextInput name="recommended_version" defaultValue={item.advisories.flatMap((advisory) => advisory.fixed_versions)[0] ?? ""} required placeholder="Safe version" /></Field> : null}
            <Field label="Message to Developer" hint="Use plain language. Explain the next action and why."><TextArea name="rationale" required maxLength={4096} /></Field>
            <button className="figma3-primary" type="submit" disabled={decidePending}>{decidePending ? "Recording…" : "Record decision"}</button>
          </form>
        </details>
      ) : null}

      {!terminal && canWriteCase ? (
        <details id="figma3-analyst-case" className="figma3-analyst-action-disclosure" {...detailsProps("case", false)}>
          <summary>{item.escalated_case_id || caseId ? "Linked investigation" : "Create a tracked case"}</summary>
          {item.escalated_case_id || caseId ? (
            <Link to="/analyst/cases/$caseId" params={{ caseId: item.escalated_case_id ?? caseId! }} className="figma3-primary">Open linked case</Link>
          ) : (
            <form onSubmit={(event) => {
              event.preventDefault();
              onClearSuccess();
              const form = new FormData(event.currentTarget);
              onEscalate({
                expected_version: item.version_counter,
                title: String(form.get("title") ?? "").trim(),
                rationale: String(form.get("rationale") ?? "").trim(),
                assignee: item.assignee,
                assignee_name: item.assignee_name,
                sla_due_at: item.sla_due_at,
              });
            }}>
              <Field label="Case title"><TextInput name="title" required defaultValue={`${item.package} security investigation`} /></Field>
              <Field label="Why a case is needed"><TextArea name="rationale" required maxLength={4096} defaultValue={selectedNode ? `Investigate ${selectedNode.label} (${selectedNode.id}) and its relationship to ${item.package}.` : "The submitted evidence needs a tracked investigation."} /></Field>
              <button className="figma3-primary" type="submit" disabled={escalatePending}>{escalatePending ? "Creating…" : "Create case"}</button>
            </form>
          )}
        </details>
      ) : null}
      {!canWriteReview && !canWriteCase ? <p className="figma3-analyst-read-only">Your current capabilities allow evidence review but no workflow changes.</p> : null}
    </>
  );
}

export function AnalystReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  const { hasCapability } = useIdentity();
  const canWriteReview = hasCapability("review.write");
  const canWriteCase = hasCapability("case.write");
  const client = useQueryClient();
  const [decision, setDecision] = useState<(typeof decisions)[number][0]>("request_changes");
  const [lens, setLens] = useState<EvidenceLens>("why");
  const [success, setSuccess] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [expandedAction, setExpandedAction] = useState<ExpandedDockSection>(null);
  const [mobileActionsOpen, setMobileActionsOpen] = useState(false);

  const review = useQuery({ queryKey: ["review", requestId], queryFn: () => api.review(requestId), refetchInterval: 10_000 });
  const graph = useQuery({ queryKey: ["review", requestId, "graph"], queryFn: () => api.reviewGraph(requestId), refetchInterval: 15_000, retry: false });
  const refresh = (value?: unknown) => {
    if (value) client.setQueryData(["review", requestId], value);
    void client.invalidateQueries({ queryKey: ["review", requestId, "graph"] });
    void client.invalidateQueries({ queryKey: ["reviews"] });
    void client.invalidateQueries({ queryKey: ["work-queue"] });
    void client.invalidateQueries({ queryKey: ["notifications"] });
  };
  const assign = useMutation({
    mutationFn: (input: Parameters<typeof api.assignReview>[1]) => api.assignReview(requestId, input),
    onSuccess: (value) => { refresh(value); setSuccess("Owner and due time updated."); },
  });
  const escalate = useMutation({
    mutationFn: (input: Parameters<typeof api.escalateReview>[1]) => api.escalateReview(requestId, input),
    onSuccess: (value) => { setCaseId(value.id); refresh(); setSuccess("A tracked case was created with this review evidence."); },
  });
  const decide = useMutation({
    mutationFn: (input: Parameters<typeof api.decideReview>[1]) => api.decideReview(requestId, input),
    onSuccess: (value) => { refresh(value); setSuccess("Decision recorded and returned to the Developer."); },
  });
  const reload = () => { assign.reset(); escalate.reset(); decide.reset(); setSuccess(null); void review.refetch(); void graph.refetch(); };

  const item = review.data;
  const terminal = item ? ["verified", "false_positive", "not_approved"].includes(item.state) : false;
  const mutationError = assign.error ?? escalate.error ?? decide.error;

  const revealAction = (section: DockSection) => {
    setExpandedAction(section);
    requestAnimationFrame(() => document.getElementById(`figma3-analyst-${section}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  if (review.isPending) {
    return <main className="figma3-page figma3-analyst-review-page"><div className="figma3-analyst-state"><StateFrame kind="loading" title="Loading developer review" detail="Reading the submitted snapshot and workflow state." /></div></main>;
  }

  if (review.isError || !item) {
    return <main className="figma3-page figma3-analyst-review-page"><div className="figma3-analyst-state"><StateFrame kind="error" title="This review could not be opened" detail={review.error instanceof Error ? review.error.message : "The record is unavailable."} action={<Link to="/analyst/queue" className="figma3-secondary">Back to work</Link>} /></div></main>;
  }

  const actionForms = (
    <ReviewActionForms
      item={item}
      terminal={terminal}
      canWriteReview={canWriteReview}
      canWriteCase={canWriteCase}
      decision={decision}
      setDecision={setDecision}
      selectedNode={selectedNode}
      caseId={caseId}
      onClearSuccess={() => setSuccess(null)}
      onAssign={(input) => assign.mutate(input)}
      onEscalate={(input) => escalate.mutate(input)}
      onDecide={(input) => decide.mutate(input)}
      assignPending={assign.isPending}
      escalatePending={escalate.isPending}
      decidePending={decide.isPending}
      expanded={expandedAction}
      setExpanded={setExpandedAction}
    />
  );

  const dockActions = !terminal && (canWriteReview || canWriteCase) ? (
    <>
      {canWriteReview && !item.assignee ? <button type="button" className="figma3-primary" onClick={() => revealAction("owner")}>Set owner <span aria-hidden="true">→</span></button> : null}
      {canWriteReview ? <button type="button" className="figma3-primary" onClick={() => revealAction("decision")}>Record decision <span aria-hidden="true">→</span></button> : null}
      {canWriteCase ? <button type="button" className="figma3-secondary" onClick={() => revealAction("case")}>{item.escalated_case_id || caseId ? "Open investigation" : "Escalate to case"}</button> : null}
    </>
  ) : undefined;

  const detailContent = (
    <>
      <Link to="/analyst/queue" className="figma3-analyst-back">← Security operations</Link>
      <header className="figma3-analyst-heading">
        <div className="figma3-analyst-meta">
          <code>{item.id}</code>
          <ReviewStateBadge state={item.state} />
          <span className={item.overdue ? "is-risk" : ""}>{item.sla_due_at ? `${item.overdue ? "Overdue" : "Due"} ${timestamp(item.sla_due_at)}` : "No SLA due time"}</span>
        </div>
        <h1>{item.owner_name} asked Security to review {item.package}@{item.version || "unpinned"}</h1>
        <p>{item.reasons.join(" ") || item.rationale}</p>
      </header>

      <section className="figma3-analyst-journey-card">
        <p className="figma3-kicker">Workflow</p>
        <FigmaJourney current={reviewJourneyStage(item)} label="Analyst review journey" />
      </section>

      <section className="figma3-analyst-request">
        <p className="figma3-kicker">Submitted context</p>
        <blockquote>{item.rationale}</blockquote>
        <small>{item.owner_name} · {timestamp(item.created_at)}</small>
      </section>

      <section className="figma3-analyst-evidence">
        <div className="figma3-analyst-question-tabs" role="group" aria-label="Decision evidence summary">
          <button type="button" aria-pressed={lens === "why"} onClick={() => setLens("why")}>Why blocked</button>
          <button type="button" aria-pressed={lens === "affected"} onClick={() => setLens("affected")}>What is affected</button>
          <button type="button" aria-pressed={lens === "outcome"} onClick={() => setLens("outcome")}>What changes the outcome</button>
        </div>
        {graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review record remains available while native evidence is prepared." /> : graph.isError ? <StateFrame kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"} title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"} detail={graph.error instanceof Error ? graph.error.message : "Retry when the evidence service is ready."} action={<button type="button" className="figma3-secondary" onClick={() => void graph.refetch()}>Retry map</button>} /> : <FigmaEvidenceGraph graph={graph.data.graph} label="Analyst review evidence graph" selectedId={selectedNode?.id} onSelect={setSelectedNode} height={218} />}
      </section>

      <LensAnswer item={item} lens={lens} selectedNode={selectedNode} />

      <section className="figma3-analyst-disclosures">
        <details><summary>Collaboration</summary><div><WorkCollaboration kind="review" id={item.id} /></div></details>
        <details><summary>Decision history <span>{item.events.length}</span></summary><div>{item.events.length ? <ol>{[...item.events].reverse().map((event) => <li key={event.id}><span aria-hidden="true" /><span><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span><time>{timestamp(event.at)}</time></li>)}</ol> : <p>No review events yet.</p>}</div></details>
      </section>
    </>
  );

  return (
    <main className="figma3-page figma3-analyst-review-page">
      <div className="figma3-analyst-desktop figma3-desktop-only">
        <section className="figma3-analyst-scroll" aria-label="Analyst review record"><article className="figma3-analyst-body">{detailContent}</article></section>
        <FigmaResponsibilityDock
          className="figma3-analyst-dock"
          responsibility={terminal ? "The review is complete." : !item.assignee ? "Assign an owner before deciding." : "Resolve the Developer’s request or open a case."}
          why={terminal ? "The recorded result is visible to the Developer and remains in the audit trail." : "Use the submitted evidence. Durable policy exceptions follow the separate CISO approval workflow."}
          actions={dockActions}
          deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined}
          overdue={item.overdue}
          blockingCondition={item.verdict === "block" ? "This recorded policy evaluation remains blocked until a governed next step is completed." : undefined}
        >
          <ConflictRecovery error={mutationError} onReload={reload} />
          {actionForms}
          {!isVersionConflict(mutationError) ? <MutationMessage error={mutationError} success={success} /> : success ? <MutationMessage error={null} success={success} /> : null}
          <div className="figma3-analyst-integrity"><IntegrityRef label="Review" value={item.id} /><IntegrityRef label="Evidence root" value={item.evidence_root_ulid} /><IntegrityRef label="Evidence digest" value={item.evidence_digest} /></div>
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-analyst-mobile figma3-mobile-only">
        <header className="figma3-analyst-mobile-header">
          <Link to="/analyst/queue" aria-label="Back to Security operations">←</Link>
          <div><strong>{item.id}</strong><small>{item.package}@{item.version || "unpinned"}</small></div>
          <ReviewStateBadge state={item.state} />
        </header>
        <section className="figma3-analyst-mobile-scroll" aria-label="Analyst review record">
          <article className="figma3-analyst-mobile-body">{detailContent}</article>
          {mobileActionsOpen ? (
            <section className="figma3-analyst-mobile-actions" aria-label="Review actions">
              <div className="figma3-analyst-mobile-actions-heading"><p className="figma3-kicker">Your responsibility</p><button type="button" onClick={() => setMobileActionsOpen(false)}>Close</button></div>
              <p>{terminal ? "The review is complete." : !item.assignee ? "Assign an owner before deciding." : "Resolve the Developer’s request or open a case."}</p>
              <ConflictRecovery error={mutationError} onReload={reload} />
              {actionForms}
              {!isVersionConflict(mutationError) ? <MutationMessage error={mutationError} success={success} /> : success ? <MutationMessage error={null} success={success} /> : null}
              <div className="figma3-analyst-integrity"><IntegrityRef label="Review" value={item.id} /><IntegrityRef label="Evidence root" value={item.evidence_root_ulid} /><IntegrityRef label="Evidence digest" value={item.evidence_digest} /></div>
            </section>
          ) : null}
        </section>
        {!terminal && (canWriteReview || canWriteCase) ? <FigmaMobileDock deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined}><button type="button" className="figma3-primary" onClick={() => setMobileActionsOpen(true)}>Review actions <span aria-hidden="true">→</span></button></FigmaMobileDock> : null}
      </div>
    </main>
  );
}
