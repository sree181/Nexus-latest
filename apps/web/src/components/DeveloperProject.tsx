import type { ReactNode } from "react";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { api, type DeveloperSession, type RepositoryContext } from "../lib/api";
import { relativeTimeMs } from "../lib/format";
import { DevIcon } from "./DeveloperIcons";
import { Hint, Status, type VisualTone } from "./DeveloperVisual";

interface ProjectScopeValue {
  projectId: string;
  setProjectId: (projectId: string) => void;
  projects: RepositoryContext[];
  sessions: DeveloperSession[];
  allSessions: DeveloperSession[];
  sessionsTotal: number;
  sessionsPending: boolean;
  sessionsError: boolean;
}

const ProjectScope = createContext<ProjectScopeValue | null>(null);

function initialProject(): string {
  return new URLSearchParams(window.location.search).get("project") ?? "all";
}

export function projectsFromSessions(sessions: DeveloperSession[]): RepositoryContext[] {
  const byId = new Map<string, RepositoryContext>();
  for (const session of sessions) {
    if (!byId.has(session.repository.id)) byId.set(session.repository.id, session.repository);
  }
  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function sessionsForProject(sessions: DeveloperSession[], projectId: string): DeveloperSession[] {
  return projectId === "all" ? sessions : sessions.filter((session) => session.repository.id === projectId);
}

export function DeveloperProjectProvider({ children }: { children: ReactNode }) {
  const [projectId, setProjectId] = useState(initialProject);
  const query = useQuery({
    queryKey: ["developer-sessions"],
    queryFn: () => api.developerSessions(200),
    refetchInterval: 5_000,
  });
  const allSessions = query.data?.sessions ?? [];
  const projects = useMemo(() => projectsFromSessions(allSessions), [allSessions]);

  useEffect(() => {
    if (projectId !== "all" && projects.length > 0 && !projects.some((project) => project.id === projectId)) {
      setProjectId("all");
    }
  }, [projectId, projects]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (projectId === "all") url.searchParams.delete("project");
    else url.searchParams.set("project", projectId);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, [projectId]);

  const sessions = sessionsForProject(allSessions, projectId);

  return (
    <ProjectScope.Provider value={{
      projectId,
      setProjectId,
      projects,
      sessions,
      allSessions,
      sessionsTotal: query.data?.total ?? 0,
      sessionsPending: query.isPending,
      sessionsError: query.isError,
    }}>
      {children}
    </ProjectScope.Provider>
  );
}

export function useDeveloperProject(): ProjectScopeValue {
  const value = useContext(ProjectScope);
  if (!value) throw new Error("useDeveloperProject must be used inside DeveloperProjectProvider");
  return value;
}

function connectionState(
  sessions: DeveloperSession[],
  devices: { last_used: number }[] | undefined,
  failed: boolean,
): { label: string; tone: VisualTone; detail: string } {
  if (failed) return { label: "Offline", tone: "danger", detail: "MeshAgent could not read connection status." };
  if (!devices) return { label: "Waiting", tone: "neutral", detail: "Reading connection status." };
  if (devices.length === 0) return { label: "Setup needed", tone: "warning", detail: "No device is paired." };
  if (sessions.some((session) => ["starting", "active", "ending"].includes(session.status))) {
    return { label: "Connected", tone: "success", detail: "A coding session is active." };
  }
  const latest = sessions[0];
  if (latest) return { label: "Waiting", tone: "accent", detail: `Last activity ${relativeTimeMs(latest.last_seen_at_ms)}.` };
  if (devices.some((device) => device.last_used > 0)) return { label: "Waiting", tone: "accent", detail: "A device is paired and ready." };
  return { label: "Waiting", tone: "warning", detail: "A device is paired but has not recorded yet." };
}

export function DeveloperTopbar({
  title,
  actions,
  showProject = true,
}: {
  title: string;
  actions?: ReactNode;
  showProject?: boolean;
}) {
  const project = useDeveloperProject();
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 10_000 });
  const state = connectionState(project.sessions, devices.data, project.sessionsError || devices.isError);
  const selected = project.projects.find((item) => item.id === project.projectId);

  return (
    <header className="dev-topbar">
      <div className="dev-topbar-title">
        <h1>{title}</h1>
        {showProject ? (
          project.projects.length > 1 ? (
            <label className="dev-project-select">
              <DevIcon name="repository" size={16} />
              <span className="sr-only">Project</span>
              <select value={project.projectId} onChange={(event) => project.setProjectId(event.target.value)}>
                <option value="all">All projects</option>
                {project.projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              <DevIcon name="chevron" size={14} />
            </label>
          ) : selected || project.projects[0] ? (
            <span className="dev-project-static"><DevIcon name="repository" size={15} />{(selected ?? project.projects[0]).name}</span>
          ) : null
        ) : null}
      </div>
      <div className="dev-topbar-actions">
        <Hint label={state.detail}>
          <Link to="/developer/connections" className="dev-connection-link">
            <Status label={state.label} tone={state.tone} icon="connect" />
          </Link>
        </Hint>
        {actions}
      </div>
    </header>
  );
}
