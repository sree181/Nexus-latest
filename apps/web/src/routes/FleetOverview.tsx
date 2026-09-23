import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type {
  AgentHit,
  CoverageOut,
  FleetOverview as Overview,
} from "../lib/api";
import { api } from "../lib/api";
import { Async, ErrorState, Loading } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { TabBar, fleetTabs } from "../components/TabBar";

/** The query behind the agents table, stated in the UI so the table is never
 *  mistaken for something narrower or broader than it is. Every agent with
 *  memory in the fleet answers this, including runs that joined it. */
const AGENTS_QUERY = "agents with memory in the fleet";

const statusTone = (status: string): "risk" | "warn" | "ok" =>
  status === "exploitable" ? "risk" : status === "present" ? "warn" : "ok";

function Tile({
  value,
  label,
  detail,
  tone = "neutral",
}: {
  value: number;
  label: string;
  detail: string;
  tone?: "neutral" | "risk" | "warn";
}) {
  const skin =
    tone === "risk"
      ? "border-risk bg-risk-soft"
      : tone === "warn"
        ? "border-warn bg-warn-soft"
        : "border-line bg-surface";
  const ink =
    tone === "risk" ? "text-risk" : tone === "warn" ? "text-warn" : "text-ink";
  return (
    <div className={`flex flex-col gap-1 rounded-2xl border p-5 ${skin}`}>
      <p className="font-mono text-[11px] tracking-wide text-slate">{label}</p>
      <p className={`font-serif text-[36px] leading-tight ${ink}`}>
        {value}
      </p>
      <p className="text-[12.5px] leading-snug text-slate">{detail}</p>
    </div>
  );
}

function Tiles({ data }: { data: Overview }) {
  return (
    <div className="grid grid-cols-2 gap-5 2xl:grid-cols-5">
      <Tile
        value={data.agents_active}
        label="AGENTS ACTIVE"
        detail="Agents with memory in the fleet hypergraph."
      />
      <Tile
        value={data.memories_governed}
        label="MEMORIES GOVERNED"
        detail="Records that passed the write gate and can be traced."
      />
      <Tile
        value={data.exploitable_findings}
        label="EXPLOITABLE FINDINGS"
        detail="Call sites with a proven taint path, fleet-wide."
        tone={data.exploitable_findings > 0 ? "risk" : "neutral"}
      />
      {/* the number that stops a low finding count reading as good news */}
      <Tile
        value={data.unassessed_findings}
        label="NOT ASSESSED"
        detail="Call sites no analyser has checked for reachability."
        tone={data.unassessed_findings > 0 ? "warn" : "neutral"}
      />
      <Tile
        value={data.deletion_certificates}
        label="DELETION CERTIFICATES"
        detail="Forgets performed, each with a retained hash."
      />
    </div>
  );
}

/** How much of what the fleet's agents wrote has a stated reason behind it.
 *
 *  The adoption number. It is deliberately the one place on this screen that
 *  can look bad while every other tile looks fine: a fleet with no proven
 *  findings over code nobody can explain is not a governed fleet, and the
 *  counts above would never say so. */
function Coverage({ data }: { data: CoverageOut }) {
  const pct =
    data.modules === 0 ? null : Math.round((data.explained / data.modules) * 100);
  return (
    <section
      aria-label="Explained code"
      className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-serif text-[17px] text-ink">Explained code</h2>
          <p className="mt-0.5 text-[12.5px] leading-snug text-slate">
            Files an agent wrote after stating why, as a share of everything the
            recorder saw it write.
          </p>
        </div>
        <span className="font-mono text-[11.5px] text-slate">
          {data.explained} of {data.modules} files
        </span>
      </div>

      {data.modules === 0 ? (
        /* No external agent has recorded here. Saying so beats showing 100%,
           which is what an empty ratio would round to. */
        <p className="rounded-xl border border-line-2 bg-wash px-4 py-3 text-[13px] leading-relaxed text-ink">
          No agent outside MeshAgent has recorded anything yet, so there is no
          adoption to measure.{" "}
          {data.self_recorded > 0 && (
            <>
              The {data.self_recorded} file
              {data.self_recorded === 1 ? "" : "s"} MeshAgent’s own loop wrote
              are not counted here: it is made to state a decision before it
              writes, so it would always read as perfect.
            </>
          )}
        </p>
      ) : (
        <>
          <div className="flex items-baseline gap-3">
            <span className="font-serif text-[36px] leading-tight text-ink">
              {pct}%
            </span>
            <Badge tone={pct !== null && pct >= 80 ? "ok" : "warn"}>
              {pct !== null && pct >= 80 ? "explained" : "mostly unexplained"}
            </Badge>
          </div>
          <div className="responsive-table-wrap">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                {["Developer", "Agents", "Files", "Rationale"].map((h) => (
                  <th
                    key={h}
                    scope="col"
                    className="pb-2 font-mono text-[10.5px] font-normal tracking-wide text-slate"
                  >
                    {h.toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.by_developer.map((row) => (
                <tr key={row.owner ?? "unowned"} className="border-b border-line">
                  <td className="py-2.5 text-[13px] text-ink">
                    <span className="flex flex-wrap items-center gap-2">
                      {row.owner_name ?? row.owner ?? "unattributed"}
                      {/* this table names people against their gaps, so a
                          name nothing proved has to say so rather than be
                          read as something the office can act on */}
                      {!row.attributed && (
                        <Badge tone="warn">name asserted</Badge>
                      )}
                    </span>
                  </td>
                  <td className="py-2.5 font-mono text-[12px] text-slate">
                    {row.agents.join(", ") || "—"}
                  </td>
                  <td className="py-2.5 font-mono text-[12.5px] text-ink">
                    {row.modules}
                  </td>
                  <td className="py-2.5">
                    {row.unexplained === 0 ? (
                      <Badge tone="ok">all stated</Badge>
                    ) : (
                      <Badge tone="warn">{row.unexplained} missing</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </section>
  );
}

function AgentsTable({ hits }: { hits: AgentHit[] }) {
  const exploitable = hits.filter((h) => h.status === "exploitable").length;
  return (
    <section
      aria-label="Agents"
      className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-serif text-[17px] text-ink">Agents</h2>
          <p className="mt-0.5 font-mono text-[11.5px] text-slate">
            matching “{AGENTS_QUERY}”
          </p>
        </div>
        <span className="font-mono text-[11.5px] text-slate">
          {hits.length} agents · {exploitable} exploitable
        </span>
      </div>
      {hits.length === 0 ? (
        <p className="font-mono text-[12px] text-slate">
          no agent in the fleet matches that query
        </p>
      ) : (
        <div className="responsive-table-wrap">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              {["Agent", "Status"].map((h) => (
                <th
                  key={h}
                  scope="col"
                  className="pb-2 font-mono text-[10.5px] font-normal tracking-wide text-slate"
                >
                  {h.toUpperCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {hits.map((hit) => (
              <tr key={hit.agent} className="border-b border-line">
                <td className="py-2.5 font-mono text-[12.5px] text-ink">
                  {hit.agent}
                  {/* the engine names some agents only by their id */}
                  {hit.name !== hit.agent && (
                    <span className="text-slate"> · {hit.name}</span>
                  )}
                </td>
                <td className="py-2.5">
                  <Badge tone={statusTone(hit.status)}>{hit.status}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}

export function FleetOverview() {
  const overview = useQuery({
    queryKey: ["fleetOverview"],
    queryFn: api.fleetOverview,
  });
  const agents = useQuery({
    queryKey: ["fleetQuery", AGENTS_QUERY],
    queryFn: () => api.fleetQuery(AGENTS_QUERY),
  });
  const coverage = useQuery({
    queryKey: ["fleetCoverage"],
    queryFn: api.fleetCoverage,
  });

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader section="Fleet" title="Overview" />
      <TabBar label="Fleet views" tabs={fleetTabs()} />
      <Async query={overview} label="counting the fleet…">
        {(data) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-6">
            <Tiles data={data} />
            {coverage.isPending ? (
              <Loading label="counting explained code…" />
            ) : coverage.isError ? (
              <ErrorState
                error={coverage.error}
                retry={() => void coverage.refetch()}
              />
            ) : (
              <Coverage data={coverage.data} />
            )}
            {agents.isPending ? (
              <Loading label="querying the fleet…" />
            ) : agents.isError ? (
              <ErrorState
                error={agents.error}
                retry={() => void agents.refetch()}
              />
            ) : (
              <AgentsTable hits={agents.data.hits} />
            )}
          </div>
        )}
      </Async>
    </main>
  );
}
