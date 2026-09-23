import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { LocalIdentityPicker } from "../components/SignInGate";

export function StartTask() {
  const [task, setTask] = useState(
    "Build a data loader for the training pipeline that reads model shards.",
  );
  const navigate = useNavigate();
  const client = useQueryClient();

  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });

  const start = useMutation({
    mutationFn: (t: string) => api.createRun(t),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void navigate({ to: "/runs/$runId", params: { runId: run.id } });
    },
  });

  const trimmed = task.trim();

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Workspace"
        title="Start a task"
        meta={<Badge tone="ok">Memory governed</Badge>}
      />

      <div className="flex flex-1 flex-col items-center overflow-auto px-16 py-14">
        <div className="flex w-full max-w-[720px] flex-col gap-3.5">
          <h2 className="text-center font-serif text-[38px] font-semibold text-ink">
            What should the agent build?
          </h2>
          <p className="mb-2 text-center text-base text-slate">
            Everything it decides, writes, and depends on is recorded as governed
            memory you can trace and undo.
          </p>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (trimmed) start.mutate(trimmed);
            }}
            className="flex flex-col gap-3.5 rounded-2xl border border-line-2 bg-surface p-[18px] shadow-[var(--shadow)]"
          >
            <label
              htmlFor="task"
              className="font-mono text-[11px] tracking-wide text-slate"
            >
              TASK
            </label>
            <textarea
              id="task"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              className="h-[84px] w-full resize-none border-0 font-sans text-[17px] leading-normal text-ink outline-none"
            />
            <div className="flex items-center justify-between border-t border-line pt-3.5">
              <span className="font-mono text-xs text-slate">
                Origin recorded as USER
              </span>
              <Button type="submit" disabled={!trimmed || start.isPending}>
                {start.isPending ? "Starting…" : "Run task"}
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </Button>
            </div>
            {start.isError && (
              <p className="font-mono text-[11.5px] leading-snug text-risk">
                {start.error instanceof Error
                  ? start.error.message
                  : "could not start the run"}
              </p>
            )}
          </form>

          {runs.data && runs.data.length > 0 && (
            <section aria-label="Recent runs" className="mt-6 flex flex-col gap-2">
              <p className="font-mono text-[11px] tracking-wide text-slate">
                RUNS WITH MEMORY
              </p>
              {runs.data.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  onClick={() =>
                    void navigate({
                      to: "/runs/$runId",
                      params: { runId: run.id },
                    })
                  }
                  className="flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <span className="font-mono text-[11.5px] text-slate">
                    {run.id}
                  </span>
                  <span className="flex-1 text-[13.5px] leading-snug text-ink">
                    {run.task}
                  </span>
                  <span className="font-mono text-[11px] text-slate">
                    {run.memory_count} memories
                  </span>
                  <Badge
                    tone={
                      run.status === "complete"
                        ? "ok"
                        : run.status === "recording"
                          ? "accent"
                          : "risk"
                    }
                  >
                    {run.status}
                  </Badge>
                </button>
              ))}
            </section>
          )}

          {/* Only rendered when no provider is configured. With one, the
              role comes from signed claims and is not ours to choose. */}
          <section className="mt-10 border-t border-line pt-6">
            <LocalIdentityPicker />
          </section>
        </div>
      </div>
    </main>
  );
}
