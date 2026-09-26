import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ConflictRecovery, ResponsibilityDock, StateFrame, WorkflowHero, WorkflowSection } from "../components/WorkflowVisual";
import { Select, SeverityBadge, StatusBadge, TextInput, dateInputToEpoch } from "../components/WorkflowUI";
import { api, type BulkWorkReceipt, type WorkKind, type WorkflowSeverity } from "../lib/api";
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

function selectedKey(kind: WorkKind, id: string): string {
  return `${kind}:${id}`;
}

export function AnalystOperations() {
  const { me, hasCapability } = useIdentity();
  const mySubject = me?.subject ?? "";
  const canAssign = hasCapability("review.write") || hasCapability("case.write");
  const client = useQueryClient();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [viewName, setViewName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkOwner, setBulkOwner] = useState("");
  const [bulkOwnerName, setBulkOwnerName] = useState("");
  const [bulkDue, setBulkDue] = useState("");
  const [bulkResult, setBulkResult] = useState<BulkWorkReceipt | null>(null);
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
      items: (queue.data?.items ?? []).filter((item) => selected.has(selectedKey(item.kind, item.id))).map((item) => ({
        kind: item.kind,
        id: item.id,
        expected_version: item.version,
      })),
      assignee: bulkOwner.trim(),
      assignee_name: bulkOwnerName.trim(),
      sla_due_at: bulkDue ? dateInputToEpoch(bulkDue) : null,
    }),
    onSuccess: (receipt) => {
      setBulkResult(receipt);
      setSelected(new Set(receipt.results.filter((result) => !result.ok).map((result) => selectedKey(result.kind, result.id))));
      void client.invalidateQueries({ queryKey: ["work-queue"] });
      void client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  const repositories = useMemo(() => Array.from(new Set(
    (queue.data?.items ?? []).map((item) => item.repository_name).filter((value): value is string => Boolean(value)),
  )).sort(), [queue.data]);

  const set = (key: keyof Filters, value: string) => {
    setBulkResult(null);
    setFilters((current) => ({ ...current, [key]: value || undefined }));
  };

  const counts = queue.data?.counts ?? {};
  const pending = queue.data?.items[0];

  return (
    <main className="workflow-page">
      <PageHeader section="Analyst" title="Security operations" meta={<a href="/analyst/activity" className="text-accent hover:underline">Search activity</a>} />
      <div className="workflow-scroll">
        <div className="workflow-layout">
          <div className="workflow-main">
            <WorkflowHero
              eyebrow="Analyst operations"
              title={`${counts.all ?? 0} open item${(counts.all ?? 0) === 1 ? "" : "s"}`}
              description="One server-ranked queue for developer reviews and tracked investigations. Priority, ownership, and due state come from the control plane."
              tone={(counts.overdue ?? 0) ? "danger" : (counts.unassigned ?? 0) ? "warning" : "accent"}
              meta={<><span>{counts.reviews ?? 0} reviews</span><span>{counts.cases ?? 0} cases</span><span>{notifications.data?.unread ?? 0} unread updates</span></>}
            />

            <div className="workflow-kpis" aria-label="Operations summary">
              <div className="workflow-kpi"><strong>{counts.all ?? 0}</strong><span>Open work</span></div>
              <div className={(counts.unassigned ?? 0) ? "workflow-kpi is-warning" : "workflow-kpi is-success"}><strong>{counts.unassigned ?? 0}</strong><span>Unassigned</span></div>
              <div className={(counts.overdue ?? 0) ? "workflow-kpi is-risk" : "workflow-kpi is-success"}><strong>{counts.overdue ?? 0}</strong><span>Overdue</span></div>
              <div className="workflow-kpi"><strong>{notifications.data?.unread ?? 0}</strong><span>New updates</span></div>
            </div>

            <WorkflowSection className="flush" eyebrow="Priority queue" title="Work requiring attention" description="Open a record for evidence, collaboration, and allowed next actions." action={<span className="font-mono text-[11px] text-slate">{queue.data?.total ?? 0} matching</span>}>
              <div className="operations-toolbar">
                <TextInput aria-label="Search work" placeholder="Search package, project, or person" value={filters.q ?? ""} onChange={(event) => set("q", event.target.value)} />
                <div className="operations-quick-filters" aria-label="Quick filters">
                  <button type="button" disabled={!mySubject} aria-pressed={filters.assignee === mySubject} onClick={() => setFilters((current) => ({ ...current, assignee: mySubject }))}>Mine</button>
                  <button type="button" aria-pressed={filters.assignee === "__unassigned__"} onClick={() => setFilters((current) => ({ ...current, assignee: "__unassigned__" }))}>Unassigned</button>
                  <button type="button" onClick={() => setFilters(EMPTY)}>Clear</button>
                </div>
                <details className="operations-filter-drawer">
                  <summary>More filters and views</summary>
                  <div className="operations-filter-grid">
                    <Select aria-label="Work type" value={filters.kind ?? ""} onChange={(event) => set("kind", event.target.value)}><option value="">All work</option><option value="review">Reviews</option><option value="case">Cases</option></Select>
                    <Select aria-label="Severity" value={filters.severity ?? ""} onChange={(event) => set("severity", event.target.value)}><option value="">All severity</option>{["critical", "high", "medium", "low", "unknown"].map((value) => <option key={value} value={value}>{value}</option>)}</Select>
                    <Select aria-label="Repository" value={filters.repository ?? ""} onChange={(event) => set("repository", event.target.value)}><option value="">All projects</option>{repositories.map((value) => <option key={value} value={value}>{value}</option>)}</Select>
                    <Select aria-label="Owner" value={filters.assignee ?? ""} onChange={(event) => set("assignee", event.target.value)}><option value="">Any owner</option>{mySubject ? <option value={mySubject}>Assigned to me</option> : null}<option value="__unassigned__">Unassigned</option></Select>
                    <div className="operations-save-view"><TextInput aria-label="Saved view name" value={viewName} onChange={(event) => setViewName(event.target.value)} placeholder="View name" /><button type="button" disabled={!viewName.trim() || saveView.isPending} onClick={() => saveView.mutate()}>{saveView.isPending ? "Saving…" : "Save view"}</button></div>
                    {views.data?.length ? <div className="operations-saved-views">{views.data.map((view) => <button key={view.id} type="button" onClick={() => setFilters(view.filters as Filters)}>{view.name}</button>)}</div> : null}
                  </div>
                </details>
              </div>

              {queue.isPending ? <StateFrame kind="loading" title="Prioritizing work" detail="Reading reviews and cases from the control plane." /> : queue.isError ? <StateFrame kind="error" title="The work queue could not be loaded" detail={queue.error instanceof Error ? queue.error.message : "Try again when the service is available."} action={<button type="button" className="workflow-secondary-action" onClick={() => void queue.refetch()}>Try again</button>} /> : queue.data.items.length ? (
                <ol className="operations-work-list">
                  {queue.data.items.map((item) => {
                    const key = selectedKey(item.kind, item.id);
                    return (
                      <li key={key} className="operations-work-item">
                        {canAssign ? <input type="checkbox" aria-label={`Select ${item.title}`} checked={selected.has(key)} onChange={(event) => { setBulkResult(null); setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(key); else next.delete(key); return next; }); }} /> : null}
                        <a href={item.route} className="operations-work-link">
                          <span className={`operations-priority ${item.overdue ? "is-overdue" : ""}`}><strong>{item.priority}</strong><small>priority</small></span>
                          <span className="operations-work-copy"><span><SeverityBadge severity={item.severity} /><StatusBadge status={item.state} /></span><strong>{item.title}</strong><small>{item.kind} · {item.subtitle}</small><small>{item.repository_name ?? "No repository"} · {item.developer_name ?? `Updated ${timestamp(item.updated_at)}`}</small></span>
                          <span className="operations-work-owner"><strong>{item.assignee_name ?? "Unassigned"}</strong><small className={item.overdue ? "text-risk" : ""}>{item.sla_due_at ? `${item.overdue ? "Overdue" : "Due"} ${timestamp(item.sla_due_at)}` : "No due time"}</small><small>{item.priority_reasons.join(" · ")}</small></span>
                        </a>
                      </li>
                    );
                  })}
                </ol>
              ) : <StateFrame kind="empty" title="Nothing matches this view" detail="Clear a filter or choose another saved view." />}
            </WorkflowSection>

            <WorkflowSection eyebrow="Activity" title="Recent updates" description="Assignments, reminders, mentions, and decisions." action={<span className="rounded-full bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">{notifications.data?.unread ?? 0} new</span>}>
              {notifications.isPending ? <p className="text-sm text-slate">Loading updates…</p> : notifications.isError ? <p role="alert" className="text-sm text-risk">Updates are unavailable.</p> : notifications.data.notifications.length ? <ol className="workflow-list">{notifications.data.notifications.slice(0, 8).map((item) => <li key={item.id} className={item.read ? "opacity-60" : ""}><a href={item.route} onClick={() => !item.read && read.mutate(item.id)} className="workflow-list-row"><span className="workflow-update-dot" aria-hidden="true" /><span className="workflow-list-row-main"><strong>{item.title}</strong><small>{item.message}</small></span><span className="font-mono text-[10px] text-slate">{timestamp(item.created_at)}</span></a></li>)}</ol> : <p className="text-sm text-slate">No new updates.</p>}
            </WorkflowSection>
          </div>

          <ResponsibilityDock
            responsibility={selected.size ? `Assign ${selected.size} selected item${selected.size === 1 ? "" : "s"}.` : pending ? "Open the highest-priority record." : "Monitor the queue."}
            why={selected.size ? "The server checks each selected record version independently. Partial failures remain selected." : pending ? "Review evidence and choose only an action allowed by the record state and your capabilities." : "New work will be ranked here as it arrives."}
            deadline={pending?.sla_due_at ? timestamp(pending.sla_due_at) : undefined}
            overdue={pending?.overdue}
          >
            {selected.size && canAssign ? (
              <form onSubmit={(event) => { event.preventDefault(); setBulkResult(null); bulkAssign.mutate(); }}>
                <TextInput aria-label="Bulk owner email" type="email" required value={bulkOwner} onChange={(event) => setBulkOwner(event.target.value)} placeholder="Owner email" />
                <TextInput aria-label="Bulk owner name" required value={bulkOwnerName} onChange={(event) => setBulkOwnerName(event.target.value)} placeholder="Owner name" />
                <TextInput aria-label="Bulk due date" type="date" value={bulkDue} onChange={(event) => setBulkDue(event.target.value)} />
                <button type="submit" className="workflow-primary-action" disabled={!bulkOwner.trim() || !bulkOwnerName.trim() || bulkAssign.isPending}>{bulkAssign.isPending ? "Assigning…" : "Assign selected"}</button>
                {bulkAssign.error ? <p role="alert" className="text-xs text-risk">{bulkAssign.error instanceof Error ? bulkAssign.error.message : "Assignment failed."}</p> : null}
              </form>
            ) : pending ? <a href={pending.route} className="workflow-primary-action">Open {pending.kind}</a> : null}
            {bulkResult ? <div className={bulkResult.failed ? "workflow-receipt is-warning" : "workflow-receipt is-success"} role="status"><strong>{bulkResult.succeeded} updated</strong><span>{bulkResult.failed ? `${bulkResult.failed} need to be refreshed and tried again.` : "All selected records were assigned."}</span></div> : null}
            <ConflictRecovery error={bulkAssign.error} onReload={() => { bulkAssign.reset(); void queue.refetch(); }} />
            {!canAssign ? <p className="text-xs leading-relaxed text-slate">Your current capabilities allow queue review but not assignment.</p> : null}
          </ResponsibilityDock>
        </div>
      </div>
    </main>
  );
}
