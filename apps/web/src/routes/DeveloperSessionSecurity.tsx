import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import {
  Metric,
  ProjectionBadge,
  verdictTone,
} from "../components/DeveloperSessionUI";
import {
  api,
  type ActivityEvent,
  type PolicyEvaluation,
  type PolicyEvaluationList,
} from "../lib/api";
import { timestampMs } from "../lib/format";

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

function value(payload: Record<string, unknown>, key: string): string {
  const result = payload[key];
  return typeof result === "string" ? result : "";
}

interface ObservedPackage {
  key: string;
  package: string;
  version: string;
  event: ActivityEvent;
  decision: PolicyEvaluation | undefined;
}

function observedPackages(
  events: ActivityEvent[],
  evaluations: PolicyEvaluation[],
): ObservedPackage[] {
  return events
    .filter((event) => event.type === "package.requested" || event.type === "package.installed")
    .map((event) => {
      const packageName = value(event.payload, "package");
      const version = value(event.payload, "version");
      const decision = evaluations.find(
        (item) => item.package === packageName && item.version === version,
      );
      return {
        key: event.event_id,
        package: packageName || "unknown package",
        version,
        event,
        decision,
      };
    });
}

function PolicyCard({ evaluation }: { evaluation: PolicyEvaluation }) {
  return (
    <article className="rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow)]">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={verdictTone(evaluation.verdict)}>{evaluation.verdict}</Badge>
            {evaluation.worst ? <Badge tone={evaluation.worst === "critical" || evaluation.worst === "high" ? "risk" : "warn"}>{evaluation.worst} advisory</Badge> : null}
            {evaluation.unavailable ? <Badge tone="warn">feed unavailable</Badge> : null}
          </div>
          <h3 className="mt-2 font-serif text-lg font-semibold text-ink">
            {evaluation.package}{evaluation.version ? `@${evaluation.version}` : ""}
          </h3>
          <p className="mt-1 text-[13px] leading-relaxed text-slate">{evaluation.policy || "No policy text was recorded."}</p>
        </div>
        <p className="shrink-0 font-mono text-[10.5px] text-slate">{timestampMs(evaluation.evaluated_at_ms)}</p>
      </div>
      {evaluation.reasons.length ? (
        <ul className="mt-4 space-y-2 border-t border-line pt-3">
          {evaluation.reasons.map((reason, index) => (
            <li key={`${evaluation.id}-reason-${index}`} className="flex gap-2 text-[12.5px] leading-relaxed text-ink">
              <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
              {reason}
            </li>
          ))}
        </ul>
      ) : null}
      {evaluation.unavailable ? (
        <p className="mt-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[12px] leading-relaxed text-ink">
          {evaluation.unavailable}
        </p>
      ) : null}
      {evaluation.advisories.length ? (
        <details className="mt-3">
          <summary className="cursor-pointer font-mono text-[10.5px] text-accent">{evaluation.advisories.length} advisory record(s)</summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-rail p-3 font-mono text-[11px] leading-relaxed text-rail-ink-dim">
            {JSON.stringify(evaluation.advisories, null, 2)}
          </pre>
        </details>
      ) : null}
    </article>
  );
}

function SecurityContent({ policies, events, runId }: { policies: PolicyEvaluationList; events: ActivityEvent[]; runId: string | null }) {
  const summary = summarizeSecurity(policies.evaluations);
  const packages = observedPackages(events, policies.evaluations);
  const projectionAttention = events.filter(
    (event) => event.projection_status === "failed" || event.projection_status === "refused",
  );

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
      <section className="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Evaluations" value={policies.total} note={`${summary.allowed} allowed`} />
        <Metric label="Blocked" value={summary.blocked} note="policy refused" />
        <Metric label="Warnings" value={summary.warnings + summary.unknown} note={`${summary.unknown} unknown`} />
        <Metric label="Projection issues" value={projectionAttention.length} note={projectionAttention.length ? "needs review" : "evidence current"} />
      </section>

      {summary.unavailable ? (
        <section role="alert" className="shrink-0 rounded-xl border border-warn bg-warn-soft px-4 py-3">
          <p className="font-medium text-ink">Advisory coverage was unavailable for {summary.unavailable} evaluation(s)</p>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate">An allow decision during feed unavailability is recorded as unchecked context, not proof that a package is safe.</p>
        </section>
      ) : null}

      <section className="shrink-0 rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] tracking-widest text-slate">PACKAGE POLICY</p>
            <h2 className="mt-1 font-serif text-xl font-semibold text-ink">Decisions made during this coding session</h2>
            <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-slate">These are the exact package-policy outcomes persisted by the adapter. They do not claim a package was installed unless a separate installation event exists.</p>
          </div>
          {runId ? (
            <Link to="/runs/$runId/security" params={{ runId }} className="text-[12px] font-medium text-accent underline-offset-2 hover:underline">Open run security →</Link>
          ) : null}
        </div>
        {policies.evaluations.length ? (
          <div className="mt-5 grid gap-3 xl:grid-cols-2">
            {policies.evaluations.map((evaluation) => <PolicyCard key={evaluation.id} evaluation={evaluation} />)}
          </div>
        ) : (
          <div className="mt-5 rounded-xl border border-dashed border-line-2 bg-surface-2 px-4 py-8 text-center">
            <p className="font-serif text-lg font-semibold text-ink">No package policy decisions</p>
            <p className="mt-1 text-sm text-slate">This session has not submitted a package request to the MeshAgent gate.</p>
          </div>
        )}
      </section>

      <section className="shrink-0 overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow)]">
        <div className="border-b border-line px-5 py-4">
          <p className="font-mono text-[10px] tracking-widest text-slate">OBSERVED PACKAGES</p>
          <h2 className="mt-1 font-serif text-lg font-semibold text-ink">Request and installation evidence</h2>
        </div>
        {packages.length ? (
          <div className="responsive-table-wrap">
            <table className="w-full border-collapse text-left">
              <thead><tr className="border-b border-line bg-surface-2">
                {["Sequence", "Observation", "Package", "Policy coverage", "Projection"].map((heading) => <th key={heading} className="px-4 py-2.5 font-mono text-[10px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}
              </tr></thead>
              <tbody>
                {packages.map((item) => (
                  <tr key={item.key} className="border-b border-line last:border-b-0">
                    <td className="px-4 py-3 font-mono text-xs text-slate">{item.event.sequence}</td>
                    <td className="px-4 py-3 text-[13px] text-ink">{item.event.type === "package.installed" ? "Installed" : "Requested"}</td>
                    <td className="px-4 py-3 font-mono text-[12px] text-ink">{item.package}{item.version ? `@${item.version}` : ""}</td>
                    <td className="px-4 py-3">{item.decision ? <Badge tone={verdictTone(item.decision.verdict)}>{item.decision.verdict}</Badge> : <Badge tone="warn">no matching decision</Badge>}</td>
                    <td className="px-4 py-3"><ProjectionBadge status={item.event.projection_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="px-5 py-8 text-center text-sm text-slate">No package request or installation activity was observed.</p>
        )}
      </section>

      {projectionAttention.length ? (
        <section className="shrink-0 rounded-2xl border border-risk bg-risk-soft p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Projection requires attention</h2>
          <p className="mt-1 text-[13px] leading-relaxed text-slate">The activity ledger is durable, but these events did not become governed evidence yet.</p>
          <ul className="mt-4 space-y-2">
            {projectionAttention.map((event) => (
              <li key={event.event_id} className="flex flex-wrap items-center gap-2 rounded-lg bg-surface px-3 py-2 text-[12px] text-ink">
                <span className="font-mono text-slate">#{event.sequence}</span>
                <span>{event.type}</span>
                <ProjectionBadge status={event.projection_status} />
                {event.projection_error ? <span className="basis-full text-risk">{event.projection_error}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

export function DeveloperSessionSecurity() {
  const { sessionId } = useParams({ from: "/developer/sessions/$sessionId" });
  const session = useQuery({
    queryKey: ["developer-session", sessionId],
    queryFn: () => api.developerSession(sessionId),
  });
  const policies = useQuery({
    queryKey: ["developer-session", sessionId, "policies"],
    queryFn: () => api.developerPolicyEvaluations(sessionId),
    refetchInterval: 5_000,
  });
  const activity = useQuery({
    queryKey: ["developer-session", sessionId, "activity"],
    queryFn: () => api.developerActivity(sessionId, { limit: 500 }),
    refetchInterval: 5_000,
  });

  if (policies.isPending || activity.isPending || session.isPending) return <Loading label="Assembling session security evidence…" />;
  if (policies.isError) return <ErrorState error={policies.error} retry={() => void policies.refetch()} />;
  if (activity.isError) return <ErrorState error={activity.error} retry={() => void activity.refetch()} />;
  if (session.isError) return <ErrorState error={session.error} retry={() => void session.refetch()} />;

  return <SecurityContent policies={policies.data} events={activity.data.events} runId={session.data.run_id} />;
}
