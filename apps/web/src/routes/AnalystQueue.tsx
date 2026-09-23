import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, OriginNote, Select, SeverityBadge, StatusBadge, TextArea, TextInput } from "../components/WorkflowUI";
import { api, type CreateCaseInput, type WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";

function CreateCaseForm() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (input: CreateCaseInput) => api.createCase(input),
    onSuccess: (created) => {
      setSuccess(`Case ${created.id} created.`);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void navigate({ to: "/analyst/cases/$caseId", params: { caseId: created.id } });
    },
  });

  return (
    <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <h2 className="font-serif text-lg font-semibold text-ink">Create a case</h2>
      <p className="mt-1 text-sm leading-relaxed text-slate">Anchor each case to a finding in a recorded run. The API verifies that evidence before creating workflow state.</p>
      <form
        className="mt-5 grid gap-4 lg:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          setSuccess(null);
          const form = new FormData(event.currentTarget);
          create.mutate({
            run_id: String(form.get("run_id") ?? "").trim(),
            finding_id: String(form.get("finding_id") ?? "").trim(),
            title: String(form.get("title") ?? "").trim(),
            severity: String(form.get("severity")) as WorkflowSeverity,
            rationale: String(form.get("rationale") ?? "").trim(),
          });
        }}
      >
        <Field label="Run ID"><TextInput name="run_id" required placeholder="run_…" /></Field>
        <Field label="Finding ID"><TextInput name="finding_id" required placeholder="Exact finding sink or ID" /></Field>
        <Field label="Case title"><TextInput name="title" required maxLength={512} placeholder="Operational finding summary" /></Field>
        <Field label="Severity">
          <Select name="severity" defaultValue="high" required>
            {(["critical", "high", "medium", "low", "unknown"] as WorkflowSeverity[]).map((value) => <option key={value} value={value}>{value}</option>)}
          </Select>
        </Field>
        <div className="lg:col-span-2">
          <Field label="Rationale" hint="State why the finding requires investigation; this becomes an immutable case event.">
            <TextArea name="rationale" required maxLength={4096} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
          <Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating case…" : "Create case"}</Button>
          <span className="text-xs text-slate">Duplicate active cases for the same run and finding return the existing case.</span>
        </div>
        <div className="lg:col-span-2"><MutationMessage error={create.error} success={success} /></div>
      </form>
    </section>
  );
}

export function AnalystQueue() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api.cases() });

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst" title="Priority queue" meta={<span>server-prioritized casework</span>} />
      <Async query={cases} label="Prioritizing cases…">
        {(data) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
            <OriginNote origin={data.origin} />
            <CreateCaseForm />
            <section className="rounded-2xl border border-line bg-surface p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <h2 className="font-serif text-lg font-semibold text-ink">Active and historical cases</h2>
                  <p className="mt-1 text-sm text-slate">Priority reflects severity, state, age, assignment, and SLA status from the server.</p>
                </div>
                <span className="font-mono text-xs text-slate">{data.total} total</span>
              </div>
              {data.cases.length === 0 ? (
                <div className="mt-5 rounded-xl border border-line-2 bg-surface-2 px-4 py-5">
                  <p className="font-medium text-ink">No cases in the queue</p>
                  <p className="mt-1 text-sm text-slate">Create a case from a known run and finding when investigation is required.</p>
                </div>
              ) : (
                <div className="responsive-table-wrap mt-5">
                  <table className="w-full border-collapse text-left">
                    <thead><tr className="border-b border-line">
                      {['Priority', 'Case', 'Finding', 'Owner / SLA', 'State'].map((heading) => <th key={heading} scope="col" className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}
                    </tr></thead>
                    <tbody>
                      {[...data.cases].sort((a, b) => b.priority - a.priority).map((item) => (
                        <tr key={item.id} className="border-b border-line align-top">
                          <td className="py-3 pr-4"><span className="font-serif text-xl text-ink">{item.priority}</span><p className="max-w-44 text-[11px] leading-snug text-slate">{item.priority_reasons.join(" · ")}</p></td>
                          <td className="py-3 pr-4"><Link to="/analyst/cases/$caseId" params={{ caseId: item.id }} className="font-medium text-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">{item.title}</Link><p className="mt-1 font-mono text-[11px] text-slate">{item.id}</p></td>
                          <td className="py-3 pr-4"><div className="flex flex-wrap gap-2"><SeverityBadge severity={item.severity} /><Link to="/analyst/investigate/$runId/$findingId" params={{ runId: item.run_id, findingId: item.finding_id }} className="font-mono text-xs text-accent underline-offset-2 hover:underline">{item.finding_id}</Link></div><p className="mt-1 font-mono text-[11px] text-slate">run {item.run_id}</p></td>
                          <td className="py-3 pr-4 text-sm text-ink">{item.assignee_name ?? "Unassigned"}<p className={item.overdue ? "mt-1 text-xs text-risk" : "mt-1 text-xs text-slate"}>{item.sla_due_at ? `${item.overdue ? "Overdue" : "Due"} ${timestamp(item.sla_due_at)}` : "No SLA due time"}</p></td>
                          <td className="py-3"><StatusBadge status={item.state} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}
      </Async>
    </main>
  );
}
