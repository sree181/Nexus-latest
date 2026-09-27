import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { useDeveloperProject } from "../components/DeveloperProject";
import { AdvisoryCard, ReviewStateBadge } from "../components/ReviewUI";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { SidePanel, Status } from "../components/DeveloperVisual";
import { ResponsibilityDock, StateFrame, WorkflowJourney, type JourneyItem } from "../components/WorkflowVisual";
import { ApiError, api, type AttentionItem, type ReviewKind } from "../lib/api";
import { relativeTimeMs } from "../lib/format";

type Filter = "open" | "blocked" | "unknown" | "sent" | "all";
type RequestStep = 1 | 2 | 3;

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

function verdictTone(item: AttentionItem) {
  if (item.verdict === "block") return "danger" as const;
  if (item.verdict === "unknown" || item.unavailable) return "warning" as const;
  return "warning" as const;
}

function focusHeadline(item: AttentionItem): string {
  const coordinate = `${item.package}${item.version ? `@${item.version}` : ""}`;
  if (item.unavailable || item.verdict === "unknown") return `${coordinate} could not be fully checked`;
  if (item.verdict === "block") return `${coordinate} needs your attention`;
  return `Review the recorded signal for ${coordinate}`;
}

function focusDescription(item: AttentionItem): string {
  if (item.unavailable) return item.unavailable;
  if (item.reasons.length) return item.reasons.join(" ");
  if (item.advisories.length) return `${item.advisories.length} published ${item.advisories.length === 1 ? "advisory is" : "advisories are"} linked to this package check.`;
  return `meshAgent recorded this package evaluation in session ${item.session_id}.`;
}

function RequestDrawer({ item, close }: { item: AttentionItem; close: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [step, setStep] = useState<RequestStep>(1);
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

  const labels: Record<ReviewKind, { title: string; detail: string }> = {
    security_guidance: { title: "Security guidance", detail: "Ask Security to interpret the recorded package signal." },
    safe_version: { title: "Safer version path", detail: "Ask Security to review the candidate version and next step." },
    exception: { title: "Temporary approval review", detail: "Ask whether a governed, time-limited exception should be considered." },
    false_positive: { title: "False-positive review", detail: "Ask Security to reassess whether the advisory applies." },
  };

  return (
    <SidePanel
      title="Ask Security"
      icon="security"
      onClose={close}
      footer={(
        <div className="attention-drawer-footer">
          {step > 1 ? <button type="button" className="dev-action" disabled={create.isPending} onClick={() => setStep((step - 1) as RequestStep)}>Back</button> : null}
          {step < 3 ? (
            <button type="button" className="dev-action dev-action-primary" disabled={step === 2 && rationale.trim().length < 20} onClick={() => setStep((step + 1) as RequestStep)}>Continue <DevIcon name="arrow" size={16} /></button>
          ) : (
            <button type="button" className="dev-action dev-action-primary" disabled={rationale.trim().length < 20 || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Sending…" : "Submit request"}<DevIcon name="arrow" size={16} /></button>
          )}
        </div>
      )}
    >
      <div className="dev-stack attention-drawer">
        <div className="attention-drawer-progress" aria-label={`Step ${step} of 3`}>
          {[1, 2, 3].map((value) => <span key={value} className={value <= step ? "is-active" : ""} />)}
        </div>
        <div>
          <p className="workflow-eyebrow">Step {step} of 3</p>
          <h2>{step === 1 ? "Choose the review you need" : step === 2 ? "Explain the constraint" : "Review the exact request"}</h2>
        </div>
        {step === 1 ? (
          <div className="attention-request-kinds" role="radiogroup" aria-label="Review kind">
            {(Object.entries(labels) as Array<[ReviewKind, { title: string; detail: string }]>).map(([value, copy]) => (
              <label key={value} className={kind === value ? "is-selected" : ""}>
                <input type="radio" name="review-kind" value={value} checked={kind === value} onChange={() => setKind(value)} />
                <span><strong>{copy.title}</strong><small>{copy.detail}</small></span>
              </label>
            ))}
          </div>
        ) : null}
        {step === 2 ? (
          <label className="attention-rationale">Context
            <textarea value={rationale} onChange={(event) => setRationale(event.target.value)} maxLength={4096} rows={7} placeholder="What are you trying to ship, and what constraint matters?" />
            <small>Minimum 20 characters. This context becomes part of the governed review request.</small>
          </label>
        ) : null}
        {step === 3 ? (
          <dl className="attention-request-review">
            <div><dt>Session</dt><dd><code>{item.session_id}</code></dd></div>
            <div><dt>Policy evaluation</dt><dd><code>{item.policy_evaluation_id}</code></dd></div>
            <div><dt>Request</dt><dd>{labels[kind].title}</dd></div>
            <div><dt>Context</dt><dd>{rationale}</dd></div>
          </dl>
        ) : null}
        {create.isError ? <p role="alert" className="text-sm text-risk">{create.error instanceof Error ? create.error.message : "Could not send the request."}</p> : null}
      </div>
    </SidePanel>
  );
}

function QueueRows({ items, selectedId, select }: { items: AttentionItem[]; selectedId: string | null; select: (id: string) => void }) {
  return (
    <div className="attention-queue-list" role="listbox" aria-label="Attention items">
      {items.map((item) => (
        <button
          type="button"
          role="option"
          aria-selected={selectedId === item.id}
          className={`attention-queue-row ${selectedId === item.id ? "is-selected" : ""}`}
          key={item.id}
          onClick={() => select(item.id)}
        >
          <span className={`attention-queue-accent is-${verdictTone(item)}`} aria-hidden="true" />
          <span className="attention-queue-row-top">
            <strong>{item.package}{item.version ? `@${item.version}` : ""}</strong>
            <Status label={item.verdict === "block" ? "Blocked" : item.unavailable ? "Incomplete" : item.verdict} tone={verdictTone(item)} />
          </span>
          <span className="attention-queue-row-meta">{item.repository_name} · {item.ecosystem}</span>
          <span className="attention-queue-row-evidence">{item.advisories.length ? item.advisories.slice(0, 2).map((advisory) => advisory.id).join(" · ") : "Check incomplete"}</span>
          <span className="attention-queue-row-bottom">
            {item.review_status ? <ReviewStateBadge state={item.review_status} /> : <small>{item.code_entities.length} linked code</small>}
            <small>{relativeTimeMs(item.checked_at_ms)}</small>
          </span>
        </button>
      ))}
    </div>
  );
}

export function DeveloperAttention() {
  const project = useDeveloperProject();
  const [filter, setFilter] = useState<Filter>("open");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [requestOpen, setRequestOpen] = useState(false);
  const attention = useQuery({ queryKey: ["developer-attention"], queryFn: () => api.developerAttention(500), refetchInterval: 8_000 });
  const scopedItems = useMemo(() => project.projectId === "all" ? attention.data?.items ?? [] : (attention.data?.items ?? []).filter((item) => item.repository_id === project.projectId), [attention.data?.items, project.projectId]);
  const items = useMemo(() => filterAttention(scopedItems, filter), [scopedItems, filter]);

  useEffect(() => {
    if (!items.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !items.some((item) => item.id === selectedId)) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = selectedId ? items.find((item) => item.id === selectedId) ?? null : null;
  const graph = useQuery({
    queryKey: ["attention", selected?.id ?? "none", "run-graph", selected?.run_id ?? "none"],
    queryFn: () => api.runGraph(selected!.run_id!),
    enabled: Boolean(selected?.run_id),
    retry: false,
    refetchInterval: 15_000,
  });
  const blocked = scopedItems.filter((item) => item.verdict === "block").length;
  const withSecurity = scopedItems.filter((item) => Boolean(item.review_request_id)).length;

  const select = (id: string) => {
    setSelectedId(id);
    setQueueOpen(false);
  };

  return (
    <main className="dev-page workflow-page attention-page">
      <div className="attention-mobile-summary">
        <button type="button" aria-expanded={queueOpen} onClick={() => setQueueOpen((value) => !value)}>
          <span><small>Attention queue</small><strong>{items.length} shown · {blocked} blocked</strong></span>
          <DevIcon name="chevron" size={16} className={queueOpen ? "is-open" : ""} />
        </button>
        {queueOpen ? (
          <div className="attention-mobile-queue-panel">
            <div className="attention-queue-filters">
              {(["open", "blocked", "unknown", "sent", "all"] as Filter[]).map((value) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "sent" ? "With security" : value[0].toUpperCase() + value.slice(1)}</button>)}
            </div>
            <QueueRows items={items} selectedId={selectedId} select={select} />
          </div>
        ) : null}
      </div>

      <div className="attention-workspace">
        <aside className="attention-queue" aria-label="Attention queue">
          <header>
            <div><p className="workflow-eyebrow">Your work</p><h1>Attention</h1></div>
            <span>{items.length}</span>
          </header>
          <p className="attention-queue-scope">{project.projectId === "all" ? "All projects" : project.projectId} · {withSecurity} with security</p>
          <div className="attention-queue-filters" aria-label="Attention filter">
            {(["open", "blocked", "unknown", "sent", "all"] as Filter[]).map((value) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "sent" ? "With security" : value[0].toUpperCase() + value.slice(1)}</button>)}
          </div>
          {attention.isPending ? <StateFrame kind="loading" title="Loading attention" detail="Reading your connected sessions." /> : attention.isError ? <StateFrame kind="error" title="Could not load attention" detail={attention.error instanceof Error ? attention.error.message : "Try again."} action={<button type="button" onClick={() => void attention.refetch()}>Try again</button>} /> : items.length ? <QueueRows items={items} selectedId={selectedId} select={select} /> : <StateFrame kind="empty" title="No items in this view" detail="Choose another filter or continue your work." />}
        </aside>

        <section className="attention-focus" aria-label="Selected attention item">
          {selected ? (
            <article className="focus-narrative">
              <header className={`focus-heading is-${verdictTone(selected)}`}>
                <div className="focus-heading-meta">
                  <span>{selected.ecosystem}</span><span>{selected.repository_name}</span><span>{relativeTimeMs(selected.checked_at_ms)}</span>
                </div>
                <div className="focus-heading-title">
                  <div><p className="workflow-eyebrow">Recorded package signal</p><h1>{focusHeadline(selected)}</h1></div>
                  <Status label={selected.verdict === "block" ? "Blocked" : selected.unavailable ? "Incomplete" : selected.verdict} tone={verdictTone(selected)} />
                </div>
                <p>{focusDescription(selected)}</p>
              </header>

              <section className="focus-journey-card">
                <p className="workflow-eyebrow">Workflow</p>
                <WorkflowJourney items={attentionJourney(selected)} label="Attention item journey" />
              </section>

              <section className="evidence-path" aria-label="Recorded evidence path">
                <p className="workflow-eyebrow">Evidence path</p>
                <div>
                  <span><small>Session</small><code>{selected.session_id}</code></span>
                  <i aria-hidden="true"><small>recorded</small>→</i>
                  <span><small>Package</small><strong>{selected.package}{selected.version ? `@${selected.version}` : " · version not recorded"}</strong></span>
                  <i aria-hidden="true"><small>has published</small>→</i>
                  <span><small>Advisories</small><strong>{selected.advisories.length}</strong></span>
                  <i aria-hidden="true"><small>linked to</small>→</i>
                  <span><small>Linked code</small><strong>{selected.code_entities.length}</strong></span>
                  <i aria-hidden="true"><small>evaluated by</small>→</i>
                  <span><small>Policy evaluation</small><code>{selected.policy_evaluation_id}</code></span>
                  <i aria-hidden="true"><small>requires</small>→</i>
                  <span className="is-action"><small>{selected.review_request_id ? "Security review" : "Developer action"}</small><strong>{selected.review_request_id ? selected.review_status?.replaceAll("_", " ") ?? "Request sent" : "Choose next step"}</strong></span>
                </div>
              </section>

              <section className="focus-evidence">
                <div className="focus-section-heading"><p className="workflow-eyebrow">Evidence graph</p><span>Native HyperMesh projection</span></div>
                {!selected.run_id ? <StateFrame kind="empty" title="No native graph for this check" detail="The package facts remain available below." /> : graph.isPending ? <StateFrame kind="loading" title="Loading native evidence" detail="Reading the run projection." /> : graph.isError ? <StateFrame kind="error" title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Evidence could not be loaded"} detail="The recorded package facts remain available." action={<button type="button" onClick={() => void graph.refetch()}>Try again</button>} /> : graph.data ? <ReviewEvidenceGraph graph={graph.data} label={`Evidence for ${selected.package}`} /> : <StateFrame kind="empty" title="No native evidence is available" />}
              </section>

              {selected.suggested_version ? (
                <section className="attention-version-signal is-informational">
                  <span className="dev-tone-info"><DevIcon name="warning" /></span>
                  <span><small>Published advisory data</small><strong>Highest fixed-version candidate: {selected.suggested_version}</strong><p>Compatibility and policy status are unverified until the next package evaluation.</p></span>
                </section>
              ) : null}

              <div className="focus-disclosures">
                <details><summary>Advisories <span>{selected.advisories.length}</span></summary><div>{selected.advisories.length ? selected.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />) : <p>No published advisories were returned for this check.</p>}</div></details>
                <details><summary>Linked code <span>{selected.code_entities.length}</span></summary><div>{selected.code_entities.length ? <ul className="dev-compact-list">{selected.code_entities.map((entity) => <li key={entity} className="dev-compact-row"><DevIcon name="code" size={16} /><span className="dev-compact-row-main"><strong>{entity}</strong></span></li>)}</ul> : <p>No linked code entities were returned.</p>}</div></details>
                <details><summary>Why this needs attention <span>{selected.reasons.length}</span></summary><div>{selected.reasons.length ? <ul className="focus-reason-list">{selected.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>No additional reason was returned.</p>}{selected.unavailable ? <p className="text-risk"><strong>Check incomplete.</strong> {selected.unavailable}</p> : null}</div></details>
              </div>
            </article>
          ) : attention.isPending ? <StateFrame kind="loading" title="Loading attention" /> : <StateFrame kind="empty" title="No selected package signal" detail="Choose an item from the queue." />}
        </section>

        <ResponsibilityDock
          responsibility={selected ? (selected.review_request_id ? "Follow the recorded Security review." : `Choose the next step for ${selected.package}.`) : "No action is waiting."}
          why={selected ? (selected.unavailable || selected.reasons[0] || "The server returned this package check for your attention.") : "New package checks that require your decision will appear in the queue."}
        >
          {selected?.review_request_id ? <Link to="/developer/reviews/$requestId" params={{ requestId: selected.review_request_id }} className="dev-action dev-action-primary w-full">Open review <DevIcon name="arrow" size={16} /></Link> : selected ? <button type="button" className="dev-action dev-action-primary w-full" onClick={() => setRequestOpen(true)}>Ask Security <DevIcon name="arrow" size={16} /></button> : null}
          {selected ? <div className={`attention-dock-verdict is-${verdictTone(selected)}`}><small>Recorded result</small><strong>{selected.verdict === "block" ? "Session action required" : selected.unavailable ? "Check incomplete" : selected.verdict}</strong>{selected.suggested_version ? <span>Published candidate {selected.suggested_version} · unverified</span> : null}</div> : null}
          {selected ? <dl className="responsibility-facts"><div><dt>Package</dt><dd>{selected.package}{selected.version ? `@${selected.version}` : ""}</dd></div><div><dt>Priority</dt><dd>{selected.priority}</dd></div><div><dt>Checked</dt><dd>{relativeTimeMs(selected.checked_at_ms)}</dd></div></dl> : null}
          <p className="text-xs leading-relaxed text-slate">Only your own sessions and review requests are returned by the server.</p>
        </ResponsibilityDock>
      </div>
      {requestOpen && selected ? <RequestDrawer key={selected.id} item={selected} close={() => setRequestOpen(false)} /> : null}
    </main>
  );
}
