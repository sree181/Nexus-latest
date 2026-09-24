import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "../components/PageHeader";
import { ReviewStateBadge } from "../components/ReviewUI";
import { Async } from "../components/Async";
import { api } from "../lib/api";
import { timestamp } from "../lib/format";

export function AnalystReviews() {
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: () => api.reviews(), refetchInterval: 10_000 });
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst" title="Developer reviews" meta={<span>package decisions and fix verification</span>} />
      <Async query={reviews} label="Prioritizing reviews…">
        {(data) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
            <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
              <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-serif text-lg font-semibold text-ink">Requests from Developers</h2><p className="mt-1 text-sm text-slate">Decide the package question, give a safe version, or escalate. The evidence snapshot cannot change underneath the decision.</p></div><span className="font-mono text-xs text-slate">{data.total} total</span></div>
              {data.requests.length ? <div className="responsive-table-wrap mt-5"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line">{["Priority", "Request", "Package", "Developer", "Status"].map((heading) => <th key={heading} scope="col" className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}</tr></thead><tbody>{data.requests.map((request) => <tr key={request.id} className="border-b border-line align-top"><td className="py-3 pr-4"><span className="font-serif text-xl text-ink">{request.priority}</span><p className="max-w-44 text-[11px] leading-snug text-slate">{request.priority_reasons.join(" · ")}</p></td><td className="py-3 pr-4"><Link to="/analyst/reviews/$requestId" params={{ requestId: request.id }} className="font-medium text-accent underline-offset-2 hover:underline">{request.kind.replaceAll("_", " ")}</Link><p className="mt-1 text-xs text-slate">{request.repository_name} · {timestamp(request.created_at)}</p></td><td className="py-3 pr-4"><strong className="font-mono text-xs text-ink">{request.package}@{request.version || "unpinned"}</strong><p className="mt-1 text-xs text-slate">{request.advisories.map((item) => item.id).join(", ") || "Security data unavailable"}</p></td><td className="py-3 pr-4 text-sm text-ink">{request.owner_name}<p className="mt-1 text-xs text-slate">{request.code_entities.length} linked code item(s)</p></td><td className="py-3"><ReviewStateBadge state={request.state} /></td></tr>)}</tbody></table></div> : <div className="mt-5 rounded-xl border border-line-2 bg-surface-2 px-4 py-5"><p className="font-medium text-ink">No review requests</p><p className="mt-1 text-sm text-slate">Developer package questions will appear here with the evidence needed to answer them.</p></div>}
            </section>
          </div>
        )}
      </Async>
    </main>
  );
}
