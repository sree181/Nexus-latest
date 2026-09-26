import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DeveloperTopbar } from "../components/DeveloperProject";
import { DevIcon } from "../components/DeveloperIcons";
import { ReviewEvidenceGraph } from "../components/ReviewEvidenceGraph";
import { AdvisoryCard, ReviewStateBadge, reviewVisual } from "../components/ReviewUI";
import { IntegrityRef, ResponsibilityDock, StateFrame, WorkflowJourney, type JourneyItem } from "../components/WorkflowVisual";
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
    <main className="dev-page workflow-page result-page">
      <DeveloperTopbar title="Review result" showProject={false} actions={<Link to="/developer/attention" className="dev-icon-button" aria-label="Back to Attention"><DevIcon name="x" /></Link>} />
      {review.isPending ? <div className="workflow-scroll"><StateFrame kind="loading" title="Loading review" detail="Reading the governed result and evidence." /></div> : review.isError || !review.data ? <div className="workflow-scroll"><StateFrame kind="error" title="This review could not be opened" detail={review.error instanceof Error ? review.error.message : "The record is not available."} action={<Link to="/developer/attention" className="dev-action">Back to Attention</Link>} /></div> : (() => {
        const item = review.data;
        const action = reviewNextStep(item);
        const visual = reviewVisual(item.state);
        const expired = Boolean(item.exception_expires_at && item.exception_expires_at * 1000 < Date.now());
        return (
          <div className="result-workspace">
            <section className="result-scroll" aria-label="Review result">
              <article className="result-narrative">
                <Link to="/developer/attention" className="focus-back-link"><DevIcon name="arrow" size={14} /> Back to Attention</Link>
                <div className="result-readonly"><DevIcon name="lock" size={14} /><span>This governed result is read-only for the requesting developer.</span></div>

                <header className={`result-hero is-${visual.tone}`}>
                  <div className="result-hero-status"><ReviewStateBadge state={item.state} /><span>{item.id}</span></div>
                  <div className="result-hero-title"><span className={`review-attention-priority dev-tone-${visual.tone}`}><DevIcon name={action.icon} /></span><div><h1>{action.title}</h1><p>{action.detail}</p></div></div>
                  <div className="result-hero-meta"><span>{item.repository_name}</span><span>{item.package}@{item.version || "unpinned"}</span><span>Updated {timestamp(item.updated_at)}</span></div>
                </header>

                <section className="result-next-step">
                  <div><small>Next step</small><strong>{action.title}</strong></div>
                  {item.state === "changes_requested" ? <Link to="/developer/sessions" className="dev-action dev-action-primary">Open sessions <DevIcon name="arrow" size={16} /></Link> : <Link to="/developer/attention" className="dev-action">Back to Attention</Link>}
                </section>

                {item.exception_expires_at || item.escalated_case_id || item.verification_evidence_id ? (
                  <section className="result-facts" aria-label="Recorded outcome facts">
                    {item.exception_expires_at ? <div className={expired ? "is-risk" : ""}><small>{expired ? "Expired" : "Exception expires"}</small><strong>{timestamp(item.exception_expires_at)}</strong></div> : null}
                    {item.escalated_case_id ? <div><small>Tracked case</small><strong>{item.escalated_case_id}</strong></div> : null}
                    {item.verification_evidence_id ? <div><small>Verification evidence</small><strong>{item.verification_evidence_id}</strong></div> : null}
                  </section>
                ) : null}

                <section className="focus-journey-card">
                  <p className="workflow-eyebrow">Review path</p>
                  <WorkflowJourney items={reviewJourney(item)} label="Developer review journey" />
                </section>

                {item.decision_rationale || item.rationale ? (
                  <section className="result-response">
                    <p className="workflow-eyebrow">{item.decision_rationale ? "Recorded decision" : "Your submitted context"}</p>
                    <blockquote><DevIcon name="prompt" /><span>{item.decision_rationale ?? item.rationale}</span></blockquote>
                  </section>
                ) : null}

                <section className="result-evidence-summary">
                  <div><p className="workflow-eyebrow">Evidence path</p><strong>{graph.data?.graph.relations.length ?? "—"} native relationships · {graph.data?.graph.nodes.length ?? "—"} entities</strong></div>
                  <div className="result-integrity-row"><IntegrityRef label="Evidence root" value={item.evidence_root_ulid} /><IntegrityRef label="Evidence digest" value={item.evidence_digest} /></div>
                </section>

                <section className="focus-evidence">
                  {graph.isPending ? <StateFrame kind="loading" title="Projecting relationships" detail="The review result is already available." /> : graph.isError ? <StateFrame kind={graph.error instanceof ApiError && graph.error.status === 404 ? "empty" : "error"} title={graph.error instanceof ApiError && graph.error.status === 404 ? "Evidence projection is pending" : "Relationship map is unavailable"} detail={graph.error instanceof Error ? graph.error.message : "Try again when the evidence service is available."} action={<button type="button" className="dev-action" onClick={() => void graph.refetch()}>Retry map</button>} /> : <><ReviewEvidenceGraph graph={graph.data.graph} label="Review evidence relationship map" /><p className="review-graph-note">{graph.data.note}</p></>}
                </section>

                <div className="focus-disclosures result-disclosures">
                  <details open={Boolean(item.advisories.length)}><summary>Published advisories <span>{item.advisories.length}</span></summary><div>{item.advisories.length ? item.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />) : <p>No published advisories were returned.</p>}</div></details>
                  <details><summary>Review history <span>{item.events.length}</span></summary><div>{item.events.length ? <ol className="workflow-list">{[...item.events].reverse().map((event) => <li className="workflow-list-row" key={event.id}><span className="dev-tone-accent"><DevIcon name={event.action === "review.verified" ? "check" : "activity"} /></span><span className="workflow-list-row-main"><strong>{event.action.replace("review.", "").replaceAll("_", " ")}</strong><small>{event.actor_name} · {event.rationale}</small></span><span className="dev-relative-time">{timestamp(event.at)}</span></li>)}</ol> : <p>No review events have been recorded.</p>}</div></details>
                </div>
              </article>
            </section>

            <ResponsibilityDock label="Your next step" responsibility={action.title} why={action.detail} deadline={item.exception_expires_at ? timestamp(item.exception_expires_at) : undefined} overdue={expired}>
              {item.state === "changes_requested" ? <Link to="/developer/sessions" className="dev-action dev-action-primary w-full">Open sessions<DevIcon name="arrow" size={16} /></Link> : null}
              <Link to="/developer/attention" className="dev-action w-full">Back to Attention</Link>
              <dl className="responsibility-facts"><div><dt>Package</dt><dd>{item.package}@{item.version || "unpinned"}</dd></div><div><dt>Review</dt><dd>{item.id}</dd></div><div><dt>Reviewed by</dt><dd>{item.analyst_name ?? "Awaiting owner"}</dd></div><div><dt>Outcome</dt><dd>{item.state.replaceAll("_", " ")}</dd></div></dl>
              <div className="grid gap-2"><IntegrityRef label="Review" value={item.id} /><IntegrityRef label="Evidence root" value={item.evidence_root_ulid} /><IntegrityRef label="Evidence digest" value={item.evidence_digest} /><IntegrityRef label="Verification" value={item.verification_evidence_id} /></div>
              <p className="text-xs leading-relaxed text-slate">Decisions and evidence are recorded by authorized security roles.</p>
            </ResponsibilityDock>
          </div>
        );
      })()}
    </main>
  );
}
