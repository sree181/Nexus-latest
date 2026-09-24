import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { Field, MutationMessage, Select, TextArea, TextInput } from "../components/WorkflowUI";
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
  const review = useQuery({ queryKey: ["review", requestId], queryFn: () => api.review(requestId), refetchInterval: 10_000 });
  const graph = useQuery({ queryKey: ["review", requestId, "graph"], queryFn: () => api.reviewGraph(requestId), refetchInterval: 15_000 });
  const decide = useMutation({
    mutationFn: (input: Parameters<typeof api.decideReview>[1]) => api.decideReview(requestId, input),
    onSuccess: (value) => {
      client.setQueryData(["review", requestId], value);
      void client.invalidateQueries({ queryKey: ["reviews"] });
      setSuccess("Decision recorded and returned to the Developer.");
    },
  });
  const item = review.data;
  const terminal = item ? ["verified", "false_positive", "not_approved"].includes(item.state) : false;

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst" title="Developer review" meta={<Link to="/analyst/reviews" className="text-accent hover:underline">Back to queue</Link>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        {review.isPending ? <p className="text-sm text-slate">Loading review…</p> : review.isError || !item ? <p role="alert" className="text-sm text-risk">Could not open this review.</p> : <>
          <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
            <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-[11px] uppercase tracking-widest text-slate">{item.repository_name} · {item.ecosystem}</p><h2 className="mt-1 font-serif text-2xl font-semibold text-ink">{item.package}@{item.version || "unpinned"}</h2><p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate">{item.rationale}</p></div><ReviewStateBadge state={item.state} /></div>
            <div className="review-context-grid mt-5"><span><small>Developer</small><strong>{item.owner_name}</strong></span><span><small>Asked for</small><strong>{item.kind.replaceAll("_", " ")}</strong></span><span><small>Linked code</small><strong>{item.code_entities.length}</strong></span><span><small>Created</small><strong>{timestamp(item.created_at)}</strong></span></div>
          </section>

          {item.advisories.length ? <section className="rounded-2xl border border-line bg-surface p-5"><div className="mb-3 flex items-center justify-between"><h2 className="font-serif text-lg font-semibold text-ink">Published advisories</h2><span className="font-mono text-xs text-slate">{item.advisories.length}</span></div>{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</section> : <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="font-medium text-amber-950">The advisory check was incomplete</h2><p className="mt-1 text-sm text-amber-900">{item.reasons.join(" ")}</p></section>}

          <section className="rounded-2xl border border-line bg-surface p-5"><div className="mb-3 flex items-center justify-between"><h2 className="font-serif text-lg font-semibold text-ink">Evidence relationship map</h2><span className="font-mono text-xs text-slate">submitted snapshot</span></div>{graph.data ? <><ReviewEvidenceGraph graph={graph.data.graph} label="Analyst review evidence graph" /><p className="mt-3 text-xs leading-relaxed text-slate">{graph.data.note}</p></> : <p className="text-sm text-slate">Loading map…</p>}</section>

          {!terminal ? <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Record a decision</h2><p className="mt-1 text-sm text-slate">Answer the Developer's question directly. MeshAgent will keep this decision with the submitted evidence.</p><form className="mt-5 grid gap-4 lg:grid-cols-2" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); const expires = String(form.get("expires_at") ?? ""); decide.mutate({ expected_version: item.version_counter, decision, rationale: String(form.get("rationale") ?? "").trim(), recommended_version: decision === "request_changes" ? String(form.get("recommended_version") ?? "").trim() : null, expires_at: decision === "approve_exception" && expires ? Math.floor(new Date(expires).getTime() / 1000) : null }); }}>
            <Field label="Decision"><Select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}>{decisions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
            {decision === "request_changes" ? <Field label="Recommended version"><TextInput name="recommended_version" defaultValue={item.advisories.flatMap((advisory) => advisory.fixed_versions)[0] ?? ""} required placeholder="First safe version" /></Field> : null}
            {decision === "approve_exception" ? <Field label="Expires"><TextInput type="datetime-local" name="expires_at" required /></Field> : null}
            <div className="lg:col-span-2"><Field label="Message to Developer" hint="Use plain language. Explain the next action and why."><TextArea name="rationale" required maxLength={4096} /></Field></div>
            <div className="flex items-center gap-3 lg:col-span-2"><button className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50" type="submit" disabled={decide.isPending}>{decide.isPending ? "Saving…" : "Record decision"}</button><span className="text-xs text-slate">Version {item.version_counter}</span></div>
            <div className="lg:col-span-2"><MutationMessage error={decide.error} success={success} /></div>
          </form></section> : null}

          <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Decision history</h2><div className="mt-3">{item.events.map((event) => <div key={event.id} className="border-b border-line py-3 last:border-0"><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-ink">{event.action.replace("review.", "").replaceAll("_", " ")}</strong><span className="text-xs text-slate">{event.actor_name} · {timestamp(event.at)}</span></div><p className="mt-1 text-sm text-slate">{event.rationale}</p></div>)}</div></section>
        </>}
      </div>
    </main>
  );
}
