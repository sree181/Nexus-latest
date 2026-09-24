import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Select, TextInput } from "../components/WorkflowUI";
import { api } from "../lib/api";
import { timestamp } from "../lib/format";

export function AnalystActivity() {
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("");
  const activity = useQuery({
    queryKey: ["work-activity", query, action],
    queryFn: () => api.workActivity({ q: query || undefined, action: action || undefined }),
    refetchInterval: 15_000,
  });
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst" title="Work history" meta={<a href="/analyst/queue" className="text-accent hover:underline">Back to operations</a>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
          <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="font-serif text-lg font-semibold text-ink">Search every handoff</h2><p className="mt-1 text-sm text-slate">Decisions, assignments, state changes, and team notes stay attached to their work.</p></div><span className="font-mono text-xs text-slate">{activity.data?.total ?? 0} events</span></div>
          <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_240px]">
            <TextInput aria-label="Search activity" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search people, actions, notes…" />
            <Select aria-label="Activity type" value={action} onChange={(event) => setAction(event.target.value)}><option value="">All activity</option><option value="work.comment">Team notes</option><option value="review.assigned">Assignments</option><option value="review.request_changes">Change requests</option><option value="review.escalated_to_case">Case escalations</option><option value="case.transition">Case changes</option></Select>
          </div>
          <Async query={activity} label="Searching work history…">
            {(data) => data.items.length ? <ol className="mt-5 divide-y divide-line">{data.items.map((item) => <li key={`${item.resource_kind}:${item.id}`} className="grid gap-2 py-4 sm:grid-cols-[140px_minmax(0,1fr)_160px]"><div><span className="rounded-full border border-line px-2 py-1 text-xs capitalize text-slate">{item.resource_kind}</span><p className="mt-2 font-mono text-[11px] text-slate">{item.action.replaceAll(".", " ")}</p></div><div><a href={item.route} className="font-medium text-accent hover:underline">{item.resource_id}</a><p className="mt-1 text-sm leading-relaxed text-ink">{item.message}</p></div><div className="text-right text-xs text-slate"><p>{item.actor_name}</p><p className="mt-1 font-mono">{timestamp(item.at)}</p></div></li>)}</ol> : <p className="mt-5 rounded-xl border border-dashed border-line-2 px-4 py-6 text-center text-sm text-slate">No activity matches this search.</p>}
          </Async>
        </section>
      </div>
    </main>
  );
}
