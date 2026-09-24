import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { DeveloperTopbar } from "../components/DeveloperProject";
import { EmptyVisual, Status } from "../components/DeveloperVisual";
import { api } from "../lib/api";

export function StartTask() {
  const [task, setTask] = useState("Build a data loader for the training pipeline that reads model shards.");
  const navigate = useNavigate();
  const client = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const start = useMutation({
    mutationFn: (value: string) => api.createRun(value),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void navigate({ to: "/runs/$runId", params: { runId: run.id } });
    },
  });
  const value = task.trim();

  return (
    <main className="dev-page">
      <DeveloperTopbar title="Demo Runner" showProject={false} actions={<Status label="Development" tone="warning" icon="tool" title="This utility is not the normal Cursor or Claude Code workflow." />} />
      <div className="dev-scroll dev-stack">
        <form className="dev-surface p-4" onSubmit={(event) => { event.preventDefault(); if (value) start.mutate(value); }}>
          <div className="dev-field"><label htmlFor="demo-task">Task</label><textarea id="demo-task" value={task} onChange={(event) => setTask(event.target.value)} rows={4} className="w-full resize-y rounded-md border border-[var(--dev-border)] p-3 text-sm" /></div>
          <div className="mt-3 flex items-center justify-end"><button type="submit" className="dev-action dev-action-primary" disabled={!value || start.isPending}>{start.isPending ? "Starting" : "Run"}<DevIcon name="arrow" size={16} /></button></div>
          {start.isError ? <div className="mt-3 dev-compact-row dev-tone-danger"><DevIcon name="warning" /><span>Try again</span></div> : null}
        </form>
        <section className="dev-surface">
          <header className="dev-panel-heading"><h2>Recent</h2></header>
          {runs.isPending ? <EmptyVisual icon="live" title="Loading" /> : runs.data?.length ? runs.data.slice(0, 8).map((run) => <button type="button" key={run.id} className="dev-compact-row w-full border-x-0 border-t-0 bg-transparent text-left" onClick={() => void navigate({ to: "/runs/$runId", params: { runId: run.id } })}><DevIcon name="session" /><span className="dev-compact-row-main"><strong>{run.task}</strong><small>{run.memory_count} memories</small></span><Status label={run.status === "complete" ? "Complete" : run.status === "recording" ? "Live" : "Failed"} tone={run.status === "complete" ? "success" : run.status === "recording" ? "accent" : "danger"} /></button>) : <EmptyVisual icon="session" title="No runs" />}
        </section>
      </div>
    </main>
  );
}
