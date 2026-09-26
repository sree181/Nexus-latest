import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DeveloperTopbar } from "../components/DeveloperProject";
import { DevIcon } from "../components/DeveloperIcons";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge, reviewVisual } from "../components/ReviewUI";
import { Status } from "../components/DeveloperVisual";
import { IntegrityRef, ResponsibilityDock, StateFrame, WorkflowHero, WorkflowJourney, WorkflowSection, type JourneyItem } from "../components/WorkflowVisual";
import { ApiError, api, type ReviewRequest } from "../lib/api";
import { timestamp } from "../lib/format";

export function reviewNextStep(review: ReviewRequest): { title: string; detail: string; icon: "check" | "warning" | "retry" | "lock" | "live" } {
  if (review.state === "changes_requested") return { title: review.recommended_version ? `Use ${review.recommended_version}` : "Change the package version", detail: "Run the package operation again. The next policy evaluation will determine whether the issue is resolved.", icon: "retry" };
  if (review.state === "waiting") return { title: "Security is reviewing", detail: "The answer will appear here when an authorized reviewer records it.", icon: "live" };
  if (review.state === "exception_approved") return { title: "Temporary approval active", detail: review.exception_expires_at ? `Valid until ${timestamp(review.exception_expires_at)}.` : "Review the recorded conditions below.", icon: "check" };
  if (review.state === "verified") return { title: "Fix verified", detail: review.verification_evidence_id ? "A later package check recorded verification evidence." : "A later clean package check closed this request.", icon: "check" };
  if (review.state === "false_positive") return { title: "No change required", detail: "Security recorded this signal as not applicable.", icon: "check" };
  if (review.state === "escalated") return { title: "Governance review continues", detail: "Security escalated this request. This view will update when a governed outcome is recorded.", icon: "warning" };
  return { title: "Do not use this version", detail: review.decision_rationale ?? "Security did not approve this request.", icon: "lock" };
}

function reviewJourney(review: ReviewRequest): JourneyItem[] {
  const decided = review.state !== "waiting";
  const terminal = ["changes_requested", "exception_approved", "not_approved", "false_positive", "verified"].includes(review.state);
  return [
    { id: "request", label: "Request sent", detail: timestamp(review.created_at), state: "complete" },
    { id: "review", label: "Security review", detail: review.analyst_name ?? "Awaiting owner", state: decided ? "complete" : "current" },
    { id: "governance", label: "Governance", detail: review.state === "escalated" ? "Escalated" : "As needed", state: review.state === "escalated" ? "current" : terminal ? "complete" : "pending" },
    { id: "outcome", label: "Result", detail: review.state.replaceAll("_", " "), state: terminal ? "complete" : "pending" },
  ];
}

export function DeveloperReviewDetail() {
  const { requestId } = useParams({ strict: false }) as { requestId: string };
  const review = useQuery({ queryKey: ["developer-review", requestId], queryFn: () => api.developerReviewRequest(requestId), refetchInterval: 8_000 });
  const graph = useQuery({ queryKey: ["developer-review", requestId, "graph"], queryFn: () => api.developerReviewGraph(requestId), refetchInterval: 12_000, retry: false });

  return (
    <main className="dev-page workflow-page">
      <DeveloperTopbar title="Review result" showProject={false} actions={<Link to="/developer/attention" className="dev-icon-button" aria-label="Back to Attention"><DevIcon name="x" /></Link>} />
      <div className="dev-scroll workflow-scroll">
        {review.isPending ? <StateFrame kind="loading" title="Loading review" detail="Reading the governed result and evidence." /> : review.isError || !review.data ? <StateFrame kind="error" title="This review could not be opened" detail={review.error instanceof Error ? review.error.message : "The record is not available."} action={<Link to="/developer/attention" className="dev-action">Back to Attention</Link>} /> : (() => {
          const item = review.data;
          const action = reviewNextStep(item);
          const visual = reviewVisual(item.state);
          return (
            <div className="workflow-layout">
              <div className="workflow-main">
                <WorkflowHero
                  eyebrow={`Developer review · ${item.id}`}
                  title={action.title}
                  description={action.detail}
                  tone={visual.tone === "danger" ? "danger" : visual.tone === "success" ? "success" : visual.tone === "info" ? "info" : "warning"}
                  status={<ReviewStateBadge state={item.state} />}
                  meta={<><span>{item.repository_name}</span><span>{item.package}@{item.version || "unpinned"}</span><span>Updated {timestamp(item.updated_at)}</span></>}
                >
                  <span className={`review-attention-priority dev-tone-${visual.tone}`}><DevIcon name={action.icon} /></span>
                </WorkflowHero>

                <WorkflowSection eyebrow="Journey" title="Where this request stands">
                  <WorkflowJourney items={reviewJourney(item)} label="Developer review journey" />
                </WorkflowSection>

                {item.decision_rationale || item.rationale ? (
                  <WorkflowSection eyebrow={item.decision_rationale ? "Security response" : "Submitted context"} title={item.decision_rationale ? "Recorded decision" : "Your request"}>
                    <blockquote className="review-quote"><DevIcon name="prompt" /><span><small>{item.decision_rationale ?? item.rationale}</small></span></blockquote>
                  </WorkflowSection>
                ) : null}

                {item.advisories.length ? <WorkflowSection eyebrow="Package evidence" title="Published advisories" action={<Status label={item.severity} tone={item.severity === "critical" || item.severity === "high" ? "danger" : "warning"} />}>{item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</WorkflowSection> : null}

                <WorkflowSection eyebrow="Evidence" title="Relationship map" description="The visual field and relationship list represent the same native GraphPayload.">
                  {graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review record is already available." /> : graph.isError ? <StateFrame kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"} title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"} detail={graph.error instanceof Error ? graph.error.message : "Try again when the evidence service is available."} action={<button type="button" className="dev-action" onClick={() => void graph.refetch()}>Retry map</button>} /> : <><ReviewEvidenceGraph graph={graph.data.graph} label="Review evidence relationship map" /><p className="review-graph-note">{graph.data.note}</p></>}
                </WorkflowSection>

                <WorkflowSection eyebrow="Audit trail" title="Review history">
                  {item.events.length ? <ol className="workflow-list">{[...item.events].reverse().map((event) => <li className="workflow-list-row" key={event.id}><span className="dev-tone-accent"><DevIcon name={event.action === "review.verified" ? "check" : "activity"} /></span><span className="workflow-list-row-main"><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span><span className="dev-relative-time">{timestamp(event.at)}</span></li>)}</ol> : <StateFrame kind="empty" title="No review events yet" />}
                </WorkflowSection>
              </div>

              <ResponsibilityDock
                label="Your next step"
                responsibility={action.title}
                why={action.detail}
                deadline={item.exception_expires_at ? timestamp(item.exception_expires_at) : undefined}
                overdue={Boolean(item.exception_expires_at && item.exception_expires_at * 1000 < Date.now())}
              >
                {item.state === "changes_requested" ? <Link to="/developer/sessions" className="dev-action dev-action-primary w-full">Open sessions<DevIcon name="arrow" size={16} /></Link> : null}
                <Link to="/developer/attention" className="dev-action w-full">Back to Attention</Link>
                <div className="grid gap-2">
                  <IntegrityRef label="Review" value={item.id} />
                  <IntegrityRef label="Evidence root" value={item.evidence_root_ulid} />
                  <IntegrityRef label="Evidence digest" value={item.evidence_digest} />
                  <IntegrityRef label="Verification" value={item.verification_evidence_id} />
                </div>
                <p className="text-xs leading-relaxed text-slate">This page is read-only. Decisions and evidence are recorded by authorized security roles.</p>
              </ResponsibilityDock>
            </div>
          );
        })()}
      </div>
    </main>
  );
}
