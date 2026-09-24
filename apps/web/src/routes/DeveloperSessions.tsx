import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";

import { Async } from "../components/Async";
import {
  AdapterBadge,
  Metric,
  SessionStatusBadge,
  compactId,
} from "../components/DeveloperSessionUI";
import { PageHeader } from "../components/PageHeader";
import { api, type DeveloperSession, type DeveloperSessionList } from "../lib/api";
import { relativeTimeMs, timestampMs } from "../lib/format";

type Filter = "all" | "live" | "completed" | "attention";

export function filterDeveloperSessions(
  sessions: DeveloperSession[],
  filter: Filter,
): DeveloperSession[] {
  if (filter === "live") {
    return sessions.filter((session) =>
      ["starting", "active", "ending"].includes(session.status),
    );
  }
  if (filter === "completed") {
    return sessions.filter((session) => session.status === "completed");
  }
  if (filter === "attention") {
    return sessions.filter(
      (session) => session.status === "failed" || session.run_id === null,
    );
  }
  return sessions;
}

function SessionRow({ session }: { session: DeveloperSession }) {
  return (
    <Link
      to="/developer/sessions/$sessionId"
      params={{ sessionId: session.id }}
      className="group grid gap-4 border-b border-line px-4 py-4 transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent lg:grid-cols-[minmax(0,1.7fr)_minmax(150px,.7fr)_minmax(150px,.65fr)_auto] lg:items-center"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <AdapterBadge adapter={session.adapter} />
          <span className="font-mono text-[11px] text-slate" title={session.id}>
            {compactId(session.id)}
          </span>
          {!session.verified ? <Badge tone="warn">unverified</Badge> : null}
        </div>
        <h3 className="mt-2 truncate font-serif text-[17px] font-semibold text-ink group-hover:text-accent">
          {session.task}
        </h3>
        <p className="mt-1 truncate text-[13px] text-slate">
          {session.repository.name}
          {session.repository.branch ? ` · ${session.repository.branch}` : ""}
        </p>
      </div>
      <div>
        <p className="font-mono text-[10px] tracking-widest text-slate">LAST ACTIVITY</p>
        <p className="mt-1 text-[13px] font-medium text-ink">
          {relativeTimeMs(session.last_seen_at_ms)}
        </p>
        <p className="mt-0.5 font-mono text-[10.5px] text-slate">
          {timestampMs(session.last_seen_at_ms)}
        </p>
      </div>
      <div>
        <p className="font-mono text-[10px] tracking-widest text-slate">EVIDENCE</p>
        <p className="mt-1 text-[13px] font-medium text-ink">
          {session.run_id ? `Run ${session.run_id}` : "Projection pending"}
        </p>
        <p className="mt-0.5 font-mono text-[10.5px] text-slate">
          through sequence {session.last_acked_sequence}
        </p>
      </div>
      <div className="flex items-center justify-between gap-3 lg:justify-end">
        <SessionStatusBadge status={session.status} />
        <span className="text-lg text-slate-2 transition group-hover:translate-x-0.5 group-hover:text-accent" aria-hidden="true">
          →
        </span>
      </div>
    </Link>
  );
}

function SessionInventory({ data }: { data: DeveloperSessionList }) {
  const [filter, setFilter] = useState<Filter>("all");
  const visible = useMemo(
    () => filterDeveloperSessions(data.sessions, filter),
    [data.sessions, filter],
  );
  const live = data.sessions.filter((session) =>
    ["starting", "active", "ending"].includes(session.status),
  ).length;
  const completed = data.sessions.filter((session) => session.status === "completed").length;
  const attention = data.sessions.filter(
    (session) => session.status === "failed" || session.run_id === null,
  ).length;
  const adapters = new Set(data.sessions.map((session) => session.adapter)).size;

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
      <section className="shrink-0 overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow)]">
        <div className="grid gap-5 px-5 py-5 lg:grid-cols-[1fr_auto] lg:items-center lg:px-6">
          <div>
            <p className="font-mono text-[10.5px] tracking-[0.16em] text-accent">CONNECTED CODING AGENTS</p>
            <h2 className="mt-2 font-serif text-[25px] font-semibold leading-tight text-ink">
              Keep working in your editor. MeshAgent records the governed trail.
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate">
              Sessions appear when an opted-in Cursor or Claude Code hook connects. Open one to inspect what changed, what policy decided, and what reached HyperMesh evidence.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/setup"
              className="rounded-lg bg-accent px-4 py-2.5 text-[13px] font-medium text-white transition hover:bg-[#0b6875] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Connect an editor
            </Link>
            <Link
              to="/developer/start"
              className="rounded-lg border border-line-2 bg-surface px-4 py-2.5 text-[13px] font-medium text-ink transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Open demo runner
            </Link>
          </div>
        </div>
      </section>

      <section className="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Sessions" value={data.total} note="owner scoped" />
        <Metric label="Live now" value={live} note="starting, active, or ending" />
        <Metric label="Completed" value={completed} note="terminal sessions" />
        <Metric label="Connections" value={adapters} note={attention ? `${attention} need attention` : "evidence linked"} />
      </section>

      <section className="shrink-0 overflow-hidden rounded-2xl border border-line bg-surface shadow-[var(--shadow)]">
        <div className="flex flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div>
            <h2 className="font-serif text-[19px] font-semibold text-ink">Your recorded sessions</h2>
            <p className="mt-1 text-[13px] text-slate">
              Newest activity first · showing {data.sessions.length} of {data.total} · refreshes while this page is open
            </p>
          </div>
          <div className="flex flex-wrap gap-1 rounded-lg bg-surface-2 p-1" aria-label="Session filter">
            {(["all", "live", "completed", "attention"] as Filter[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
                className={filter === value
                  ? "rounded-md bg-surface px-3 py-1.5 text-xs font-medium text-ink shadow-sm"
                  : "rounded-md px-3 py-1.5 text-xs text-slate transition hover:text-ink"}
              >
                {value[0].toUpperCase() + value.slice(1)}
              </button>
            ))}
          </div>
        </div>
        {data.sessions.length === 0 ? (
          <div className="px-5 py-12 text-center">
            <p className="font-serif text-xl font-semibold text-ink">No connected sessions yet</p>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-slate">
              Connect Cursor or Claude Code, then keep coding normally. The first opted-in prompt opens a session here.
            </p>
            <Link to="/setup" className="mt-5 inline-flex rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white">
              Connect your editor
            </Link>
          </div>
        ) : visible.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-slate">No sessions match this filter.</div>
        ) : (
          <div>{visible.map((session) => <SessionRow key={session.id} session={session} />)}</div>
        )}
        {data.total > data.sessions.length ? (
          <p role="status" className="border-t border-warn bg-warn-soft px-5 py-3 text-[12.5px] text-ink">
            This deployment has more than {data.sessions.length} sessions for your identity. The current API returns the newest page only.
          </p>
        ) : null}
      </section>
    </div>
  );
}

export function DeveloperSessions() {
  const sessions = useQuery({
    queryKey: ["developer-sessions"],
    queryFn: () => api.developerSessions(200),
    refetchInterval: 5_000,
  });

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Developer"
        title="Sessions"
        meta={<span>live adapter activity</span>}
      />
      <Async query={sessions} label="Loading connected sessions…">
        {(data) => <SessionInventory data={data} />}
      </Async>
    </main>
  );
}
