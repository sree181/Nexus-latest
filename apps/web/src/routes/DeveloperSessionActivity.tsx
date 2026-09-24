import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import {
  Metric,
  ProjectionBadge,
  activityCopy,
  activityDot,
  compactId,
  summarizeProjection,
} from "../components/DeveloperSessionUI";
import { api, type ActivityEvent } from "../lib/api";
import { timestampMs } from "../lib/format";

function displayPayload(payload: Record<string, unknown>): Record<string, unknown> {
  if (typeof payload.code !== "string") return payload;
  return {
    ...payload,
    code:
      payload.code.length > 1_200
        ? `${payload.code.slice(0, 1_200)}\n… ${payload.code.length - 1_200} more characters`
        : payload.code,
  };
}

export function EventRow({ event, last }: { event: ActivityEvent; last: boolean }) {
  const copy = activityCopy(event);
  const lag = Math.max(0, event.received_at_ms - event.occurred_at_ms);
  return (
    <li className="relative grid grid-cols-[42px_minmax(0,1fr)] gap-3 sm:grid-cols-[56px_minmax(0,1fr)]">
      {!last ? <span className="absolute bottom-[-20px] left-[20px] top-8 w-px bg-line sm:left-[27px]" aria-hidden="true" /> : null}
      <div className={`relative z-10 mt-1 flex h-10 w-10 items-center justify-center rounded-full border-4 border-paper ${activityDot[copy.group]} font-mono text-[10px] font-semibold text-white sm:ml-2`}>
        {event.sequence}
      </div>
      <article className="min-w-0 rounded-xl border border-line bg-surface px-4 py-4 shadow-[var(--shadow)]">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[10px] tracking-widest text-slate">{copy.label}</span>
              <ProjectionBadge status={event.projection_status} />
              {event.projection_attempts > 1 ? <Badge tone="warn">attempt {event.projection_attempts}</Badge> : null}
            </div>
            <h3 className="mt-2 break-words font-serif text-[17px] font-semibold text-ink">{copy.title}</h3>
            <p className="mt-1 break-words text-[13px] leading-relaxed text-slate">{copy.detail}</p>
          </div>
          <div className="shrink-0 text-left sm:text-right">
            <p className="font-mono text-[10.5px] text-slate">{timestampMs(event.occurred_at_ms)}</p>
            <p className="mt-1 font-mono text-[10px] text-slate-2">received +{lag} ms</p>
          </div>
        </div>

        {event.projection_error ? (
          <div role="alert" className="mt-3 rounded-lg border border-risk bg-risk-soft px-3 py-2 text-[12px] leading-relaxed text-risk">
            {event.projection_error}
            {event.projection_next_attempt_at_ms ? ` · retry after ${timestampMs(event.projection_next_attempt_at_ms)}` : ""}
          </div>
        ) : null}

        <details className="mt-3 border-t border-line pt-3">
          <summary className="cursor-pointer select-none font-mono text-[10.5px] text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
            Inspect recorded payload and evidence IDs
          </summary>
          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
            <pre className="max-h-72 overflow-auto rounded-lg bg-rail p-3 font-mono text-[11px] leading-relaxed text-rail-ink-dim">
              {JSON.stringify(displayPayload(event.payload), null, 2)}
            </pre>
            <dl className="space-y-2 text-[11px]">
              <div><dt className="font-mono text-slate">EVENT ID</dt><dd className="mt-0.5 break-all text-ink">{event.event_id}</dd></div>
              <div><dt className="font-mono text-slate">SOURCE EVENT</dt><dd className="mt-0.5 break-all text-ink">{event.source_event_id}</dd></div>
              <div><dt className="font-mono text-slate">PAYLOAD SHA-256</dt><dd className="mt-0.5 break-all text-ink">{event.payload_sha256}</dd></div>
              <div><dt className="font-mono text-slate">RUN</dt><dd className="mt-0.5 text-ink">{event.run_id ?? "not projected"}</dd></div>
            </dl>
          </div>
        </details>
      </article>
    </li>
  );
}

export function DeveloperSessionActivity() {
  const { sessionId } = useParams({ from: "/developer/sessions/$sessionId" });
  const session = useQuery({
    queryKey: ["developer-session", sessionId],
    queryFn: () => api.developerSession(sessionId),
  });
  const activity = useQuery({
    queryKey: ["developer-session", sessionId, "activity"],
    queryFn: () => api.developerActivity(sessionId, { limit: 500 }),
    refetchInterval: () =>
      session.data && ["starting", "active", "ending"].includes(session.data.status)
        ? 2_500
        : false,
  });

  if (activity.isPending) return <Loading label="Reading ordered session activity…" />;
  if (activity.isError) return <ErrorState error={activity.error} retry={() => void activity.refetch()} />;

  const summary = summarizeProjection(activity.data.events);
  const latest = activity.data.events[activity.data.events.length - 1];
  return (
    <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
      <section className="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Events" value={activity.data.events.length} note={`through sequence ${latest?.sequence ?? 0}`} />
        <Metric label="Governed" value={summary.projected} note="projected to HyperMesh" />
        <Metric label="In progress" value={summary.pending} note="pending or projecting" />
        <Metric label="Attention" value={summary.attention} note={summary.allProjected ? "all evidence current" : "failed or refused"} />
      </section>

      <section className="shrink-0 rounded-2xl border border-line bg-surface-2 px-4 py-3 sm:px-5">
        <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
          <div>
            <p className="font-mono text-[10px] tracking-widest text-slate">ORDERED ACTIVITY LEDGER</p>
            <p className="mt-1 text-[13px] leading-relaxed text-ink">
              Observed editor activity is shown in server-accepted sequence. Projection badges state whether each record became governed HyperMesh evidence.
            </p>
          </div>
          {session.data?.run_id ? (
            <Link to="/runs/$runId/memory" params={{ runId: session.data.run_id }} className="shrink-0 text-[12px] font-medium text-accent underline-offset-2 hover:underline">
              Inspect provenance →
            </Link>
          ) : null}
        </div>
      </section>

      {activity.data.events.length === 0 ? (
        <section className="grid flex-1 place-items-center rounded-2xl border border-dashed border-line-2 bg-surface p-8 text-center">
          <div className="max-w-lg">
            <h2 className="font-serif text-xl font-semibold text-ink">No activity recorded yet</h2>
            <p className="mt-2 text-sm leading-relaxed text-slate">The session exists, but no ordered editor activity is available. Continue working in the connected editor.</p>
          </div>
        </section>
      ) : (
        <ol className="shrink-0 space-y-5" aria-label="Ordered developer activity">
          {activity.data.events.map((event, index) => (
            <EventRow key={event.event_id} event={event} last={index === activity.data.events.length - 1} />
          ))}
        </ol>
      )}

      {activity.data.next_after_sequence ? (
        <p role="status" className="rounded-xl border border-warn bg-warn-soft px-4 py-3 text-sm text-ink">
          This session contains more than 500 events. Showing the first page through sequence {activity.data.next_after_sequence}.
        </p>
      ) : null}
      <p className="pb-2 font-mono text-[10.5px] text-slate">Session {compactId(sessionId, 14, 8)} · timestamps shown in UTC</p>
    </div>
  );
}
