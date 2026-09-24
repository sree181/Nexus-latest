import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Metric, Select, SeverityBadge, StatusBadge, TextInput, dateInputToEpoch } from "../components/WorkflowUI";
import { api, type WorkKind, type WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

type Filters = {
  kind?: WorkKind;
  state?: string;
  assignee?: string;
  repository?: string;
  severity?: WorkflowSeverity;
  q?: string;
};

const EMPTY: Filters = {};

export function AnalystOperations() {
  const { me } = useIdentity();
  const mySubject = me?.subject ?? "";
  const client = useQueryClient();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [viewName, setViewName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkOwner, setBulkOwner] = useState("");
  const [bulkOwnerName, setBulkOwnerName] = useState("");
  const [bulkDue, setBulkDue] = useState("");
  const queue = useQuery({
    queryKey: ["work-queue", filters],
    queryFn: () => api.workQueue(filters),
    refetchInterval: 10_000,
  });
  const views = useQuery({ queryKey: ["work-views"], queryFn: api.savedWorkViews });
  const notifications = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 15_000 });
  const saveView = useMutation({
    mutationFn: () => api.saveWorkView({
      name: viewName.trim(),
      filters: Object.fromEntries(Object.entries(filters).filter(([, value]) => Boolean(value))) as Record<string, string>,
    }),
    onSuccess: () => {
      setViewName("");
      void client.invalidateQueries({ queryKey: ["work-views"] });
    },
  });
  const read = useMutation({
    mutationFn: api.readNotification,
    onSuccess: () => void client.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const bulkAssign = useMutation({
    mutationFn: () => api.bulkAssignWork({
      items: (queue.data?.items ?? []).filter((item) => selected.has(`${item.kind}:${item.id}`)).map((item) => ({
        kind: item.kind,
        id: item.id,
        expected_version: item.version,
      })),
      assignee: bulkOwner.trim(),
      assignee_name: bulkOwnerName.trim(),
      sla_due_at: bulkDue ? dateInputToEpoch(bulkDue) : null,
    }),
    onSuccess: () => {
      setSelected(new Set());
      void client.invalidateQueries({ queryKey: ["work-queue"] });
      void client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  const repositories = useMemo(() => Array.from(new Set(
    (queue.data?.items ?? []).map((item) => item.repository_name).filter((value): value is string => Boolean(value)),
  )).sort(), [queue.data]);

  const set = (key: keyof Filters, value: string) => setFilters((current) => ({
    ...current,
    [key]: value || undefined,
  }));

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst" title="Security operations" meta={<a href="/analyst/activity" className="text-accent hover:underline">Search activity</a>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        <Async query={queue} label="Prioritizing work…">
          {(data) => <>
            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <Metric label="Open work" value={data.counts.all ?? 0} detail="Reviews and cases" />
              <Metric label="Unassigned" value={data.counts.unassigned ?? 0} detail="Needs an owner" tone={(data.counts.unassigned ?? 0) ? "warn" : "ok"} />
              <Metric label="Overdue" value={data.counts.overdue ?? 0} detail="Past the promised time" tone={(data.counts.overdue ?? 0) ? "risk" : "ok"} />
              <Metric label="Reviews" value={data.counts.reviews ?? 0} detail="Developer requests" />
              <Metric label="Cases" value={data.counts.cases ?? 0} detail="Tracked investigations" />
            </section>

            <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="font-serif text-lg font-semibold text-ink">One queue</h2>
                  <p className="mt-1 text-sm text-slate">Developer questions and investigations, ranked by impact, age, ownership, and due time.</p>
                </div>
                <span className="font-mono text-xs text-slate">{data.total} matching</span>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                <TextInput aria-label="Search work" placeholder="Search package, project, person…" value={filters.q ?? ""} onChange={(event) => set("q", event.target.value)} className="xl:col-span-2" />
                <Select aria-label="Work type" value={filters.kind ?? ""} onChange={(event) => set("kind", event.target.value)}><option value="">All work</option><option value="review">Reviews</option><option value="case">Cases</option></Select>
                <Select aria-label="Severity" value={filters.severity ?? ""} onChange={(event) => set("severity", event.target.value)}><option value="">All severity</option>{["critical", "high", "medium", "low", "unknown"].map((value) => <option key={value} value={value}>{value}</option>)}</Select>
                <Select aria-label="Repository" value={filters.repository ?? ""} onChange={(event) => set("repository", event.target.value)}><option value="">All projects</option>{repositories.map((value) => <option key={value} value={value}>{value}</option>)}</Select>
                <Select aria-label="Owner" value={filters.assignee ?? ""} onChange={(event) => set("assignee", event.target.value)}><option value="">Any owner</option>{mySubject ? <option value={mySubject}>Assigned to me</option> : null}</Select>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button type="button" disabled={!mySubject} onClick={() => setFilters({ ...filters, assignee: mySubject })} className="rounded-full border border-line px-3 py-1.5 text-xs text-ink hover:bg-surface-2 disabled:opacity-50">Mine</button>
                <button type="button" onClick={() => setFilters({ ...filters, assignee: "__unassigned__" })} className="rounded-full border border-line px-3 py-1.5 text-xs text-ink hover:bg-surface-2">Unassigned</button>
                <button type="button" onClick={() => setFilters(EMPTY)} className="rounded-full border border-line px-3 py-1.5 text-xs text-slate hover:bg-surface-2">Clear</button>
                <span className="h-5 w-px bg-line" />
                <TextInput aria-label="Saved view name" value={viewName} onChange={(event) => setViewName(event.target.value)} placeholder="View name" className="max-w-44 py-1.5" />
                <button type="button" disabled={!viewName.trim() || saveView.isPending} onClick={() => saveView.mutate()} className="rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">Save view</button>
                {views.data?.map((view) => <button key={view.id} type="button" onClick={() => setFilters(view.filters as Filters)} className="rounded-full border border-accent/30 bg-accent-soft px-3 py-1.5 text-xs text-accent">{view.name}</button>)}
              </div>

              {selected.size ? <div className="mt-4 grid gap-3 rounded-xl border border-accent/30 bg-accent-soft p-4 sm:grid-cols-2 xl:grid-cols-[auto_1fr_1fr_180px_auto] xl:items-end">
                <div><strong className="text-sm text-ink">{selected.size} selected</strong><p className="text-xs text-slate">Assign together</p></div>
                <TextInput aria-label="Bulk owner email" type="email" value={bulkOwner} onChange={(event) => setBulkOwner(event.target.value)} placeholder="Owner email" />
                <TextInput aria-label="Bulk owner name" value={bulkOwnerName} onChange={(event) => setBulkOwnerName(event.target.value)} placeholder="Owner name" />
                <TextInput aria-label="Bulk due date" type="date" value={bulkDue} onChange={(event) => setBulkDue(event.target.value)} />
                <button type="button" disabled={!bulkOwner.trim() || !bulkOwnerName.trim() || bulkAssign.isPending} onClick={() => bulkAssign.mutate()} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{bulkAssign.isPending ? "Assigning…" : "Assign"}</button>
              </div> : null}

              {data.items.length ? <div className="responsive-table-wrap mt-5"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line"><th scope="col" className="pb-2 pr-3"><span className="sr-only">Select</span></th>{["Priority", "Work", "Context", "Owner / SLA", "State"].map((heading) => <th key={heading} scope="col" className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}</tr></thead><tbody>{data.items.map((item) => { const key = `${item.kind}:${item.id}`; return <tr key={key} className="border-b border-line align-top">
                <td className="py-3 pr-3"><input type="checkbox" aria-label={`Select ${item.title}`} checked={selected.has(key)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(key); else next.delete(key); return next; })} /></td>
                <td className="py-3 pr-4"><span className="font-serif text-xl text-ink">{item.priority}</span><p className="max-w-44 text-[11px] leading-snug text-slate">{item.priority_reasons.join(" · ")}</p></td>
                <td className="py-3 pr-4"><a href={item.route} className="font-medium text-accent underline-offset-2 hover:underline">{item.title}</a><p className="mt-1 text-xs capitalize text-slate">{item.kind} · {item.subtitle}</p></td>
                <td className="py-3 pr-4"><div className="flex flex-wrap gap-2"><SeverityBadge severity={item.severity} />{item.repository_name ? <span className="text-xs text-slate">{item.repository_name}</span> : null}</div><p className="mt-1 text-xs text-slate">{item.developer_name ?? `Updated ${timestamp(item.updated_at)}`}</p></td>
                <td className="py-3 pr-4 text-sm text-ink">{item.assignee_name ?? "Unassigned"}<p className={item.overdue ? "mt-1 text-xs text-risk" : "mt-1 text-xs text-slate"}>{item.sla_due_at ? `${item.overdue ? "Overdue" : "Due"} ${timestamp(item.sla_due_at)}` : "No due time"}</p></td>
                <td className="py-3"><StatusBadge status={item.state} /></td>
              </tr>; })}</tbody></table></div> : <div className="mt-5 rounded-xl border border-dashed border-line-2 px-4 py-6 text-center"><p className="font-medium text-ink">Nothing matches</p><p className="mt-1 text-sm text-slate">Clear a filter or choose another saved view.</p></div>}
            </section>
          </>}
        </Async>

        <section className="rounded-2xl border border-line bg-surface p-5">
          <div className="flex items-center justify-between gap-3"><div><h2 className="font-serif text-lg font-semibold text-ink">Updates</h2><p className="mt-1 text-sm text-slate">Assignments, due-time reminders, mentions, and decisions.</p></div><span className="rounded-full bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">{notifications.data?.unread ?? 0} new</span></div>
          {notifications.isPending ? <p className="mt-4 text-sm text-slate">Loading updates…</p> : notifications.data?.notifications.length ? <ol className="mt-4 divide-y divide-line">{notifications.data.notifications.slice(0, 8).map((item) => <li key={item.id} className={`flex flex-wrap items-start justify-between gap-3 py-3 ${item.read ? "opacity-60" : ""}`}><a href={item.route} onClick={() => !item.read && read.mutate(item.id)} className="min-w-0 flex-1"><strong className="text-sm text-ink">{item.title}</strong><p className="mt-1 text-sm text-slate">{item.message}</p></a><span className="font-mono text-[11px] text-slate">{timestamp(item.created_at)}</span></li>)}</ol> : <p className="mt-4 text-sm text-slate">No new updates.</p>}
        </section>
      </div>
    </main>
  );
}
