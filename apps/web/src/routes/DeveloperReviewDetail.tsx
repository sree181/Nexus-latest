import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DeveloperTopbar } from "../components/DeveloperProject";
import { DevIcon } from "../components/DeveloperIcons";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge, reviewVisual } from "../components/ReviewUI";
import { EmptyVisual, Status } from "../components/DeveloperVisual";
import { api, type ReviewRequest } from "../lib/api";
import { timestamp } from "../lib/format";

function nextStep(review: ReviewRequest): { title: string; detail: string; icon: "check" | "warning" | "retry" | "lock" | "live" } {
  if (review.state === "changes_requested") return { title: review.recommended_version ? `Use ${review.recommended_version}` : "Change the package version", detail: "Run the install again. MeshAgent will recheck it and close this request automatically when clean.", icon: "retry" };
  if (review.state === "waiting") return { title: "Security is reviewing", detail: "You can keep working unless the package was blocked. The answer will appear here.", icon: "live" };
  if (review.state === "exception_approved") return { title: "Temporary approval active", detail: review.exception_expires_at ? `Valid until ${timestamp(review.exception_expires_at)}.` : "Check the review history for its conditions.", icon: "check" };
  if (review.state === "verified") return { title: "Nothing else to do", detail: "A clean package check verified the fix.", icon: "check" };
  if (review.state === "false_positive") return { title: "No change needed", detail: "Security reviewed this signal and marked it as not applicable.", icon: "check" };
  if (review.state === "escalated") return { title: "Waiting for a CISO decision", detail: "The security team escalated this request.", icon: "warning" };
  return { title: "Do not use this version", detail: review.decision_rationale ?? "Security did not approve this request.", icon: "lock" };
}

export function DeveloperReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  const review = useQuery({ queryKey: ["developer-review", requestId], queryFn: () => api.developerReviewRequest(requestId), refetchInterval: 8_000 });
  const graph = useQuery({ queryKey: ["developer-review", requestId, "graph"], queryFn: () => api.developerReviewGraph(requestId), refetchInterval: 12_000 });

  return (
    <main className="dev-page">
      <DeveloperTopbar title="Review" showProject={false} actions={<Link to="/developer/attention" className="dev-icon-button" aria-label="Back to Attention"><DevIcon name="x" /></Link>} />
      <div className="dev-scroll dev-stack">
        {review.isPending ? <EmptyVisual icon="live" title="Loading" /> : review.isError || !review.data ? <EmptyVisual icon="warning" title="Could not open review" /> : (() => {
          const item = review.data;
          const action = nextStep(item);
          const visual = reviewVisual(item.state);
          return <>
            <section className="review-decision-banner">
              <span className={`dev-tone-${visual.tone}`}><DevIcon name={action.icon} /></span>
              <span><strong>{action.title}</strong><small>{action.detail}</small></span>
              <ReviewStateBadge state={item.state} />
            </section>
            <section className="dev-surface">
              <header className="dev-panel-heading"><h2>{item.package}@{item.version || "unpinned"}</h2><Status label={item.severity} tone={item.severity === "critical" || item.severity === "high" ? "danger" : "warning"} /></header>
              <div className="review-context-grid">
                <span><small>Project</small><strong>{item.repository_name}</strong></span>
                <span><small>Asked for</small><strong>{item.kind.replaceAll("_", " ")}</strong></span>
                <span><small>Reviewer</small><strong>{item.analyst_name ?? "Waiting"}</strong></span>
                <span><small>Updated</small><strong>{timestamp(item.updated_at)}</strong></span>
              </div>
              {item.decision_rationale ? <blockquote className="review-quote"><DevIcon name="prompt" /><span><strong>Security response</strong><small>{item.decision_rationale}</small></span></blockquote> : <blockquote className="review-quote"><DevIcon name="prompt" /><span><strong>Your note</strong><small>{item.rationale}</small></span></blockquote>}
            </section>
            {item.advisories.length ? <section className="dev-surface"><header className="dev-panel-heading"><h2>Advisories</h2></header>{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</section> : null}
            <section className="dev-surface">
              <header className="dev-panel-heading"><h2>Relationship map</h2></header>
              {graph.data ? <><ReviewEvidenceGraph graph={graph.data.graph} label="Review evidence relationship map" /><p className="review-graph-note">{graph.data.note}</p></> : <EmptyVisual icon="live" title="Loading map" />}
            </section>
            <section className="dev-surface"><header className="dev-panel-heading"><h2>Review history</h2></header>{item.events.map((event) => <div className="dev-compact-row" key={event.id}><DevIcon name={event.action === "review.verified" ? "check" : "activity"} /><span className="dev-compact-row-main"><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.rationale}</small></span><span className="dev-relative-time">{timestamp(event.at)}</span></div>)}</section>
          </>;
        })()}
      </div>
    </main>
  );
}
