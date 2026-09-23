import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { MemoryOrigin, MemoryStatus, RewindMemory, RewindOut } from "../lib/api";
import { api } from "../lib/api";
import { Async } from "../components/Async";
import { entityLabel, timestamp } from "../lib/format";

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

export function RunRewind() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const { at } = useSearch({ from: "/runs/$runId/rewind" });
  const navigate = useNavigate();
  // pinned once, so moving the slider does not re-key the query every tick
  const [now] = useState(() => Math.floor(Date.now() / 1000));
  const asked = at ?? now;

  const rewind = useQuery({
    queryKey: ["rewind", runId, asked],
    queryFn: () => api.runRewind(runId, asked),
    placeholderData: (prev) => prev,
  });

  return (
    <Async query={rewind} label="reconstructing memory…">
      {(data) => (
        <Timeline
          data={data}
          now={now}
          asked={asked}
          onAsk={(next) =>
            void navigate({
              to: "/runs/$runId/rewind",
              params: { runId },
              search: { at: next },
              replace: true,
            })
          }
        />
      )}
    </Async>
  );
}

function Timeline({
  data,
  now,
  asked,
  onAsk,
}: {
  data: RewindOut;
  now: number;
  asked: number;
  onAsk: (at: number) => void;
}) {
  // the present is always a stop, and is usually past the last write
  const stops = useMemo(() => {
    const ms = data.milestones;
    return ms.length > 0 && ms[ms.length - 1] === now ? ms : [...ms, now];
  }, [data.milestones, now]);

  const position = Math.max(
    0,
    stops.findIndex((s) => s >= asked) === -1
      ? stops.length - 1
      : stops.findIndex((s) => s >= asked),
  );
  const isPresent = asked >= stops[stops.length - 1];
  const lost = data.now - data.held;

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5">
      <div className="mx-auto max-w-[900px]">
        <header className="mb-5">
          <h2 className="font-serif text-[20px] text-ink">
            What this run knew, and when
          </h2>
          <p className="mt-1.5 max-w-[68ch] text-[13.5px] leading-snug text-slate">
            Reconstructed from each memory’s validity window and the record of
            what superseded or deleted it — not from a saved snapshot. There is
            no snapshot, and keeping one would be a second copy of memory that a
            deletion could not reach.
          </p>
        </header>

        {stops.length < 2 ? (
          <SingleInstant at={stops[0] ?? now} held={data.held} />
        ) : (
          <fieldset className="rounded-xl border border-line bg-surface px-4 py-3.5">
            <label
              htmlFor="rewind-stop"
              className="font-mono text-[11px] uppercase tracking-wide text-slate"
            >
              Rewind to
            </label>
            <input
              id="rewind-stop"
              type="range"
              min={0}
              max={stops.length - 1}
              step={1}
              value={position}
              onChange={(e) => onAsk(stops[Number(e.target.value)])}
              className="mt-2 w-full accent-accent"
            />
            <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-2">
              <p className="font-mono text-[12.5px] text-ink">
                {isPresent ? "now" : timestamp(asked)}
              </p>
              <p className="font-mono text-[11px] text-slate">
                stop {position + 1} of {stops.length} · moments this run’s
                memory changed
              </p>
            </div>
          </fieldset>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2.5">
          <Badge tone="accent">
            {data.held} held{isPresent ? "" : " then"}
          </Badge>
          <Badge tone="neutral">{data.now} held now</Badge>
          {data.redacted > 0 && (
            <Badge tone="risk">{data.redacted} since forgotten</Badge>
          )}
          {data.sample && <Badge tone="warn">sample data</Badge>}
          {!isPresent && lost > 0 && (
            <span className="text-[13px] text-slate">
              memory has grown by {lost} since this moment
            </span>
          )}
        </div>

        {data.redacted > 0 && <DeletionNotice count={data.redacted} />}

        {data.memories.length === 0 ? (
          <p className="mt-6 rounded-xl border border-line bg-surface px-4 py-6 text-center text-[13.5px] text-slate">
            Nothing yet. This run held no memory at {timestamp(asked)}.
          </p>
        ) : (
          <ul className="mt-4 space-y-2">
            {data.memories.map((m) => (
              <Held key={m.ulid} memory={m} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Some runs are written in one burst — a batch import, or a build fast enough
 *  to finish inside a second. Offering a slider over a single instant would
 *  imply a history that was never recorded. */
function SingleInstant({ at, held }: { at: number; held: number }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3.5">
      <p className="text-[13.5px] leading-snug text-ink">
        This run’s memory was written in a single instant, so there is no
        timeline to walk. All {held} memories arrived at {timestamp(at)}.
      </p>
    </div>
  );
}

/** The one claim this screen must never be read as making. */
function DeletionNotice({ count }: { count: number }) {
  return (
    <div className="mt-4 flex items-start gap-3 rounded-xl border border-risk bg-risk-soft px-4 py-3">
      <Badge tone="risk">deleted</Badge>
      <p className="text-[12.5px] leading-snug text-ink">
        {count} {count === 1 ? "memory was" : "memories were"} held at this
        moment and {count === 1 ? "has" : "have"} since been forgotten. Rewind
        can show that {count === 1 ? "it" : "they"} existed, because the
        hyperedge and its content hash survive a deletion — and cannot show
        what {count === 1 ? "it" : "they"} said, because the payload was
        destroyed. A reconstruction that could would repeal every deletion
        certificate this run has issued.
      </p>
    </div>
  );
}

function Held({ memory }: { memory: RewindMemory }) {
  if (memory.redacted) {
    return (
      <li className="rounded-xl border border-dashed border-line-2 bg-surface-2 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] tracking-wide text-slate">
            {memory.kind.toUpperCase()}
          </span>
          <Badge tone="neutral">forgotten since</Badge>
        </div>
        <p className="mt-2 text-[14.5px] italic leading-snug text-slate">
          Held here, and no longer readable.
        </p>
        <p className="mt-1.5 font-mono text-[11px] text-slate">
          {entityLabel(memory.entity)} · {memory.ulid}
        </p>
      </li>
    );
  }
  return (
    <li className="rounded-xl border border-line bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] tracking-wide text-slate">
          {memory.kind.toUpperCase()}
        </span>
        <Badge tone={originTone[memory.origin]}>{memory.origin}</Badge>
        <Badge tone={statusTone[memory.status]}>{memory.status}</Badge>
      </div>
      <p className="mt-2 text-[14.5px] leading-snug text-ink">
        {memory.statement ?? entityLabel(memory.entity)}
      </p>
      <p className="mt-1.5 font-mono text-[11px] text-slate">
        {entityLabel(memory.entity)} · {memory.ulid}
      </p>
    </li>
  );
}
