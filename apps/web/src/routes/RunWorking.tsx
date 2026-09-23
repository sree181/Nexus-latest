import { useEffect, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";
import type { MemoryEvent, MemoryOrigin, MemoryStatus } from "../lib/api";
import { useRunStream } from "../lib/useRunStream";
import { entityLabel } from "../lib/format";

const originTone: Record<MemoryOrigin, "accent" | "neutral" | "warn"> = {
  USER: "accent",
  AGENT: "neutral",
  EXTERNAL: "warn",
};

const statusTone: Record<MemoryStatus, "ok" | "warn" | "accent" | "risk"> = {
  VERIFIED: "ok",
  UNVERIFIED: "warn",
  USER_STATED: "accent",
  QUARANTINED: "risk",
};

/** Consecutive events from the same step, so "found three dangerous calls"
 *  reads as one thing the agent did rather than three. */
interface Step {
  step: string;
  events: MemoryEvent[];
}

function toSteps(events: MemoryEvent[]): Step[] {
  const steps: Step[] = [];
  for (const event of events) {
    const last = steps[steps.length - 1];
    if (last && last.step === event.step) last.events.push(event);
    else steps.push({ step: event.step, events: [event] });
  }
  return steps;
}

function Timeline({ steps, live }: { steps: Step[]; live: boolean }) {
  return (
    <ol className="flex flex-col">
      {steps.map((step, i) => (
        <li key={`${step.step}-${i}`} className="flex gap-4">
          <div className="flex flex-col items-center">
            <span
              aria-hidden="true"
              className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-accent"
            />
            {(i < steps.length - 1 || live) && (
              <span aria-hidden="true" className="w-px flex-1 bg-line-2" />
            )}
          </div>
          <div className="flex flex-col gap-1 pb-6">
            <p className="font-mono text-[11px] tracking-wide text-slate">
              {step.step.toUpperCase()}
            </p>
            {step.events.map((event) => (
              <p key={event.ulid ?? event.detail} className="text-[14px] leading-snug text-ink">
                {event.detail}
              </p>
            ))}
          </div>
        </li>
      ))}
      {live && (
        <li className="flex gap-4">
          <span
            aria-hidden="true"
            className="mt-1.5 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-accent-bright"
          />
          <p role="status" className="font-mono text-[12px] text-slate">
            working…
          </p>
        </li>
      )}
    </ol>
  );
}

function MemoryCard({ event }: { event: MemoryEvent }) {
  return (
    <li className="flex flex-col gap-2 rounded-xl border border-line px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={originTone[event.origin]}>{event.origin}</Badge>
        <Badge tone={statusTone[event.status]}>{event.status}</Badge>
      </div>
      <p className="text-[13px] leading-snug text-ink">{event.detail}</p>
      <ul className="flex flex-wrap gap-1">
        {event.members.map((member) => (
          <li
            key={member}
            className="rounded border border-line-2 bg-surface-2 px-1.5 py-0.5 font-mono text-[10.5px] text-slate"
          >
            {entityLabel(member)}
          </li>
        ))}
      </ul>
      <p className="border-t border-line pt-2 font-mono text-[10.5px] leading-snug text-slate">
        {event.gate}
      </p>
    </li>
  );
}

function Stream({ runId }: { runId: string }) {
  const stream = useRunStream(runId);
  const client = useQueryClient();
  const live = stream.status === "connecting" || stream.status === "streaming";

  // once the run stops writing, the run list and every view derived from its
  // memory are stale, so let the other tabs refetch
  useEffect(() => {
    if (stream.status !== "done") return;
    void client.invalidateQueries({ queryKey: ["runs"] });
    void client.invalidateQueries({ queryKey: ["run", runId] });
  }, [stream.status, client, runId]);

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-hidden p-6 xl:flex-row">
      <section
        aria-label="Agent steps"
        className="flex flex-1 flex-col gap-4 overflow-auto rounded-2xl border border-line bg-surface p-6"
      >
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-[19px] text-ink">What the agent did</h2>
          <span className="font-mono text-[11px] text-slate">
            {stream.events.length} writes
          </span>
        </div>
        {stream.notices.map((notice) => (
          <p
            key={notice}
            className="rounded-xl border border-line-2 bg-surface-2 px-3.5 py-2.5 text-[12.5px] leading-snug text-slate"
          >
            {notice}
          </p>
        ))}
        {stream.status === "error" && (
          <p className="rounded-xl border border-risk bg-risk-soft px-3.5 py-2.5 text-[12.5px] text-risk">
            {stream.error}
          </p>
        )}
        {stream.events.length === 0 && !live && stream.status !== "error" && (
          <p className="text-[13.5px] text-slate">
            This run has not written any memory.
          </p>
        )}
        <Timeline steps={toSteps(stream.events)} live={live} />
      </section>

      <aside
        aria-label="Memory forming"
        className="flex max-h-[45vh] w-full shrink-0 flex-col gap-3 overflow-auto rounded-2xl border border-line bg-surface p-5 xl:max-h-none xl:w-[380px]"
      >
        <div>
          <h2 className="font-mono text-[11px] tracking-wide text-slate">
            MEMORY FORMING
          </h2>
          <p className="mt-1 text-[12.5px] leading-snug text-slate">
            Every write carries who authored it, how far it is verified, and
            what the write gate decided.
          </p>
        </div>
        {stream.events.length === 0 ? (
          <p className="font-mono text-[12px] text-slate">
            {live ? "waiting for the first write…" : "no memory yet"}
          </p>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {stream.events.map((event, i) => (
              <MemoryCard key={event.ulid ?? i} event={event} />
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}

export function RunWorking() {
  const { runId } = useParams({ from: "/runs/$runId" });
  // bumping this remounts the subscription, which replays the run's memory
  const [attempt, setAttempt] = useState(0);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line bg-surface px-6 py-2.5">
        <p className="text-[13px] text-slate">
          Streamed from the engine as the run records it.
        </p>
        <Button variant="ghost" onClick={() => setAttempt((n) => n + 1)}>
          Replay stream
        </Button>
      </div>
      <Stream key={`${runId}-${attempt}`} runId={runId} />
    </div>
  );
}
