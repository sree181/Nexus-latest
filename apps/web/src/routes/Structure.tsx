import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import { api, latestRun } from "../lib/api";
import { PolygonHypergraph } from "../features/graph/PolygonHypergraph";
import { PageHeader } from "../components/PageHeader";
import { TabBar, fleetTabs } from "../components/TabBar";

export function Structure() {
  const [scaleIdx, setScaleIdx] = useState(0);

  // the newest run, not a fixed one: the structure of a run you just recorded
  // is the whole reason to look at this screen
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const run = latestRun(runs.data);

  const dec = useQuery({
    queryKey: ["decomp", run],
    queryFn: () => api.runDecomposition(run as string),
    enabled: Boolean(run),
  });
  const scales = useQuery({
    queryKey: ["scales", run],
    queryFn: () => api.runScales(run as string),
    enabled: Boolean(run),
  });

  const etaByBlock = useMemo(() => {
    const m: Record<string, number> = {};
    dec.data?.blocks.forEach((b) => (m[b.id] = b.eta));
    return m;
  }, [dec.data]);

  const scale = scales.data?.[Math.min(scaleIdx, (scales.data?.length ?? 1) - 1)];

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="Fleet"
        title="Structure"
        meta={<span>topological decomposition · run {run ?? "…"}</span>}
      />
      <TabBar label="Fleet views" tabs={fleetTabs()} />

      <div className="flex flex-1 flex-col gap-5 overflow-hidden p-6 xl:flex-row">
        {/* polygon canvas + scale slider */}
        <div className="flex flex-1 flex-col gap-3 overflow-hidden">
          <div className="flex items-center gap-4 rounded-xl border border-line-2 bg-surface px-4 py-3">
            <span className="font-mono text-[11px] tracking-wide text-slate">SCALE</span>
            <input
              type="range"
              min={0}
              max={(scales.data?.length ?? 1) - 1}
              value={scaleIdx}
              onChange={(e) => setScaleIdx(Number(e.target.value))}
              className="flex-1 accent-[var(--accent)]"
              aria-label="Simplification scale"
            />
            <span className="min-w-[190px] text-right font-mono text-xs text-ink">
              {scale ? `${scale.label} · B₁=${scale.b1}` : "…"}
            </span>
          </div>
          <div className="relative flex-1 overflow-hidden rounded-2xl border border-line bg-surface">
            {scale ? (
              <PolygonHypergraph graph={scale.graph} etaByBlock={etaByBlock} />
            ) : (
              <div className="grid h-full place-items-center font-mono text-sm text-slate">loading…</div>
            )}
            <div className="pointer-events-none absolute bottom-3 left-4 flex gap-3 font-mono text-[10px] text-slate">
              <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "#B3413C" }} /> block (entangled)</span>
              <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "#0E7C8B" }} /> bridge</span>
              <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "#B4C0CA" }} /> branch</span>
            </div>
          </div>
        </div>

        {/* decomposition panel */}
        <aside className="flex max-h-[45vh] w-full shrink-0 flex-col gap-4 overflow-auto rounded-2xl border border-line bg-surface p-5 xl:max-h-none xl:w-[380px]">
          <div>
            <p className="font-mono text-[11px] tracking-wide text-slate">ENTANGLEMENT</p>
            <div className="mt-2 flex items-baseline gap-3">
              <span className="font-serif text-[34px] text-ink">{dec.data?.entanglement ?? "—"}</span>
              <span className="font-mono text-xs text-slate">
                B₀={dec.data?.b0 ?? "—"} · B₁={dec.data?.b1 ?? "—"}
              </span>
            </div>
            <p className="mt-1 text-[13px] leading-snug text-slate">
              Coupling score: independent cycles per element. Higher means more tightly knotted risk.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <p className="font-mono text-[11px] tracking-wide text-slate">TOPOLOGICAL BLOCKS</p>
            {dec.data?.blocks.length === 0 && (
              <p className="text-[13px] text-slate">No entangled blocks. Fully tree-structured.</p>
            )}
            {dec.data?.blocks.map((b) => (
              <div key={b.id} className="rounded-xl border border-line px-3.5 py-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[13px] text-ink">{b.id}</span>
                  {b.forbidden > 0 && <Badge tone="risk">{b.forbidden} forbidden</Badge>}
                </div>
                <div className="mt-2 h-1.5 w-full rounded-full bg-surface-2">
                  <div className="h-1.5 rounded-full bg-risk" style={{ width: `${Math.min(b.eta, 1) * 100}%` }} />
                </div>
                <div className="mt-1.5 flex justify-between font-mono text-[11px] text-slate">
                  <span>&eta; {b.eta}</span>
                  <span>{b.primal} entities · {b.dual} edges · {b.b1} cycles</span>
                </div>
              </div>
            ))}
          </div>

          {(dec.data?.forbidden.length ?? 0) > 0 && (
            <div className="rounded-xl border border-[color-mix(in_srgb,var(--risk)_30%,white)] bg-[var(--risk-soft)] p-3.5">
              <p className="font-mono text-[11px] text-risk">UNAVOIDABLE COUPLING</p>
              {dec.data?.forbidden.map((f, i) => (
                <p key={i} className="mt-1.5 text-[12.5px] leading-snug text-ink">
                  {f.edges.length} hyperedges share {f.shared.map((s) => s.split(":").slice(1).join(":")).join(", ")}.
                  This overlap cannot be drawn away; it is real tight coupling.
                </p>
              ))}
            </div>
          )}

          {(dec.data?.bridges.length ?? 0) > 0 && (
            <div className="flex flex-col gap-2">
              <p className="font-mono text-[11px] tracking-wide text-slate">BRIDGES · CUT FOR MAX DECOUPLING</p>
              {dec.data?.bridges.map((br) => (
                <div key={br.id} className="rounded-xl border border-line px-3.5 py-3">
                  <p className="text-[12.5px] leading-snug text-ink">{br.recommendation}</p>
                </div>
              ))}
            </div>
          )}

          <p className="mt-auto font-mono text-[11px] text-slate">
            {dec.data?.branches ?? 0} peripheral branch(es) · pruned first at coarser scales
          </p>
        </aside>
      </div>
    </main>
  );
}
