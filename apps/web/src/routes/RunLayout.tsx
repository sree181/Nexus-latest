import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import { api, type RunStatus } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { TabBar, runTabs } from "../components/TabBar";

const tones: Record<RunStatus, "ok" | "accent" | "risk"> = {
  complete: "ok",
  recording: "accent",
  failed: "risk",
};

/** Shared chrome for the four views over one run: which run, what state its
 *  memory is in, and where its data came from. */
export function RunLayout() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const run = runs.data?.find((r) => r.id === runId);
  // Every run the API will serve this caller is in that list, so a miss means
  // the tabs below have nothing behind them. Held until the list has settled:
  // a run created a moment ago is briefly absent from a cached list, and
  // announcing it missing would be wrong about the one run the developer
  // definitely does own.
  const unlisted = runs.isSuccess && !runs.isFetching && run === undefined;

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Run"
        title={runId}
        meta={
          run && (
            <>
              <Badge tone={tones[run.status]}>{run.status}</Badge>
              {/* so nobody reads the deployment's own fixture as their work */}
              {run.seeded && <Badge tone="accent">seeded</Badge>}
              <span>
                {run.memory_count} memories · {run.findings} findings
              </span>
              <span>{run.sample ? "sample data" : "engine memory"}</span>
              {run.model && <span>built by {run.model}</span>}
            </>
          )
        }
      />
      <TabBar label="Run views" tabs={runTabs(runId)} />
      {run?.seeded && <SeededNotice />}
      {run?.reference_build && <ReferenceBuildNotice task={run.task} />}
      {unlisted ? <NotYours runId={runId} /> : <Outlet />}
    </main>
  );
}

/** Says whose memory this is, which is nobody's.
 *
 *  The reference build is readable by every caller, so it is the one run two
 *  developers can open and see the same findings in. Without this it reads as
 *  work one of them did. */
function SeededNotice() {
  return (
    <div className="flex shrink-0 items-start gap-3 border-b border-line bg-accent-soft px-6 py-3">
      <Badge tone="accent">seeded</Badge>
      <p className="text-[12.5px] leading-snug text-ink">
        This is the reference build this deployment publishes, not your work. It
        belongs to nobody and every caller here sees the same memory, so it is
        the run to read these screens against before your own has anything in
        it.
      </p>
    </div>
  );
}

/** What the six tabs would otherwise be: empty, with no explanation.
 *
 *  Says only what is known — the API did not list this run for this caller —
 *  because that covers a run that does not exist and a colleague's run alike,
 *  and the API deliberately does not distinguish them. */
function NotYours({ runId }: { runId: string }) {
  return (
    <div className="grid flex-1 place-items-center p-8">
      <div className="flex max-w-[460px] flex-col items-center gap-3 text-center">
        <p className="font-serif text-[19px] text-ink">
          Nothing here belongs to you
        </p>
        <p className="text-[13.5px] leading-snug text-slate">
          The API did not list run{" "}
          <span className="font-mono text-[12.5px]">{runId}</span> among the
          ones it will serve you. Either there is no run by that id or it is
          another developer&rsquo;s, and this deployment will not say which —
          whose runs exist is not something to confirm from here.
        </p>
        <Link
          to="/"
          className="rounded-lg border border-line bg-surface px-4 py-2 text-[13px] text-ink transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Back to your runs
        </Link>
      </div>
    </div>
  );
}

/** Says plainly that the memory below is not an execution of the task.
 *
 *  This has to live on the layout, not in the run stream: the stream says it
 *  once while recording, and anyone who reloads or opens Security directly
 *  would otherwise see shard-loader findings under their own task and have no
 *  way to know why. */
function ReferenceBuildNotice({ task }: { task: string }) {
  return (
    <div className="flex shrink-0 items-start gap-3 border-b border-warn bg-warn-soft px-6 py-3">
      <Badge tone="warn">reference build</Badge>
      <p className="text-[12.5px] leading-snug text-ink">
        No model is configured, so MeshAgent did not carry out{" "}
        <span className="font-medium">“{task}”</span>. The memory below is its
        reference shard-loader build, recorded so there is something real to
        govern. Every record went through the write gate and every finding,
        chain and certificate here is genuine — but none of it was derived from
        your task.
      </p>
    </div>
  );
}
