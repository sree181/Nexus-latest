import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Metric, OriginNote } from "../components/WorkflowUI";
import { api } from "../lib/api";
import { timestamp } from "../lib/format";

export function CisoOverview() {
  const overview = useQuery({ queryKey: ["governanceOverview"], queryFn: api.governanceOverview });

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO" title="Executive overview" meta={<span>governance posture</span>} />
      <Async query={overview} label="Reading governance posture…">
        {(data) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
            <OriginNote origin={data.origin} />
            <section aria-labelledby="workflow-heading">
              <h2 id="workflow-heading" className="font-serif text-lg font-semibold text-ink">Control-plane workload</h2>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
                <Metric label="Open cases" value={data.workflow.open_cases} detail="Cases not resolved or closed." tone={data.workflow.open_cases > 0 ? "warn" : "neutral"} />
                <Metric label="Overdue cases" value={data.workflow.overdue_cases} detail="Open cases past their server-calculated SLA." tone={data.workflow.overdue_cases > 0 ? "risk" : "neutral"} />
                <Metric label="Pending approvals" value={data.workflow.pending_approvals} detail="Unexpired decisions waiting for separation-of-duties review." tone={data.workflow.pending_approvals > 0 ? "warn" : "neutral"} />
                <Metric label="Active exceptions" value={data.workflow.active_exceptions} detail="Approved policy exceptions not yet expired." />
                <Metric label="Open remediations" value={data.workflow.open_remediations} detail="Remediation work not yet verified or failed." tone={data.workflow.open_remediations > 0 ? "warn" : "neutral"} />
              </div>
            </section>

            <section aria-labelledby="fleet-heading">
              <h2 id="fleet-heading" className="font-serif text-lg font-semibold text-ink">Observed fleet</h2>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="Active agents" value={data.fleet.agents_active} detail="Agents represented in fleet memory." />
                <Metric label="Reachable findings" value={data.fleet.exploitable_findings} detail="Findings with a traced path to a dangerous sink." tone={data.fleet.exploitable_findings > 0 ? "risk" : "neutral"} />
                <Metric label="Not assessed" value={data.fleet.unassessed_findings} detail="Findings with unknown reachability; not treated as safe." tone={data.fleet.unassessed_findings > 0 ? "warn" : "neutral"} />
                <Metric label="Runs unscanned" value={data.fleet.runs_unscanned} detail="Runs without external scanner evidence." tone={data.fleet.runs_unscanned > 0 ? "warn" : "neutral"} />
              </div>
            </section>

            <div className="grid gap-5 xl:grid-cols-2">
              <section className="rounded-2xl border border-line bg-surface p-5">
                <h2 className="font-serif text-lg font-semibold text-ink">Data health</h2>
                <p className="mt-1 text-sm leading-relaxed text-slate">These are deployment checks, not inferred posture scores.</p>
                <dl className="mt-4 divide-y divide-line">
                  {[
                    ["Durable storage", data.data_health.durable],
                    ["Evidence engine", data.data_health.engine],
                    ["Writes persist", data.data_health.persists],
                    ["Audit chain intact", data.data_health.audit_intact],
                    ["Identity provider verified", data.data_health.identity_verified],
                  ].map(([label, ok]) => <div key={String(label)} className="flex items-center justify-between gap-4 py-3"><dt className="text-sm text-ink">{String(label)}</dt><dd><Badge tone={ok ? "ok" : "warn"}>{ok ? "yes" : "no"}</Badge></dd></div>)}
                </dl>
              </section>
              <section className="rounded-2xl border border-line bg-surface p-5">
                <h2 className="font-serif text-lg font-semibold text-ink">Posture observations</h2>
                <p className="mt-1 text-sm leading-relaxed text-slate">Stored snapshots returned by the API. No chart is synthesized when history is absent.</p>
                {data.trends.length === 0 ? <p className="mt-4 rounded-xl border border-line-2 bg-surface-2 px-4 py-4 text-sm text-slate">No historical posture snapshots are available yet.</p> : (
                  <div className="responsive-table-wrap mt-4"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line"><th className="pb-2 font-mono text-[10.5px] font-normal tracking-widest text-slate">CAPTURED</th><th className="pb-2 font-mono text-[10.5px] font-normal tracking-widest text-slate">ORIGIN</th><th className="pb-2 font-mono text-[10.5px] font-normal tracking-widest text-slate">OBSERVATIONS</th></tr></thead><tbody>{data.trends.map((trend) => <tr key={trend.id} className="border-b border-line align-top"><td className="py-3 pr-4 font-mono text-xs text-ink">{timestamp(trend.captured_at)}</td><td className="py-3 pr-4"><Badge tone={trend.origin === "live" ? "ok" : trend.origin === "mixed" ? "warn" : "neutral"}>{trend.origin}</Badge></td><td className="py-3 text-xs text-slate">{Object.entries(trend.metrics).map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`).join(" · ") || "No metrics returned"}</td></tr>)}</tbody></table></div>
                )}
              </section>
            </div>
          </div>
        )}
      </Async>
    </main>
  );
}
