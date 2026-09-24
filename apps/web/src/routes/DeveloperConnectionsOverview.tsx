import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { useDeveloperProject } from "../components/DeveloperProject";
import { EmptyVisual, Status, VisualFlow } from "../components/DeveloperVisual";
import { api } from "../lib/api";
import { relativeTimeMs } from "../lib/format";

export function DeveloperConnectionsOverview() {
  const project = useDeveloperProject();
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 10_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false });
  const adapters = new Set(project.sessions.map((session) => session.adapter));
  const evidenceCount = project.sessions.filter((session) => Boolean(session.run_id)).length;
  const active = project.sessions.filter((session) => ["starting", "active", "ending"].includes(session.status)).length;
  const latest = project.sessions[0];

  return (
    <div className="dev-scroll dev-stack">
      <section className="dev-surface dev-connection-topology">
        <VisualFlow label="Connection topology" nodes={[
          { id: "editor", label: "Editor", icon: "cursor", tone: adapters.size ? "success" : "warning", detail: adapters.size ? `${[...adapters].map((item) => item === "claude-code" ? "Claude Code" : "Cursor").join(" and ")} observed.` : "No editor activity yet." },
          { id: "device", label: "Device", icon: "device", tone: devices.isError ? "danger" : devices.data?.length ? "success" : "warning", detail: devices.isError ? "Device status is unavailable." : devices.data?.length ? `${devices.data.length} paired device${devices.data.length === 1 ? "" : "s"}.` : "No device is paired." },
          { id: "repository", label: "Project", icon: "repository", tone: project.projects.length ? "success" : "warning", detail: project.projects.length ? `${project.projects.length} observed project${project.projects.length === 1 ? "" : "s"}.` : "No repository activity yet." },
          { id: "meshagent", label: "MeshAgent", icon: "connect", tone: health.isError ? "danger" : health.data ? "success" : "neutral", detail: health.isError ? "MeshAgent is unavailable." : health.data ? "MeshAgent is reachable." : "Checking MeshAgent." },
          { id: "evidence", label: "Evidence", icon: "evidence", tone: evidenceCount ? "success" : project.sessions.length ? "warning" : "neutral", detail: evidenceCount ? `${evidenceCount} session${evidenceCount === 1 ? "" : "s"} linked to evidence.` : "No evidence run is linked yet." },
        ]} />
      </section>

      <div className="dev-connection-grid">
        <Link to="/developer/connections/editors" className="dev-integration-tile">
          <span className="dev-integration-logo"><DevIcon name="cursor" /></span>
          <span className="dev-integration-copy"><strong>Editors</strong><small>{adapters.size ? `${adapters.size} observed` : "Setup needed"}</small></span>
          <Status label={adapters.size ? "Connected" : "Setup"} tone={adapters.size ? "success" : "warning"} />
        </Link>
        <Link to="/developer/connections/repositories" className="dev-integration-tile">
          <span className="dev-integration-logo"><DevIcon name="repository" /></span>
          <span className="dev-integration-copy"><strong>Projects</strong><small>{project.projects.length ? `${project.projects.length} observed` : "Waiting"}</small></span>
          <Status label={project.projects.length ? "Observed" : "Waiting"} tone={project.projects.length ? "success" : "warning"} />
        </Link>
        <Link to="/developer/connections/devices" className="dev-integration-tile">
          <span className="dev-integration-logo"><DevIcon name="device" /></span>
          <span className="dev-integration-copy"><strong>Devices</strong><small>{devices.data?.length ? `${devices.data.length} paired` : "Pair a device"}</small></span>
          <Status label={devices.data?.length ? "Ready" : "Setup"} tone={devices.data?.length ? "success" : "warning"} />
        </Link>
        <Link to="/developer/sessions" className="dev-integration-tile">
          <span className="dev-integration-logo"><DevIcon name="session" /></span>
          <span className="dev-integration-copy"><strong>Sessions</strong><small>{latest ? `Updated ${relativeTimeMs(latest.last_seen_at_ms)}` : "Waiting"}</small></span>
          <Status label={active ? `${active} live` : "Idle"} tone={active ? "accent" : "neutral"} />
        </Link>
      </div>

      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Recent</h2></header>
        {project.sessionsPending ? <EmptyVisual icon="live" title="Loading" /> : project.sessions.slice(0, 5).length ? project.sessions.slice(0, 5).map((session) => (
          <Link key={session.id} to="/developer/sessions/$sessionId" params={{ sessionId: session.id }} className="dev-compact-row text-inherit no-underline">
            <DevIcon name="repository" size={17} />
            <span className="dev-compact-row-main"><strong>{session.repository.name}</strong><small>{session.task}</small></span>
            <span className="dev-relative-time">{relativeTimeMs(session.last_seen_at_ms)}</span>
          </Link>
        )) : <EmptyVisual icon="activity" title="Waiting" />}
      </section>
    </div>
  );
}
