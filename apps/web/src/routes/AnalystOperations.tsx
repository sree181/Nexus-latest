import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ConflictRecovery, ResponsibilityDock, StateFrame } from "../components/WorkflowVisual";
import { Select, SeverityBadge, StatusBadge, TextInput, dateInputToEpoch } from "../components/WorkflowUI";
import { api, type BulkWorkReceipt, type WorkItem, type WorkKind, type WorkflowSeverity } from "../lib/api";
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

function dueLabel(item: WorkItem): string {
  if (!item.sla_due_at) return "No due time";
  return `${item.overdue ? "Overdue" : "Due"} ${timestamp(item.sla_due_at)}`;
}

export function AnalystOperations() {
  const { me, hasCapability } = useIdentity();
  const mySubject = me?.subject ?? "";
  const canAssign = hasCapability("review.write") || hasCapability("case.write");
  const client = useQueryClient();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [viewName, setViewName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [bulkOwner, setBulkOwner] = useState("");
  const [bulkOwnerName, setBulkOwnerName] = useState("");
  const [bulkDue, setBulkDue] = useState("");
  const [bulkResult, setBulkResult] = useState<BulkWorkReceipt | null>(null);
  const queue = useQuery({ queryKey: ["work-queue", filters], queryFn: () => api.workQueue(filters), refetchInterval: 10_000 });
  const views = useQuery({ queryKey: ["work-views"], queryFn: api.savedWorkViews });
  const notifications = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 15_000 });
  const saveView = useMutation({
    mutationFn: () => api.saveWorkView({ name: viewName.trim(), filters: Object.fromEntries(Object.entries(filters).filter(([, value]) => Boolean(value))) as Record<string, string> }),
    onSuccess: () => { setViewName(""); void client.invalidateQueries({ queryKey: ["work-views"] }); },
  });
  const read = useMutation({ mutationFn: api.readNotification, onSuccess: () => void client.invalidateQueries({ queryKey: ["notifications"] }) });
  const bulkAssign = useMutation({
    mutationFn: () => api.bulkAssignWork({
      items: (queue.data?.items ?? []).filter((item) => selected.has(selectedKey(item.kind, item.id))).map((item) => ({ kind: item.kind, id: item.id, expected_version: item.version })),
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
  const repositories = useMemo(() => Array.from(new Set((queue.data?.items ?? []).map((item) => item.repository_name).filter((value): value is string => Boolean(value)))).sort(), [queue.data]);
  const items = queue.data?.items ?? [];

  useEffect(() => {
    if (!items.length) { setFocusedKey(null); return; }
    if (!focusedKey || !items.some((item) => selectedKey(item.kind, item.id) === focusedKey)) setFocusedKey(selectedKey(items[0].kind, items[0].id));
  }, [focusedKey, items]);

  const focused = items.find((item) => selectedKey(item.kind, item.id) === focusedKey) ?? null;
  const set = (key: keyof Filters, value: string) => { setBulkResult(null); setFilters((current) => ({ ...current, [key]: value || undefined })); };
  const setOwnerView = (value?: string) => setFilters((current) => ({ ...current, assignee: value || undefined }));
  const counts = queue.data?.counts ?? {};

  return (
    <main className="workflow-page operations-page">
      <PageHeader section="Analyst" title="Security operations" meta={<a href="/analyst/activity" className="text-accent hover:underline">Work history</a>} />
      <div className="operations-workbench">
        <aside className="operations-queue-pane" aria-label="Security work queue">
          <header className="operations-queue-header">
            <div><p className="workflow-eyebrow">Security operations</p><h1>{counts.all ?? 0} open</h1></div>
            <span className={(counts.overdue ?? 0) ? "is-risk" : ""}>{counts.overdue ?? 0} overdue</span>
          </header>
          <div className="operations-owner-tabs" aria-label="Ownership view">
            <button type="button" disabled={!mySubject} aria-pressed={filters.assignee === mySubject} onClick={() => setOwnerView(mySubject)}>My work</button>
            <button type="button" aria-pressed={filters.assignee === "__unassigned__"} onClick={() => setOwnerView("__unassigned__")}>Unassigned</button>
            <button type="button" aria-pressed={!filters.assignee} onClick={() => setOwnerView()}>All work</button>
          </div>
          <div className="operations-queue-search">
            <TextInput aria-label="Search work" placeholder="Search work" value={filters.q ?? ""} onChange={(event) => set("q", event.target.value)} />
            <details className="operations-filter-drawer">
              <summary>More filters and saved views</summary>
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
          {selected.size ? <div className="operations-selection-bar"><strong>{selected.size} selected</strong><button type="button" onClick={() => setSelected(new Set())}>Clear</button></div> : null}
          <div className="operations-queue-scroll">
            {queue.isPending ? <StateFrame kind="loading" title="Prioritizing work" detail="Reading reviews and cases." /> : queue.isError ? <StateFrame kind="error" title="Queue unavailable" detail={queue.error instanceof Error ? queue.error.message : "Try again."} action={<button type="button" onClick={() => void queue.refetch()}>Try again</button>} /> : items.length ? (
              <ol className="operations-compact-list" aria-label="Ranked work">
                {items.map((item) => {
                  const key = selectedKey(item.kind, item.id);
                  return (
                    <li key={key} className={`${focusedKey === key ? "is-focused" : ""} ${item.overdue ? "is-overdue" : ""}`}>
                      {canAssign ? <input type="checkbox" aria-label={`Select ${item.title}`} checked={selected.has(key)} onChange={(event) => { setBulkResult(null); setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(key); else next.delete(key); return next; }); }} /> : null}
                      <button type="button" className="operations-row-focus" aria-pressed={focusedKey === key} onClick={() => setFocusedKey(key)}>
                        <span className="operations-row-top"><small>{item.kind} · {item.id}</small><SeverityBadge severity={item.severity} /></span>
                        <strong>{item.title}</strong>
                        <span className="operations-row-subtitle">{item.subtitle || item.repository_name || "No repository"}</span>
                        <span className="operations-row-bottom"><small>{item.assignee_name ?? "Unassigned"}</small><small className={item.overdue ? "text-risk" : ""}>{dueLabel(item)}</small></span>
                      </button>
                      <a href={item.route} className="operations-row-open">Open</a>
                    </li>
                  );
                })}
              </ol>
            ) : <StateFrame kind="empty" title="Nothing matches this view" detail="Clear a filter or choose another saved view." />}
          </div>
        </aside>

        <section className="operations-focus-pane" aria-label="Focused work item">
          {focused ? (
            <article className="operations-focus-narrative">
              <header>
                <div className="operations-focus-badges"><span>{focused.kind} · {focused.id}</span><SeverityBadge severity={focused.severity} /><StatusBadge status={focused.state} /></div>
                <h1>{focused.title}</h1>
                {focused.subtitle ? <p>{focused.subtitle}</p> : null}
              </header>
              <section className="operations-focus-facts">
                <div><small>Priority</small><strong>{focused.priority}</strong></div>
                <div><small>Assignee</small><strong>{focused.assignee_name ?? "Unassigned"}</strong></div>
                <div className={focused.overdue ? "is-risk" : ""}><small>Due state</small><strong>{dueLabel(focused)}</strong></div>
              </section>
              <dl className="operations-focus-context">
                <div><dt>Repository</dt><dd>{focused.repository_name ?? "No repository"}</dd></div>
                {focused.developer_name ? <div><dt>Developer</dt><dd>{focused.developer_name}</dd></div> : null}
                <div><dt>Updated</dt><dd>{timestamp(focused.updated_at)}</dd></div>
              </dl>
              {focused.priority_reasons.length ? <section className="operations-focus-reasons"><p className="workflow-eyebrow">Why this is ranked</p><ul>{focused.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></section> : null}
              <a href={focused.route} className="workflow-primary-action operations-focus-open">Open {focused.kind}</a>
              <div className="focus-disclosures operations-updates">
                <details><summary>Recent updates <span>{notifications.data?.unread ?? 0} new</span></summary><div>{notifications.isPending ? <p>Loading updates…</p> : notifications.isError ? <p role="alert">Updates are unavailable.</p> : notifications.data.notifications.length ? <ol className="workflow-list">{notifications.data.notifications.slice(0, 8).map((item) => <li key={item.id} className={item.read ? "opacity-60" : ""}><a href={item.route} onClick={() => !item.read && read.mutate(item.id)} className="workflow-list-row"><span className="workflow-update-dot" aria-hidden="true" /><span className="workflow-list-row-main"><strong>{item.title}</strong><small>{item.message}</small></span><span className="font-mono text-[10px] text-slate">{timestamp(item.created_at)}</span></a></li>)}</ol> : <p>No new updates.</p>}</div></details>
              </div>
            </article>
          ) : queue.isPending ? <StateFrame kind="loading" title="Loading work" /> : <StateFrame kind="empty" title="No focused record" detail="Select a queue item to see its responsibility and route." />}
        </section>

        <ResponsibilityDock
          responsibility={selected.size ? `Assign ${selected.size} selected item${selected.size === 1 ? "" : "s"}.` : focused ? `Open this ${focused.kind} and review its evidence.` : "Monitor the queue."}
          why={selected.size ? "The server checks each selected record version independently. Partial failures remain selected." : focused ? "The record detail contains the native evidence and actions allowed by its state and your capabilities." : "New work will be ranked here as it arrives."}
          deadline={focused?.sla_due_at ? timestamp(focused.sla_due_at) : undefined}
          overdue={focused?.overdue}
        >
          {selected.size && canAssign ? (
            <form onSubmit={(event) => { event.preventDefault(); setBulkResult(null); bulkAssign.mutate(); }}>
              <TextInput aria-label="Bulk owner email" type="email" required value={bulkOwner} onChange={(event) => setBulkOwner(event.target.value)} placeholder="Owner email" />
              <TextInput aria-label="Bulk owner name" required value={bulkOwnerName} onChange={(event) => setBulkOwnerName(event.target.value)} placeholder="Owner name" />
              <TextInput aria-label="Bulk due date" type="date" value={bulkDue} onChange={(event) => setBulkDue(event.target.value)} />
              <button type="submit" className="workflow-primary-action" disabled={!bulkOwner.trim() || !bulkOwnerName.trim() || bulkAssign.isPending}>{bulkAssign.isPending ? "Assigning…" : "Assign selected"}</button>
              {bulkAssign.error ? <p role="alert" className="text-xs text-risk">{bulkAssign.error instanceof Error ? bulkAssign.error.message : "Assignment failed."}</p> : null}
            </form>
          ) : focused ? <a href={focused.route} className="workflow-primary-action">Open {focused.kind}</a> : null}
          {bulkResult ? <div className={bulkResult.failed ? "workflow-receipt is-warning" : "workflow-receipt is-success"} role="status"><strong>{bulkResult.succeeded} updated</strong><span>{bulkResult.failed ? `${bulkResult.failed} need to be refreshed and tried again.` : "All selected records were assigned."}</span></div> : null}
          <ConflictRecovery error={bulkAssign.error} onReload={() => { bulkAssign.reset(); void queue.refetch(); }} />
          {!canAssign ? <p className="text-xs leading-relaxed text-slate">Your current capabilities allow queue review but not assignment.</p> : null}
        </ResponsibilityDock>
      </div>
    </main>
  );
}
