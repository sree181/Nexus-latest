import { useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { Severity } from "@meshagent/graph";
import type { Capability, CveImpact, SbomOut } from "../lib/api";
import { api } from "../lib/api";
import { Async, EmptyState, ErrorState, Loading } from "../components/Async";
import { entityLabel } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

const severityTone: Record<Severity, "risk" | "warn" | "neutral"> = {
  critical: "risk",
  high: "risk",
  medium: "warn",
  low: "neutral",
  unknown: "neutral",
};

export function canReadFleetImpact(
  capabilities: readonly Capability[],
  cve: string | undefined,
): boolean {
  return Boolean(cve) && capabilities.includes("fleet.read");
}

function RestrictedImpact({ cve }: { cve: string }) {
  return (
    <section className="rounded-2xl border border-warn bg-warn-soft p-5">
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="font-serif text-[17px] text-ink">
          Advisory recorded for this run
        </h2>
        <Badge tone="warn">{cve}</Badge>
      </div>
      <p className="mt-2 text-[13.5px] leading-snug text-ink">
        The software bill of materials below remains available to the run owner.
        Organization-wide impact crosses other teams and agents, so that view is
        limited to Security Analyst and CISO roles.
      </p>
    </section>
  );
}

/** The blast radius the way the hypergraph holds it: a vulnerable version
 *  reaches a package, the package reaches classes, the classes reach the
 *  decisions that chose them. */
function Tiers({ impact }: { impact: CveImpact }) {
  const tiers = [
    {
      label: "VERSION",
      items: impact.versions,
      note: "the vulnerable release",
    },
    {
      label: "PACKAGE",
      items: impact.packages,
      note: "every agent inherits it from here",
    },
    {
      label: "CLASSES",
      items: impact.classes,
      note: "code that imports the package",
    },
    {
      label: "DECISIONS",
      items: impact.decisions,
      note: "choices made because of that code",
    },
  ];

  return (
    <section
      aria-label="Blast radius"
      className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-[17px] text-ink">Blast radius</h2>
        <span className="font-mono text-[11px] text-slate">
          {impact.agents.length} agents affected
        </span>
      </div>
      <ol className="flex flex-col">
        {tiers.map((tier, i) => (
          <li key={tier.label} className="flex gap-4">
            <div className="flex flex-col items-center">
              <span
                aria-hidden="true"
                className="mt-2.5 h-2.5 w-2.5 shrink-0 rounded-full bg-accent"
              />
              {i < tiers.length - 1 && (
                <span aria-hidden="true" className="w-px flex-1 bg-line-2" />
              )}
            </div>
            <div className="flex-1 pb-4">
              <div className="flex items-baseline gap-2">
                <p className="font-mono text-[11px] tracking-wide text-slate">
                  {tier.label}
                </p>
                <span className="font-mono text-[11px] text-slate">
                  {tier.items.length}
                </span>
              </div>
              {tier.items.length === 0 ? (
                <p className="font-mono text-[12px] text-slate">
                  none recorded
                </p>
              ) : (
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {tier.items.map((item) => (
                    <li
                      key={item}
                      className="rounded border border-line-2 bg-surface-2 px-2 py-0.5 font-mono text-[11.5px] text-ink"
                    >
                      {entityLabel(item)}
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-1 text-[12px] text-slate">{tier.note}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function CveHeader({ impact }: { impact: CveImpact }) {
  return (
    <section
      aria-label="Vulnerability"
      className="flex flex-col gap-2 rounded-2xl border border-risk bg-risk-soft p-5"
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="font-mono text-[17px] text-ink">{impact.cve}</h2>
        <Badge
          tone={impact.severity ? severityTone[impact.severity] : "neutral"}
        >
          {impact.severity ?? "severity not recorded"}
        </Badge>
        {impact.feed && <Badge tone="neutral">{impact.feed}</Badge>}
      </div>
      <p className="text-[13.5px] leading-snug text-ink">
        {impact.summary ?? "The advisory carries no summary in this feed."}
      </p>
      <p className="font-mono text-[11.5px] text-slate">
        {/* a version entity already names its package, so one of the two is
            enough to identify what is affected */}
        {(impact.versions.length > 0 ? impact.versions : impact.packages)
          .map(entityLabel)
          .join(", ")}{" "}
        · {impact.agents.length} agents inherit it
      </p>
    </section>
  );
}

function SbomTable({ sbom }: { sbom: SbomOut }) {
  return (
    <section
      aria-label="Software bill of materials"
      className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5"
    >
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-[17px] text-ink">
          Software bill of materials
        </h2>
        <span className="font-mono text-[11px] text-slate">
          {sbom.entries.length} package{sbom.entries.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="responsive-table-wrap">
        <table className="w-full min-w-[680px] border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              {["Package", "Version", "License", "Advisories", "Feed"].map(
                (h) => (
                  <th
                    key={h}
                    scope="col"
                    className="pb-2 font-mono text-[10.5px] font-normal tracking-wide text-slate"
                  >
                    {h.toUpperCase()}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {sbom.entries.map((entry) => (
              <tr
                key={`${entry.package}@${entry.version}`}
                className="border-b border-line"
              >
                <td className="py-2.5 font-mono text-[12.5px] text-ink">
                  {entityLabel(entry.package)}
                </td>
                <td className="py-2.5 font-mono text-[12.5px] text-ink">
                  {entityLabel(entry.version)}
                </td>
                <td className="py-2.5 font-mono text-[12.5px] text-slate">
                  {entry.license}
                </td>
                <td className="py-2.5">
                  {entry.cves.length === 0 ? (
                    <span className="font-mono text-[12px] text-slate">
                      none
                    </span>
                  ) : (
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[12.5px] text-ink">
                        {entry.cves.join(", ")}
                      </span>
                      {entry.severity && (
                        <Badge tone={severityTone[entry.severity]}>
                          {entry.severity}
                        </Badge>
                      )}
                    </span>
                  )}
                </td>
                <td className="py-2.5 font-mono text-[12px] text-slate">
                  {entry.feed ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RunSupply() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const identity = useIdentity();

  const sbom = useQuery({
    queryKey: ["run", runId, "sbom"],
    queryFn: () => api.runSbom(runId),
  });

  // the CVE in the header is whichever advisory the SBOM actually carries,
  // not a fixed one
  const cve = sbom.data?.entries.flatMap((e) => e.cves)[0];
  const canViewFleetImpact = canReadFleetImpact(
    identity.me?.capabilities ?? [],
    cve,
  );
  const impact = useQuery({
    queryKey: ["cve", cve],
    queryFn: () => api.cveImpact(cve as string),
    enabled: canViewFleetImpact,
  });

  return (
    <Async
      query={sbom}
      label="reading the bill of materials…"
      isEmpty={(d) => d.entries.length === 0}
      emptyTitle="No dependencies recorded"
      emptyDetail="This run has not written a package into memory yet."
    >
      {(d) => (
        <div className="flex flex-1 flex-col gap-5 overflow-auto p-6">
          {!cve ? (
            <EmptyState
              title="No advisories on these packages"
              detail="Every package in this run's memory is free of recorded advisories, so there is no blast radius to draw."
            />
          ) : !canViewFleetImpact ? (
            <RestrictedImpact cve={cve} />
          ) : impact.isPending ? (
            <Loading label="resolving the blast radius…" />
          ) : impact.isError ? (
            <ErrorState
              error={impact.error}
              retry={() => void impact.refetch()}
            />
          ) : (
            <>
              <CveHeader impact={impact.data} />
              <Tiers impact={impact.data} />
            </>
          )}
          <SbomTable sbom={d} />
        </div>
      )}
    </Async>
  );
}
