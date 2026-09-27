import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { useDeveloperProject } from "../components/DeveloperProject";
import { AdvisoryCard } from "../components/ReviewUI";
import { SidePanel } from "../components/DeveloperVisual";
import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type EvidenceLens,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { StateFrame } from "../components/WorkflowVisual";
import {
  ApiError,
  api,
  type AttentionEvidenceAnswer,
  type AttentionEvidenceQuestion,
  type AttentionItem,
  type ReviewKind,
} from "../lib/api";
import { relativeTimeMs } from "../lib/format";

type Filter = "open" | "blocked" | "unknown" | "sent" | "all";
type RequestStep = 1 | 2 | 3;

const evidenceQuestions: Array<{
  id: AttentionEvidenceQuestion;
  label: string;
  lens: EvidenceLens;
}> = [
  { id: "why_blocked", label: "Why was this blocked?", lens: "why" },
  { id: "affected_code", label: "What code is affected?", lens: "affected" },
  { id: "outcome_change", label: "What changes the outcome?", lens: "outcome" },
];

function EvidenceAnswerPanel({
  answer,
  question,
  setQuestion,
  packageName,
  mobile = false,
}: {
  answer: AttentionEvidenceAnswer;
  question: AttentionEvidenceQuestion;
  setQuestion: (question: AttentionEvidenceQuestion) => void;
  packageName: string;
  mobile?: boolean;
}) {
  const selectedQuestion = evidenceQuestions.find((item) => item.id === question)!;
  return (
    <div className={`figma3-answer ${mobile ? "is-mobile" : ""}`}>
      <div className="figma3-server-questions" role="tablist" aria-label="Evidence question">
        {evidenceQuestions.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={question === item.id}
            onClick={() => setQuestion(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <section className={`figma3-answer-summary is-${answer.status}`} aria-live="polite">
        <div>
          <span>{answer.status}</span>
          <h2>{answer.headline}</h2>
        </div>
        {answer.statements.length ? (
          <ul>
            {answer.statements.slice(0, 3).map((statement) => (
              <li key={`${statement.text}-${statement.relation_ids.join("-")}`}>{statement.text}</li>
            ))}
          </ul>
        ) : <p>No supporting statement was recorded for this question.</p>}
        {answer.statements.length > 3 ? (
          <details className="figma3-answer-more">
            <summary>More recorded statements ({answer.statements.length - 3})</summary>
            <ul>
              {answer.statements.slice(3).map((statement) => (
                <li key={`${statement.text}-${statement.relation_ids.join("-")}`}>{statement.text}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>
      <FigmaEvidenceGraph
        graph={answer.graph}
        label={`${selectedQuestion.label} · ${packageName}`}
        lens={selectedQuestion.lens}
        showQuestions={false}
        height={200}
        mobileEvidenceOnly={mobile}
      />
      {answer.limitations.length ? (
        <details className="figma3-answer-limitations">
          <summary>Evidence boundary</summary>
          <ul>{answer.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        </details>
      ) : null}
    </div>
  );
}

export function filterAttention(items: AttentionItem[], filter: Filter): AttentionItem[] {
  if (filter === "open") return items.filter((item) => !item.review_status || !["verified", "false_positive", "not_approved"].includes(item.review_status));
  if (filter === "blocked") return items.filter((item) => item.verdict === "block");
  if (filter === "unknown") return items.filter((item) => item.verdict === "unknown" || Boolean(item.unavailable));
  if (filter === "sent") return items.filter((item) => Boolean(item.review_request_id));
  return items;
}

function currentJourneyStage(item: AttentionItem): FigmaJourneyStage {
  if (["verified", "false_positive", "not_approved"].includes(item.review_status ?? "")) return "verified";
  if (item.review_status === "exception_approved") return "remediation";
  if (item.review_status === "escalated") return "investigation";
  if (item.review_request_id) return "security_review";
  return "developer_action";
}

function severity(item: AttentionItem): string {
  return item.worst ?? (item.verdict === "block" ? "critical" : item.verdict);
}

function conciseDescription(item: AttentionItem): string {
  const coordinate = `${item.package}${item.version ? ` ${item.version}` : ""}`;
  if (item.unavailable) return `${coordinate} could not be fully evaluated. ${item.unavailable}`;
  const advisory = item.advisories[0];
  if (advisory) {
    return `Your connected session introduced ${coordinate}, which has ${advisory.severity === "unknown" ? "a published" : `a ${advisory.severity}`} advisory (${advisory.id}). The recorded policy evaluation requires your response before this item can be resolved.`;
  }
  if (item.reasons[0]) return `${coordinate} requires your response. ${item.reasons[0]}`;
  return `meshAgent recorded ${coordinate} in this connected session and returned it for your attention.`;
}

function QueueRow({ item, selected, onSelect }: { item: AttentionItem; selected: boolean; onSelect: () => void }) {
  const tone = item.verdict === "block" ? "blocked" : severity(item);
  return (
    <button type="button" role="option" aria-selected={selected} className={`figma3-queue-row is-${tone} ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="figma3-queue-row-top">
        <strong>{item.package}{item.version ? ` ${item.version}` : ""}</strong>
        <span className={`figma3-severity is-${severity(item)}`}>{item.verdict === "block" ? "Blocked" : severity(item)}</span>
      </span>
      <small>{item.policy_evaluation_id} · {item.repository_name}</small>
      <small>{item.advisories[0]?.id ?? "Check incomplete"} · {relativeTimeMs(item.checked_at_ms)}</small>
    </button>
  );
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
        <div className="attention-drawer-progress" aria-label={`Step ${step} of 3`}>{[1, 2, 3].map((value) => <span key={value} className={value <= step ? "is-active" : ""} />)}</div>
        <div><p className="workflow-eyebrow">Step {step} of 3</p><h2>{step === 1 ? "Choose the review you need" : step === 2 ? "Explain the constraint" : "Review the exact request"}</h2></div>
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

function EvidencePath({ item }: { item: AttentionItem }) {
  const tokens = [
    { label: item.session_id, sub: "Connected session", mono: true },
    { label: `${item.package}${item.version ? ` ${item.version}` : ""}`, sub: item.ecosystem, mono: true },
    { label: item.advisories[0]?.id ?? `${item.advisories.length} advisories`, sub: item.worst ? `${item.worst} severity` : "Published advisory data", mono: true },
    { label: `${item.code_entities.length} code location${item.code_entities.length === 1 ? "" : "s"}`, sub: item.repository_name, mono: false },
    { label: item.policy_evaluation_id, sub: "Policy evaluation", mono: true },
    { label: item.review_request_id ? "Security review" : "Developer action", sub: item.review_request_id ? item.review_status?.replaceAll("_", " ") ?? "Request sent" : "Required", mono: false },
  ];
  const verbs = ["introduced", "has advisory", "reaches", "evaluated by", "requires"];
  return (
    <div className="figma3-evidence-chain">
      {tokens.flatMap((token, index) => [
        <span key={`token-${index}`} className={`figma3-evidence-token ${index === tokens.length - 1 ? "is-action" : ""}`}>
          {token.mono ? <code title={token.label}>{token.label}</code> : <strong title={token.label}>{token.label}</strong>}
          <small>{token.sub}</small>
        </span>,
        index < verbs.length ? <i key={`verb-${index}`} className="figma3-evidence-verb">{verbs[index]}</i> : null,
      ])}
    </div>
  );
}

function RemediationSignal({ item }: { item: AttentionItem }) {
  if (!item.suggested_version) {
    return (
      <section className="figma3-remediation">
        <span className="figma3-remediation-icon"><DevIcon name="security" size={15} /></span>
        <span className="figma3-remediation-copy"><small>Next verified step</small><strong>Ask Security to evaluate this signal.</strong><p>No fixed-version candidate was returned by the advisory data.</p></span>
      </section>
    );
  }
  return (
    <section className="figma3-remediation" id="version-path">
      <span className="figma3-remediation-icon"><DevIcon name="arrow" size={15} /></span>
      <span className="figma3-remediation-copy">
        <small>Published fixed-version candidate</small>
        <strong>{item.package} {item.version} → {item.package} {item.suggested_version}</strong>
        <p>The advisory identifies this candidate. Compatibility and organizational policy remain unverified until the next package evaluation.</p>
      </span>
    </section>
  );
}

function MoreItems({ items, selectedId, filter, setFilter, select, selected }: {
  items: AttentionItem[];
  selectedId: string | null;
  filter: Filter;
  setFilter: (filter: Filter) => void;
  select: (id: string) => void;
  selected: AttentionItem;
}) {
  const others = items.filter((item) => item.id !== selectedId);
  return (
    <details className="figma3-more">
      <summary>More items ({others.length} other{others.length === 1 ? "" : "s"})</summary>
      <div className="figma3-queue-filters" aria-label="Attention filter">
        {(["open", "blocked", "unknown", "sent", "all"] as Filter[]).map((value) => <button key={value} type="button" aria-pressed={filter === value} onClick={(event) => { event.preventDefault(); setFilter(value); }}>{value === "sent" ? "With security" : value[0].toUpperCase() + value.slice(1)}</button>)}
      </div>
      {others.length ? <div className="figma3-more-list">{others.map((item) => <QueueRow key={item.id} item={item} selected={false} onSelect={() => select(item.id)} />)}</div> : <p>No other attention items in this view.</p>}
      <details className="figma3-detail-disclosures">
        <summary>Record evidence</summary>
        <div>
          <strong>Advisories ({selected.advisories.length})</strong>
          {selected.advisories.length ? selected.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />) : <p>No published advisories were returned.</p>}
          <strong>Linked code ({selected.code_entities.length})</strong>
          {selected.code_entities.length ? <ul>{selected.code_entities.map((entity) => <li key={entity}><code>{entity}</code></li>)}</ul> : <p>No linked code entities were returned.</p>}
        </div>
      </details>
    </details>
  );
}

function QuickSteps({ item }: { item: AttentionItem }) {
  const steps = item.suggested_version ? [
    `Review ${item.package} ${item.suggested_version} in the project manifest.`,
    "Test the dependency change in a separate branch.",
    "Run the project tests and package evaluation again.",
    "Ask Security if policy still blocks the session.",
  ] : [
    "Open the recorded evidence.",
    "Describe the business or delivery constraint.",
    "Send the governed request to Security.",
    "Follow the returned review result.",
  ];
  return (
    <div className="figma3-quick-steps">
      <p className="figma3-kicker">Quick steps</p>
      {steps.map((step, index) => <div key={step} className="figma3-quick-step"><span>{index + 1}</span><div>{step}</div></div>)}
    </div>
  );
}

export function DeveloperAttention() {
  const project = useDeveloperProject();
  const [filter, setFilter] = useState<Filter>("open");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evidenceQuestion, setEvidenceQuestion] = useState<AttentionEvidenceQuestion>("why_blocked");
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
  const evidence = useQuery({
    queryKey: ["developer-attention", selected?.id ?? "none", "evidence"],
    queryFn: () => api.developerAttentionEvidence(selected!.id),
    enabled: Boolean(selected),
    retry: false,
    refetchInterval: 15_000,
  });
  const answer = evidence.data?.[evidenceQuestion];

  const select = (id: string) => {
    setSelectedId(id);
    setEvidenceQuestion("why_blocked");
  };
  const primaryAction = selected?.review_request_id ? (
    <Link to="/developer/reviews/$requestId" params={{ requestId: selected.review_request_id }} className="figma3-primary">Open Security review <DevIcon name="arrow" size={15} /></Link>
  ) : selected?.suggested_version ? (
    <button type="button" className="figma3-primary" onClick={() => document.getElementById("version-path")?.scrollIntoView({ behavior: "smooth", block: "center" })}>View version steps <DevIcon name="arrow" size={15} /></button>
  ) : selected ? (
    <button type="button" className="figma3-primary" onClick={() => setRequestOpen(true)}>Ask Security <DevIcon name="arrow" size={15} /></button>
  ) : null;
  const secondaryAction = selected && !selected.review_request_id && selected.suggested_version ? <button type="button" className="figma3-secondary" onClick={() => setRequestOpen(true)}>Ask Security</button> : null;

  const mainContent = selected ? (
    <>
      <h1>This change needs your attention.</h1>
      <p className="figma3-attention-lede">{conciseDescription(selected)}</p>
      <section className="figma3-journey-card"><p className="figma3-kicker">Workflow</p><FigmaJourney current={currentJourneyStage(selected)} label="Attention item journey" /></section>
      <section className="figma3-evidence-path"><p className="figma3-kicker">Evidence path</p><EvidencePath item={selected} /></section>
      <section className="figma3-evidence-section">
        <p className="figma3-kicker">Evidence answers</p>
        {evidence.isPending ? <StateFrame kind="loading" title="Loading scoped evidence" detail="Resolving this policy evaluation against its native HyperMesh relations." /> : evidence.isError ? <StateFrame kind="error" title={evidence.error instanceof ApiError && evidence.error.status === 404 ? "Evidence projection is pending" : "Evidence could not be loaded"} detail="No run-wide graph is substituted because it could imply relationships that were not recorded for this item." action={<button type="button" onClick={() => void evidence.refetch()}>Try again</button>} /> : answer ? <EvidenceAnswerPanel answer={answer} question={evidenceQuestion} setQuestion={setEvidenceQuestion} packageName={selected.package} /> : <StateFrame kind="empty" title="No scoped evidence is available" />}
      </section>
      <RemediationSignal item={selected} />
      <MoreItems items={items} selectedId={selectedId} filter={filter} setFilter={setFilter} select={select} selected={selected} />
    </>
  ) : attention.isPending ? <StateFrame kind="loading" title="Loading attention" detail="Reading your connected sessions." /> : attention.isError ? <StateFrame kind="error" title="Could not load attention" detail={attention.error instanceof Error ? attention.error.message : "Try again."} action={<button type="button" onClick={() => void attention.refetch()}>Try again</button>} /> : <StateFrame kind="empty" title="No items need your attention" detail="Your recent sessions have no unresolved package signals in this view." />;

  return (
    <main className="figma3-page">
      <div className="figma3-attention-desktop figma3-desktop-only">
        <aside className="figma3-attention-queue" aria-label="Attention queue">
          <header><strong>Attention</strong><span>{items.length} item{items.length === 1 ? "" : "s"} · {project.projectId === "all" ? "all projects" : project.projectId}</span></header>
          <div className="figma3-attention-queue-body" role="listbox" aria-label="Attention items">
            {selected ? <QueueRow item={selected} selected onSelect={() => undefined} /> : attention.isPending ? <StateFrame kind="loading" title="Loading" /> : <StateFrame kind="empty" title="No items" />}
          </div>
        </aside>
        <section className="figma3-attention-main" aria-label="Selected attention item"><article className="figma3-attention-body">{mainContent}</article></section>
        <FigmaResponsibilityDock
          responsibility={selected ? (selected.review_request_id ? "Follow the recorded Security review." : "Review the version path or ask Security.") : "No action is waiting."}
          why={selected ? "This item remains governed by its recorded policy evaluation. Only evidence returned for your own session is shown." : "New package checks that require your response will appear here."}
          actions={<>{primaryAction}{secondaryAction}</>}
          blockingCondition={selected?.verdict === "block" ? "This recorded policy evaluation remains blocked until a governed next step is completed." : undefined}
        >
          {selected ? <QuickSteps item={selected} /> : null}
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-mobile-attention figma3-mobile-only">
        <div className="figma3-mobile-scroll">
          {selected ? (
            <>
              <h1>This change needs your attention.</h1>
              <p className="figma3-attention-lede">{conciseDescription(selected)}</p>
              <div className="figma3-mobile-summary-card"><RemediationSignal item={selected} /></div>
              <section className="figma3-mobile-journey"><p className="figma3-kicker">Journey</p><FigmaJourney current={currentJourneyStage(selected)} vertical label="Attention item journey" /></section>
              {evidence.isError ? <StateFrame kind="error" title="Evidence could not be loaded" detail="The Attention record remains available while the scoped evidence request is retried." action={<button type="button" onClick={() => void evidence.refetch()}>Try again</button>} /> : answer ? <EvidenceAnswerPanel answer={answer} question={evidenceQuestion} setQuestion={setEvidenceQuestion} packageName={selected.package} mobile /> : <StateFrame kind={evidence.isPending ? "loading" : "empty"} title={evidence.isPending ? "Loading scoped evidence" : "Evidence trail unavailable"} />}
              <MoreItems items={items} selectedId={selectedId} filter={filter} setFilter={setFilter} select={select} selected={selected} />
            </>
          ) : mainContent}
        </div>
        {selected && primaryAction ? <FigmaMobileDock><>{primaryAction}{secondaryAction}</></FigmaMobileDock> : null}
      </div>
      {requestOpen && selected ? <RequestDrawer key={selected.id} item={selected} close={() => setRequestOpen(false)} /> : null}
    </main>
  );
}
