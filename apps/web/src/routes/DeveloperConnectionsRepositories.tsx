import { useState } from "react";

import { DevIcon } from "../components/DeveloperIcons";
import { useDeveloperProject } from "../components/DeveloperProject";
import { CopyButton, EmptyVisual, SidePanel, Status } from "../components/DeveloperVisual";
import type { RepositoryContext } from "../lib/api";
import { relativeTimeMs } from "../lib/format";
import { repositoryConfig } from "./DeveloperConnectionsEditors";

function RepositoryPanel({ repository, close }: { repository: RepositoryContext; close: () => void }) {
  const [gate, setGate] = useState(true);
  const [excludes, setExcludes] = useState(["*.env", "secrets/*", "*.pem"]);
  const [next, setNext] = useState("");
  const config = repositoryConfig(gate, excludes);
  return (
    <SidePanel title={repository.name} icon="repository" onClose={close} footer={<span className="dev-muted text-xs">Copy config to save locally.</span>}>
      <div className="dev-stack">
        <dl className="dev-kv"><dt>Repository</dt><dd>{repository.name}</dd><dt>Branch</dt><dd>{repository.branch ?? "Default"}</dd><dt>Remote</dt><dd className="dev-mono">{repository.remote ?? "Not reported"}</dd><dt>Commit</dt><dd className="dev-mono">{repository.commit ?? "Not reported"}</dd></dl>
        <button type="button" className={`dev-option ${gate ? "dev-option-selected" : ""}`} onClick={() => setGate((value) => !value)}><DevIcon name="security" /><span className="dev-integration-copy"><strong>{gate ? "Protect" : "Observe"}</strong><small>Package mode</small></span></button>
        <div className="dev-exclusion-list">{excludes.map((item) => <button type="button" className="dev-exclusion-chip" key={item} onClick={() => setExcludes((values) => values.filter((value) => value !== item))}><DevIcon name="eye" size={13} />{item}<DevIcon name="x" size={12} /></button>)}</div>
        <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); const value = next.trim(); if (value && !excludes.includes(value)) setExcludes((values) => [...values, value]); setNext(""); }}><div className="dev-field flex-1"><label htmlFor="repository-exclude">Exclude</label><input id="repository-exclude" value={next} onChange={(event) => setNext(event.target.value)} placeholder="private/*" /></div><button className="dev-action self-end" type="submit">Add</button></form>
        <div className="dev-code-block"><span className="dev-code-copy"><CopyButton value={config} label="Copy configuration" /></span>{config}</div>
      </div>
    </SidePanel>
  );
}

export function DeveloperConnectionsRepositories() {
  const project = useDeveloperProject();
  const [selected, setSelected] = useState<RepositoryContext | null>(null);
  return (
    <div className="dev-scroll">
      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Projects</h2><Status label={`${project.projects.length}`} tone="neutral" icon="repository" /></header>
        {project.sessionsPending ? <EmptyVisual icon="live" title="Loading" /> : project.projects.length ? project.projects.map((repository) => {
          const sessions = project.allSessions.filter((session) => session.repository.id === repository.id);
          const latest = sessions[0];
          const live = sessions.some((session) => ["starting", "active", "ending"].includes(session.status));
          const evidence = sessions.some((session) => Boolean(session.run_id));
          return <button key={repository.id} type="button" className="dev-session-row" onClick={() => setSelected(repository)}>
            <span className="dev-session-primary"><span><DevIcon name="repository" size={17} /></span><span className="dev-session-text"><strong>{repository.name}</strong><small>{repository.branch ?? "default branch"}</small></span></span>
            <span className="dev-session-task">{repository.remote ?? repository.id}</span>
            <Status label={live ? "Live" : "Observed"} tone={live ? "accent" : "success"} icon={live ? "live" : "check"} />
            <Status label={evidence ? "Recorded" : "Waiting"} tone={evidence ? "success" : "warning"} icon="evidence" />
            <span className="dev-session-time">{latest ? relativeTimeMs(latest.last_seen_at_ms) : "—"}</span>
          </button>;
        }) : <EmptyVisual icon="repository" title="Waiting" />}
      </section>
      {selected ? <RepositoryPanel repository={selected} close={() => setSelected(null)} /> : null}
    </div>
  );
}
