import { useState } from "react";
import type { GraphNode } from "@meshagent/graph";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { WorkCollaboration } from "../components/WorkCollaboration";
import { ConflictRecovery, IntegrityRef, ResponsibilityDock, StateFrame, WorkflowHero, WorkflowJourney, WorkflowSection, isVersionConflict, type JourneyItem } from "../components/WorkflowVisual";
import { Field, MutationMessage, Select, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { ApiError, api, type ReviewRequest } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

const decisions = [
  ["request_changes", "Ask for a safer version"],
  ["false_positive", "Mark not applicable"],
  ["reject", "Do not approve"],
] as const;

function reviewJourney(item: ReviewRequest): JourneyItem[] {
  const assigned = Boolean(item.assignee);
  const decided = item.state !== "waiting" && item.state !== "escalated";
  const verified = item.state === "verified";
  return [
    { id: "request", label: "Developer request", detail: timestamp(item.created_at), state: "complete" },
    { id: "owner", label: "Owner", detail: item.assignee_name ?? "Unassigned", state: assigned ? "complete" : "current" },
    { id: "review", label: "Evidence review", detail: item.state === "waiting" ? "In progress" : item.state.replaceAll("_", " "), state: decided || verified ? "complete" : assigned ? "current" : "pending" },
    { id: "case", label: "Investigation", detail: item.escalated_case_id ? "Tracked case" : "If needed", state: item.escalated_case_id ? "current" : "pending" },
    { id: "result", label: "Developer result", detail: verified ? "Verified" : decided ? "Recorded" : "Pending", state: verified || decided ? "complete" : "pending" },
  ];
}

export function AnalystReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  const { hasCapability } = useIdentity();
  const canWriteReview = hasCapability("review.write");
  const canWriteCase = hasCapability("case.write");
  const client = useQueryClient();
  const [decision, setDecision] = useState<(typeof decisions)[number][0]>("request_changes");
  const [success, setSuccess] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const review = useQuery({ queryKey: ["review", requestId], queryFn: () => api.review(requestId), refetchInterval: 10_000 });
  const graph = useQuery({ queryKey: ["review", requestId, "graph"], queryFn: () => api.reviewGraph(requestId), refetchInterval: 15_000, retry: false });
  const refresh = (value?: unknown) => {
    if (value) client.setQueryData(["review", requestId], value);
    void client.invalidateQueries({ queryKey: ["review", requestId, "graph"] });
    void client.invalidateQueries({ queryKey: ["reviews"] });
    void client.invalidateQueries({ queryKey: ["work-queue"] });
    void client.invalidateQueries({ queryKey: ["notifications"] });
  };
  const reload = () => {
    assign.reset();
    escalate.reset();
    decide.reset();
    setSuccess(null);
    void review.refetch();
    void graph.refetch();
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
  const item = review.data;
  const terminal = item ? ["verified", "false_positive", "not_approved"].includes(item.state) : false;
  const mutationError = assign.error ?? escalate.error ?? decide.error;

  return (
    <main className="workflow-page">
      <PageHeader section="Analyst / Operations" title="Developer review" meta={<Link to="/analyst/queue" className="text-accent hover:underline">Back to work</Link>} />
      <div className="workflow-scroll">
        {review.isPending ? <StateFrame kind="loading" title="Loading developer review" detail="Reading the submitted snapshot and workflow state." /> : review.isError || !item ? <StateFrame kind="error" title="This review could not be opened" detail={review.error instanceof Error ? review.error.message : "The record is unavailable."} action={<Link to="/analyst/queue" className="workflow-secondary-action">Back to work</Link>} /> : (
          <div className="workflow-layout">
            <div className="workflow-main">
              <WorkflowHero
                eyebrow={`Analyst review · ${item.id}`}
                title={`${item.package}@${item.version || "unpinned"}`}
                description={item.rationale}
                tone={item.severity === "critical" || item.severity === "high" ? "danger" : item.overdue ? "warning" : "accent"}
                status={<ReviewStateBadge state={item.state} />}
                meta={<><span>{item.repository_name}</span><span>{item.ecosystem}</span><span>{item.owner_name}</span><span>Version {item.version_counter}</span></>}
              />

              <WorkflowSection eyebrow="Journey" title="Review path" description="Each step reflects the current server state, not a local simulation.">
                <WorkflowJourney items={reviewJourney(item)} label="Analyst review journey" />
              </WorkflowSection>

              {item.advisories.length ? <WorkflowSection eyebrow="Package evidence" title="Published advisories" action={<span className="font-mono text-xs text-slate">{item.advisories.length}</span>}>{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</WorkflowSection> : <WorkflowSection eyebrow="Evidence state" title="Advisory check incomplete"><p className="text-sm leading-relaxed text-slate">{item.reasons.join(" ") || "No published advisory details were returned with this review."}</p></WorkflowSection>}

              <WorkflowSection eyebrow="Evidence" title="Relationship map" description="Select an entity to focus your investigation or draft a case rationale.">
                {graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review record remains available while native evidence is prepared." /> : graph.isError ? <StateFrame kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"} title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"} detail={graph.error instanceof Error ? graph.error.message : "Retry when the evidence service is ready."} action={<button type="button" className="workflow-secondary-action" onClick={() => void graph.refetch()}>Retry map</button>} /> : <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_230px]"><div><ReviewEvidenceGraph graph={graph.data.graph} label="Analyst review evidence graph" selectedId={selectedNode?.id} onSelect={setSelectedNode} /><p className="review-graph-note">{graph.data.note}</p></div><aside className="evidence-selection">{selectedNode ? <><p className="workflow-eyebrow">Selected {selectedNode.kind}</p><h3>{selectedNode.label}</h3><code>{selectedNode.id}</code><p>Use this selection to focus your rationale. The case itself remains bound to the submitted review snapshot.</p></> : <><p className="workflow-eyebrow">Evidence focus</p><h3>Select an entity</h3><p>The same native relations remain available as text beneath the visual field.</p></>}</aside></div>}
              </WorkflowSection>

              <WorkCollaboration kind="review" id={item.id} />

              <WorkflowSection eyebrow="Audit trail" title="Decision history">
                {item.events.length ? <ol className="workflow-list">{[...item.events].reverse().map((event) => <li key={event.id} className="workflow-list-row"><span className="workflow-update-dot" aria-hidden="true" /><span className="workflow-list-row-main"><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span><span className="font-mono text-[10px] text-slate">{timestamp(event.at)}</span></li>)}</ol> : <StateFrame kind="empty" title="No review events yet" />}
              </WorkflowSection>
            </div>

            <ResponsibilityDock
              responsibility={terminal ? "The review is complete." : !item.assignee ? "Assign an owner before deciding." : "Resolve the Developer’s request or open a case."}
              why={terminal ? "The recorded result is visible to the Developer and remains in the audit trail." : "Use the submitted evidence. Durable policy exceptions follow the separate CISO approval workflow."}
              deadline={item.sla_due_at ? timestamp(item.sla_due_at) : undefined}
              overdue={item.overdue}
            >
              <ConflictRecovery error={mutationError} onReload={reload} />
              {!terminal && canWriteReview ? (
                <details className="workflow-action-disclosure" open={!item.assignee}>
                  <summary>Owner and due time</summary>
                  <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); const due = String(form.get("sla_due_at") ?? ""); assign.mutate({ expected_version: item.version_counter, assignee: String(form.get("assignee") ?? "").trim(), assignee_name: String(form.get("assignee_name") ?? "").trim(), sla_due_at: due ? dateInputToEpoch(due) : null }); }}>
                    <Field label="Owner email"><TextInput name="assignee" type="email" required defaultValue={item.assignee ?? ""} /></Field>
                    <Field label="Owner name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
                    <Field label="Due date"><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
                    <button className="workflow-primary-action" type="submit" disabled={assign.isPending}>{assign.isPending ? "Saving…" : "Save owner"}</button>
                  </form>
                </details>
              ) : null}

              {!terminal && canWriteReview ? (
                <details className="workflow-action-disclosure" open={Boolean(item.assignee)}>
                  <summary>Return a decision</summary>
                  <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); decide.mutate({ expected_version: item.version_counter, decision, rationale: String(form.get("rationale") ?? "").trim(), recommended_version: decision === "request_changes" ? String(form.get("recommended_version") ?? "").trim() : null, expires_at: null }); }}>
                    <Field label="Decision"><Select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>{decisions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
                    {decision === "request_changes" ? <Field label="Recommended version"><TextInput name="recommended_version" defaultValue={item.advisories.flatMap((advisory) => advisory.fixed_versions)[0] ?? ""} required placeholder="Safe version" /></Field> : null}
                    <Field label="Message to Developer" hint="Use plain language. Explain the next action and why."><TextArea name="rationale" required maxLength={4096} /></Field>
                    <button className="workflow-primary-action" type="submit" disabled={decide.isPending}>{decide.isPending ? "Recording…" : "Record decision"}</button>
                  </form>
                </details>
              ) : null}

              {!terminal && canWriteCase ? (
                <details className="workflow-action-disclosure">
                  <summary>{item.escalated_case_id || caseId ? "Linked investigation" : "Create a tracked case"}</summary>
                  {item.escalated_case_id || caseId ? <Link to="/analyst/cases/$caseId" params={{ caseId: item.escalated_case_id ?? caseId! }} className="workflow-primary-action">Open linked case</Link> : <form onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); escalate.mutate({ expected_version: item.version_counter, title: String(form.get("title") ?? "").trim(), rationale: String(form.get("rationale") ?? "").trim(), assignee: item.assignee, assignee_name: item.assignee_name, sla_due_at: item.sla_due_at }); }}>
                    <Field label="Case title"><TextInput name="title" required defaultValue={`${item.package} security investigation`} /></Field>
                    <Field label="Why a case is needed"><TextArea name="rationale" required maxLength={4096} defaultValue={selectedNode ? `Investigate ${selectedNode.label} (${selectedNode.id}) and its relationship to ${item.package}.` : "The submitted evidence needs a tracked investigation."} /></Field>
                    <button className="workflow-primary-action" type="submit" disabled={escalate.isPending}>{escalate.isPending ? "Creating…" : "Create case"}</button>
                  </form>}
                </details>
              ) : null}

              {!canWriteReview && !canWriteCase ? <p className="text-xs leading-relaxed text-slate">Your current capabilities allow evidence review but no workflow changes.</p> : null}
              {!isVersionConflict(mutationError) ? <MutationMessage error={mutationError} success={success} /> : success ? <MutationMessage error={null} success={success} /> : null}
              <div className="grid gap-2">
                <IntegrityRef label="Review" value={item.id} />
                <IntegrityRef label="Evidence root" value={item.evidence_root_ulid} />
                <IntegrityRef label="Evidence digest" value={item.evidence_digest} />
              </div>
            </ResponsibilityDock>
          </div>
        )}
      </div>
    </main>
  );
}
