import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import "./developer-result.css";

import { DevIcon } from "../components/DeveloperIcons";
import { AdvisoryCard, ReviewStateBadge, reviewVisual } from "../components/ReviewUI";
import {
  FigmaEvidenceGraph,
  FigmaJourney,
  FigmaMobileDock,
  FigmaResponsibilityDock,
  type FigmaJourneyStage,
} from "../components/FigmaWorkflowV3";
import { IntegrityRef, StateFrame } from "../components/WorkflowVisual";
import { ApiError, api, type ReviewRequest } from "../lib/api";
import { timestamp } from "../lib/format";

/** This guidance intentionally derives only from the server-recorded review state. */
export function reviewNextStep(review: ReviewRequest): { title: string; detail: string; icon: "check" | "warning" | "retry" | "lock" | "live" } {
  if (review.state === "changes_requested") return { title: review.recommended_version ? `Use ${review.recommended_version}` : "Change the package version", detail: "Run the package operation again. The next policy evaluation will determine whether the issue is resolved.", icon: "retry" };
  if (review.state === "waiting") return { title: "Security is reviewing", detail: "The answer will appear here when an authorized reviewer records it.", icon: "live" };
  if (review.state === "exception_approved") return { title: "Temporary approval active", detail: review.exception_expires_at ? `Valid until ${timestamp(review.exception_expires_at)}.` : "Review the recorded conditions below.", icon: "check" };
  if (review.state === "verified") return { title: "Fix verified", detail: review.verification_evidence_id ? "A later package check recorded verification evidence." : "A later clean package check closed this request.", icon: "check" };
  if (review.state === "false_positive") return { title: "No change required", detail: "Security recorded this signal as not applicable.", icon: "check" };
  if (review.state === "escalated") return { title: "Governance review continues", detail: "Security escalated this request. This view will update when a governed outcome is recorded.", icon: "warning" };
  return { title: "Do not use this version", detail: review.decision_rationale ?? "Security did not approve this request.", icon: "lock" };
}

function reviewJourneyStage(review: ReviewRequest): FigmaJourneyStage {
  if (review.state === "waiting") return "security_review";
  if (review.state === "escalated") return "investigation";
  if (review.state === "exception_approved" || review.state === "not_approved") return "ciso_decision";
  if (review.state === "changes_requested") return "remediation";
  return "verified";
}

function EvidencePath({ review }: { review: ReviewRequest }) {
  const packageCoordinate = `${review.package}@${review.version || "unpinned"}`;
  const firstAdvisory = review.advisories[0];
  const tokens = [
    { label: review.session_id, sub: "Connected session", mono: true },
    { label: packageCoordinate, sub: review.ecosystem, mono: true },
    {
      label: firstAdvisory?.id ?? (review.advisories.length ? `${review.advisories.length} advisories` : "No published advisory"),
      sub: firstAdvisory ? `${firstAdvisory.severity} severity` : "Published advisory data",
      mono: true,
    },
    { label: review.policy_evaluation_id, sub: `${review.verdict} policy evaluation`, mono: true },
    { label: review.id, sub: review.state.replaceAll("_", " "), mono: true, action: true },
  ];
  const verbs = ["recorded", "checked against", "evaluated by", "reviewed as"];

  return (
    <div className="figma3-result-evidence-chain" aria-label="Recorded evidence path">
      {tokens.flatMap((token, index) => [
        <span key={`token-${token.label}-${index}`} className={`figma3-result-evidence-token ${token.action ? "is-action" : ""}`}>
          {token.mono ? <code title={token.label}>{token.label}</code> : <strong title={token.label}>{token.label}</strong>}
          <small>{token.sub}</small>
        </span>,
        index < verbs.length ? <i key={`verb-${index}`} className="figma3-result-evidence-verb">{verbs[index]}</i> : null,
      ])}
    </div>
  );
}

function RecordedFacts({ review, expired }: { review: ReviewRequest; expired: boolean }) {
  if (!review.exception_expires_at && !review.escalated_case_id && !review.verification_evidence_id) return null;
  return (
    <section className="figma3-result-facts" aria-label="Recorded outcome facts">
      {review.exception_expires_at ? (
        <div className={expired ? "is-risk" : ""}>
          <small>{expired ? "Expired" : "Exception expires"}</small>
          <strong>{timestamp(review.exception_expires_at)}</strong>
        </div>
      ) : null}
      {review.escalated_case_id ? <div><small>Tracked case</small><strong>{review.escalated_case_id}</strong></div> : null}
      {review.verification_evidence_id ? <div><small>Verification evidence</small><strong>{review.verification_evidence_id}</strong></div> : null}
    </section>
  );
}

function ReviewDisclosures({ review }: { review: ReviewRequest }) {
  return (
    <div className="figma3-result-disclosures">
      <details>
        <summary><span>Published advisories</span><span>{review.advisories.length}</span><DevIcon name="chevron" size={14} /></summary>
        <div>
          {review.advisories.length ? review.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />) : <p>No published advisories were returned.</p>}
        </div>
      </details>
      <details>
        <summary><span>Review history</span><span>{review.events.length}</span><DevIcon name="chevron" size={14} /></summary>
        <div>
          {review.events.length ? (
            <ol className="figma3-result-history">
              {[...review.events].reverse().map((event) => (
                <li key={event.id}>
                  <span className="figma3-result-history-icon"><DevIcon name={event.action === "review.verified" ? "check" : "activity"} size={14} /></span>
                  <span className="figma3-result-history-copy"><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span>
                  <time>{timestamp(event.at)}</time>
                </li>
              ))}
            </ol>
          ) : <p>No review events have been recorded.</p>}
        </div>
      </details>
    </div>
  );
}

function DockFacts({ review }: { review: ReviewRequest }) {
  return (
    <>
      <dl className="figma3-result-dock-facts">
        <div><dt>Package</dt><dd>{review.package}@{review.version || "unpinned"}</dd></div>
        <div><dt>Review</dt><dd>{review.id}</dd></div>
        <div><dt>Reviewed by</dt><dd>{review.analyst_name ?? "Awaiting owner"}</dd></div>
        <div><dt>Outcome</dt><dd>{review.state.replaceAll("_", " ")}</dd></div>
      </dl>
      <div className="figma3-result-integrity">
        <IntegrityRef label="Review" value={review.id} />
        <IntegrityRef label="Evidence root" value={review.evidence_root_ulid} />
        <IntegrityRef label="Evidence digest" value={review.evidence_digest} />
        <IntegrityRef label="Verification" value={review.verification_evidence_id} />
      </div>
      <p className="figma3-result-dock-notice">Decisions and evidence are recorded by authorized security roles.</p>
    </>
  );
}

export function DeveloperReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  // Both endpoints are server-scoped to the authenticated developer and deliberately remain independently refreshed.
  const review = useQuery({ queryKey: ["developer-review", requestId], queryFn: () => api.developerReviewRequest(requestId), refetchInterval: 8_000 });
  const graph = useQuery({ queryKey: ["developer-review", requestId, "graph"], queryFn: () => api.developerReviewGraph(requestId), refetchInterval: 12_000, retry: false });

  if (review.isPending) {
    return <main className="figma3-page figma3-result-page"><div className="figma3-result-state"><StateFrame kind="loading" title="Loading review" detail="Reading the governed result and evidence." /></div></main>;
  }

  if (review.isError || !review.data) {
    return (
      <main className="figma3-page figma3-result-page">
        <div className="figma3-result-state"><StateFrame kind="error" title="This review could not be opened" detail={review.error instanceof Error ? review.error.message : "The record is not available."} action={<Link to="/developer/attention" className="figma3-secondary">Back to Attention</Link>} /></div>
      </main>
    );
  }

  const item = review.data;
  const action = reviewNextStep(item);
  const visual = reviewVisual(item.state);
  const expired = Boolean(item.exception_expires_at && item.exception_expires_at * 1000 < Date.now());
  const primaryAction = item.state === "changes_requested" ? (
    <Link to="/developer/sessions" className="figma3-primary">Open sessions <DevIcon name="arrow" size={16} /></Link>
  ) : (
    <Link to="/developer/attention" className="figma3-secondary">Back to Attention <DevIcon name="arrow" size={16} /></Link>
  );
  const dockDeadline = item.exception_expires_at ? timestamp(item.exception_expires_at) : undefined;

  return (
    <main className="figma3-page figma3-result-page">
      <div className="figma3-result-desktop figma3-desktop-only">
        <section className="figma3-result-main" aria-label="Review result">
          <article className="figma3-result-narrative">
            <Link to="/developer/attention" className="figma3-result-back"><DevIcon name="arrow" size={14} /> Back to Attention</Link>
            <div className="figma3-result-readonly"><DevIcon name="lock" size={14} /><span>This governed result is read-only for the requesting developer.</span></div>

            <header className={`figma3-result-hero is-${visual.tone}`}>
              <div className="figma3-result-hero-status"><ReviewStateBadge state={item.state} /><code>{item.id}</code></div>
              <div className="figma3-result-hero-copy">
                <span className="figma3-result-hero-icon"><DevIcon name={action.icon} size={19} /></span>
                <div><p>{reviewVisual(item.state).label}</p><h1>{action.title}</h1><div>{action.detail}</div></div>
              </div>
              <div className="figma3-result-hero-meta"><span>{item.repository_name}</span><code>{item.package}@{item.version || "unpinned"}</code><span>Updated {timestamp(item.updated_at)}</span></div>
            </header>

            <section className="figma3-result-action-dock" aria-label="Next step">
              <div><p>Next step</p><strong>{action.title}</strong><span>{action.detail}</span></div>
              {primaryAction}
            </section>

            <RecordedFacts review={item} expired={expired} />

            <section className="figma3-result-journey-card">
              <p className="figma3-kicker">Workflow</p>
              <FigmaJourney current={reviewJourneyStage(item)} label="Developer review journey" />
            </section>

            {item.decision_rationale || item.rationale ? (
              <section className="figma3-result-response">
                <p className="figma3-kicker">{item.decision_rationale ? "Recorded decision" : "Your submitted context"}</p>
                <blockquote><DevIcon name="prompt" size={15} /><span>{item.decision_rationale ?? item.rationale}</span></blockquote>
              </section>
            ) : null}

            <section className="figma3-result-evidence-path">
              <p className="figma3-kicker">Evidence path</p>
              <EvidencePath review={item} />
            </section>

            <section className="figma3-result-evidence-section">
              <p className="figma3-kicker">Evidence graph</p>
              {graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review result is already available." /> : graph.isError ? (
                <StateFrame
                  kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"}
                  title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"}
                  detail={graph.error instanceof Error ? graph.error.message : "Try again when the evidence service is available."}
                  action={<button type="button" className="figma3-secondary" onClick={() => void graph.refetch()}>Retry map</button>}
                />
              ) : (
                <>
                  <FigmaEvidenceGraph graph={graph.data.graph} label="Review evidence relationship map" height={180} />
                  <p className="figma3-result-graph-note">{graph.data.note}</p>
                </>
              )}
            </section>

            <ReviewDisclosures review={item} />
          </article>
        </section>

        <FigmaResponsibilityDock
          className="figma3-result-dock"
          label="Review context"
          responsibility={`${item.package}@${item.version || "unpinned"}`}
          why={`Decision recorded by ${item.analyst_name ?? "Security"}. The evidence below is read-only for the requesting Developer.`}
          actions={primaryAction}
          deadline={dockDeadline}
          overdue={expired}
          blockingCondition={item.state === "changes_requested" ? "A later package evaluation determines whether the issue is resolved." : undefined}
        >
          <DockFacts review={item} />
        </FigmaResponsibilityDock>
      </div>

      <div className="figma3-result-mobile figma3-mobile-only">
        <div className="figma3-result-mobile-scroll">
          <Link to="/developer/attention" className="figma3-result-mobile-back"><DevIcon name="arrow" size={15} /> Back to Attention</Link>
          <div className="figma3-result-readonly"><DevIcon name="lock" size={14} /><span>This governed result is read-only for the requesting developer.</span></div>

          <header className={`figma3-result-hero is-${visual.tone}`}>
            <div className="figma3-result-hero-status"><ReviewStateBadge state={item.state} /><code>{item.id}</code></div>
            <div className="figma3-result-hero-copy"><span className="figma3-result-hero-icon"><DevIcon name={action.icon} size={18} /></span><div><p>{reviewVisual(item.state).label}</p><h1>{action.title}</h1><div>{action.detail}</div></div></div>
            <div className="figma3-result-hero-meta"><span>{item.repository_name}</span><code>{item.package}@{item.version || "unpinned"}</code></div>
          </header>

          <RecordedFacts review={item} expired={expired} />

          <section className="figma3-result-mobile-journey">
            <p className="figma3-kicker">Journey</p>
            <FigmaJourney current={reviewJourneyStage(item)} vertical label="Developer review journey" />
          </section>

          {item.decision_rationale || item.rationale ? <section className="figma3-result-response"><p className="figma3-kicker">{item.decision_rationale ? "Recorded decision" : "Your submitted context"}</p><blockquote><DevIcon name="prompt" size={15} /><span>{item.decision_rationale ?? item.rationale}</span></blockquote></section> : null}

          <section className="figma3-result-mobile-evidence">
            <p className="figma3-kicker">Evidence path</p>
            {graph.data ? <FigmaEvidenceGraph graph={graph.data.graph} label="Review evidence relationship map" mobileEvidenceOnly /> : graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review result is already available." /> : graph.isError ? <StateFrame kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"} title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"} detail={graph.error instanceof Error ? graph.error.message : "Try again when the evidence service is available."} action={<button type="button" className="figma3-secondary" onClick={() => void graph.refetch()}>Retry map</button>} /> : null}
          </section>

          <ReviewDisclosures review={item} />
        </div>
        <FigmaMobileDock deadline={dockDeadline}>{primaryAction}</FigmaMobileDock>
      </div>
    </main>
  );
}
