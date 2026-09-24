import { useState } from "react";
import type { GraphNode } from "@meshagent/graph";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { WorkCollaboration } from "../components/WorkCollaboration";
import { Field, MutationMessage, Select, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api } from "../lib/api";
import { timestamp } from "../lib/format";

const decisions = [
  ["request_changes", "Ask for a safer version"],
  ["approve_exception", "Allow temporarily"],
  ["false_positive", "Mark not applicable"],
  ["reject", "Do not approve"],
  ["escalate", "Send to CISO"],
] as const;

export function AnalystReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  const client = useQueryClient();
  const [decision, setDecision] = useState<(typeof decisions)[number][0]>("request_changes");
  const [success, setSuccess] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const review = useQuery({ queryKey: ["review", requestId], queryFn: () => api.review(requestId), refetchInterval: 10_000 });
  const graph = useQuery({ queryKey: ["review", requestId, "graph"], queryFn: () => api.reviewGraph(requestId), refetchInterval: 15_000 });
  const refresh = (value?: unknown) => {
    if (value) client.setQueryData(["review", requestId], value);
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
    onSuccess: (value) => { setCaseId(value.id); refresh(); setSuccess("A tracked case was created with this evidence."); },
  });
  const decide = useMutation({
    mutationFn: (input: Parameters<typeof api.decideReview>[1]) => api.decideReview(requestId, input),
    onSuccess: (value) => { refresh(value); setSuccess("Decision recorded and returned to the Developer."); },
  });
  const item = review.data;
  const terminal = item ? ["verified", "false_positive", "not_approved"].includes(item.state) : false;

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst / Operations" title="Developer review" meta={<Link to="/analyst/queue" className="text-accent hover:underline">Back to work</Link>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        {review.isPending ? <p className="text-sm text-slate">Loading review…</p> : review.isError || !item ? <p role="alert" className="text-sm text-risk">Could not open this review.</p> : <>
          <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
            <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-[11px] uppercase tracking-widest text-slate">{item.repository_name} · {item.ecosystem}</p><h2 className="mt-1 font-serif text-2xl font-semibold text-ink">{item.package}@{item.version || "unpinned"}</h2><p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate">{item.rationale}</p></div><div className="flex flex-wrap gap-2"><ReviewStateBadge state={item.state} />{item.overdue ? <span className="rounded-full bg-risk-soft px-2.5 py-1 text-xs font-medium text-risk">Overdue</span> : null}</div></div>
            <div className="review-context-grid mt-5"><span><small>Developer</small><strong>{item.owner_name}</strong></span><span><small>Owner</small><strong>{item.assignee_name ?? "Unassigned"}</strong></span><span><small>Due</small><strong>{item.sla_due_at ? timestamp(item.sla_due_at) : "Not set"}</strong></span><span><small>Linked code</small><strong>{item.code_entities.length}</strong></span></div>
            {item.escalated_case_id ? <p className="mt-4 text-sm text-slate">Tracked as <Link to="/analyst/cases/$caseId" params={{ caseId: item.escalated_case_id }} className="font-medium text-accent hover:underline">case {item.escalated_case_id}</Link>.</p> : null}
          </section>

          {!terminal ? <section className="grid gap-5 xl:grid-cols-2">
            <div className="rounded-2xl border border-line bg-surface p-5">
              <h2 className="font-serif text-lg font-semibold text-ink">Owner and due time</h2>
              <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); const due = String(form.get("sla_due_at") ?? ""); assign.mutate({ expected_version: item.version_counter, assignee: String(form.get("assignee") ?? "").trim(), assignee_name: String(form.get("assignee_name") ?? "").trim(), sla_due_at: due ? dateInputToEpoch(due) : null }); }}>
                <Field label="Owner email"><TextInput name="assignee" type="email" required defaultValue={item.assignee ?? ""} placeholder="priya@example.com" /></Field>
                <Field label="Owner name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
                <Field label="Due date"><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
                <div className="flex items-end"><button className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50" type="submit" disabled={assign.isPending}>{assign.isPending ? "Saving…" : "Save owner"}</button></div>
              </form>
            </div>
            <div className="rounded-2xl border border-line bg-surface p-5">
              <h2 className="font-serif text-lg font-semibold text-ink">Create a tracked case</h2>
              <p className="mt-1 text-sm text-slate">Use this when the review needs investigation beyond a package answer.</p>
              {item.escalated_case_id || caseId ? <p className="mt-4 rounded-xl border border-ok bg-ok-soft px-4 py-3 text-sm text-ok"><Link to="/analyst/cases/$caseId" params={{ caseId: item.escalated_case_id ?? caseId! }} className="font-medium underline">Open the linked case</Link></p> : <form className="mt-4 grid gap-3" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); escalate.mutate({ expected_version: item.version_counter, title: String(form.get("title") ?? "").trim(), rationale: String(form.get("rationale") ?? "").trim(), assignee: item.assignee, assignee_name: item.assignee_name, sla_due_at: item.sla_due_at }); }}>
                <Field label="Case title"><TextInput name="title" required defaultValue={`${item.package} security investigation`} /></Field>
                <Field label="Why a case is needed"><TextArea name="rationale" required maxLength={4096} defaultValue={selectedNode ? `Investigate ${selectedNode.label} and its relationship to ${item.package}.` : "The submitted evidence needs a tracked investigation."} /></Field>
                <button className="w-fit rounded-lg border border-accent px-4 py-2 text-sm font-medium text-accent hover:bg-accent-soft" type="submit" disabled={escalate.isPending}>{escalate.isPending ? "Creating…" : "Create case"}</button>
              </form>}
            </div>
          </section> : null}

          {item.advisories.length ? <section className="rounded-2xl border border-line bg-surface p-5"><div className="mb-3 flex items-center justify-between"><h2 className="font-serif text-lg font-semibold text-ink">Published advisories</h2><span className="font-mono text-xs text-slate">{item.advisories.length}</span></div>{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</section> : <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="font-medium text-amber-950">The advisory check was incomplete</h2><p className="mt-1 text-sm text-amber-900">{item.reasons.join(" ")}</p></section>}

          <section className="rounded-2xl border border-line bg-surface p-5"><div className="mb-3 flex items-center justify-between"><div><h2 className="font-serif text-lg font-semibold text-ink">Evidence relationship map</h2><p className="mt-1 text-sm text-slate">Select a node to focus the next note or case.</p></div><span className="font-mono text-xs text-slate">submitted snapshot</span></div>{graph.data ? <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]"><div><ReviewEvidenceGraph graph={graph.data.graph} label="Analyst review evidence graph" selectedId={selectedNode?.id} onSelect={setSelectedNode} /><p className="mt-3 text-xs leading-relaxed text-slate">{graph.data.note}</p></div><aside className="rounded-xl border border-line-2 bg-surface-2 p-4">{selectedNode ? <><span className="text-xs uppercase tracking-wider text-slate">{selectedNode.kind}</span><h3 className="mt-1 font-medium text-ink">{selectedNode.label}</h3><p className="mt-2 break-all font-mono text-[11px] text-slate">{selectedNode.id}</p><p className="mt-4 text-xs text-slate">This exact evidence node will be referenced in the case rationale when you create a case above.</p></> : <p className="text-sm text-slate">Select a package, advisory, code item, contributor, or decision.</p>}</aside></div> : <p className="text-sm text-slate">Loading map…</p>}</section>

          {!terminal ? <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Record a decision</h2><p className="mt-1 text-sm text-slate">Answer the Developer directly. MeshAgent keeps the decision with the submitted evidence.</p><form className="mt-5 grid gap-4 lg:grid-cols-2" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); const expires = String(form.get("expires_at") ?? ""); decide.mutate({ expected_version: item.version_counter, decision, rationale: String(form.get("rationale") ?? "").trim(), recommended_version: decision === "request_changes" ? String(form.get("recommended_version") ?? "").trim() : null, expires_at: decision === "approve_exception" && expires ? Math.floor(new Date(expires).getTime() / 1000) : null }); }}>
            <Field label="Decision"><Select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>{decisions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
            {decision === "request_changes" ? <Field label="Recommended version"><TextInput name="recommended_version" defaultValue={item.advisories.flatMap((advisory) => advisory.fixed_versions)[0] ?? ""} required placeholder="Safe version" /></Field> : null}
            {decision === "approve_exception" ? <Field label="Expires"><TextInput type="datetime-local" name="expires_at" required /></Field> : null}
            <div className="lg:col-span-2"><Field label="Message to Developer" hint="Use plain language. Explain the next action and why."><TextArea name="rationale" required maxLength={4096} /></Field></div>
            <div className="flex items-center gap-3 lg:col-span-2"><button className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50" type="submit" disabled={decide.isPending}>{decide.isPending ? "Saving…" : "Record decision"}</button><span className="text-xs text-slate">Version {item.version_counter}</span></div>
          </form></section> : null}

          <MutationMessage error={assign.error ?? escalate.error ?? decide.error} success={success} />
          <WorkCollaboration kind="review" id={item.id} />
          <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Decision history</h2><div className="mt-3">{item.events.map((event) => <div key={event.id} className="border-b border-line py-3 last:border-0"><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-ink">{event.action.replace("review.", "").replaceAll("_", " ")}</strong><span className="text-xs text-slate">{event.actor_name} · {timestamp(event.at)}</span></div><p className="mt-1 text-sm text-slate">{event.rationale}</p></div>)}</div></section>
        </>}
      </div>
    </main>
  );
}
