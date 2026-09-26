import { useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { DeveloperTopbar, useDeveloperProject } from "../components/DeveloperProject";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { SidePanel, Status } from "../components/DeveloperVisual";
import { ResponsibilityDock, StateFrame, WorkflowHero, WorkflowJourney, WorkflowSection, type JourneyItem } from "../components/WorkflowVisual";
import { api, type AttentionItem, type ReviewKind } from "../lib/api";
import { relativeTimeMs } from "../lib/format";

type Filter = "open" | "blocked" | "unknown" | "sent" | "all";

export function filterAttention(items: AttentionItem[], filter: Filter): AttentionItem[] {
  if (filter === "open") return items.filter((item) => !item.review_status || !["verified", "false_positive", "not_approved"].includes(item.review_status));
  if (filter === "blocked") return items.filter((item) => item.verdict === "block");
  if (filter === "unknown") return items.filter((item) => item.verdict === "unknown" || Boolean(item.unavailable));
  if (filter === "sent") return items.filter((item) => Boolean(item.review_request_id));
  return items;
}

function attentionJourney(item: AttentionItem): JourneyItem[] {
  const terminal = item.review_status ? ["verified", "false_positive", "not_approved"].includes(item.review_status) : false;
  return [
    { id: "detected", label: "Detected", detail: `${item.package}${item.version ? `@${item.version}` : ""}`, state: "complete" },
    { id: "developer", label: "Developer action", detail: item.review_request_id ? "Request sent" : "Choose the next step", state: item.review_request_id ? "complete" : "current" },
    { id: "security", label: "Security review", detail: item.review_status ? item.review_status.replaceAll("_", " ") : "Not started", state: terminal ? "complete" : item.review_request_id ? "current" : "pending" },
    { id: "result", label: "Result", detail: terminal ? "Available" : "Pending", state: terminal ? "complete" : "pending" },
  ];
}

function AttentionDetail({ item, close }: { item: AttentionItem; close: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [kind, setKind] = useState<ReviewKind>(item.suggested_version ? "safe_version" : "security_guidance");
  const [rationale, setRationale] = useState("");
  const create = useMutation({
    mutationFn: () => api.createDeveloperReviewRequest({
      session_id: item.session_id,
      policy_evaluation_id: item.policy_evaluation_id,
      kind,
      rationale: rationale.trim(),
    }),
    onSuccess: (request) => {
      void client.invalidateQueries({ queryKey: ["developer-attention"] });
      void navigate({ to: "/developer/reviews/$requestId", params: { requestId: request.id } });
    },
  });
  const version = item.version || "unpinned";
  return (
    <SidePanel title={`${item.package}@${version}`} icon="warning" onClose={close} footer={
      item.review_request_id ? (
        <Link to="/developer/reviews/$requestId" params={{ requestId: item.review_request_id }} className="dev-action dev-action-primary w-full">Open review <DevIcon name="arrow" size={16} /></Link>
      ) : (
        <button type="button" className="dev-action dev-action-primary w-full" disabled={!rationale.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Sending…" : "Send to security"}<DevIcon name="arrow" size={16} /></button>
      )
    }>
      <div className="dev-stack">
        <WorkflowJourney items={attentionJourney(item)} label="Attention item journey" />
        <section className="review-impact-summary" aria-label="Evidence summary">
          <span className="review-impact-package"><DevIcon name="package" /><strong>{item.package}</strong><small>{item.ecosystem} · {version}</small></span>
          <DevIcon name="arrow" size={16} />
          <span className="review-impact-package"><DevIcon name="security" /><strong>{item.advisories.length || "—"}</strong><small>{item.advisories.length === 1 ? "advisory" : "advisories"}</small></span>
          <DevIcon name="arrow" size={16} />
          <span className="review-impact-package"><DevIcon name="code" /><strong>{item.code_entities.length}</strong><small>linked code</small></span>
        </section>
        {item.reasons.length ? <section className="review-fix-card"><span className="dev-tone-warning"><DevIcon name="warning" /></span><span><strong>Why this needs attention</strong><small>{item.reasons.join(" ")}</small></span></section> : null}
        {item.suggested_version ? <div className="review-fix-card"><span className="dev-tone-success"><DevIcon name="check" /></span><span><strong>Suggested next version: {item.suggested_version}</strong><small>This is a candidate from the advisory response. The next package check remains authoritative.</small></span></div> : null}
        {item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}
        {item.unavailable ? <div className="review-fix-card"><span className="dev-tone-warning"><DevIcon name="warning" /></span><span><strong>Check incomplete</strong><small>{item.unavailable}</small></span></div> : null}
        {item.code_entities.length ? <section className="dev-surface"><header className="dev-panel-heading"><h2>Linked code</h2></header><ul className="dev-compact-list">{item.code_entities.map((entity) => <li key={entity} className="dev-compact-row"><DevIcon name="code" size={16} /><span className="dev-compact-row-main"><strong>{entity}</strong></span></li>)}</ul></section> : null}
        {item.review_status ? <div className="flex items-center gap-2"><span className="text-xs text-slate">Current review state</span><ReviewStateBadge state={item.review_status} /></div> : (
          <section className="review-request-form" aria-label="Security review request">
            <label>What do you need?
              <select value={kind} onChange={(event) => setKind(event.target.value as ReviewKind)}>
                <option value="safe_version">A safer version</option>
                <option value="security_guidance">Security guidance</option>
                <option value="exception">Temporary approval</option>
                <option value="false_positive">False-positive review</option>
              </select>
            </label>
            <label>Context
              <textarea value={rationale} onChange={(event) => setRationale(event.target.value)} maxLength={4096} placeholder="What are you trying to ship, and what constraint matters?" />
            </label>
            {create.isError ? <p role="alert" className="text-sm text-risk">{create.error instanceof Error ? create.error.message : "Could not send the request."}</p> : null}
          </section>
        )}
      </div>
    </SidePanel>
  );
}

function verdictTone(item: AttentionItem) {
  if (item.verdict === "block") return "danger" as const;
  if (item.verdict === "unknown" || item.unavailable) return "warning" as const;
  return "warning" as const;
}

export function DeveloperAttention() {
  const project = useDeveloperProject();
  const [filter, setFilter] = useState<Filter>("open");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const attention = useQuery({ queryKey: ["developer-attention"], queryFn: () => api.developerAttention(500), refetchInterval: 8_000 });
  const scopedItems = useMemo(() => project.projectId === "all" ? attention.data?.items ?? [] : (attention.data?.items ?? []).filter((item) => item.repository_id === project.projectId), [attention.data?.items, project.projectId]);
  const items = useMemo(() => filterAttention(scopedItems, filter), [scopedItems, filter]);
  const selected = selectedId ? scopedItems.find((item) => item.id === selectedId) ?? null : null;
  const open = scopedItems.filter((item) => !item.review_status || !["verified", "false_positive", "not_approved"].includes(item.review_status));
  const blocked = scopedItems.filter((item) => item.verdict === "block").length;
  const incomplete = scopedItems.filter((item) => item.verdict === "unknown" || Boolean(item.unavailable)).length;
  const withSecurity = scopedItems.filter((item) => Boolean(item.review_request_id)).length;

  return (
    <main className="dev-page workflow-page">
      <DeveloperTopbar title="Attention" />
      <div className="dev-scroll workflow-scroll">
        <div className="workflow-layout">
          <div className="workflow-main">
            <WorkflowHero
              eyebrow="Developer attention"
              title={open.length ? `${open.length} item${open.length === 1 ? "" : "s"} need your choice` : "No action is waiting"}
              description="Package signals from your connected coding-agent sessions. Open an item to see the recorded evidence and choose whether security should review it."
              tone={blocked ? "danger" : incomplete ? "warning" : "success"}
              meta={<><span>{project.projectId === "all" ? "All projects" : project.projectId}</span><span>{withSecurity} with security</span></>}
              status={<Status label={blocked ? `${blocked} blocked` : incomplete ? `${incomplete} incomplete` : "Clear"} tone={blocked ? "danger" : incomplete ? "warning" : "success"} />}
            />

            <div className="workflow-kpis" aria-label="Attention summary">
              <div className={open.length ? "workflow-kpi is-warning" : "workflow-kpi is-success"}><strong>{open.length}</strong><span>Need a choice</span></div>
              <div className={blocked ? "workflow-kpi is-risk" : "workflow-kpi"}><strong>{blocked}</strong><span>Blocked packages</span></div>
              <div className={incomplete ? "workflow-kpi is-warning" : "workflow-kpi"}><strong>{incomplete}</strong><span>Checks incomplete</span></div>
              <div className="workflow-kpi"><strong>{withSecurity}</strong><span>With security</span></div>
            </div>

            <WorkflowSection className="flush" eyebrow="Your work" title="Attention queue" description="Highest priority appears first. Select a row to reveal evidence and the next action." action={<span className="font-mono text-[11px] text-slate">{items.length} shown</span>}>
              <div className="dev-list-toolbar">
                <div className="dev-filter-chips" aria-label="Attention filter">
                  {(["open", "blocked", "unknown", "sent", "all"] as Filter[]).map((value) => <button key={value} type="button" className="dev-filter-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "sent" ? "With security" : value[0].toUpperCase() + value.slice(1)}</button>)}
                </div>
              </div>
              {attention.isPending ? <StateFrame kind="loading" title="Loading attention" detail="Reading package checks from your connected sessions." /> : attention.isError ? <StateFrame kind="error" title="Attention could not be loaded" detail={attention.error instanceof Error ? attention.error.message : "Try again when the service is available."} action={<button type="button" className="dev-action" onClick={() => void attention.refetch()}>Try again</button>} /> : items.length ? (
                <div className="review-attention-list">
                  {items.map((item) => (
                    <button type="button" className="review-attention-row" key={item.id} onClick={() => setSelectedId(item.id)}>
                      <span className={`review-attention-priority dev-tone-${verdictTone(item)}`}><DevIcon name={item.verdict === "block" ? "lock" : "warning"} /></span>
                      <span className="review-attention-main"><strong>{item.package}{item.version ? `@${item.version}` : ""}</strong><small>{item.repository_name} · {item.ecosystem}</small></span>
                      <span className="review-attention-cves">{item.advisories.length ? item.advisories.slice(0, 2).map((advisory) => <span key={advisory.id}>{advisory.id}</span>) : <span>Check incomplete</span>}</span>
                      <span className="review-attention-fix">{item.suggested_version ? <>Suggested <strong>{item.suggested_version}</strong></> : "Review needed"}</span>
                      {item.review_status ? <ReviewStateBadge state={item.review_status} /> : <Status label={item.verdict === "block" ? "Blocked" : "Review"} tone={verdictTone(item)} />}
                      <span className="dev-relative-time">{relativeTimeMs(item.checked_at_ms)}</span>
                    </button>
                  ))}
                </div>
              ) : <StateFrame kind="empty" title={filter === "open" ? "Nothing needs attention" : "No items match this view"} detail={filter === "open" ? "New package checks that need your decision will appear here." : "Choose another filter to see the rest of your work."} />}
            </WorkflowSection>
          </div>

          <ResponsibilityDock
            responsibility={open.length ? "Review the highest-priority package signal." : "Continue your development work."}
            why={open.length ? "You decide when more context is needed. Security receives a request only after you submit one." : "meshAgent will surface the next package check that needs a decision."}
          >
            {open[0] ? <button type="button" className="dev-action dev-action-primary w-full" onClick={() => setSelectedId(open[0].id)}>Review {open[0].package}<DevIcon name="arrow" size={16} /></button> : null}
            {withSecurity ? <Link to="/developer/attention" search={{}} className="dev-action w-full" onClick={() => setFilter("sent")}>View requests with security</Link> : null}
            <p className="text-xs leading-relaxed text-slate">Only your own sessions and review requests are returned by the server.</p>
          </ResponsibilityDock>
        </div>
      </div>
      {selected ? <AttentionDetail key={selected.id} item={selected} close={() => setSelectedId(null)} /> : null}
    </main>
  );
}
