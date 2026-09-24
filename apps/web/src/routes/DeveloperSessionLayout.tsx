import { createContext, useContext } from "react";
import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { DeveloperTopbar } from "../components/DeveloperProject";
import { IconTabs, Status } from "../components/DeveloperVisual";
import { sessionVisual } from "../components/DeveloperSessionUI";
import { ErrorState, Loading } from "../components/Async";
import { api, type DeveloperSession } from "../lib/api";
import { relativeTimeMs } from "../lib/format";

const SessionContext = createContext<DeveloperSession | null>(null);

export function useOpenDeveloperSession(): DeveloperSession {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useOpenDeveloperSession must be used inside DeveloperSessionLayout");
  return value;
}

export function developerSessionTabs(sessionId: string) {
  const params = { sessionId };
  return [
    { label: "Overview", icon: "session" as const, to: "/developer/sessions/$sessionId" as const, params, exact: true },
    { label: "Activity", icon: "activity" as const, to: "/developer/sessions/$sessionId/activity" as const, params },
    { label: "Security", icon: "security" as const, to: "/developer/sessions/$sessionId/security" as const, params },
    { label: "Evidence", icon: "evidence" as const, to: "/developer/sessions/$sessionId/evidence" as const, params },
  ];
}

export function DeveloperSessionLayout() {
  const { sessionId } = useParams({ from: "/developer/sessions/$sessionId" });
  const session = useQuery({
    queryKey: ["developer-session", sessionId],
    queryFn: () => api.developerSession(sessionId),
    refetchInterval: (query) => ["starting", "active", "ending"].includes(query.state.data?.status ?? "") ? 3_000 : false,
  });

  if (session.isPending) return <main className="dev-page"><DeveloperTopbar title="Session" showProject={false} /><Loading label="Loading" /></main>;
  if (session.isError) return <main className="dev-page"><DeveloperTopbar title="Session" showProject={false} /><ErrorState error={session.error} retry={() => void session.refetch()} /></main>;

  const state = sessionVisual(session.data.status);
  return (
    <SessionContext.Provider value={session.data}>
      <main className="dev-page">
        <DeveloperTopbar title="Session" showProject={false} actions={
          session.data.run_id ? (
            <Link to="/runs/$runId/memory" params={{ runId: session.data.run_id }} className="dev-icon-button" aria-label="Open provenance">
              <DevIcon name="external" />
            </Link>
          ) : undefined
        } />
        <section className="dev-context">
          <div className="dev-context-main">
            <span className="dev-context-icon"><DevIcon name="repository" size={18} /></span>
            <span className="dev-context-title">
              <strong>{session.data.repository.name}</strong>
              <small>{session.data.repository.branch ?? "default branch"} · {session.data.task}</small>
            </span>
          </div>
          <div className="dev-context-actions">
            {!session.data.verified ? <Status label="Local" tone="warning" icon="user" title="This identity was not verified by company sign-in." /> : null}
            <Status label={state.label} tone={state.tone} icon={state.label === "Live" ? "live" : state.label === "Failed" ? "warning" : "check"} />
            <span className="dev-relative-time">{relativeTimeMs(session.data.last_seen_at_ms)}</span>
          </div>
        </section>
        <IconTabs label="Session" tabs={developerSessionTabs(sessionId)} />
        <Outlet />
      </main>
    </SessionContext.Provider>
  );
}
