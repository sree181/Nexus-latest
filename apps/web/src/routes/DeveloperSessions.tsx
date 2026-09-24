import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { DeveloperTopbar, useDeveloperProject } from "../components/DeveloperProject";
import {
  EmptyVisual,
  Hint,
  IconButton,
  SidePanel,
  Status,
  VisualFlow,
} from "../components/DeveloperVisual";
import { sessionVisual } from "../components/DeveloperSessionUI";
import { api, type DeveloperSession } from "../lib/api";
import { relativeTimeMs, timestampMs } from "../lib/format";

type Filter = "all" | "live" | "completed" | "attention";

export function filterDeveloperSessions(
  sessions: DeveloperSession[],
  filter: Filter,
): DeveloperSession[] {
  if (filter === "live") return sessions.filter((session) => ["starting", "active", "ending"].includes(session.status));
  if (filter === "completed") return sessions.filter((session) => session.status === "completed");
  if (filter === "attention") return sessions.filter((session) => session.status === "failed" || session.run_id === null);
  return sessions;
}

function matches(session: DeveloperSession, query: string): boolean {
  const value = query.trim().toLowerCase();
  if (!value) return true;
  return [
    session.task,
    session.repository.name,
    session.repository.branch ?? "",
    session.id,
    session.run_id ?? "",
    session.adapter,
  ].some((item) => item.toLowerCase().includes(value));
}

function adapterLabel(session: DeveloperSession): string {
  return session.adapter === "claude-code" ? "Claude" : "Cursor";
}

function SessionRow({ session, select }: { session: DeveloperSession; select: () => void }) {
  const state = sessionVisual(session.status);
  const evidence = session.run_id
    ? { label: "Recorded", tone: "success" as const, detail: `Evidence run ${session.run_id}` }
    : { label: "Syncing", tone: "warning" as const, detail: "Evidence is not linked yet." };
  return (
    <button type="button" className="dev-session-row" onClick={select} aria-label={`Open ${session.repository.name} session`}>
      <span className="dev-session-primary">
        <span><DevIcon name="repository" size={17} /></span>
        <span className="dev-session-text">
          <strong>{session.repository.name}</strong>
          <small>{session.repository.branch ?? "default branch"}</small>
        </span>
      </span>
      <span className="dev-session-task">{session.task}</span>
      <Hint label={session.adapter === "claude-code" ? "Claude Code" : "Cursor"}>
        <span className="dev-status dev-tone-neutral"><DevIcon name="cursor" size={14} />{adapterLabel(session)}</span>
      </Hint>
      <span className="flex items-center gap-2">
        <Status label={state.label} tone={state.tone} icon={state.label === "Live" ? "live" : state.label === "Failed" ? "warning" : "check"} />
        <Hint label={evidence.detail}><span className={`dev-status dev-tone-${evidence.tone}`}><DevIcon name="evidence" size={14} /></span></Hint>
      </span>
      <span className="dev-session-time">{relativeTimeMs(session.last_seen_at_ms)}</span>
    </button>
  );
}

function SessionPreview({ session, close }: { session: DeveloperSession; close: () => void }) {
  const state = sessionVisual(session.status);
  return (
    <SidePanel title={session.repository.name} icon="session" onClose={close} footer={
      <Link to="/developer/sessions/$sessionId" params={{ sessionId: session.id }} className="dev-action dev-action-primary w-full">
        Open <DevIcon name="arrow" size={16} />
      </Link>
    }>
      <div className="dev-stack">
        <div className="dev-session-text">
          <strong>{session.task}</strong>
          <small>{session.repository.branch ?? "default branch"} · {adapterLabel(session)}</small>
        </div>
        <VisualFlow label="Session flow" nodes={[
          { id: "editor", label: "Editor", icon: "cursor", tone: "success", detail: `${adapterLabel(session)} connected.` },
          { id: "activity", label: "Activity", icon: "activity", tone: session.last_acked_sequence > 1 ? "success" : "warning", detail: `${session.last_acked_sequence} events accepted.` },
          { id: "check", label: "Checks", icon: "security", tone: "neutral", detail: "Open Security for package checks." },
          { id: "evidence", label: "Evidence", icon: "evidence", tone: session.run_id ? "success" : "warning", detail: session.run_id ? `Run ${session.run_id} is linked.` : "Evidence is not linked yet." },
        ]} />
        <dl className="dev-kv">
          <dt>Status</dt><dd><Status label={state.label} tone={state.tone} /></dd>
          <dt>Started</dt><dd>{timestampMs(session.started_at_ms)}</dd>
          <dt>Last activity</dt><dd>{timestampMs(session.last_seen_at_ms)}</dd>
          <dt>Last event</dt><dd>{session.last_acked_sequence}</dd>
          <dt>Identity</dt><dd>{session.verified ? "Verified" : "Local"}</dd>
          <dt>Session</dt><dd className="dev-mono">{session.id}</dd>
          <dt>Evidence</dt><dd>{session.run_id ?? "Syncing"}</dd>
        </dl>
      </div>
    </SidePanel>
  );
}

export function DeveloperSessions() {
  const project = useDeveloperProject();
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices });
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<DeveloperSession | null>(null);
  const visible = useMemo(
    () => filterDeveloperSessions(project.sessions, filter).filter((session) => matches(session, query)),
    [filter, project.sessions, query],
  );

  return (
    <main className="dev-page">
      <DeveloperTopbar title="Sessions" actions={
        <Hint label="Connect Cursor or Claude Code">
          <Link to="/developer/connections/editors" className="dev-icon-button" aria-label="Connect editor"><DevIcon name="connect" /></Link>
        </Hint>
      } />
      <div className="dev-scroll">
        <section className="dev-surface">
          <div className="dev-list-toolbar">
            <label className="dev-search">
              <DevIcon name="search" size={17} />
              <span className="sr-only">Search sessions</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" />
            </label>
            <div className="dev-filter-chips" aria-label="Session filter">
              {(["all", "live", "completed", "attention"] as Filter[]).map((value) => (
                <button key={value} type="button" className="dev-filter-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {value === "all" ? `All ${project.sessions.length}` : value === "attention" ? "Needs attention" : value[0].toUpperCase() + value.slice(1)}
                </button>
              ))}
              <IconButton label="Filters" icon="filter" />
            </div>
          </div>
          <div className="dev-list-heading" aria-hidden="true"><span>Project</span><span>Session</span><span>Editor</span><span>Status</span><span>Updated</span></div>
          {project.sessionsPending ? (
            <EmptyVisual icon="live" title="Loading" />
          ) : project.sessionsError ? (
            <EmptyVisual icon="warning" title="Try again" />
          ) : visible.length ? (
            visible.map((session) => <SessionRow key={session.id} session={session} select={() => setSelected(session)} />)
          ) : project.sessions.length === 0 ? (
            <EmptyVisual
              icon={devices.data?.length ? "live" : "connect"}
              title={devices.data?.length ? "Waiting for activity" : "Connect editor"}
              action={<Link to="/developer/connections/editors" className="dev-action dev-action-primary">Open connections</Link>}
            />
          ) : (
            <EmptyVisual icon="filter" title="No matches" action={<button type="button" className="dev-action" onClick={() => { setFilter("all"); setQuery(""); }}>Clear filters</button>} />
          )}
        </section>
      </div>
      {selected ? <SessionPreview session={selected} close={() => setSelected(null)} /> : null}
    </main>
  );
}
