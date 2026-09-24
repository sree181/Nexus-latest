import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { AdapterBadge, SessionStatusBadge, compactId } from "../components/DeveloperSessionUI";
import { PageHeader } from "../components/PageHeader";
import { TabBar, type Tab } from "../components/TabBar";
import { api } from "../lib/api";
import { relativeTimeMs, timestampMs } from "../lib/format";

export function developerSessionTabs(sessionId: string): Tab[] {
  const params = { sessionId };
  return [
    { label: "Activity", to: "/developer/sessions/$sessionId", params },
    { label: "Security", to: "/developer/sessions/$sessionId/security", params },
  ];
}

export function DeveloperSessionLayout() {
  const { sessionId } = useParams({ from: "/developer/sessions/$sessionId" });
  const session = useQuery({
    queryKey: ["developer-session", sessionId],
    queryFn: () => api.developerSession(sessionId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "starting" || status === "active" || status === "ending"
        ? 3_000
        : false;
    },
  });

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Developer sessions"
        title={compactId(sessionId, 12, 6)}
        meta={
          session.data ? (
            <>
              <AdapterBadge adapter={session.data.adapter} />
              <SessionStatusBadge status={session.data.status} />
              <span>{relativeTimeMs(session.data.last_seen_at_ms)}</span>
            </>
          ) : undefined
        }
      />
      {session.isPending ? <Loading label="Opening session record…" /> : null}
      {session.isError ? (
        <ErrorState error={session.error} retry={() => void session.refetch()} />
      ) : null}
      {session.data ? (
        <>
          <section className="shrink-0 border-b border-line bg-surface px-4 py-4 sm:px-6">
            <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-start">
              <div className="min-w-0">
                <p className="font-mono text-[10px] tracking-widest text-slate">
                  {session.data.repository.name.toUpperCase()}
                  {session.data.repository.branch ? ` / ${session.data.repository.branch}` : ""}
                </p>
                <h2 className="mt-1 max-w-4xl font-serif text-[21px] font-semibold leading-tight text-ink">
                  {session.data.task}
                </h2>
                <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-slate">
                  <span title={session.data.id}>Session {compactId(session.data.id)}</span>
                  <span>Started {timestampMs(session.data.started_at_ms)}</span>
                  <span>Sequence {session.data.last_acked_sequence}</span>
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                {!session.data.verified ? <Badge tone="warn">unverified local identity</Badge> : null}
                {session.data.run_id ? (
                  <Link
                    to="/runs/$runId"
                    params={{ runId: session.data.run_id }}
                    className="rounded-lg border border-line-2 bg-surface px-3 py-2 text-[12px] font-medium text-accent transition hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    Open governed evidence →
                  </Link>
                ) : (
                  <Badge tone="warn">evidence run pending</Badge>
                )}
              </div>
            </div>
          </section>
          <TabBar label="Developer session views" tabs={developerSessionTabs(sessionId)} />
          <Outlet />
        </>
      ) : null}
    </main>
  );
}
