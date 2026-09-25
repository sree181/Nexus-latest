import { useQuery } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { api, type GovernanceEvidence } from "../lib/api";

const kindLabel: Record<string, string> = {
  policy_version: "Policy version",
  policy_activation: "Activation",
  policy_supersession: "Supersession",
  exception_request: "Exception request",
  exception_decision: "Decision",
  exception_expiry: "Expiry",
  exception_revocation: "Revocation",
};

function short(value: string): string {
  return value.length > 22 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
}

export function GovernanceEvidencePanel({
  kind,
  id,
}: {
  kind: "policy" | "exception";
  id: string;
}) {
  const query = useQuery<GovernanceEvidence>({
    queryKey: ["governance-evidence", kind, id],
    queryFn: () => kind === "policy" ? api.policyEvidence(id) : api.exceptionEvidence(id),
    retry: false,
  });

  return (
    <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]" aria-labelledby={`${kind}-evidence-heading`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Badge tone="ok">HyperMesh</Badge>
            <span className="font-mono text-[10.5px] tracking-widest text-slate">NATIVE EVIDENCE</span>
          </div>
          <h2 id={`${kind}-evidence-heading`} className="mt-2 font-serif text-lg font-semibold text-ink">Decision history</h2>
          <p className="mt-1 text-sm text-slate">Recorded relationships only. No inferred links are added here.</p>
        </div>
        <Button type="button" variant="ghost" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Checking…" : "Refresh"}
        </Button>
      </div>

      {query.isPending ? (
        <div className="mt-5 space-y-3" aria-label="Loading native evidence">
          {[0, 1, 2].map((item) => <div key={item} className="h-16 animate-pulse rounded-xl bg-surface-2" />)}
        </div>
      ) : query.isError ? (
        <div className="mt-5 rounded-xl border border-warn bg-warn-soft p-4">
          <p className="text-sm font-medium text-ink">Native evidence is not ready</p>
          <p className="mt-1 text-xs leading-relaxed text-slate">The workflow record remains available. MeshAgent will retry its evidence projection automatically.</p>
        </div>
      ) : (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">RELATIONS</p><p className="mt-1 font-serif text-2xl font-semibold text-ink">{query.data.graph.relations.length}</p></div>
            <div className="rounded-xl bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">ENTITIES</p><p className="mt-1 font-serif text-2xl font-semibold text-ink">{query.data.graph.nodes.length}</p></div>
            <div className="rounded-xl bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">ACKNOWLEDGED</p><p className="mt-1 font-serif text-2xl font-semibold text-ink">{query.data.projection_count}</p></div>
          </div>
          <ol className="mt-5 space-y-3">
            {query.data.graph.relations.map((relation, index) => (
              <li key={relation.id} className="relative rounded-xl border border-line-2 bg-white p-4 sm:pl-12">
                <span className="mb-2 inline-flex h-7 min-w-7 items-center justify-center rounded-full bg-accent-soft px-2 font-mono text-[11px] font-semibold text-accent sm:absolute sm:left-3 sm:top-4">{index + 1}</span>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium text-ink">{kindLabel[relation.kind] ?? relation.kind.replace(/_/g, " ")}</p>
                  <span className="font-mono text-[10px] text-slate" title={relation.id}>{short(relation.id)}</span>
                </div>
                <p className="mt-1 text-xs text-slate">{relation.label}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {relation.members.filter((member) => !member.startsWith("governance-event:")).slice(0, 8).map((member) => (
                    <span key={member} title={member} className="rounded-md border border-line-2 bg-surface-2 px-2 py-1 font-mono text-[10px] text-slate">{short(member)}</span>
                  ))}
                </div>
              </li>
            ))}
          </ol>
          <details className="mt-4 rounded-xl border border-line-2 bg-surface-2 px-4 py-3">
            <summary className="cursor-pointer text-xs font-medium text-ink">Integrity references</summary>
            <dl className="mt-3 space-y-2">
              {query.data.native_ulids.map((ulid, index) => (
                <div key={ulid} className="grid gap-1 border-t border-line pt-2 first:border-0 first:pt-0 sm:grid-cols-[9rem_minmax(0,1fr)]">
                  <dt className="font-mono text-[10px] tracking-widest text-slate">NATIVE ULID</dt>
                  <dd className="break-all font-mono text-[11px] text-ink">{ulid}</dd>
                  <dt className="font-mono text-[10px] tracking-widest text-slate">PAYLOAD SHA-256</dt>
                  <dd className="break-all font-mono text-[11px] text-slate">{query.data.payload_sha256[index]}</dd>
                </div>
              ))}
            </dl>
          </details>
        </>
      )}
    </section>
  );
}
