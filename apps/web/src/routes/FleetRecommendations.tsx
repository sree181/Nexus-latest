import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";
import type { ApplyReceipt, Recommendation } from "../lib/api";
import { api } from "../lib/api";
import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { TabBar, fleetTabs } from "../components/TabBar";
import { entityLabel, timestamp } from "../lib/format";

const kindTone: Record<
  Recommendation["kind"],
  "risk" | "warn" | "accent" | "neutral"
> = {
  CRITICAL: "risk",
  HIGH: "risk",
  POLICY: "accent",
  REVIEW: "warn",
  LICENSE: "neutral",
};

function Receipt({ receipt }: { receipt: ApplyReceipt }) {
  const cert = receipt.certificates[0];
  return (
    <section
      aria-label="Receipt"
      className="flex flex-col gap-3 rounded-2xl border border-ok bg-ok-soft p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        {/* names what was applied: the list can change underneath a receipt,
            because applying is what changed it */}
        <h3 className="font-serif text-[17px] text-ink">
          Applied · {receipt.title}
        </h3>
        <Badge tone={receipt.changed_memory ? "ok" : "neutral"}>
          {receipt.changed_memory ? "memory changed" : "recorded only"}
        </Badge>
      </div>
      <p className="text-[13.5px] leading-snug text-ink">{receipt.action}</p>
      <dl className="flex gap-6">
        <div>
          <dt className="font-mono text-[10.5px] tracking-wide text-slate">
            AGENTS
          </dt>
          <dd className="font-serif text-[24px] text-ink">{receipt.agents}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10.5px] tracking-wide text-slate">
            MEMORIES WRITTEN
          </dt>
          <dd className="font-serif text-[24px] text-ink">
            {receipt.memories_written}
          </dd>
        </div>
        {cert && (
          <div>
            <dt className="font-mono text-[10.5px] tracking-wide text-slate">
              PURGED
            </dt>
            <dd className="font-serif text-[24px] text-ink">
              {cert.purged_count}
            </dd>
          </div>
        )}
      </dl>
      {cert && (
        <div>
          <p className="font-mono text-[10.5px] tracking-wide text-slate">
            RETAINED HASH
          </p>
          <p className="break-all font-mono text-[11px] leading-snug text-ink">
            {cert.retained_hash}
          </p>
        </div>
      )}
      <p className="border-t border-ok pt-3 text-[12.5px] leading-snug text-slate">
        {receipt.note}
      </p>
      <p className="font-mono text-[11px] text-slate">
        {timestamp(receipt.issued_at)}
      </p>
    </section>
  );
}

function Detail({
  rec,
  onApply,
  pending,
  error,
}: {
  rec: Recommendation;
  onApply: () => void;
  pending: boolean;
  error: unknown;
}) {
  const radius = Object.entries(rec.blast_radius);

  return (
    <>
      <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-6">
        <div className="flex flex-wrap items-center gap-2.5">
          <Badge tone={kindTone[rec.kind]}>{rec.kind}</Badge>
          <h2 className="font-serif text-[21px] text-ink">{rec.title}</h2>
        </div>
        <p className="text-[14px] leading-snug text-slate">{rec.detail}</p>

        {radius.length > 0 && (
          <>
            <h3 className="mt-2 font-mono text-[11px] tracking-wide text-slate">
              BLAST RADIUS
            </h3>
            <dl className="flex flex-wrap gap-6">
              {radius.map(([key, value]) => (
                <div key={key}>
                  <dt className="font-mono text-[10.5px] tracking-wide text-slate">
                    {key.replace(/_/g, " ").toUpperCase()}
                  </dt>
                  <dd className="font-serif text-[30px] leading-tight text-ink">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </>
        )}

        {rec.agent_ids.length > 0 && (
          <>
            <h3 className="mt-2 font-mono text-[11px] tracking-wide text-slate">
              AGENTS IT WOULD TOUCH
            </h3>
            <ul className="flex flex-wrap gap-1.5">
              {rec.agent_ids.map((agent) => (
                <li
                  key={agent}
                  className="rounded border border-line-2 bg-surface-2 px-2 py-0.5 font-mono text-[11.5px] text-ink"
                >
                  {entityLabel(agent)}
                </li>
              ))}
            </ul>
          </>
        )}

        <div className="mt-2 flex items-center gap-3 border-t border-line pt-4">
          <Button disabled={pending} onClick={onApply}>
            {pending
              ? "Applying…"
              : `Apply to ${rec.agents} agent${rec.agents === 1 ? "" : "s"}`}
          </Button>
          <p className="text-[12.5px] leading-snug text-slate">
            The receipt says exactly what changed, so nothing is claimed that
            did not happen.
          </p>
        </div>
        {error !== null && (
          <p className="font-mono text-[11.5px] leading-snug text-risk">
            {error instanceof Error ? error.message : "apply failed"}
          </p>
        )}
      </section>
    </>
  );
}

function Rail({ recs }: { recs: Recommendation[] }) {
  const [selected, setSelected] = useState(recs[0].id);
  const rec = recs.find((r) => r.id === selected) ?? recs[0];
  const client = useQueryClient();

  // The receipt lives above the recommendation it came from. Applying a cut
  // really forgets memory, which can remove the recommendation from this very
  // list -- and the receipt is the proof the user needs to keep seeing.
  const [receipt, setReceipt] = useState<ApplyReceipt | null>(null);
  const apply = useMutation({
    mutationFn: (id: string) => api.applyRecommendation(id),
    onSuccess: (r) => {
      setReceipt(r);
      void client.invalidateQueries({ queryKey: ["recommendations"] });
      void client.invalidateQueries({ queryKey: ["fleetOverview"] });
      void client.invalidateQueries({ queryKey: ["fleetQuery"] });
      void client.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-hidden p-6 xl:flex-row">
      <nav
        aria-label="Recommendations"
        className="flex max-h-[45vh] w-full shrink-0 flex-col gap-2 overflow-auto rounded-2xl border border-line bg-surface p-4 xl:max-h-none xl:w-[320px]"
      >
        <p className="px-1 font-mono text-[11px] tracking-wide text-slate">
          {recs.length} RECOMMENDATIONS
        </p>
        {recs.map((r) => {
          const active = r.id === rec.id;
          return (
            <button
              key={r.id}
              type="button"
              aria-current={active ? "true" : undefined}
              onClick={() => {
                setSelected(r.id);
                apply.reset();
              }}
              className={`flex flex-col gap-1.5 rounded-xl border px-3.5 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                active
                  ? "border-accent bg-accent-soft"
                  : "border-line hover:bg-surface-2"
              }`}
            >
              <span className="flex items-center gap-2">
                <Badge tone={kindTone[r.kind]}>{r.kind}</Badge>
                <span className="font-mono text-[10.5px] text-slate">
                  {r.agents} agent{r.agents === 1 ? "" : "s"}
                </span>
              </span>
              <span className="text-[13.5px] leading-snug text-ink">
                {r.title}
              </span>
            </button>
          );
        })}
      </nav>
      <div className="flex flex-1 flex-col gap-5 overflow-auto">
        <Detail
          key={rec.id}
          rec={rec}
          onApply={() => apply.mutate(rec.id)}
          pending={apply.isPending}
          error={apply.error}
        />
        {receipt && <Receipt receipt={receipt} />}
      </div>
    </div>
  );
}

export function FleetRecommendations() {
  const recs = useQuery({
    queryKey: ["recommendations"],
    queryFn: api.recommendations,
  });

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Fleet"
        title="Recommendations"
        meta={<span>derived from the fleet hypergraph</span>}
      />
      <TabBar label="Fleet views" tabs={fleetTabs()} />
      <Async
        query={recs}
        label="deriving recommendations…"
        isEmpty={(r) => r.length === 0}
        emptyTitle="Nothing to recommend"
        emptyDetail="The fleet hypergraph shows no shared vulnerability, forbidden cluster or untrusted source worth acting on."
      >
        {(r) => <Rail recs={r} />}
      </Async>
    </main>
  );
}
