import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, StatusBadge, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api, type CreateRemediationInput } from "../lib/api";
import { timestamp } from "../lib/format";

export function CisoRemediation() {
  const remediation = useQuery({ queryKey: ["remediations"], queryFn: api.remediations });
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api.cases() });
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({ mutationFn: (input: CreateRemediationInput) => api.createRemediation(input), onSuccess: (created) => { setSuccess(`Remediation ${created.id} accepted.`); void client.invalidateQueries({ queryKey: ["remediations"] }); void client.invalidateQueries({ queryKey: ["governanceOverview"] }); } });
  const nextWeek = Math.floor(Date.now() / 1000) + 7 * 86400;

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO" title="Remediation" meta={<span>accepted control-plane work</span>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Create remediation</h2>
          <p className="mt-1 text-sm text-slate">Remediation work must reference an existing case and have an accountable owner and future due date.</p>
          {cases.isPending ? <Loading label="Loading cases…" /> : cases.isError ? <ErrorState error={cases.error} retry={() => void cases.refetch()} /> : cases.data.cases.length === 0 ? <p className="mt-4 rounded-xl border border-line-2 bg-surface-2 px-4 py-4 text-sm text-slate">No case exists to remediate. Analysts create cases from recorded findings.</p> : (
            <form className="mt-4 grid gap-4 lg:grid-cols-2" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); create.mutate({ case_id: String(form.get("case_id")), title: String(form.get("title") ?? "").trim(), owner: String(form.get("owner") ?? "").trim(), due_at: dateInputToEpoch(form.get("due_at")), target_revision: String(form.get("target_revision") ?? "").trim() || null }); }}>
              <Field label="Case"><select name="case_id" required className="w-full rounded-lg border border-line-2 bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft">{cases.data.cases.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.id}</option>)}</select></Field>
              <Field label="Work title"><TextInput name="title" required maxLength={512} /></Field>
              <Field label="Owner"><TextInput name="owner" required maxLength={256} placeholder="team or accountable person" /></Field>
              <Field label="Due date"><TextInput name="due_at" type="date" required min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} defaultValue={epochToDateInput(nextWeek)} /></Field>
              <div className="lg:col-span-2"><Field label="Target revision" hint="Optional commit, release, image, or deployment identifier."><TextInput name="target_revision" maxLength={256} /></Field></div>
              <div className="lg:col-span-2"><Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating remediation…" : "Create remediation"}</Button></div>
              <div className="lg:col-span-2"><MutationMessage error={create.error} success={success} /></div>
            </form>
          )}
        </section>

        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Remediation register</h2>
          {remediation.isPending ? <Loading label="Loading remediation work…" /> : remediation.isError ? <ErrorState error={remediation.error} retry={() => void remediation.refetch()} /> : remediation.data.length === 0 ? <p className="mt-4 text-sm text-slate">No remediation work has been created.</p> : (
            <div className="responsive-table-wrap mt-4"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line">{["Work", "Case", "Owner", "Target", "Due", "Status"].map((heading) => <th key={heading} className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}</tr></thead><tbody>{remediation.data.map((item) => <tr key={item.id} className="border-b border-line align-top"><td className="py-3 pr-4"><p className="font-medium text-ink">{item.title}</p><p className="font-mono text-[11px] text-slate">{item.id}</p></td><td className="py-3 pr-4"><Link to="/analyst/cases/$caseId" params={{ caseId: item.case_id }} className="font-mono text-xs text-accent hover:underline">{item.case_id}</Link></td><td className="py-3 pr-4 text-sm text-ink">{item.owner}</td><td className="py-3 pr-4 font-mono text-xs text-slate">{item.target_revision ?? "Not set"}</td><td className="py-3 pr-4 font-mono text-xs text-slate">{timestamp(item.due_at)}</td><td className="py-3"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div>
          )}
        </section>
      </div>
    </main>
  );
}
