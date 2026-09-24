import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import {
  CopyButton,
  Disclosure,
  EmptyVisual,
  IconButton,
  SidePanel,
  Status,
  VisualFlow,
} from "../components/DeveloperVisual";
import {
  activityCopy,
  activityIcon,
  activityNeedsAttention,
  projectionVisual,
} from "../components/DeveloperSessionUI";
import { api, type ActivityEvent } from "../lib/api";
import { relativeTimeMs, timestampMs } from "../lib/format";
import { useOpenDeveloperSession } from "./DeveloperSessionLayout";

type ActivityFilter = "all" | "code" | "tools" | "packages" | "attention";

export function filterActivity(events: ActivityEvent[], filter: ActivityFilter, query: string): ActivityEvent[] {
  return events.filter((event) => {
    const filterMatch = filter === "all"
      || (filter === "code" && event.type === "file.changed")
      || (filter === "tools" && event.type.startsWith("tool."))
      || (filter === "packages" && (event.type.startsWith("package.") || event.type === "policy.evaluated"))
      || (filter === "attention" && activityNeedsAttention(event));
    if (!filterMatch) return false;
    const value = query.trim().toLowerCase();
    if (!value) return true;
    const copy = activityCopy(event);
    return [event.type, copy.title, copy.detail, JSON.stringify(event.payload)].some((item) => item.toLowerCase().includes(value));
  });
}

function EventDetail({ event, close }: { event: ActivityEvent; close: () => void }) {
  const [revealed, setRevealed] = useState(false);
  const copy = activityCopy(event);
  const projection = projectionVisual(event.projection_status);
  const lag = Math.max(0, event.received_at_ms - event.occurred_at_ms);
  return (
    <SidePanel title={copy.title} icon={activityIcon(event.type)} onClose={close}>
      <div className="dev-stack">
        <VisualFlow label="Evidence flow" nodes={[
          { id: "editor", label: "Editor", icon: activityIcon(event.type), tone: "success", detail: `Editor event ${event.sequence} was observed.` },
          { id: "stored", label: "Stored", icon: "check", tone: "success", detail: "MeshAgent received this event." },
          { id: "evidence", label: projection.label, icon: "evidence", tone: projection.tone, detail: projection.detail },
        ]} />
        <dl className="dev-kv">
          <dt>Action</dt><dd>{copy.detail}</dd>
          <dt>Occurred</dt><dd>{timestampMs(event.occurred_at_ms)}</dd>
          <dt>Received</dt><dd>{timestampMs(event.received_at_ms)} · +{lag} ms</dd>
          <dt>Sequence</dt><dd>{event.sequence}</dd>
          <dt>Evidence</dt><dd><Status label={projection.label} tone={projection.tone} /></dd>
          <dt>Attempts</dt><dd>{event.projection_attempts}</dd>
          {event.projection_error ? <><dt>Error</dt><dd className="dev-tone-danger">{event.projection_error}</dd></> : null}
          {event.projection_next_attempt_at_ms ? <><dt>Trying again</dt><dd>{timestampMs(event.projection_next_attempt_at_ms)}</dd></> : null}
        </dl>
        <div className="dev-sensitive">
          {revealed ? (
            <pre className="dev-code-block w-full">{JSON.stringify(event.payload, null, 2)}</pre>
          ) : (
            <button type="button" className="dev-action" onClick={() => setRevealed(true)}><DevIcon name="eye" size={16} />Reveal payload</button>
          )}
        </div>
        <Disclosure label="Technical details" icon="code">
          <dl className="dev-kv">
            <dt>Event</dt><dd className="dev-mono">{event.event_id} <CopyButton value={event.event_id} label="Copy event ID" /></dd>
            <dt>Editor event</dt><dd className="dev-mono">{event.source_event_id}</dd>
            <dt>Digest</dt><dd className="dev-mono">{event.payload_sha256}</dd>
            <dt>Run</dt><dd>{event.run_id ?? "Not recorded"}</dd>
          </dl>
        </Disclosure>
      </div>
    </SidePanel>
  );
}

export function EventRow({ event, select }: { event: ActivityEvent; select: () => void }) {
  const copy = activityCopy(event);
  const projection = projectionVisual(event.projection_status);
  const attention = activityNeedsAttention(event);
  return (
    <button type="button" className={`dev-activity-row ${attention ? "dev-activity-row-attention" : ""}`} onClick={select}>
      <span className="dev-activity-icon"><DevIcon name={activityIcon(event.type)} size={16} /></span>
      <span className="dev-activity-copy"><strong>{copy.title}</strong><small>{copy.detail}</small></span>
      <span className="dev-sequence">#{event.sequence}</span>
      <Status label={projection.label} tone={projection.tone} icon="evidence" title={projection.detail} />
      <span className="dev-relative-time">{relativeTimeMs(event.occurred_at_ms)}</span>
    </button>
  );
}

export function DeveloperSessionActivity() {
  const session = useOpenDeveloperSession();
  const [filter, setFilter] = useState<ActivityFilter>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ActivityEvent | null>(null);
  const [following, setFollowing] = useState(false);
  const activity = useQuery({
    queryKey: ["developer-session", session.id, "activity"],
    queryFn: () => api.developerActivity(session.id, { limit: 500 }),
    refetchInterval: ["starting", "active", "ending"].includes(session.status) ? 2_500 : false,
  });
  const visible = useMemo(() => filterActivity(activity.data?.events ?? [], filter, query), [activity.data?.events, filter, query]);

  return (
    <div className="dev-scroll">
      <section className="dev-surface">
        <div className="dev-list-toolbar">
          <label className="dev-search"><DevIcon name="search" size={17} /><span className="sr-only">Search activity</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" /></label>
          <div className="dev-filter-chips">
            {(["all", "code", "tools", "packages", "attention"] as ActivityFilter[]).map((value) => <button key={value} type="button" className="dev-filter-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === "all" ? `All ${activity.data?.events.length ?? 0}` : value[0].toUpperCase() + value.slice(1)}</button>)}
            <IconButton label={following ? "Stop following" : "Follow live"} icon="live" selected={following} onClick={() => setFollowing((value) => !value)} />
          </div>
        </div>
        {activity.isPending ? <EmptyVisual icon="live" title="Loading" /> : activity.isError ? <EmptyVisual icon="warning" title="Try again" action={<button type="button" className="dev-action" onClick={() => void activity.refetch()}>Retry</button>} /> : visible.length ? (
          <div aria-label="Ordered activity">{visible.map((event) => <EventRow key={event.event_id} event={event} select={() => setSelected(event)} />)}</div>
        ) : <EmptyVisual icon={activity.data.events.length ? "filter" : "activity"} title={activity.data.events.length ? "No matches" : "Waiting"} />}
        {activity.data?.next_after_sequence ? <div className="dev-compact-row dev-tone-warning"><DevIcon name="warning" /><span className="dev-compact-row-main"><strong>More events</strong><small>Showing the first 500.</small></span></div> : null}
      </section>
      {selected ? <EventDetail event={selected} close={() => setSelected(null)} /> : null}
    </div>
  );
}
