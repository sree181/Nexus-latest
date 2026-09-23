import { useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";
import type { GraphPayload, Relation } from "@meshagent/graph";
import type {
  DeletionCertificate,
  EvidenceNode,
  ForgetPreview,
  MemoryOrigin,
  MemoryStatus,
  WhyOut,
} from "../lib/api";
import { ApiError, api } from "../lib/api";
import { Async, Loading } from "../components/Async";
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

/** An untrusted source is one the agent took in from outside and never
 *  verified. That is exactly the thing a forget should start from. */
function isUntrustedSource(node: EvidenceNode): boolean {
  return (
    node.kind === "source" &&
    node.origin === "EXTERNAL" &&
    node.status === "UNVERIFIED"
  );
}

function ChainLink({
  node,
  flagged,
  last,
  via,
  relation,
}: {
  node: EvidenceNode;
  flagged: boolean;
  last: boolean;
  /** how the memory above gave rise to this one, once the chain is read
   *  forwards. The first link has nothing above it. */
  via: string | null;
  /** the hyperedge this link *is*, when the graph carries it. A chain link
   *  is one memory, and that memory usually spans more entities than the one
   *  it is filed under. */
  relation?: Relation;
}) {
  // the entity this link is filed under is already the headline above, so
  // only the rest of the span is news
  const alsoSpans =
    relation?.members.filter((m) => m !== node.entity) ?? [];
  return (
    <li className="flex gap-4">
      <div className="flex flex-col items-center">
        <span
          aria-hidden="true"
          className={`mt-3 h-3 w-3 shrink-0 rounded-full ${
            flagged ? "bg-risk" : "bg-accent"
          }`}
        />
        {!last && <span aria-hidden="true" className="w-px flex-1 bg-line-2" />}
      </div>
      <div
        className={`mb-3 flex-1 rounded-xl border px-4 py-3 ${
          flagged ? "border-risk bg-risk-soft" : "border-line bg-surface"
        }`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] tracking-wide text-slate">
            {node.kind.toUpperCase()}
          </span>
          <Badge tone={originTone[node.origin]}>{node.origin}</Badge>
          <Badge tone={statusTone[node.status]}>{node.status}</Badge>
          {flagged && <Badge tone="risk">untrusted source</Badge>}
          {node.tombstoned && <Badge tone="neutral">forgotten</Badge>}
        </div>
        <p className="mt-2 text-[14.5px] leading-snug text-ink">
          {node.statement ?? entityLabel(node.entity)}
        </p>
        <p className="mt-1.5 font-mono text-[11px] text-slate">
          {entityLabel(node.entity)} · source {node.source}
        </p>
        {via && (
          <p className="mt-1 font-mono text-[11px] text-slate">
            derived from the memory above via {via}
          </p>
        )}
        {alsoSpans.length > 0 && (
          <div className="mt-2.5 border-t border-line-2 pt-2">
            <ul className="flex flex-wrap gap-1.5">
              {alsoSpans.map((m) => (
                <li
                  key={m}
                  className="rounded border border-line-2 bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-ink"
                >
                  {entityLabel(m)}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 font-mono text-[11px] text-slate">
              one {relation?.kind} memory, spanning{" "}
              {relation?.members.length} entities · forgotten as a unit
            </p>
          </div>
        )}
      </div>
    </li>
  );
}

/** What the forget would destroy, shown before it does.
 *
 *  The statements are listed rather than counted on purpose. The closure is
 *  the part an operator cannot work out for themselves — they pick one
 *  poisoned source and the derivation walk takes everything downstream — so a
 *  number alone asks them to approve something they have not seen. */
function ForgetImpact({
  preview,
  pending,
  onConfirm,
  onCancel,
}: {
  preview: ForgetPreview;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <section
      aria-label="What this forget would destroy"
      className="flex flex-col gap-3 rounded-xl border border-risk bg-risk-soft p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-serif text-[15px] text-ink">
          {preview.purged_count}{" "}
          {preview.purged_count === 1 ? "memory" : "memories"} would be
          destroyed
        </h3>
        <Badge tone="risk">not yet done</Badge>
      </div>
      {preview.warnings.map((w) => (
        <p key={w} className="text-[13px] leading-snug text-ink">
          {w}
        </p>
      ))}
      <ul className="flex max-h-56 flex-col gap-1.5 overflow-auto">
        {preview.doomed.map((d) => (
          <li
            key={d.ulid}
            className="rounded border border-line-2 bg-surface px-2.5 py-1.5"
          >
            <span className="font-mono text-[10.5px] tracking-wide text-slate">
              {d.entity ? entityLabel(d.entity) : d.ulid}
            </span>
            <p className="text-[12px] leading-snug text-ink">{d.statement}</p>
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button variant="danger" disabled={pending} onClick={onConfirm}>
          {pending ? "Forgetting…" : "Destroy these"}
        </Button>
        <Button variant="ghost" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}

function Certificate({ cert }: { cert: DeletionCertificate }) {
  return (
    <section
      aria-label="Deletion certificate"
      className="flex flex-col gap-3 rounded-2xl border border-ok bg-ok-soft p-5"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-serif text-[17px] text-ink">Deletion certificate</h3>
        <Badge tone="ok">issued</Badge>
      </div>
      <p className="text-[13px] leading-snug text-ink">
        Forgot {entityLabel(cert.node)} and everything derived from it. The
        payloads are gone; their hashes are kept so the audit chain still holds.
      </p>
      <dl className="grid grid-cols-2 gap-3">
        <div>
          <dt className="font-mono text-[10.5px] tracking-wide text-slate">
            PURGED
          </dt>
          <dd className="font-serif text-[26px] text-ink">
            {cert.purged_count}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-[10.5px] tracking-wide text-slate">
            CLASSES PRUNED
          </dt>
          <dd className="font-serif text-[26px] text-ink">
            {cert.classes_pruned.length}
          </dd>
        </div>
      </dl>
      {cert.classes_pruned.length > 0 && (
        <p className="font-mono text-[11px] leading-snug text-slate">
          {cert.classes_pruned.map(entityLabel).join(", ")}
        </p>
      )}
      <div>
        <p className="font-mono text-[10.5px] tracking-wide text-slate">
          RETAINED HASH
        </p>
        <p className="break-all font-mono text-[11px] leading-snug text-ink">
          {cert.retained_hash}
        </p>
      </div>
      <p className="font-mono text-[11px] text-slate">
        {timestamp(cert.issued_at)} · actor {cert.actor} · reason {cert.reason}
      </p>
    </section>
  );
}

function Chain({ why, graph }: { why: WhyOut; graph: GraphPayload }) {
  // The engine walks the chain nearest-first, so each node's `via` is the
  // edge it followed to reach the next one. Read forwards as
  // source -> decision -> class, that same edge is what produced the
  // following node, which is why the relation is shifted by one below.
  const chain = [...why.chain].reverse();

  // Each link *is* a hyperedge, so its full span comes from the payload's
  // relations rather than being reassembled from pairwise edges. The old
  // version read the class's packages back off "imports" edges, which only
  // ever worked for that one link and quietly implied the edges were the
  // record.
  const byUlid = new Map(graph.relations.map((r) => [r.id, r]));

  return (
    <>
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="font-serif text-[19px] text-ink">
          Why {entityLabel(why.node)} exists
        </h2>
        <span className="font-mono text-[11px] text-slate">
          {chain.length} links
        </span>
      </div>
      <ol className="flex flex-col">
          {chain.map((node, i) => (
            <ChainLink
              key={node.ulid}
              node={node}
              flagged={isUntrustedSource(node)}
              last={i === chain.length - 1}
              via={i === 0 ? null : (chain[i - 1]?.via ?? null)}
              relation={byUlid.get(node.ulid)}
            />
          ))}
      </ol>
    </>
  );
}

export function RunMemory() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const client = useQueryClient();

  const graph = useQuery({
    queryKey: ["run", runId, "graph"],
    queryFn: () => api.runGraph(runId),
  });

  // the class the run wrote is the end of the provenance chain, so it is where
  // a "why" starts. It comes from the graph rather than being assumed.
  const node = graph.data?.nodes.find((n) => n.kind === "class")?.id;

  const why = useQuery({
    queryKey: ["run", runId, "why", node],
    queryFn: () => api.runWhy(runId, node as string),
    enabled: Boolean(node),
  });

  // Asking costs nothing and destroys nothing, so the preview is its own
  // step. Nothing is purged until the operator has read this back.
  const preview = useMutation({
    mutationFn: (target: string) => api.runForgetPreview(runId, target),
  });

  // The forget lives out here, above the chain it destroys: a successful
  // forget removes the class, which empties the chain, and the certificate is
  // the proof the user came for. It must not unmount with its own cause.
  const forget = useMutation({
    mutationFn: (p: ForgetPreview) => api.runForget(runId, p),
    onSuccess: () => {
      preview.reset();
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["run", runId] });
    },
  });

  // 412 is the one failure worth its own sentence: nothing was destroyed, and
  // the fix is to look again rather than to try again.
  const stale = forget.error instanceof ApiError && forget.error.status === 412;

  const untrusted = why.data
    ? [...why.data.chain].reverse().find(isUntrustedSource)
    : undefined;

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-hidden p-6 xl:flex-row">
      <section
        aria-label="Provenance chain"
        className="flex flex-1 flex-col overflow-auto rounded-2xl border border-line bg-surface p-6"
      >
        <Async
          query={graph}
          label="loading the run's graph…"
          isEmpty={(g) => !g.nodes.some((n) => n.kind === "class")}
          emptyTitle="No code recorded here"
          emptyDetail="This run holds no class, so there is no provenance chain to trace. A forget can leave it this way, and that is the point."
        >
          {(g) =>
            why.isPending ? (
              <Loading label="tracing the evidence chain…" />
            ) : (
              <Async query={why} label="tracing the evidence chain…">
                {(w) => <Chain why={w} graph={g} />}
              </Async>
            )
          }
        </Async>
      </section>

      <aside className="flex max-h-[45vh] w-full shrink-0 flex-col gap-4 overflow-auto xl:max-h-none xl:w-[380px]">
        <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-mono text-[11px] tracking-wide text-slate">
            FORGET
          </h2>
          {untrusted ? (
            <>
              <p className="text-[13px] leading-snug text-ink">
                {entityLabel(untrusted.entity)} entered from outside and was
                never verified. Forgetting it removes its whole derivation
                closure and issues a certificate.
              </p>
              {preview.data ? (
                <ForgetImpact
                  preview={preview.data}
                  pending={forget.isPending}
                  onConfirm={() => forget.mutate(preview.data)}
                  onCancel={() => preview.reset()}
                />
              ) : (
                <Button
                  variant="danger"
                  disabled={preview.isPending || untrusted.tombstoned}
                  onClick={() => preview.mutate(untrusted.entity ?? "")}
                >
                  {untrusted.tombstoned
                    ? "Already forgotten"
                    : preview.isPending
                      ? "Checking what it would destroy…"
                      : "Forget source…"}
                </Button>
              )}
              {preview.isError && (
                <p className="font-mono text-[11.5px] leading-snug text-risk">
                  {preview.error instanceof Error
                    ? preview.error.message
                    : "could not read the closure"}
                </p>
              )}
            </>
          ) : (
            <p className="text-[13px] leading-snug text-slate">
              {forget.data
                ? "The untrusted source is gone, so there is nothing left here to forget."
                : "No untrusted source in this chain, so there is nothing here that a forget should start from."}
            </p>
          )}
          {forget.isError && (
            <p className="font-mono text-[11.5px] leading-snug text-risk">
              {stale
                ? "Nothing was destroyed. The memory changed while you were reading, so that closure is out of date — check it again."
                : forget.error instanceof Error
                  ? forget.error.message
                  : "forget failed"}
            </p>
          )}
        </section>
        {forget.data && <Certificate cert={forget.data} />}
      </aside>
    </div>
  );
}
