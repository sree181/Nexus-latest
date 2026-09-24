import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { EmptyVisual, Status, VisualFlow } from "../components/DeveloperVisual";
import { projectionVisual, summarizeProjection } from "../components/DeveloperSessionUI";
import { api } from "../lib/api";
import { useOpenDeveloperSession } from "./DeveloperSessionLayout";

export function DeveloperSessionEvidence() {
  const session = useOpenDeveloperSession();
  const activity = useQuery({
    queryKey: ["developer-session", session.id, "activity"],
    queryFn: () => api.developerActivity(session.id, { limit: 500 }),
    refetchInterval: ["starting", "active", "ending"].includes(session.status) ? 5_000 : false,
  });
  const events = activity.data?.events ?? [];
  const summary = summarizeProjection(events);
  const files = new Set(events.filter((event) => event.type === "file.changed").map((event) => String(event.payload.path ?? ""))).size;
  const tools = events.filter((event) => event.type.startsWith("tool.")).length;
  const packages = new Set(events.filter((event) => event.type.startsWith("package.")).map((event) => `${String(event.payload.package ?? "")}@${String(event.payload.version ?? "")}`)).size;
  const decisions = events.filter((event) => event.type === "decision.recorded" || event.type === "policy.evaluated").length;
  const evidence = summary.attention ? { label: "Failed", tone: "danger" as const } : summary.pending ? { label: "Syncing", tone: "warning" as const } : summary.projected ? { label: "Recorded", tone: "success" as const } : { label: "Waiting", tone: "neutral" as const };

  return (
    <div className="dev-scroll dev-stack">
      <section className="dev-surface dev-connection-topology">
        <VisualFlow label="Evidence map" nodes={[
          { id: "session", label: "Session", icon: "session", tone: "success", detail: "The coding session is stored." },
          { id: "events", label: `${events.length} events`, icon: "activity", tone: events.length ? "success" : "neutral", detail: `${events.length} ordered events.` },
          { id: "decisions", label: `${decisions} decisions`, icon: "security", tone: decisions ? "success" : "neutral", detail: `${decisions} decisions and package checks.` },
          { id: "objects", label: `${files + tools + packages} objects`, icon: "package", tone: files + tools + packages ? "success" : "neutral", detail: `${files} files, ${tools} tool events, ${packages} packages.` },
          { id: "run", label: evidence.label, icon: "evidence", tone: evidence.tone, detail: session.run_id ? `HyperMesh run ${session.run_id}.` : "No governed run is linked yet." },
        ]} />
      </section>

      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Evidence</h2>{session.run_id ? <Link to="/runs/$runId/memory" params={{ runId: session.run_id }} className="dev-action">Open provenance <DevIcon name="external" size={15} /></Link> : null}</header>
        {activity.isPending ? <EmptyVisual icon="live" title="Loading" /> : events.length ? (
          <div>
            <div className="dev-compact-row"><DevIcon name="activity" /><span className="dev-compact-row-main"><strong>{events.length} events</strong><small>Ordered by MeshAgent sequence</small></span><Status label="Stored" tone="success" /></div>
            <div className="dev-compact-row"><DevIcon name="evidence" /><span className="dev-compact-row-main"><strong>{summary.projected} recorded</strong><small>{summary.pending ? `${summary.pending} syncing` : "Evidence is current"}</small></span><Status label={evidence.label} tone={evidence.tone} /></div>
            <div className="dev-compact-row"><DevIcon name="file" /><span className="dev-compact-row-main"><strong>{files} files</strong><small>{tools} tool events · {packages} packages</small></span></div>
            {events.filter((event) => event.projection_status !== "projected").slice(0, 8).map((event) => {
              const state = projectionVisual(event.projection_status);
              return <div className="dev-compact-row" key={event.event_id}><DevIcon name="evidence" /><span className="dev-compact-row-main"><strong>Event #{event.sequence}</strong><small>{event.projection_error ?? state.detail}</small></span><Status label={state.label} tone={state.tone} /></div>;
            })}
          </div>
        ) : <EmptyVisual icon="evidence" title="Waiting" />}
      </section>
    </div>
  );
}
