import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import { api } from "../lib/api";
import { FleetCytoscape } from "../features/graph/FleetCytoscape";
import { PageHeader } from "../components/PageHeader";
import { TabBar, fleetTabs } from "../components/TabBar";

const toneFor = (s: string): "risk" | "warn" | "ok" =>
  s === "exploitable" ? "risk" : s === "present" ? "warn" : "ok";

export function FleetGraph() {
  const [query, setQuery] = useState("agents importing numpy@1.26.4");
  const [submitted, setSubmitted] = useState(query);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["fleetQuery", submitted],
    queryFn: () => api.fleetQuery(submitted),
  });

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Fleet"
        title="Hypergraph"
        meta={
          <span>
            live query · {data ? `${data.hits.length} agents matched` : "…"}
          </span>
        }
      />
      <TabBar label="Fleet views" tabs={fleetTabs()} />

      <div className="flex flex-1 flex-col gap-4 overflow-hidden p-6">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setSubmitted(query);
          }}
          className="flex items-center gap-3 rounded-xl border border-line-2 bg-surface px-4 py-3"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7E8A97" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" /><path d="M20 20l-3-3" />
          </svg>
          <input
            aria-label="Fleet query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 border-0 bg-transparent font-mono text-sm text-ink outline-none"
          />
          <button className="rounded-md bg-accent px-3.5 py-1.5 font-mono text-xs text-white hover:brightness-110">
            Run
          </button>
        </form>

        {/* The graph is the point of this screen, so the result list gives way
            to it: side by side only where both fit, stacked below otherwise. */}
        <div className="flex flex-1 flex-col gap-5 overflow-hidden xl:flex-row">
          <div className="relative min-h-[280px] flex-1 overflow-hidden rounded-2xl border border-line bg-surface xl:min-w-[420px]">
            {isLoading && <div className="grid h-full place-items-center font-mono text-sm text-slate">running query…</div>}
            {isError && <div className="grid h-full place-items-center font-mono text-sm text-risk">query failed — is the API running?</div>}
            {data && <FleetCytoscape payload={data.graph} />}
          </div>

          <aside className="flex w-full max-h-[40vh] shrink-0 flex-col gap-3 overflow-auto rounded-2xl border border-line bg-surface p-5 xl:max-h-none xl:w-[340px]">
            <div className="flex items-baseline justify-between">
              <p className="font-mono text-[11px] tracking-wide text-slate">
                RESULT · {data?.hits.length ?? 0} AGENTS
              </p>
              <span className="font-mono text-[11px] text-risk">
                {data?.hits.filter((h) => h.status === "exploitable").length ?? 0} exploitable
              </span>
            </div>
            {data?.interpreted && (
              <p className="text-[12px] leading-snug text-slate">
                {data.interpreted}
              </p>
            )}
            {data && data.hits.length === 0 && (
              <p className="text-[13px] text-ink">
                No agent in fleet memory matches this query.
              </p>
            )}
            {data?.hits.map((h) => (
              <div key={h.agent} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5">
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-2 font-mono text-[11px] text-slate">
                  {h.agent}
                </div>
                <span className="flex-1 text-[13px] text-ink">{h.name}</span>
                <Badge tone={toneFor(h.status)}>{h.status}</Badge>
              </div>
            ))}
          </aside>
        </div>
      </div>
    </main>
  );
}
