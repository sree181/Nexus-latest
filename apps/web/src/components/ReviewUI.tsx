import { DevIcon } from "./DeveloperIcons";
import { Status, type VisualTone } from "./DeveloperVisual";
import type { PolicyAdvisory, ReviewState } from "../lib/api";

export function reviewVisual(state: ReviewState): { label: string; tone: VisualTone; detail: string } {
  const values: Record<ReviewState, { label: string; tone: VisualTone; detail: string }> = {
    waiting: { label: "Waiting for review", tone: "warning", detail: "The security team has not decided yet." },
    changes_requested: { label: "Change needed", tone: "warning", detail: "Use the recommended version, then rerun the check." },
    exception_approved: { label: "Temporary approval", tone: "info", detail: "An exception is active until its expiry date." },
    not_approved: { label: "Not approved", tone: "danger", detail: "Do not use this package version." },
    false_positive: { label: "Not a risk", tone: "success", detail: "The reviewer marked this signal as a false positive." },
    escalated: { label: "Escalated", tone: "danger", detail: "A CISO decision is required." },
    verified: { label: "Fix verified", tone: "success", detail: "A later clean package check closed this request." },
  };
  return values[state];
}

export function ReviewStateBadge({ state }: { state: ReviewState }) {
  const visual = reviewVisual(state);
  return <Status label={visual.label} tone={visual.tone} title={visual.detail} />;
}

export function releasedFixes(advisory: PolicyAdvisory): string[] {
  return advisory.fixed_versions.filter((value) => /^v?\d+(?:\.\d+){1,3}(?:[-+].*)?$/.test(value));
}

export function AdvisoryCard({ advisory }: { advisory: PolicyAdvisory }) {
  const fixes = releasedFixes(advisory);
  return (
    <article className="review-advisory-card">
      <span className={`review-advisory-icon review-severity-${advisory.severity}`}><DevIcon name="warning" size={16} /></span>
      <span className="review-advisory-main">
        <strong>{advisory.id}</strong>
        <small>{advisory.summary}</small>
        <span className="review-advisory-meta">
          <Status label={advisory.severity} tone={advisory.severity === "critical" || advisory.severity === "high" ? "danger" : advisory.severity === "medium" ? "warning" : "neutral"} />
          {advisory.cwe ? <span>{advisory.cwe}</span> : null}
          {fixes.length ? <span>Fixed in {fixes.join(", ")}</span> : <span>No released fix listed</span>}
        </span>
      </span>
      {advisory.references[0] ? <a className="dev-icon-button" href={advisory.references[0]} target="_blank" rel="noreferrer" aria-label={`Open source for ${advisory.id}`}><DevIcon name="external" size={15} /></a> : null}
    </article>
  );
}
