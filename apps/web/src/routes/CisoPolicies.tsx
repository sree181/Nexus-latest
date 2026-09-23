import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, Select, SeverityBadge, StatusBadge, TextArea, TextInput, dateInputToEpoch, epochToDateInput } from "../components/WorkflowUI";
import { api, type CreateExceptionInput, type CreatePolicyInput, type WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";

function PolicyForm() {
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({ mutationFn: (input: CreatePolicyInput) => api.createPolicy(input), onSuccess: (policy) => { setSuccess(`Policy ${policy.name} created at version ${policy.active_version}.`); void client.invalidateQueries({ queryKey: ["policies"] }); } });
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-serif text-lg font-semibold text-ink">Create policy</h2>
      <p className="mt-1 text-sm text-slate">Policy creation is committed to the action log. Values below become the active version.</p>
      <form className="mt-4 grid gap-4 lg:grid-cols-2" onSubmit={(event) => {
        event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget);
        create.mutate({ name: String(form.get("name") ?? "").trim(), scope: String(form.get("scope") ?? "").trim(), severity_threshold: String(form.get("severity_threshold")) as WorkflowSeverity, denied_licenses: String(form.get("denied_licenses") ?? "").split(",").map((value) => value.trim()).filter(Boolean), block_on_unknown: form.get("block_on_unknown") === "on", rationale: String(form.get("rationale") ?? "").trim() });
      }}>
        <Field label="Policy name"><TextInput name="name" required maxLength={256} /></Field>
        <Field label="Scope"><TextInput name="scope" required maxLength={512} placeholder="production/*" /></Field>
        <Field label="Severity threshold"><Select name="severity_threshold" defaultValue="high">{(["critical", "high", "medium", "low", "unknown"] as WorkflowSeverity[]).map((severity) => <option key={severity} value={severity}>{severity}</option>)}</Select></Field>
        <Field label="Denied licenses" hint="Comma-separated SPDX identifiers."><TextInput name="denied_licenses" placeholder="AGPL-3.0, SSPL-1.0" /></Field>
        <div className="lg:col-span-2"><Field label="Rationale"><TextArea name="rationale" required maxLength={4096} /></Field></div>
        <label className="flex items-center gap-2 text-sm text-ink lg:col-span-2"><input type="checkbox" name="block_on_unknown" defaultChecked className="h-4 w-4 accent-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" />Block when evidence is unknown</label>
        <div className="lg:col-span-2"><Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating policy…" : "Create policy"}</Button></div>
        <div className="lg:col-span-2"><MutationMessage error={create.error} success={success} /></div>
      </form>
    </section>
  );
}

function ExceptionForm({ policies }: { policies: { id: string; name: string }[] }) {
  const client = useQueryClient();
  const [success, setSuccess] = useState<string | null>(null);
  const request = useMutation({ mutationFn: (input: CreateExceptionInput) => api.requestException(input), onSuccess: (approval) => { setSuccess(`Exception request sent for approval as ${approval.id}.`); void client.invalidateQueries({ queryKey: ["approvals"] }); void client.invalidateQueries({ queryKey: ["exceptions"] }); } });
  const inSevenDays = Math.floor(Date.now() / 1000) + 7 * 86400;
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <h2 className="font-serif text-lg font-semibold text-ink">Request an exception</h2>
      <p className="mt-1 text-sm text-slate">Requests require an owner, an expiry, and compensating controls. The requester cannot approve their own request.</p>
      {policies.length === 0 ? <p className="mt-4 rounded-xl border border-line-2 bg-surface-2 px-4 py-4 text-sm text-slate">Create a policy before requesting an exception.</p> : <form className="mt-4 grid gap-4 lg:grid-cols-2" onSubmit={(event) => {
        event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget);
        request.mutate({ policy_id: String(form.get("policy_id")), scope: String(form.get("scope") ?? "").trim(), rationale: String(form.get("rationale") ?? "").trim(), compensating_controls: String(form.get("controls") ?? "").trim(), owner: String(form.get("owner") ?? "").trim(), expires_at: dateInputToEpoch(form.get("expires_at")) });
      }}>
        <Field label="Policy"><Select name="policy_id" required>{policies.map((policy) => <option key={policy.id} value={policy.id}>{policy.name}</option>)}</Select></Field>
        <Field label="Exception scope"><TextInput name="scope" required maxLength={512} /></Field>
        <Field label="Owner"><TextInput name="owner" required maxLength={256} /></Field>
        <Field label="Expires"><TextInput name="expires_at" type="date" required min={epochToDateInput(Math.floor(Date.now() / 1000) + 86400)} defaultValue={epochToDateInput(inSevenDays)} /></Field>
        <div className="lg:col-span-2"><Field label="Rationale"><TextArea name="rationale" required maxLength={4096} /></Field></div>
        <div className="lg:col-span-2"><Field label="Compensating controls"><TextArea name="controls" required maxLength={4096} /></Field></div>
        <div className="lg:col-span-2"><Button type="submit" disabled={request.isPending}>{request.isPending ? "Submitting request…" : "Request exception"}</Button></div>
        <div className="lg:col-span-2"><MutationMessage error={request.error} success={success} /></div>
      </form>}
    </section>
  );
}

export function CisoPolicies() {
  const policies = useQuery({ queryKey: ["policies"], queryFn: api.policies });
  const exceptions = useQuery({ queryKey: ["exceptions"], queryFn: api.exceptions });
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO" title="Policies & exceptions" meta={<span>live control-plane records</span>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        <div className="grid gap-5 2xl:grid-cols-2"><PolicyForm />{policies.isPending ? <Loading label="Loading policies…" /> : policies.isError ? <ErrorState error={policies.error} retry={() => void policies.refetch()} /> : <ExceptionForm policies={policies.data.map(({ id, name }) => ({ id, name }))} />}</div>
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Active policy versions</h2>
          {policies.isPending ? <Loading label="Loading policy versions…" /> : policies.isError ? <ErrorState error={policies.error} retry={() => void policies.refetch()} /> : policies.data.length === 0 ? <p className="mt-4 text-sm text-slate">No policies have been created.</p> : <div className="responsive-table-wrap mt-4"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line">{["Policy", "Scope", "Controls", "Version", "Updated"].map((heading) => <th key={heading} className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}</tr></thead><tbody>{policies.data.map((policy) => <tr key={policy.id} className="border-b border-line align-top"><td className="py-3 pr-4"><p className="font-medium text-ink">{policy.name}</p><p className="font-mono text-[11px] text-slate">{policy.id}</p></td><td className="py-3 pr-4 text-sm text-ink">{policy.scope}</td><td className="py-3 pr-4"><div className="flex flex-wrap gap-2"><SeverityBadge severity={policy.current.severity_threshold} /><StatusBadge status={policy.current.block_on_unknown ? "blocks unknown" : "allows unknown"} /></div><p className="mt-1 text-xs text-slate">Denied: {policy.current.denied_licenses.join(", ") || "none"}</p></td><td className="py-3 pr-4 font-mono text-xs text-ink">{policy.active_version}</td><td className="py-3 font-mono text-xs text-slate">{timestamp(policy.updated_at)}</td></tr>)}</tbody></table></div>}
        </section>
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="font-serif text-lg font-semibold text-ink">Exception register</h2>
          {exceptions.isPending ? <Loading label="Loading exceptions…" /> : exceptions.isError ? <ErrorState error={exceptions.error} retry={() => void exceptions.refetch()} /> : exceptions.data.length === 0 ? <p className="mt-4 text-sm text-slate">No exception requests have been recorded.</p> : <div className="responsive-table-wrap mt-4"><table className="w-full border-collapse text-left"><thead><tr className="border-b border-line">{["Scope", "Owner", "Controls", "Expiry", "Status"].map((heading) => <th key={heading} className="pb-2 pr-4 font-mono text-[10.5px] font-normal tracking-widest text-slate">{heading.toUpperCase()}</th>)}</tr></thead><tbody>{exceptions.data.map((item) => <tr key={item.id} className="border-b border-line align-top"><td className="py-3 pr-4 text-sm text-ink">{item.scope}<p className="mt-1 font-mono text-[11px] text-slate">policy {item.policy_id}</p></td><td className="py-3 pr-4 text-sm text-ink">{item.owner}</td><td className="max-w-sm py-3 pr-4 text-xs leading-relaxed text-slate">{item.compensating_controls}</td><td className="py-3 pr-4 font-mono text-xs text-slate">{timestamp(item.expires_at)}</td><td className="py-3"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div>}
        </section>
      </div>
    </main>
  );
}
