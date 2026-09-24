import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import {
  EmptyVisual,
  Status,
  VisualFlow,
  type FlowNode,
  type VisualTone,
} from "../components/DeveloperVisual";
import {
  activityCopy,
  activityIcon,
  activityNeedsAttention,
  summarizeProjection,
} from "../components/DeveloperSessionUI";
import { api, type ActivityEvent, type PolicyEvaluation } from "../lib/api";
import { relativeTimeMs } from "../lib/format";
import { useOpenDeveloperSession } from "./DeveloperSessionLayout";

function packageTone(policies: PolicyEvaluation[]): VisualTone {
  if (policies.some((item) => item.verdict === "block")) return "danger";
  if (policies.some((item) => item.verdict === "warn" || item.verdict === "unknown" || item.unavailable)) return "warning";
  if (policies.length) return "success";
  return "neutral";
}

function meaningful(events: ActivityEvent[]): ActivityEvent[] {
  const preferred = events.filter((event) => !["session.started", "session.ended"].includes(event.type));
  return (preferred.length ? preferred : events).slice(-5).reverse();
}

export function DeveloperSessionOverview() {
  const session = useOpenDeveloperSession();
  const activity = useQuery({
    queryKey: ["developer-session", session.id, "activity"],
    queryFn: () => api.developerActivity(session.id, { limit: 500 }),
    refetchInterval: ["starting", "active", "ending"].includes(session.status) ? 2_500 : false,
  });
  const policies = useQuery({
    queryKey: ["developer-session", session.id, "policies"],
    queryFn: () => api.developerPolicyEvaluations(session.id),
    refetchInterval: ["starting", "active", "ending"].includes(session.status) ? 5_000 : false,
  });

  const events = activity.data?.events ?? [];
  const evaluations = policies.data?.evaluations ?? [];
  const projection = summarizeProjection(events);
  const attentionEvents = events.filter(activityNeedsAttention);
  const policyAttention = evaluations.filter((item) => item.verdict !== "allow" || Boolean(item.unavailable));
  const attention = attentionEvents.length + policyAttention.length + (session.status === "failed" ? 1 : 0);
  const flow: FlowNode[] = [
    { id: "editor", label: "Editor", icon: "cursor", tone: "success", detail: `${session.adapter === "claude-code" ? "Claude Code" : "Cursor"} connected.` },
    { id: "activity", label: "Activity", icon: "activity", tone: events.length ? "success" : "warning", detail: events.length ? `${events.length} events received.` : "Waiting for activity." },
    { id: "checks", label: "Checks", icon: "security", tone: packageTone(evaluations), detail: evaluations.length ? `${evaluations.length} package checks.` : "No package checks." },
    { id: "evidence", label: "Evidence", icon: "evidence", tone: projection.attention ? "danger" : projection.pending ? "warning" : projection.projected ? "success" : "neutral", detail: projection.attention ? `${projection.attention} evidence failures.` : projection.pending ? `${projection.pending} events syncing.` : projection.projected ? `${projection.projected} events recorded.` : "No evidence yet." },
  ];

  return (
    <div className="dev-scroll dev-stack">
      <section className="dev-surface dev-connection-topology">
        <VisualFlow label="Session flow" nodes={flow} />
      </section>

      <div className="dev-grid-2">
        <section className="dev-surface">
          <header className="dev-panel-heading">
            <h2>Recent</h2>
            <Link to="/developer/sessions/$sessionId/activity" params={{ sessionId: session.id }} className="dev-icon-button" aria-label="Open activity"><DevIcon name="arrow" size={17} /></Link>
          </header>
          {activity.isPending ? <EmptyVisual icon="live" title="Loading" /> : meaningful(events).length ? (
            <ul className="dev-compact-list">
              {meaningful(events).map((event) => {
                const copy = activityCopy(event);
                return (
                  <li key={event.event_id} className="dev-compact-row">
                    <DevIcon name={activityIcon(event.type)} size={17} />
                    <span className="dev-compact-row-main"><strong>{copy.title}</strong><small>{copy.detail}</small></span>
                    <span className="dev-relative-time">{relativeTimeMs(event.occurred_at_ms)}</span>
                  </li>
                );
              })}
            </ul>
          ) : <EmptyVisual icon="activity" title="Waiting" />}
        </section>

        <section className="dev-surface">
          <header className="dev-panel-heading">
            <h2>Attention</h2>
            <Link to="/developer/sessions/$sessionId/security" params={{ sessionId: session.id }} className="dev-icon-button" aria-label="Open security"><DevIcon name="arrow" size={17} /></Link>
          </header>
          {attention === 0 ? (
            <div className="dev-security-state"><span className="dev-tone-success"><DevIcon name="check" /></span><strong>No action</strong></div>
          ) : (
            <ul className="dev-attention-list">
              {session.status === "failed" ? <li className="dev-compact-row"><DevIcon name="warning" /><span className="dev-compact-row-main"><strong>Session failed</strong><small>{session.failure_reason ?? "Open technical details."}</small></span><Status label="Failed" tone="danger" /></li> : null}
              {policyAttention.slice(0, 3).map((item) => <li key={item.id} className="dev-compact-row"><DevIcon name="package" /><span className="dev-compact-row-main"><strong>{item.package}{item.version ? `@${item.version}` : ""}</strong><small>{item.unavailable ? "Security data unavailable" : item.reasons[0] ?? item.policy}</small></span><Status label={item.unavailable ? "Unknown" : item.verdict === "block" ? "Blocked" : "Review"} tone={item.verdict === "block" ? "danger" : "warning"} /></li>)}
              {attentionEvents.slice(0, 3).map((event) => <li key={event.event_id} className="dev-compact-row"><DevIcon name={activityIcon(event.type)} /><span className="dev-compact-row-main"><strong>{activityCopy(event).title}</strong><small>{event.projection_error ?? activityCopy(event).detail}</small></span><Status label="Review" tone="danger" /></li>)}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
