import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { Severity } from "@meshagent/graph";
import type { Finding, FindingsOut, Reachability, ScanOut } from "../lib/api";
import { ApiError, api } from "../lib/api";
import { Async } from "../components/Async";
import { timestamp } from "../lib/format";

const severityTone: Record<Severity, "risk" | "warn" | "neutral"> = {
  critical: "risk",
  high: "risk",
  medium: "warn",
  low: "neutral",
  unknown: "neutral",
};

/** An analyser's name, said the way a person would say it. */
function analyser(name: string | null): string {
  if (!name) return "nobody";
  return name === "builtin" ? "MeshAgent's own walk" : name;
}

function Tile({
  value,
  label,
  detail,
  tone,
}: {
  value: number;
  label: string;
  detail: string;
  tone: "neutral" | "risk" | "warn";
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
    <div className={`flex flex-col rounded-2xl border p-5 ${skin}`}>
      <p className="font-mono text-[11px] tracking-wide text-slate">{label}</p>
      <p className={`font-serif text-[40px] leading-tight ${ink}`}>{value}</p>
      <p className="text-[13px] leading-snug text-slate">{detail}</p>
    </div>
  );
}

function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li className="flex flex-col gap-1.5 rounded-xl border border-line px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-ink">{finding.sink}</span>
        {finding.cwe && <Badge tone="neutral">{finding.cwe}</Badge>}
        {finding.severity && (
          <Badge tone={severityTone[finding.severity]}>
            {finding.severity}
          </Badge>
        )}
        {/* who made the call is part of the call */}
        {finding.asserted_by && (
          <Badge tone="neutral">per {analyser(finding.asserted_by)}</Badge>
        )}
        {finding.disputed_by && (
          <Badge tone="warn">{finding.disputed_by} did not agree</Badge>
        )}
      </div>
      <p className="text-[12.5px] leading-snug text-slate">
        {[
          finding.cwe_title,
          finding.owner ? `called in ${finding.owner}` : null,
        ]
          .filter(Boolean)
          .join(" · ") ||
          "Nothing is recorded about this call beyond the call itself."}
      </p>
      {finding.disputed_by && (
        <p className="text-[12.5px] leading-snug text-warn">
          {finding.disputed_by} read this module and did not flag this call.
          That is not a refutation — its rules may not cover this sink — but
          the two analysers do not agree.
        </p>
      )}
    </li>
  );
}

function TaintPath({ finding }: { finding: Finding }) {
  return (
    <section
      aria-label={`Taint path to ${finding.sink}`}
      className="flex flex-col gap-3 rounded-2xl border border-risk bg-surface p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-serif text-[17px] text-ink">
          Proven path to {finding.sink}
        </h3>
        <div className="flex items-center gap-2">
          {finding.severity && (
            <Badge tone={severityTone[finding.severity]}>
              {finding.severity}
            </Badge>
          )}
          {finding.rule && <Badge tone="neutral">{finding.rule}</Badge>}
        </div>
      </div>
      <ol className="flex flex-col">
        {finding.path.map((hop, i) => (
          <li key={`${hop}-${i}`} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                aria-hidden="true"
                className={`mt-2 h-2.5 w-2.5 shrink-0 rounded-full ${
                  i === finding.path.length - 1 ? "bg-risk" : "bg-accent"
                }`}
              />
              {i < finding.path.length - 1 && (
                <span aria-hidden="true" className="w-px flex-1 bg-line-2" />
              )}
            </div>
            <div className="pb-3">
              <p className="font-mono text-[10.5px] tracking-wide text-slate">
                {i === 0
                  ? "UNTRUSTED ENTRY"
                  : i === finding.path.length - 1
                    ? "SINK"
                    : "FLOWS THROUGH"}
              </p>
              <p className="font-mono text-[13px] text-ink">{hop}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="border-t border-line pt-3 text-[12.5px] leading-snug text-slate">
        {analyser(finding.asserted_by)} traced this path, and it is why the
        call is called reachable. Without it the call would still be present,
        and nothing about reachability would be claimed.
      </p>
    </section>
  );
}

function Column({
  title,
  detail,
  findings,
  tone,
}: {
  title: string;
  detail: string;
  findings: Finding[];
  tone: "risk" | "warn" | "line";
}) {
  const edge =
    tone === "risk"
      ? "border-risk"
      : tone === "warn"
        ? "border-warn"
        : "border-line";
  return (
    <section
      aria-label={title}
      className={`flex flex-1 flex-col gap-3 rounded-2xl border ${edge} bg-surface p-5`}
    >
      <div>
        <h2 className="font-serif text-[17px] text-ink">
          {title} · {findings.length}
        </h2>
        <p className="mt-1 text-[12.5px] leading-snug text-slate">{detail}</p>
      </div>
      {findings.length === 0 ? (
        <p className="font-mono text-[12px] text-slate">none</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {findings.map((f) => (
            <FindingRow key={`${f.sink}-${f.owner ?? ""}`} finding={f} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Hands the run a scanner's own SARIF output.
 *
 *  This is the only place a developer can replace MeshAgent's guess about
 *  reachability with somebody's evidence, and the screen has told them to do
 *  it since before there was a control for it. */
function UploadScan({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [problem, setProblem] = useState("");
  const [read, setRead] = useState<ScanOut | null>(null);

  const ingest = useMutation({
    mutationFn: async (file: File) => {
      let sarif: unknown;
      try {
        sarif = JSON.parse(await file.text());
      } catch {
        // thrown rather than posted: a document the API cannot parse either
        // would come back as a 422 that says nothing about which file it was
        throw new Error(
          `${file.name} is not valid JSON, so nothing was sent. A SARIF run is a JSON document — check you picked the report rather than a log of it.`,
        );
      }
      return api.ingestScan(runId, sarif);
    },
    onSuccess: (got) => {
      setProblem("");
      setRead(got);
      void queryClient.invalidateQueries({
        queryKey: ["run", runId, "findings"],
      });
    },
    onError: (e: unknown) => {
      setRead(null);
      setProblem(
        e instanceof ApiError && e.status === 404
          ? `Nothing here can take a scan: this run holds no recorded memory, or it is not one of yours. The API said: ${e.message}`
          : e instanceof Error
            ? e.message
            : String(e),
      );
    },
  });

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      <label
        htmlFor="sarif"
        className="font-mono text-[10.5px] tracking-wide text-slate"
      >
        UPLOAD A SARIF RUN
      </label>
      <input
        id="sarif"
        type="file"
        accept=".sarif,.json"
        disabled={ingest.isPending}
        onChange={(e) => {
          const file = e.target.files?.[0];
          // cleared so picking the same file again is still a change event:
          // re-running a scanner and re-uploading is the normal case
          e.target.value = "";
          if (file) ingest.mutate(file);
        }}
        className="text-[13px] text-ink file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-[12.5px] file:text-ink"
      />
      <p className="text-[12.5px] leading-snug text-slate">
        Semgrep&rsquo;s or CodeQL&rsquo;s own output. Its traced flows become
        this run&rsquo;s reachability evidence, and the modules it merely read
        are what licenses calling an unflagged call site assessed rather than
        unexamined.
      </p>

      <p role="status" className="text-[12.5px] leading-snug text-ink">
        {ingest.isPending ? "Reading the document…" : ""}
      </p>

      {problem && (
        <p
          role="alert"
          className="rounded-xl border border-risk bg-risk-soft px-4 py-3 text-[12.5px] leading-snug text-ink"
        >
          {problem}
        </p>
      )}

      {read && (
        <div className="flex flex-col gap-1.5 rounded-xl border border-line-2 bg-surface-2 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={read.sample ? "warn" : "ok"}>
              {read.sample ? "read, not recorded" : "recorded"}
            </Badge>
            <span className="text-[13px] text-ink">
              {read.tool} named {read.modules.length}{" "}
              {read.modules.length === 1 ? "module" : "modules"} and traced{" "}
              {read.reachable} of {read.results}{" "}
              {read.results === 1 ? "result" : "results"}
            </span>
          </div>
          {read.sample && (
            <p className="text-[12.5px] leading-snug text-ink">
              This deployment has no engine to record into, so the document was
              parsed and counted and then discarded. The findings below are
              unchanged by it.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Scans({ data, runId }: { data: FindingsOut; runId: string }) {
  const unread = data.scans.length === 0;
  return (
    <section
      aria-label="Scanner coverage"
      className={`flex flex-col gap-2 rounded-2xl border p-5 ${
        unread ? "border-warn bg-warn-soft" : "border-line bg-surface"
      }`}
    >
      {unread ? (
        <>
          <h2 className="font-serif text-[17px] text-ink">
            No external scanner has read this code
          </h2>
          <p className="text-[13px] leading-snug text-ink">
            Reachability here rests only on MeshAgent&rsquo;s own AST walk,
            which is intraprocedural and Python-only. Everything it did not
            flag is unassessed rather than safe. Upload a Semgrep or CodeQL
            SARIF run against this run to replace that guess with evidence.
          </p>
        </>
      ) : (
        <>
          <h2 className="font-serif text-[17px] text-ink">Scanner coverage</h2>
          <ul className="flex flex-col gap-1.5">
            {data.scans.map((s) => (
              <li key={`${s.tool}-${s.at}`} className="flex flex-wrap gap-2">
                <Badge tone="ok">{s.tool}</Badge>
                <span className="text-[13px] text-ink">
                  read {s.modules.length}{" "}
                  {s.modules.length === 1 ? "module" : "modules"} (
                  {s.modules.join(", ") || "none named"}), traced {s.reachable}{" "}
                  of {s.results} {s.results === 1 ? "result" : "results"}
                </span>
                <span className="ml-auto font-mono text-[11.5px] text-slate">
                  {timestamp(s.at)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <UploadScan runId={runId} />
    </section>
  );
}

function by(state: Reachability) {
  return (f: Finding) => f.reachability === state;
}

function Security({ data, runId }: { data: FindingsOut; runId: string }) {
  const reachable = data.findings.filter(by("reachable"));
  const unassessed = data.findings.filter(by("not-assessed"));
  const cleared = data.findings.filter(by("not-reachable"));

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-auto p-6">
      <p className="font-mono text-[13px] text-slate">
        present {data.present} / reachable {data.reachable} / not assessed{" "}
        {data.not_assessed}
      </p>

      <div className="grid grid-cols-2 gap-5 2xl:grid-cols-4">
        <Tile
          value={data.present}
          label="PRESENT"
          detail="Dangerous call sites found in this run's code."
          tone="neutral"
        />
        <Tile
          value={data.reachable}
          label="REACHABLE"
          detail="An analyser traced untrusted input all the way to the call."
          tone="risk"
        />
        {/* the number a boolean used to hide inside "not exploitable" */}
        <Tile
          value={data.not_assessed}
          label="NOT ASSESSED"
          detail="Nobody has checked whether input reaches these. Not the same as safe."
          tone={data.not_assessed > 0 ? "warn" : "neutral"}
        />
        <Tile
          value={data.not_reachable}
          label="NOT REACHABLE"
          detail="A scanner read the module and found no path to these."
          tone="neutral"
        />
      </div>

      <Scans data={data} runId={runId} />

      <div className="grid gap-5 xl:grid-cols-3">
        <Column
          title="Reachable, with evidence"
          detail="An analyser traced untrusted input from an entry point all the way to the call. That evidence is the whole difference."
          findings={reachable}
          tone="risk"
        />
        <Column
          title="Not assessed"
          detail="Real call sites that no analyser has examined for reachability. They carry no severity, because nothing has been established either way."
          findings={unassessed}
          tone="warn"
        />
        <Column
          title="Checked, no path found"
          detail="A scanner read the module these sit in and traced no route to them. This is the only group that has actually been cleared."
          findings={cleared}
          tone="line"
        />
      </div>

      {reachable.map((f) => (
        <TaintPath key={`${f.sink}-${f.owner ?? ""}`} finding={f} />
      ))}
    </div>
  );
}

export function RunSecurity() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const findings = useQuery({
    queryKey: ["run", runId, "findings"],
    queryFn: () => api.runFindings(runId),
  });

  // no findings has two causes, and they are not the same news: code that was
  // read and is clean, or no code to read at all
  const scanned = findings.data?.scanned ?? 0;

  return (
    <Async
      query={findings}
      label="reading the run's findings…"
      isEmpty={(d) => d.findings.length === 0}
      emptyTitle={scanned === 0 ? "No code recorded" : "No dangerous call sites"}
      emptyDetail={
        scanned === 0
          ? "This run has not written any code for the scanner to look at."
          : `The scanner read ${scanned} class${scanned === 1 ? "" : "es"} in this run's code and found no dangerous calls. Nothing is claimed about reachability, because there is nothing here to reach.`
      }
    >
      {(d) => <Security data={d} runId={runId} />}
    </Async>
  );
}
