import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, Select, SeverityBadge, StatusBadge, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api, type AssignCaseInput, type CaseRecord, type CaseTransition, type CreateExceptionInput, type TransitionCaseInput } from "../lib/api";
import { timestamp } from "../lib/format";

const transitions: Record<CaseRecord["state"], CaseTransition[]> = {
  open: ["triaged", "investigating", "closed"],
  triaged: ["investigating", "remediation", "closed"],
  investigating: ["remediation", "resolved", "closed"],
  remediation: ["resolved", "investigating", "closed"],
  resolved: ["reopened", "closed"],
  closed: ["reopened"],
};

function Assignment({ item }: { item: CaseRecord }) {
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const assign = useMutation({
    mutationFn: (input: AssignCaseInput) => api.assignCase(item.id, input),
    onSuccess: () => {
      setSuccess("Assignment updated.");
      void client.invalidateQueries({ queryKey: ["case", item.id] });
      void client.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-serif text-lg font-semibold text-ink">Assignment and SLA</h2>
      <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const due = String(form.get("sla_due_at") ?? "");
        assign.mutate({
          expected_version: item.version,
          assignee: String(form.get("assignee") ?? "").trim(),
          assignee_name: String(form.get("assignee_name") ?? "").trim(),
          sla_due_at: due ? dateInputToEpoch(due) : null,
        });
      }}>
        <Field label="Assignee identifier"><TextInput name="assignee" required defaultValue={item.assignee ?? ""} placeholder="analyst@example.com" /></Field>
        <Field label="Assignee name"><TextInput name="assignee_name" required defaultValue={item.assignee_name ?? ""} /></Field>
        <Field label="SLA due date" hint="Optional; the server computes overdue state."><TextInput name="sla_due_at" type="date" defaultValue={item.sla_due_at ? epochToDateInput(item.sla_due_at) : ""} /></Field>
        <div className="flex items-end"><Button type="submit" disabled={assign.isPending}>{assign.isPending ? "Saving…" : "Save assignment"}</Button></div>
        <div className="sm:col-span-2"><MutationMessage error={assign.error} success={success} /></div>
      </form>
    </section>
  );
}

function Transition({ item }: { item: CaseRecord }) {
  const client = useQueryClient();
  const [target, setTarget] = useState<CaseTransition>(transitions[item.state][0]);
  const [success, setSuccess] = useState<string | null>(null);
  const isResolution = target === "resolved" || target === "closed";
  const transition = useMutation({
    mutationFn: (input: TransitionCaseInput) => api.transitionCase(item.id, input),
    onSuccess: (updated) => {
      setSuccess(`Case moved to ${updated.state}.`);
      void client.invalidateQueries({ queryKey: ["case", item.id] });
      void client.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-serif text-lg font-semibold text-ink">Change case state</h2>
      <p className="mt-1 text-sm text-slate">Every transition records its rationale. Resolution and closure also require a disposition and verification evidence.</p>
      <form className="mt-4 grid gap-4" onSubmit={(event) => {
        event.preventDefault();
        setSuccess(null);
        const form = new FormData(event.currentTarget);
        const evidence = String(form.get("evidence_ids") ?? "").split(/[\n,]/).map((value) => value.trim()).filter(Boolean);
        transition.mutate({
          expected_version: item.version,
          to_state: target,
          disposition: String(form.get("disposition") ?? "").trim() || null,
          rationale: String(form.get("rationale") ?? "").trim(),
          evidence_ids: evidence,
        });
      }}>
        <Field label="Next state"><Select name="to_state" value={target} onChange={(event) => setTarget(event.target.value as CaseTransition)}>{transitions[item.state].map((state) => <option key={state} value={state}>{state}</option>)}</Select></Field>
        <Field label="Rationale"><TextArea name="rationale" required maxLength={4096} /></Field>
        <Field label={`Disposition${isResolution ? " (required)" : ""}`}><TextInput name="disposition" required={isResolution} maxLength={256} placeholder="verified remediated, accepted risk, false positive…" /></Field>
        <Field label={`Evidence IDs${isResolution ? " (required)" : ""}`} hint="Comma- or line-separated IDs for scans, revisions, receipts, or other verification records."><TextArea name="evidence_ids" required={isResolution} placeholder="scan:codeql:replacement-build" /></Field>
        <div><Button type="submit" disabled={transition.isPending}>{transition.isPending ? "Recording transition…" : `Move to ${target}`}</Button></div>
        <MutationMessage error={transition.error} success={success} />
      </form>
    </section>
  );
}

function ExceptionRequest({ item }: { item: CaseRecord }) {
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const [success, setSuccess] = useState<string | null>(null);
  const request = useMutation({
    mutationFn: (input: CreateExceptionInput) => api.requestException(input),
    onSuccess: (approval) => setSuccess(`Request ${approval.id} is waiting for a different CISO to decide.`),
  });
  const defaultExpiry = epochToDateInput(Math.floor(Date.now() / 1000) + 7 * 86400);

  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-serif text-lg font-semibold text-ink">Request a policy exception</h2>
      <p className="mt-1 text-sm leading-relaxed text-slate">Use this only when remediation cannot finish before a policy deadline. The request is time bounded and a different CISO must decide it.</p>
      {policies.isPending ? <p role="status" className="mt-4 text-sm text-slate">Loading active policies…</p> : policies.isError ? <p role="alert" className="mt-4 text-sm text-risk">Policies could not be loaded.</p> : policies.data.length === 0 ? <p className="mt-4 rounded-xl border border-line-2 bg-surface-2 px-4 py-3 text-sm text-slate">No active policy is available for an exception request.</p> : (
        <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={(event) => {
          event.preventDefault();
          setSuccess(null);
          const form = new FormData(event.currentTarget);
          request.mutate({
            policy_id: String(form.get("policy_id") ?? ""),
            scope: String(form.get("scope") ?? "").trim(),
            rationale: String(form.get("rationale") ?? "").trim(),
            compensating_controls: String(form.get("controls") ?? "").trim(),
            owner: String(form.get("owner") ?? "").trim(),
            expires_at: dateInputToEpoch(form.get("expires_at")),
          });
        }}>
          <Field label="Policy"><Select name="policy_id" required>{policies.data.map((policy) => <option key={policy.id} value={policy.id}>{policy.name}</option>)}</Select></Field>
          <Field label="Scope"><TextInput name="scope" required defaultValue={`case:${item.id}`} /></Field>
          <Field label="Owner"><TextInput name="owner" required defaultValue={item.assignee ?? ""} placeholder="service or team owner" /></Field>
          <Field label="Expires"><TextInput name="expires_at" type="date" required defaultValue={defaultExpiry} min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} /></Field>
          <div className="sm:col-span-2"><Field label="Risk rationale"><TextArea name="rationale" required maxLength={4096} /></Field></div>
          <div className="sm:col-span-2"><Field label="Compensating controls"><TextArea name="controls" required maxLength={4096} /></Field></div>
          <div className="sm:col-span-2"><Button type="submit" disabled={request.isPending}>{request.isPending ? "Submitting…" : "Request exception"}</Button></div>
          <div className="sm:col-span-2"><MutationMessage error={request.error} success={success} /></div>
        </form>
      )}
    </section>
  );
}

export function AnalystCaseDetail() {
  const { caseId } = useParams({ from: "/analyst/cases/$caseId" });
  const detail = useQuery({ queryKey: ["case", caseId], queryFn: () => api.case(caseId) });
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="Analyst / Cases" title={caseId} />
      <Async query={detail} label="Reading case…">
        {(item) => (
          <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
            <section className="rounded-2xl border border-line bg-surface p-5">
              <div className="flex flex-wrap items-center gap-2"><SeverityBadge severity={item.severity} /><StatusBadge status={item.state} />{item.overdue ? <StatusBadge status="overdue" /> : null}<span className="font-mono text-xs text-slate">version {item.version}</span></div>
              <h1 className="mt-3 font-serif text-2xl font-semibold text-ink">{item.title}</h1>
              <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
                <div><p className="font-mono text-[10.5px] tracking-widest text-slate">FINDING</p><Link to="/analyst/investigate/$runId/$findingId" params={{ runId: item.run_id, findingId: item.finding_id }} className="mt-1 block break-all text-accent hover:underline">{item.finding_id}</Link></div>
                <div><p className="font-mono text-[10.5px] tracking-widest text-slate">RUN</p><Link to="/runs/$runId/security" params={{ runId: item.run_id }} className="mt-1 block break-all text-accent hover:underline">{item.run_id}</Link></div>
                <div><p className="font-mono text-[10.5px] tracking-widest text-slate">ASSIGNEE</p><p className="mt-1 text-ink">{item.assignee_name ?? "Unassigned"}</p></div>
                <div><p className="font-mono text-[10.5px] tracking-widest text-slate">UPDATED</p><p className="mt-1 text-ink">{timestamp(item.updated_at)}</p></div>
              </div>
              {item.disposition ? <p className="mt-4 rounded-xl border border-ok bg-ok-soft px-4 py-3 text-sm text-ink"><strong>Disposition:</strong> {item.disposition}</p> : null}
            </section>
            <div className="grid gap-5 xl:grid-cols-2"><Assignment item={item} /><Transition item={item} /></div>
            <ExceptionRequest item={item} />
            <section className="rounded-2xl border border-line bg-surface p-5">
              <h2 className="font-serif text-lg font-semibold text-ink">Case history</h2>
              {item.events.length === 0 ? <p className="mt-3 text-sm text-slate">No case events were returned.</p> : (
                <ol className="mt-4 space-y-3">{[...item.events].reverse().map((event) => <li key={event.id} className="rounded-xl border border-line-2 px-4 py-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium text-ink">{event.action.replace(/\./g, " ")}</span><span className="font-mono text-[11px] text-slate">{timestamp(event.at)}</span></div><p className="mt-1 text-sm leading-relaxed text-slate">{event.rationale}</p><p className="mt-2 font-mono text-[11px] text-slate">{event.actor_name} · {event.from_state || "created"} → {event.to_state || "—"}</p>{event.evidence_ids.length > 0 ? <ul className="mt-2 flex flex-wrap gap-2">{event.evidence_ids.map((id) => <li key={id} className="rounded bg-surface-2 px-2 py-1 font-mono text-[11px] text-ink">{id}</li>)}</ul> : null}</li>)}</ol>
              )}
            </section>
          </div>
        )}
      </Async>
    </main>
  );
}
