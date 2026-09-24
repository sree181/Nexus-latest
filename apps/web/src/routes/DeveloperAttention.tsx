import { useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { DeveloperTopbar, useDeveloperProject } from "../components/DeveloperProject";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { EmptyVisual, SidePanel, Status } from "../components/DeveloperVisual";
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
      rationale,
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
        <button type="button" className="dev-action dev-action-primary w-full" disabled={!rationale.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Sending…" : "Ask security"}<DevIcon name="arrow" size={16} /></button>
      )
    }>
      <div className="dev-stack">
        <section className="review-impact-summary">
          <span className="review-impact-package"><DevIcon name="package" /><strong>{item.package}</strong><small>{item.ecosystem} · {version}</small></span>
          <DevIcon name="arrow" size={16} />
          <span className="review-impact-package"><DevIcon name="security" /><strong>{item.advisories.length || "?"}</strong><small>{item.advisories.length === 1 ? "advisory" : "advisories"}</small></span>
          <DevIcon name="arrow" size={16} />
          <span className="review-impact-package"><DevIcon name="code" /><strong>{item.code_entities.length}</strong><small>linked code</small></span>
        </section>
        {item.suggested_version ? <div className="review-fix-card"><span className="dev-tone-success"><DevIcon name="check" /></span><span><strong>Move to {item.suggested_version}</strong><small>This release clears every advisory returned for the current version.</small></span></div> : null}
        {item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}
        {item.unavailable ? <div className="review-fix-card"><span className="dev-tone-warning"><DevIcon name="warning" /></span><span><strong>Could not complete the check</strong><small>{item.unavailable}</small></span></div> : null}
        {item.code_entities.length ? <section className="dev-surface"><header className="dev-panel-heading"><h2>Linked code</h2></header><ul className="dev-compact-list">{item.code_entities.map((entity) => <li key={entity} className="dev-compact-row"><DevIcon name="code" size={16} /><span className="dev-compact-row-main"><strong>{entity}</strong></span></li>)}</ul></section> : null}
        {item.review_status ? <ReviewStateBadge state={item.review_status} /> : (
          <section className="review-request-form">
            <label>What do you need?
              <select value={kind} onChange={(event) => setKind(event.target.value as ReviewKind)}>
                <option value="safe_version">Safe version</option>
                <option value="security_guidance">Security guidance</option>
                <option value="exception">Temporary approval</option>
                <option value="false_positive">Report a false positive</option>
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
  const [selected, setSelected] = useState<AttentionItem | null>(null);
  const attention = useQuery({ queryKey: ["developer-attention"], queryFn: () => api.developerAttention(500), refetchInterval: 8_000 });
  const items = useMemo(() => {
    const scoped = project.projectId === "all" ? attention.data?.items ?? [] : (attention.data?.items ?? []).filter((item) => item.repository_id === project.projectId);
    return filterAttention(scoped, filter);
  }, [attention.data?.items, filter, project.projectId]);
  const openCount = (attention.data?.items ?? []).filter((item) => !item.review_status || !["verified", "false_positive", "not_approved"].includes(item.review_status)).length;

  return (
    <main className="dev-page">
      <DeveloperTopbar title="Attention" />
      <div className="dev-scroll dev-stack">
        <section className="dev-attention-strip">
          <span className={openCount ? "dev-tone-warning" : "dev-tone-success"}><DevIcon name={openCount ? "warning" : "check"} /></span>
          <strong>{openCount ? `${openCount} to review` : "Nothing needs attention"}</strong>
          <span className="ml-auto dev-muted text-xs">Package checks only · evidence-backed</span>
        </section>
        <section className="dev-surface">
          <div className="dev-list-toolbar">
            <div className="dev-filter-chips" aria-label="Attention filter">
              {(["open", "blocked", "unknown", "sent", "all"] as Filter[]).map((value) => <button key={value} type="button" className="dev-filter-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "sent" ? "With security" : value[0].toUpperCase() + value.slice(1)}</button>)}
            </div>
          </div>
          {attention.isPending ? <EmptyVisual icon="live" title="Loading" /> : attention.isError ? <EmptyVisual icon="warning" title="Try again" /> : items.length ? (
            <div className="review-attention-list">
              {items.map((item) => (
                <button type="button" className="review-attention-row" key={item.id} onClick={() => setSelected(item)}>
                  <span className={`review-attention-priority dev-tone-${verdictTone(item)}`}><DevIcon name={item.verdict === "block" ? "lock" : "warning"} /></span>
                  <span className="review-attention-main"><strong>{item.package}{item.version ? `@${item.version}` : ""}</strong><small>{item.repository_name} · {item.ecosystem}</small></span>
                  <span className="review-attention-cves">{item.advisories.length ? item.advisories.slice(0, 2).map((advisory) => <span key={advisory.id}>{advisory.id}</span>) : <span>Check incomplete</span>}</span>
                  <span className="review-attention-fix">{item.suggested_version ? <>Fix <strong>{item.suggested_version}</strong></> : "Review needed"}</span>
                  {item.review_status ? <ReviewStateBadge state={item.review_status} /> : <Status label={item.verdict === "block" ? "Blocked" : "Review"} tone={verdictTone(item)} />}
                  <span className="dev-relative-time">{relativeTimeMs(item.checked_at_ms)}</span>
                </button>
              ))}
            </div>
          ) : <EmptyVisual icon="check" title={filter === "open" ? "Nothing needs attention" : "No matches"} />}
        </section>
      </div>
      {selected ? <AttentionDetail item={selected} close={() => setSelected(null)} /> : null}
    </main>
  );
}
