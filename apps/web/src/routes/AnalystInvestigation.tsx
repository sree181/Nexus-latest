import { useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, Select, SeverityBadge, StatusBadge, TextArea, TextInput } from "../components/WorkflowUI";
import { api, type CreateCaseInput, type WorkflowSeverity } from "../lib/api";

export function AnalystInvestigation() {
  const { runId, findingId } = useParams({ from: "/analyst/investigate/$runId/$findingId" });
  const finding = useQuery({ queryKey: ["run", runId, "finding", findingId], queryFn: () => api.runFinding(runId, findingId) });
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api.cases() });
  const client = useQueryClient();
  const navigate = useNavigate();
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (input: CreateCaseInput) => api.createCase(input),
    onSuccess: (item) => {
      setSuccess(`Case ${item.id} created.`);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void navigate({ to: "/analyst/cases/$caseId", params: { caseId: item.id } });
    },
  });
  const linked = cases.data?.cases.filter((item) => item.run_id === runId && item.finding_id === findingId) ?? [];

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst / Investigation" title={findingId} meta={<span>run {runId}</span>} />
      <Async query={finding} label="Reading finding evidence…">
        {(data) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
            <section className="rounded-2xl border border-line bg-surface p-5">
              <div className="flex flex-wrap items-center gap-2">
                {data.severity ? <SeverityBadge severity={data.severity} /> : null}
                <StatusBadge status={data.reachability} />
                {data.cwe ? <span className="rounded border border-line-2 bg-surface-2 px-2 py-1 font-mono text-xs text-ink">{data.cwe}</span> : null}
              </div>
              <h1 className="mt-3 break-all font-serif text-2xl font-semibold text-ink">{data.sink}</h1>
              <p className="mt-2 text-sm leading-relaxed text-slate">{data.cwe_title ?? "No CWE title was recorded for this finding."}</p>
              <dl className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <div><dt className="font-mono text-[10.5px] tracking-widest text-slate">OWNER</dt><dd className="mt-1 text-sm text-ink">{data.owner ?? "Unknown"}</dd></div>
                <div><dt className="font-mono text-[10.5px] tracking-widest text-slate">ANALYSER</dt><dd className="mt-1 text-sm text-ink">{data.asserted_by ?? "Not recorded"}</dd></div>
                <div><dt className="font-mono text-[10.5px] tracking-widest text-slate">RULE</dt><dd className="mt-1 text-sm text-ink">{data.rule ?? "Not recorded"}</dd></div>
                <div><dt className="font-mono text-[10.5px] tracking-widest text-slate">ENTRY</dt><dd className="mt-1 break-all text-sm text-ink">{data.entry ?? "Not established"}</dd></div>
              </dl>
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,.8fr)]">
              <section className="rounded-2xl border border-line bg-surface p-5">
                <h2 className="font-serif text-lg font-semibold text-ink">Recorded path</h2>
                {data.path.length === 0 ? <p className="mt-3 rounded-xl border border-warn bg-warn-soft px-4 py-3 text-sm text-ink">No path is recorded. This finding must not be described as reachable without path evidence.</p> : <ol className="mt-4 space-y-2">{data.path.map((step, index) => <li key={`${step}-${index}`} className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft font-mono text-[11px] text-accent">{index + 1}</span><code className="break-all pt-0.5 text-xs text-ink">{step}</code></li>)}</ol>}
                {data.disputed_by ? <p className="mt-4 rounded-xl border border-warn bg-warn-soft px-4 py-3 text-sm text-ink">{data.disputed_by} did not agree with this finding. The disagreement is retained for the investigation.</p> : null}
                <div className="mt-5 flex flex-wrap gap-3 border-t border-line pt-4">
                  <Link to="/runs/$runId/security" params={{ runId }} className="rounded-lg border border-line-2 bg-white px-3 py-2 text-sm text-ink hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">Open run security evidence</Link>
                  <Link to="/runs/$runId/memory" params={{ runId }} className="rounded-lg border border-line-2 bg-white px-3 py-2 text-sm text-ink hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">Open provenance graph</Link>
                  <Link to="/runs/$runId/code" params={{ runId }} className="rounded-lg border border-line-2 bg-white px-3 py-2 text-sm text-ink hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">Open recorded code</Link>
                </div>
              </section>

              <section className="rounded-2xl border border-line bg-surface p-5">
                <h2 className="font-serif text-lg font-semibold text-ink">Case linkage</h2>
                {cases.isError ? <MutationMessage error={cases.error} /> : linked.length > 0 ? <ul className="mt-4 space-y-2">{linked.map((item) => <li key={item.id}><Link to="/analyst/cases/$caseId" params={{ caseId: item.id }} className="block rounded-xl border border-line-2 px-4 py-3 hover:bg-surface-2"><span className="font-medium text-accent">{item.title}</span><span className="mt-2 flex items-center gap-2"><StatusBadge status={item.state} /><span className="font-mono text-[11px] text-slate">{item.id}</span></span></Link></li>)}</ul> : (
                  <form className="mt-4 space-y-4" onSubmit={(event) => {
                    event.preventDefault();
                    setSuccess(null);
                    const form = new FormData(event.currentTarget);
                    create.mutate({ run_id: runId, finding_id: findingId, title: String(form.get("title") ?? "").trim(), severity: String(form.get("severity")) as WorkflowSeverity, rationale: String(form.get("rationale") ?? "").trim() });
                  }}>
                    <Field label="Case title"><TextInput name="title" required defaultValue={`${data.reachability === "reachable" ? "Reachable" : "Review"}: ${data.sink}`} /></Field>
                    <Field label="Severity"><Select name="severity" required defaultValue={data.severity ?? "unknown"}>{(["critical", "high", "medium", "low", "unknown"] as WorkflowSeverity[]).map((severity) => <option key={severity} value={severity}>{severity}</option>)}</Select></Field>
                    <Field label="Rationale"><TextArea name="rationale" required defaultValue={data.path.length > 0 ? `Investigate the ${data.asserted_by ?? "recorded"} path from ${data.entry ?? "the entry"} to ${data.sink}.` : `Assess whether ${data.sink} is reachable and determine disposition.`} /></Field>
                    <Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating case…" : "Create linked case"}</Button>
                    <MutationMessage error={create.error} success={success} />
                  </form>
                )}
              </section>
            </div>
          </div>
        )}
      </Async>
    </main>
  );
}
