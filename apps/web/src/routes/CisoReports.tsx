import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, StatusBadge, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api, type CreateReportInput, type GovernanceReport } from "../lib/api";
import { timestamp } from "../lib/format";

function ReportDetail({ report }: { report: GovernanceReport }) {
  const detail = useQuery({ queryKey: ["report", report.id], queryFn: () => api.report(report.id) });
  if (detail.isPending) return <Loading label="Loading report manifest…" />;
  if (detail.isError) return <ErrorState error={detail.error} retry={() => void detail.refetch()} />;
  const manifest = detail.data.manifest;
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><StatusBadge status={detail.data.status} /><span className="rounded border border-ok bg-ok-soft px-2 py-1 font-mono text-[11px] text-ok">live data only</span></div><h2 className="mt-3 font-serif text-xl font-semibold text-ink">{detail.data.title}</h2><p className="mt-1 font-mono text-xs text-slate">{timestamp(detail.data.period_start)} — {timestamp(detail.data.period_end)}</p></div><button type="button" onClick={() => navigator.clipboard?.writeText(detail.data.digest)} className="rounded-lg border border-line-2 px-3 py-2 text-xs text-ink hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">Copy digest</button></div>
      <p className="mt-4 break-all rounded-lg bg-surface-2 px-3 py-2 font-mono text-[11px] text-slate">SHA-256 {detail.data.digest}</p>
      {manifest ? <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><div><p className="font-mono text-[10.5px] tracking-widest text-slate">AUTHENTICATED RUNS</p><p className="mt-1 font-serif text-2xl text-ink">{manifest.coverage.authenticated_runs}</p></div><div><p className="font-mono text-[10.5px] tracking-widest text-slate">PRESENT FINDINGS</p><p className="mt-1 font-serif text-2xl text-ink">{manifest.findings.present}</p></div><div><p className="font-mono text-[10.5px] tracking-widest text-slate">REACHABLE</p><p className="mt-1 font-serif text-2xl text-risk">{manifest.findings.reachable}</p></div><div><p className="font-mono text-[10.5px] tracking-widest text-slate">NOT ASSESSED</p><p className="mt-1 font-serif text-2xl text-warn">{manifest.findings.not_assessed}</p></div><p className="text-xs leading-relaxed text-slate sm:col-span-2 xl:col-span-4"><strong>Coverage basis:</strong> {manifest.coverage.basis}. Sample and seeded runs are excluded. Run IDs: {manifest.run_ids.join(", ") || "none"}.</p></div> : <p className="mt-4 text-sm text-slate">The list response contains metadata only; select a report to load its manifest.</p>}
    </section>
  );
}

export function CisoReports() {
  const reports = useQuery({ queryKey: ["reports"], queryFn: api.reports });
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({ mutationFn: (input: CreateReportInput) => api.createReport(input), onSuccess: (created) => { setSuccess(`Report ${created.id} generated.`); setSelected(created.id); void client.invalidateQueries({ queryKey: ["reports"] }); } });
  const now = Math.floor(Date.now() / 1000);
  const selectedReport = reports.data?.find((report) => report.id === selected) ?? null;
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO" title="Reports" meta={<span>authenticated live evidence only</span>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Generate governance report</h2>
          <p className="mt-1 text-sm leading-relaxed text-slate">Reports exclude sample and seeded runs. Generation fails rather than producing a document with no authenticated live runs in the period.</p>
          <form className="mt-4 grid gap-4 lg:grid-cols-3" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); create.mutate({ title: String(form.get("title") ?? "").trim(), period_start: dateInputToEpoch(form.get("period_start")), period_end: dateInputToEpoch(form.get("period_end")) + 86399 }); }}>
            <Field label="Report title"><TextInput name="title" required maxLength={256} defaultValue="Executive posture report" /></Field>
            <Field label="Period start"><TextInput name="period_start" type="date" required defaultValue={epochToDateInput(now - 30 * 86400)} /></Field>
            <Field label="Period end"><TextInput name="period_end" type="date" required defaultValue={epochToDateInput(now)} /></Field>
            <div className="lg:col-span-3"><Button type="submit" disabled={create.isPending}>{create.isPending ? "Generating report…" : "Generate report"}</Button></div>
            <div className="lg:col-span-3"><MutationMessage error={create.error} success={success} /></div>
          </form>
        </section>
        <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
          <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Generated reports</h2>{reports.isPending ? <Loading label="Loading reports…" /> : reports.isError ? <ErrorState error={reports.error} retry={() => void reports.refetch()} /> : reports.data.length === 0 ? <p className="mt-4 text-sm text-slate">No reports have been generated.</p> : <ul className="mt-4 space-y-2">{reports.data.map((report) => <li key={report.id}><button type="button" aria-pressed={selected === report.id} onClick={() => setSelected(report.id)} className={`w-full rounded-xl border px-4 py-3 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${selected === report.id ? "border-accent bg-accent-soft" : "border-line-2 hover:bg-surface-2"}`}><span className="font-medium text-ink">{report.title}</span><span className="mt-2 flex items-center justify-between gap-2"><StatusBadge status={report.status} /><span className="font-mono text-[11px] text-slate">{timestamp(report.created_at)}</span></span></button></li>)}</ul>}</section>
          {selectedReport ? <ReportDetail report={selectedReport} /> : <section className="grid min-h-56 place-items-center rounded-2xl border border-dashed border-line-2 bg-surface p-6 text-center text-sm text-slate">Select a report to inspect its signed manifest, coverage, and digest.</section>}
        </div>
      </div>
    </main>
  );
}
