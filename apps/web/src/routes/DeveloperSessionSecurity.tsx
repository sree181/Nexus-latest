import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { AdvisoryCard } from "../components/ReviewUI";
import {
  EmptyVisual,
  SidePanel,
  Status,
  VisualFlow,
  type VisualTone,
} from "../components/DeveloperVisual";
import { projectionVisual } from "../components/DeveloperSessionUI";
import { api, type ActivityEvent, type PolicyEvaluation } from "../lib/api";
import { relativeTimeMs, timestampMs } from "../lib/format";
import { useOpenDeveloperSession } from "./DeveloperSessionLayout";

export interface SecuritySummary {
  allowed: number;
  warnings: number;
  blocked: number;
  unknown: number;
  unavailable: number;
}

export function summarizeSecurity(evaluations: PolicyEvaluation[]): SecuritySummary {
  return {
    allowed: evaluations.filter((item) => item.verdict === "allow").length,
    warnings: evaluations.filter((item) => item.verdict === "warn").length,
    blocked: evaluations.filter((item) => item.verdict === "block").length,
    unknown: evaluations.filter((item) => item.verdict === "unknown").length,
    unavailable: evaluations.filter((item) => Boolean(item.unavailable)).length,
  };
}

function verdictVisual(evaluation: PolicyEvaluation): { label: string; tone: VisualTone; detail: string } {
  if (evaluation.unavailable || evaluation.verdict === "unknown") return { label: "Unknown", tone: "warning", detail: "Security data unavailable." };
  if (evaluation.verdict === "block") return { label: "Blocked", tone: "danger", detail: "Blocked by policy." };
  if (evaluation.verdict === "warn") return { label: "Warning", tone: "warning", detail: "Review the policy reason." };
  return { label: "Allowed", tone: "success", detail: "The package request passed the check." };
}

function value(payload: Record<string, unknown>, key: string): string {
  const result = payload[key];
  return typeof result === "string" ? result : "";
}

function matchingPackageEvents(events: ActivityEvent[], evaluation: PolicyEvaluation): ActivityEvent[] {
  return events.filter((event) =>
    (event.type === "package.requested" || event.type === "package.installed")
    && value(event.payload, "package") === evaluation.package
    && value(event.payload, "version") === evaluation.version,
  );
}

function PolicyDetail({ evaluation, events, close }: { evaluation: PolicyEvaluation; events: ActivityEvent[]; close: () => void }) {
  const verdict = verdictVisual(evaluation);
  const packageEvents = matchingPackageEvents(events, evaluation);
  const requested = packageEvents.some((event) => event.type === "package.requested");
  const installed = packageEvents.some((event) => event.type === "package.installed");
  return (
    <SidePanel title={`${evaluation.package}${evaluation.version ? `@${evaluation.version}` : ""}`} icon="package" onClose={close}>
      <div className="dev-stack">
        <VisualFlow label="Package lifecycle" nodes={[
          { id: "request", label: "Requested", icon: "package", tone: requested ? "success" : "neutral", detail: requested ? "A package request was observed." : "No request event was recorded." },
          { id: "check", label: verdict.label, icon: "security", tone: verdict.tone, detail: verdict.detail },
          { id: "install", label: "Installed", icon: "check", tone: installed ? "success" : "neutral", detail: installed ? "An installation event was observed." : "No installation event." },
        ]} />
        <dl className="dev-kv">
          <dt>Package</dt><dd className="dev-mono">{evaluation.package}{evaluation.version ? `@${evaluation.version}` : ""}</dd>
          <dt>Registry</dt><dd>{evaluation.ecosystem}</dd>
          <dt>Decision</dt><dd><Status label={verdict.label} tone={verdict.tone} /></dd>
          <dt>Policy</dt><dd>{evaluation.policy || "Not recorded"}</dd>
          <dt>Checked</dt><dd>{timestampMs(evaluation.evaluated_at_ms)}</dd>
          <dt>Installed</dt><dd>{installed ? "Observed" : "Not observed"}</dd>
          <dt>Evidence</dt><dd>{packageEvents.every((event) => event.projection_status === "projected") ? "Recorded" : "Check Activity"}</dd>
        </dl>
        {evaluation.reasons.length ? (
          <section className="dev-surface">
            <header className="dev-panel-heading"><h2>Reasons</h2></header>
            <ul className="dev-compact-list">{evaluation.reasons.map((reason, index) => <li className="dev-compact-row" key={`${evaluation.id}-${index}`}><DevIcon name="info" size={16} /><span className="dev-compact-row-main"><strong>{reason}</strong></span></li>)}</ul>
          </section>
        ) : null}
        {evaluation.unavailable ? <div className="dev-compact-row dev-tone-warning"><DevIcon name="warning" /><span className="dev-compact-row-main"><strong>Security data unavailable</strong><small>{evaluation.unavailable}</small></span></div> : null}
        {evaluation.advisories.length ? <section className="dev-surface"><header className="dev-panel-heading"><h2>Advisories</h2></header>{evaluation.advisories.map((advisory) => <AdvisoryCard key={advisory.id} advisory={advisory} />)}</section> : null}
        {(evaluation.verdict !== "allow" || evaluation.unavailable) ? <Link to="/developer/attention" className="dev-action dev-action-primary w-full">Open Attention <DevIcon name="arrow" size={16} /></Link> : null}
      </div>
    </SidePanel>
  );
}

export function DeveloperSessionSecurity() {
  const session = useOpenDeveloperSession();
  const [selected, setSelected] = useState<PolicyEvaluation | null>(null);
  const policies = useQuery({ queryKey: ["developer-session", session.id, "policies"], queryFn: () => api.developerPolicyEvaluations(session.id), refetchInterval: 5_000 });
  const activity = useQuery({ queryKey: ["developer-session", session.id, "activity"], queryFn: () => api.developerActivity(session.id, { limit: 500 }), refetchInterval: 5_000 });
  const evaluations = policies.data?.evaluations ?? [];
  const events = activity.data?.events ?? [];
  const summary = summarizeSecurity(evaluations);
  const evidenceIssues = events.filter((event) => event.projection_status === "failed" || event.projection_status === "refused");
  const dominant = summary.blocked
    ? { label: `${summary.blocked} blocked`, tone: "danger" as const, icon: "lock" as const }
    : summary.warnings + summary.unknown + summary.unavailable
      ? { label: "Review", tone: "warning" as const, icon: "warning" as const }
      : evidenceIssues.length
        ? { label: "Evidence issue", tone: "danger" as const, icon: "evidence" as const }
        : { label: "No issues", tone: "success" as const, icon: "check" as const };

  return (
    <div className="dev-scroll dev-stack">
      <section className="dev-surface dev-security-state">
        <span className={`dev-tone-${dominant.tone}`}><DevIcon name={dominant.icon} /></span>
        <strong>{dominant.label}</strong>
        <span className="ml-auto dev-muted text-xs">{evaluations.length} checks</span>
      </section>

      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Package checks</h2></header>
        {policies.isPending || activity.isPending ? <EmptyVisual icon="live" title="Loading" /> : evaluations.length ? (
          <div>
            {evaluations.map((evaluation) => {
              const verdict = verdictVisual(evaluation);
              const installed = matchingPackageEvents(events, evaluation).some((event) => event.type === "package.installed");
              return (
                <button key={evaluation.id} type="button" className="dev-policy-row" onClick={() => setSelected(evaluation)}>
                  <span className="dev-policy-package"><DevIcon name="package" size={17} /><span>{evaluation.package}{evaluation.version ? `@${evaluation.version}` : ""}</span></span>
                  <Status label={verdict.label} tone={verdict.tone} icon={evaluation.verdict === "block" ? "lock" : evaluation.unavailable ? "warning" : "security"} title={verdict.detail} />
                  <Status label={installed ? "Installed" : "Not installed"} tone={installed ? "success" : "neutral"} icon={installed ? "check" : "package"} title={installed ? "An installation event was observed." : "No installation event."} />
                  <span className="dev-relative-time">{relativeTimeMs(evaluation.evaluated_at_ms)}</span>
                </button>
              );
            })}
          </div>
        ) : <EmptyVisual icon="security" title="No checks" />}
      </section>

      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Evidence sync</h2><Link to="/developer/sessions/$sessionId/activity" params={{ sessionId: session.id }} className="dev-icon-button" aria-label="Open activity"><DevIcon name="arrow" size={17} /></Link></header>
        {evidenceIssues.length ? evidenceIssues.map((event) => {
          const projection = projectionVisual(event.projection_status);
          return <div key={event.event_id} className="dev-compact-row"><DevIcon name="evidence" /><span className="dev-compact-row-main"><strong>Event #{event.sequence}</strong><small>{event.projection_error ?? projection.detail}</small></span><Status label={projection.label} tone={projection.tone} /></div>;
        }) : <div className="dev-security-state"><span className="dev-tone-success"><DevIcon name="check" /></span><strong>Recorded</strong></div>}
      </section>
      {selected ? <PolicyDetail evaluation={selected} events={events} close={() => setSelected(null)} /> : null}
    </div>
  );
}
