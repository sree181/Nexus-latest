import { useEffect, useMemo, useState } from "react";
import type { GraphNode } from "@meshagent/graph";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { ConflictRecovery, StateFrame } from "../components/WorkflowVisual";
import { Select, SeverityBadge, StatusBadge, TextInput, dateInputToEpoch } from "../components/WorkflowUI";
import { ApiError, api, type BulkWorkReceipt, type WorkItem, type WorkKind, type WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";
import "./analyst-operations.css";

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

function stageFor(item: WorkItem): FigmaJourneyStage {
  const state = item.state;
  if (["verified", "false_positive", "not_approved", "resolved", "closed"].includes(state)) return "verified";
  if (["remediation", "exception_approved"].includes(state)) return "remediation";
  if (["investigating", "escalated", "triaged"].includes(state)) return "investigation";
  if (item.kind === "review" && !item.assignee) return "developer_action";
  return "security_review";
}

function sourceReviewId(findingId: string | undefined): string | null {
  if (!findingId?.startsWith("review:")) return null;
  return findingId.slice("review:".length).trim() || null;
}

function QueueRow({
  item,
  focused,
  selected,
  canAssign,
  onFocus,
  onToggle,
}: {
  item: WorkItem;
  focused: boolean;
  selected: boolean;
  canAssign: boolean;
  onFocus: () => void;
  onToggle: (checked: boolean) => void;
}) {
  return (
    <li className={`figma3-ops-queue-row ${focused ? "is-focused" : ""} ${item.overdue ? "is-overdue" : ""}`}>
      {canAssign ? (
        <label className="figma3-ops-row-check">
          <input
            type="checkbox"
            aria-label={`Select ${item.title}`}
            checked={selected}
            onChange={(event) => onToggle(event.target.checked)}
          />
        </label>
      ) : null}
      <button type="button" className="figma3-ops-row-select" aria-pressed={focused} onClick={onFocus}>
        <span className="figma3-ops-row-top">
          <code>{item.kind} · {item.id}</code>
          <SeverityBadge severity={item.severity} />
        </span>
        <strong>{item.title}</strong>
        <span className="figma3-ops-row-subtitle">{item.subtitle || item.repository_name || "No repository"}</span>
        <span className="figma3-ops-row-bottom">
          <small>{item.assignee_name ?? "Unassigned"}</small>
          <small className={item.overdue ? "figma3-ops-risk" : ""}>{dueLabel(item)}</small>
        </span>
      </button>
      <a href={item.route} className="figma3-ops-row-open">Open<span aria-hidden="true"> →</span></a>
    </li>
  );
}

function EvidenceState({
  isPending,
  error,
  graph,
  label,
  selectedNode,
  onSelect,
  onRetry,
}: {
  isPending: boolean;
  error: unknown;
  graph: Parameters<typeof FigmaEvidenceGraph>[0]["graph"] | undefined;
  label: string;
  selectedNode: GraphNode | null;
  onSelect: (node: GraphNode) => void;
  onRetry: () => void;
}) {
  if (isPending) return <StateFrame kind="loading" title="Loading native evidence" detail="Reading the record's source projection." />;
  if (error) {
    const pending = error instanceof ApiError && error.status === 404;
    return (
      <StateFrame
        kind={pending ? "empty" : "error"}
        title={pending ? "Evidence projection is pending" : "Evidence could not be loaded"}
        detail={error instanceof Error ? error.message : "The selected work record remains available."}
        action={<button type="button" className="figma3-secondary" onClick={onRetry}>Try again</button>}
      />
    );
  }
  if (!graph) return <StateFrame kind="empty" title="No native evidence is available" detail="No source graph was returned for this record." />;
  return <FigmaEvidenceGraph graph={graph} label={label} selectedId={selectedNode?.id} onSelect={onSelect} height={176} />;
}

function QueueFilters({
  filters,
  mySubject,
  repositories,
  viewName,
  views,
  savePending,
  onOwnerView,
  onFilter,
  onViewName,
  onSave,
  onSavedView,
}: {
  filters: Filters;
  mySubject: string;
  repositories: string[];
  viewName: string;
  views: Array<{ id: string; name: string; filters: Record<string, string> }> | undefined;
  savePending: boolean;
  onOwnerView: (value?: string) => void;
  onFilter: (key: keyof Filters, value: string) => void;
  onViewName: (value: string) => void;
  onSave: () => void;
  onSavedView: (filters: Filters) => void;
}) {
  return (
    <>
      <div className="figma3-ops-owner-tabs" aria-label="Ownership view">
        <button type="button" disabled={!mySubject} aria-pressed={filters.assignee === mySubject} onClick={() => onOwnerView(mySubject)}>My work</button>
        <button type="button" aria-pressed={filters.assignee === "__unassigned__"} onClick={() => onOwnerView("__unassigned__")}>Unassigned</button>
        <button type="button" aria-pressed={!filters.assignee} onClick={() => onOwnerView()}>All work</button>
      </div>
      <div className="figma3-ops-queue-search">
        <TextInput aria-label="Search work" placeholder="Search work" value={filters.q ?? ""} onChange={(event) => onFilter("q", event.target.value)} />
        <details className="figma3-ops-filter-disclosure">
          <summary>Filters and saved views</summary>
          <div className="figma3-ops-filter-grid">
            <Select aria-label="Work type" value={filters.kind ?? ""} onChange={(event) => onFilter("kind", event.target.value)}>
              <option value="">All work</option><option value="review">Reviews</option><option value="case">Cases</option>
            </Select>
            <Select aria-label="Severity" value={filters.severity ?? ""} onChange={(event) => onFilter("severity", event.target.value)}>
              <option value="">All severity</option>{["critical", "high", "medium", "low", "unknown"].map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            <Select aria-label="Repository" value={filters.repository ?? ""} onChange={(event) => onFilter("repository", event.target.value)}>
              <option value="">All projects</option>{repositories.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            <Select aria-label="Owner" value={filters.assignee ?? ""} onChange={(event) => onFilter("assignee", event.target.value)}>
              <option value="">Any owner</option>{mySubject ? <option value={mySubject}>Assigned to me</option> : null}<option value="__unassigned__">Unassigned</option>
            </Select>
            <div className="figma3-ops-save-view">
              <TextInput aria-label="Saved view name" value={viewName} onChange={(event) => onViewName(event.target.value)} placeholder="View name" />
              <button type="button" className="figma3-secondary" disabled={!viewName.trim() || savePending} onClick={onSave}>{savePending ? "Saving…" : "Save view"}</button>
            </div>
            {views?.length ? <div className="figma3-ops-saved-views">{views.map((view) => <button key={view.id} type="button" onClick={() => onSavedView(view.filters as Filters)}>{view.name}</button>)}</div> : null}
          </div>
        </details>
      </div>
    </>
  );
}

function AssignmentFields({
  formId,
  owner,
  ownerName,
  due,
  pending,
  error,
  result,
  onOwner,
  onOwnerName,
  onDue,
  onSubmit,
}: {
  formId?: string;
  owner: string;
  ownerName: string;
  due: string;
  pending: boolean;
  error: unknown;
  result: BulkWorkReceipt | null;
  onOwner: (value: string) => void;
  onOwnerName: (value: string) => void;
  onDue: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <form id={formId} className="figma3-ops-assignment-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <label><span>Owner email</span><TextInput aria-label="Bulk owner email" type="email" required value={owner} onChange={(event) => onOwner(event.target.value)} placeholder="Owner email" /></label>
      <label><span>Owner name</span><TextInput aria-label="Bulk owner name" required value={ownerName} onChange={(event) => onOwnerName(event.target.value)} placeholder="Owner name" /></label>
      <label><span>Due date</span><TextInput aria-label="Bulk due date" type="date" value={due} onChange={(event) => onDue(event.target.value)} /></label>
      <button type="submit" className="figma3-primary" disabled={!owner.trim() || !ownerName.trim() || pending}>{pending ? "Assigning…" : "Assign selected"}</button>
      {error ? <p role="alert" className="figma3-ops-form-error">{error instanceof Error ? error.message : "Assignment failed."}</p> : null}
      {result ? <div className={`figma3-ops-receipt ${result.failed ? "is-warning" : "is-success"}`} role="status"><strong>{result.succeeded} updated</strong><span>{result.failed ? `${result.failed} need to be refreshed and tried again.` : "All selected records were assigned."}</span></div> : null}
    </form>
  );
}

export function AnalystOperations() {
  const { me, hasCapability } = useIdentity();
  const mySubject = me?.subject ?? "";
  const analystLabel = me?.name || mySubject || "Security analyst";
  const canAssign = hasCapability("review.write") || hasCapability("case.write");
  const client = useQueryClient();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [ownerInitialized, setOwnerInitialized] = useState(false);
  const [viewName, setViewName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [bulkOwner, setBulkOwner] = useState("");
  const [bulkOwnerName, setBulkOwnerName] = useState("");
  const [bulkDue, setBulkDue] = useState("");
  const [bulkResult, setBulkResult] = useState<BulkWorkReceipt | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

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
    if (!ownerInitialized && mySubject) {
      setFilters((current) => ({ ...current, assignee: current.assignee ?? mySubject }));
      setOwnerInitialized(true);
    }
  }, [mySubject, ownerInitialized]);

  useEffect(() => {
    if (!items.length) { setFocusedKey(null); return; }
    if (!focusedKey || !items.some((item) => selectedKey(item.kind, item.id) === focusedKey)) setFocusedKey(selectedKey(items[0].kind, items[0].id));
  }, [focusedKey, items]);

  useEffect(() => { setSelectedNode(null); }, [focusedKey]);

  const focused = items.find((item) => selectedKey(item.kind, item.id) === focusedKey) ?? null;
  const focusedCase = useQuery({
    queryKey: ["figma3-operations", "case", focused?.kind === "case" ? focused.id : "none"],
    queryFn: () => api.case(focused!.id),
    enabled: focused?.kind === "case",
    retry: false,
  });
  const focusedCaseReviewId = sourceReviewId(focusedCase.data?.finding_id);
  const reviewEvidence = useQuery({
    queryKey: ["figma3-operations", "review-graph", focused?.kind === "review" ? focused.id : "none"],
    queryFn: () => api.reviewGraph(focused!.id),
    enabled: focused?.kind === "review",
    retry: false,
    refetchInterval: 15_000,
  });
  const caseReviewEvidence = useQuery({
    queryKey: ["figma3-operations", "case-review-graph", focusedCaseReviewId ?? "none"],
    queryFn: () => api.reviewGraph(focusedCaseReviewId!),
    enabled: focused?.kind === "case" && Boolean(focusedCaseReviewId),
    retry: false,
    refetchInterval: 15_000,
  });
  const caseRunEvidence = useQuery({
    queryKey: ["figma3-operations", "case-run-graph", focused?.kind === "case" ? focusedCase.data?.run_id ?? "none" : "none"],
    queryFn: () => api.runGraph(focusedCase.data!.run_id),
    enabled: focused?.kind === "case" && Boolean(focusedCase.data && !focusedCaseReviewId),
    retry: false,
    refetchInterval: 15_000,
  });

  const evidenceGraph = focused?.kind === "review" ? reviewEvidence.data?.graph : focusedCaseReviewId ? caseReviewEvidence.data?.graph : caseRunEvidence.data;
  const evidencePending = focused?.kind === "review" ? reviewEvidence.isPending : focused?.kind === "case" ? focusedCase.isPending || (focusedCaseReviewId ? caseReviewEvidence.isPending : caseRunEvidence.isPending) : false;
  const evidenceError = focused?.kind === "review" ? reviewEvidence.error : focused?.kind === "case" ? focusedCase.error ?? (focusedCaseReviewId ? caseReviewEvidence.error : caseRunEvidence.error) : null;
  const retryEvidence = () => {
    if (focused?.kind === "review") { void reviewEvidence.refetch(); return; }
    if (focused?.kind === "case") {
      if (focusedCase.error) { void focusedCase.refetch(); return; }
      if (focusedCaseReviewId) void caseReviewEvidence.refetch(); else void caseRunEvidence.refetch();
    }
  };

  const set = (key: keyof Filters, value: string) => { setBulkResult(null); setFilters((current) => ({ ...current, [key]: value || undefined })); };
  const setOwnerView = (value?: string) => setFilters((current) => ({ ...current, assignee: value || undefined }));
  const toggleSelected = (item: WorkItem, checked: boolean) => {
    setBulkResult(null);
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(selectedKey(item.kind, item.id)); else next.delete(selectedKey(item.kind, item.id));
      return next;
    });
  };
  const counts = queue.data?.counts ?? {};
  const focusAction = focused ? <a href={focused.route} className="figma3-primary">Open {focused.kind}<span aria-hidden="true"> →</span></a> : null;
  const selectedResponsibility = `Assign ${selected.size} selected item${selected.size === 1 ? "" : "s"}.`;
  const renderAssignment = (formId?: string) => canAssign ? (
    <AssignmentFields
      formId={formId}
      owner={bulkOwner}
      ownerName={bulkOwnerName}
      due={bulkDue}
      pending={bulkAssign.isPending}
      error={bulkAssign.error}
      result={bulkResult}
      onOwner={setBulkOwner}
      onOwnerName={setBulkOwnerName}
      onDue={setBulkDue}
      onSubmit={() => { setBulkResult(null); bulkAssign.mutate(); }}
    />
  ) : null;
  const assignment = renderAssignment();

  const focusContent = focused ? (
    <article className="figma3-ops-narrative">
      <header className="figma3-ops-focus-header">
        <div className="figma3-ops-focus-badges"><code>{focused.kind} · {focused.id}</code><SeverityBadge severity={focused.severity} /><StatusBadge status={focused.state} />{focused.overdue ? <span className="figma3-ops-overdue">Overdue</span> : null}</div>
        <h1>{focused.title}</h1>
        {focused.subtitle ? <p>{focused.subtitle}</p> : null}
        <div className="figma3-ops-header-meta"><span>{focused.repository_name ?? "No repository"}</span><span>{focused.assignee_name ?? "Unassigned"}</span><span>Updated {timestamp(focused.updated_at)}</span></div>
      </header>

      <section className="figma3-ops-journey-card">
        <p className="figma3-kicker">Seven-stage journey</p>
        <FigmaJourney current={stageFor(focused)} label={`${focused.kind} journey`} />
      </section>

      <section className="figma3-ops-fact-strip" aria-label="Work item facts">
        <div><small>Priority</small><strong>{focused.priority}</strong></div>
        <div><small>Owner</small><strong>{focused.assignee_name ?? "Unassigned"}</strong></div>
        <div className={focused.overdue ? "is-risk" : ""}><small>Due state</small><strong>{dueLabel(focused)}</strong></div>
        {focused.developer_name ? <div><small>Developer</small><strong>{focused.developer_name}</strong></div> : null}
      </section>

      <section className="figma3-ops-evidence-section">
        <div className="figma3-ops-section-heading"><div><p className="figma3-kicker">Compact evidence graph</p><h2>Trace the record's source evidence</h2></div><a href={focused.route} className="figma3-secondary">Open full record</a></div>
        <EvidenceState isPending={evidencePending} error={evidenceError} graph={evidenceGraph} label={`${focused.kind} source evidence`} selectedNode={selectedNode} onSelect={setSelectedNode} onRetry={retryEvidence} />
        {selectedNode ? <p className="figma3-ops-node-selection"><strong>Focused entity:</strong> {selectedNode.label} <code>{selectedNode.id}</code></p> : null}
      </section>

      <div className="figma3-ops-disclosures">
        <details open={Boolean(focused.priority_reasons.length)}>
          <summary>Why this is ranked <span>{focused.priority_reasons.length}</span></summary>
          <div>{focused.priority_reasons.length ? <ul>{focused.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>No ranking reasons were returned for this record.</p>}</div>
        </details>
        <details>
          <summary>Recent updates <span>{notifications.data?.unread ?? 0} new</span></summary>
          <div>
            {notifications.isPending ? <p>Loading updates…</p> : notifications.isError ? <p role="alert">Updates are unavailable.</p> : notifications.data.notifications.length ? (
              <ol className="figma3-ops-updates">
                {notifications.data.notifications.slice(0, 8).map((item) => <li key={item.id} className={item.read ? "is-read" : ""}><a href={item.route} onClick={() => !item.read && read.mutate(item.id)}><span aria-hidden="true" /><span><strong>{item.title}</strong><small>{item.message}</small></span><time>{timestamp(item.created_at)}</time></a></li>)}
              </ol>
            ) : <p>No new updates.</p>}
          </div>
        </details>
      </div>
    </article>
  ) : queue.isPending ? <StateFrame kind="loading" title="Loading work" /> : <StateFrame kind="empty" title="No focused record" detail="Select a queue item to see its responsibility and route." />;

  return (
    <main className="figma3-page figma3-ops-page">
      <div className="figma3-ops-desktop figma3-desktop-only">
        <nav className="figma3-ops-rail" aria-label="Security operations navigation">
          <div className="figma3-ops-mark" aria-hidden="true">M</div>
          <div className="figma3-ops-rail-links"><a href="/analyst/queue" aria-current="page" title="Queue">⌁</a><a href="/analyst/activity" title="Work history">◷</a></div>
          <span className="figma3-ops-identity" title={analystLabel}>{analystLabel.slice(0, 2).toUpperCase()}</span>
        </nav>

        <aside className="figma3-ops-queue" aria-label="Security work queue">
          <header className="figma3-ops-queue-header">
            <div><p className="figma3-kicker">Security operations</p><h1>{counts.all ?? 0} open</h1></div>
            <span className={(counts.overdue ?? 0) ? "is-risk" : ""}>{counts.overdue ?? 0} overdue</span>
          </header>
          <QueueFilters filters={filters} mySubject={mySubject} repositories={repositories} viewName={viewName} views={views.data} savePending={saveView.isPending} onOwnerView={setOwnerView} onFilter={set} onViewName={setViewName} onSave={() => saveView.mutate()} onSavedView={setFilters} />
          {selected.size ? <div className="figma3-ops-selection-bar"><strong>{selected.size} selected</strong><button type="button" onClick={() => setSelected(new Set())}>Clear</button></div> : null}
          <div className="figma3-ops-queue-scroll">
            {queue.isPending ? <StateFrame kind="loading" title="Prioritizing work" detail="Reading reviews and cases." /> : queue.isError ? <StateFrame kind="error" title="Queue unavailable" detail={queue.error instanceof Error ? queue.error.message : "Try again."} action={<button type="button" className="figma3-secondary" onClick={() => void queue.refetch()}>Try again</button>} /> : items.length ? (
              <ol className="figma3-ops-work-list" aria-label="Ranked work">
                {items.map((item) => <QueueRow key={selectedKey(item.kind, item.id)} item={item} focused={focusedKey === selectedKey(item.kind, item.id)} selected={selected.has(selectedKey(item.kind, item.id))} canAssign={canAssign} onFocus={() => setFocusedKey(selectedKey(item.kind, item.id))} onToggle={(checked) => toggleSelected(item, checked)} />)}
              </ol>
            ) : <StateFrame kind="empty" title="Nothing matches this view" detail="Clear a filter or choose another saved view." />}
          </div>
        </aside>

        <section className="figma3-ops-main" aria-label="Focused work item">{focusContent}</section>

        <FigmaResponsibilityDock
          className="figma3-ops-responsibility"
          responsibility={selected.size ? selectedResponsibility : focused ? `Open this ${focused.kind} and review its evidence.` : "Monitor the work queue."}
          why={selected.size ? "The server checks each selected record version independently. Partial failures remain selected." : focused ? "The full record holds the native evidence and only the actions allowed by its state and your capabilities." : "New work will be ranked here as it arrives."}
          actions={selected.size ? undefined : focusAction}
          deadline={focused?.sla_due_at ? timestamp(focused.sla_due_at) : undefined}
          overdue={focused?.overdue}
        >
          {selected.size ? assignment : null}
          {selected.size ? <ConflictRecovery error={bulkAssign.error} onReload={() => { bulkAssign.reset(); void queue.refetch(); }} /> : null}
          {selected.size && !canAssign ? <p className="figma3-ops-capability-note">Your current capabilities allow queue review but not assignment.</p> : null}
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-ops-mobile figma3-mobile-only">
        <header className="figma3-ops-mobile-header"><div><strong>Security operations</strong><small>{analystLabel}</small></div><span>{counts.overdue ?? 0} overdue</span></header>
        <div className="figma3-ops-mobile-scroll">
          <section className="figma3-ops-mobile-summary">
            <p className="figma3-kicker">Focused work</p>
            <strong>{counts.all ?? 0} open</strong><span>{focused ? `${focused.kind} · ${focused.id}` : "Choose a queue item"}</span>
          </section>
          {focused ? (
            <>
              <h1>{focused.title}</h1>
              {focused.subtitle ? <p className="figma3-ops-mobile-lede">{focused.subtitle}</p> : null}
              <div className="figma3-ops-mobile-facts"><span>{focused.assignee_name ?? "Unassigned"}</span><span className={focused.overdue ? "figma3-ops-risk" : ""}>{dueLabel(focused)}</span></div>
              <section className="figma3-ops-mobile-journey"><p className="figma3-kicker">Journey</p><FigmaJourney current={stageFor(focused)} vertical label={`${focused.kind} journey`} /></section>
              <section className="figma3-ops-mobile-evidence"><p className="figma3-kicker">Evidence</p><EvidenceState isPending={evidencePending} error={evidenceError} graph={evidenceGraph} label={`${focused.kind} source evidence`} selectedNode={selectedNode} onSelect={setSelectedNode} onRetry={retryEvidence} /></section>
              <div className="figma3-ops-disclosures">
                <details><summary>Why this is ranked <span>{focused.priority_reasons.length}</span></summary><div>{focused.priority_reasons.length ? <ul>{focused.priority_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>No ranking reasons were returned for this record.</p>}</div></details>
                <details><summary>Updates <span>{notifications.data?.unread ?? 0} new</span></summary><div>{notifications.isPending ? <p>Loading updates…</p> : notifications.isError ? <p role="alert">Updates are unavailable.</p> : notifications.data.notifications.length ? <ol className="figma3-ops-updates">{notifications.data.notifications.slice(0, 8).map((item) => <li key={item.id} className={item.read ? "is-read" : ""}><a href={item.route} onClick={() => !item.read && read.mutate(item.id)}><span aria-hidden="true" /><span><strong>{item.title}</strong><small>{item.message}</small></span><time>{timestamp(item.created_at)}</time></a></li>)}</ol> : <p>No new updates.</p>}</div></details>
              </div>
            </>
          ) : queue.isPending ? <StateFrame kind="loading" title="Loading work" /> : null}
          <section className="figma3-ops-mobile-queue" aria-label="Security work queue">
            <div className="figma3-ops-mobile-queue-heading"><strong>Queue</strong>{selected.size ? <button type="button" onClick={() => setSelected(new Set())}>Clear {selected.size}</button> : null}</div>
            <QueueFilters filters={filters} mySubject={mySubject} repositories={repositories} viewName={viewName} views={views.data} savePending={saveView.isPending} onOwnerView={setOwnerView} onFilter={set} onViewName={setViewName} onSave={() => saveView.mutate()} onSavedView={setFilters} />
            {queue.isError ? <StateFrame kind="error" title="Queue unavailable" detail={queue.error instanceof Error ? queue.error.message : "Try again."} action={<button type="button" className="figma3-secondary" onClick={() => void queue.refetch()}>Try again</button>} /> : items.length ? <ol className="figma3-ops-work-list">{items.map((item) => <QueueRow key={selectedKey(item.kind, item.id)} item={item} focused={focusedKey === selectedKey(item.kind, item.id)} selected={selected.has(selectedKey(item.kind, item.id))} canAssign={canAssign} onFocus={() => setFocusedKey(selectedKey(item.kind, item.id))} onToggle={(checked) => toggleSelected(item, checked)} />)}</ol> : queue.isPending ? null : <StateFrame kind="empty" title="Nothing matches this view" detail="Clear a filter or choose another saved view." />}
          </section>
          {selected.size && canAssign ? <section className="figma3-ops-mobile-assignment"><p className="figma3-kicker">Assign selected</p>{renderAssignment("figma3-ops-mobile-bulk-form")}</section> : null}
          {selected.size && !canAssign ? <p className="figma3-ops-capability-note">Your current capabilities allow queue review but not assignment.</p> : null}
          {selected.size ? <ConflictRecovery error={bulkAssign.error} onReload={() => { bulkAssign.reset(); void queue.refetch(); }} /> : null}
        </div>
        {selected.size && canAssign ? <FigmaMobileDock deadline={focused?.sla_due_at ? timestamp(focused.sla_due_at) : undefined}><button type="submit" form="figma3-ops-mobile-bulk-form" className="figma3-primary" disabled={!bulkOwner.trim() || !bulkOwnerName.trim() || bulkAssign.isPending}>{bulkAssign.isPending ? "Assigning…" : `Assign ${selected.size} selected`}</button></FigmaMobileDock> : focused && !selected.size ? <FigmaMobileDock deadline={focused.sla_due_at ? timestamp(focused.sla_due_at) : undefined}>{focusAction}</FigmaMobileDock> : null}
      </div>
    </main>
  );
}
