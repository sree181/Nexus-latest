import { useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button } from "@meshagent/ui";

import { ErrorState, Loading } from "../components/Async";
import { GovernanceEvidencePanel } from "../components/GovernanceEvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { Field, MutationMessage, Select, SeverityBadge, StatusBadge, TextArea } from "../components/WorkflowUI";
import { api, type CreatePolicyVersionInput, type Policy, type PolicyLifecycleInput, type PolicyVersion, type WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";
import { useIdentity } from "../lib/useIdentity";

function difference(version: PolicyVersion, active: PolicyVersion | undefined): string[] {
  if (!active || active.version === version.version) return ["Current effective controls"];
  const changes: string[] = [];
  if (version.severity_threshold !== active.severity_threshold) changes.push(`Severity ${active.severity_threshold} → ${version.severity_threshold}`);
  if (version.block_on_unknown !== active.block_on_unknown) changes.push(version.block_on_unknown ? "Unknown evidence will be blocked" : "Unknown evidence will be allowed");
  const added = version.denied_licenses.filter((item) => !active.denied_licenses.includes(item));
  const removed = active.denied_licenses.filter((item) => !version.denied_licenses.includes(item));
  if (added.length) changes.push(`Deny ${added.join(", ")}`);
  if (removed.length) changes.push(`Allow ${removed.join(", ")}`);
  return changes.length ? changes : ["No control changes from the active version"];
}

function VersionActions({ policy, version }: { policy: Policy; version: PolicyVersion }) {
  const client = useQueryClient();
  const { me } = useIdentity();
  const [mode, setMode] = useState<"submit" | "activate" | "withdraw" | null>(null);
  const [rationale, setRationale] = useState("");
  const [success, setSuccess] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: ({ mode: action, input }: { mode: "submit" | "activate" | "withdraw"; input: PolicyLifecycleInput }) => {
      if (action === "submit") return api.submitPolicyVersion(policy.id, version.version, input);
      if (action === "activate") return api.activatePolicyVersion(policy.id, version.version, input);
      return api.withdrawPolicyVersion(policy.id, version.version, input);
    },
    onSuccess: (updated, variables) => {
      setSuccess(`Version ${version.version} ${variables.mode === "submit" ? "sent for review" : variables.mode === "activate" ? "activated" : "withdrawn"}.`);
      setMode(null);
      setRationale("");
      client.setQueryData(["policy", policy.id], updated);
      void client.invalidateQueries({ queryKey: ["policies"] });
      void client.invalidateQueries({ queryKey: ["governance-evidence", "policy", policy.id] });
      void client.invalidateQueries({ queryKey: ["governanceOverview"] });
    },
  });
  const canSubmit = version.state === "draft";
  const canActivate = version.state === "in_review";
  const canWithdraw = version.state === "draft" || version.state === "in_review";
  const ownSubmission = me?.subject === version.submitted_by || (!version.submitted_by && me?.subject === version.created_by);

  if (!canSubmit && !canActivate && !canWithdraw) return null;
  return (
    <div className="mt-4 border-t border-line pt-4">
      {canActivate && ownSubmission ? <p className="mb-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-xs text-ink">Another CISO must activate this version in production.</p> : null}
      {mode ? (
        <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); mutation.mutate({ mode, input: { expected_version: policy.version, rationale: rationale.trim() } }); }}>
          <Field label={`${mode} rationale`}><TextArea value={rationale} onChange={(event) => setRationale(event.target.value)} required maxLength={4096} autoFocus /></Field>
          <div className="flex flex-wrap gap-2"><Button type="submit" disabled={mutation.isPending || !rationale.trim()} variant={mode === "withdraw" ? "danger" : undefined}>{mutation.isPending ? "Recording…" : `Confirm ${mode}`}</Button><Button type="button" variant="ghost" onClick={() => { setMode(null); mutation.reset(); }}>Cancel</Button></div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          {canSubmit ? <Button type="button" onClick={() => setMode("submit")}>Send for review</Button> : null}
          {canActivate ? <Button type="button" onClick={() => setMode("activate")} disabled={Boolean(ownSubmission && me?.verified)}>Activate version</Button> : null}
          {canWithdraw ? <Button type="button" variant="ghost" onClick={() => setMode("withdraw")}>Withdraw</Button> : null}
        </div>
      )}
      <MutationMessage error={mutation.error} success={success} />
    </div>
  );
}

function NewVersion({ policy }: { policy: Policy }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (input: CreatePolicyVersionInput) => api.createPolicyVersion(policy.id, input),
    onSuccess: (updated) => {
      setSuccess(`Draft version ${updated.current.version} created.`);
      setOpen(false);
      client.setQueryData(["policy", policy.id], updated);
      void client.invalidateQueries({ queryKey: ["policies"] });
    },
  });
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-serif text-lg font-semibold text-ink">Prepare a revision</h2><p className="mt-1 text-sm text-slate">Create a draft without changing the effective policy.</p></div><Button type="button" variant="ghost" onClick={() => setOpen((value) => !value)}>{open ? "Close" : "New version"}</Button></div>
      {open ? <form className="mt-5 grid gap-4 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); setSuccess(null); const form = new FormData(event.currentTarget); create.mutate({ expected_version: policy.version, severity_threshold: String(form.get("severity_threshold")) as WorkflowSeverity, denied_licenses: String(form.get("denied_licenses") ?? "").split(",").map((item) => item.trim()).filter(Boolean), block_on_unknown: form.get("block_on_unknown") === "on", rationale: String(form.get("rationale") ?? "").trim() }); }}>
        <Field label="Severity threshold"><Select name="severity_threshold" defaultValue={policy.current.severity_threshold}>{(["critical", "high", "medium", "low", "unknown"] as WorkflowSeverity[]).map((level) => <option key={level}>{level}</option>)}</Select></Field>
        <Field label="Denied licenses" hint="Comma-separated SPDX identifiers"><input name="denied_licenses" defaultValue={policy.current.denied_licenses.join(", ")} className="w-full rounded-lg border border-line-2 bg-white px-3 py-2.5 text-sm text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft" /></Field>
        <label className="flex items-center gap-2 text-sm text-ink sm:col-span-2"><input type="checkbox" name="block_on_unknown" defaultChecked={policy.current.block_on_unknown} className="h-4 w-4 accent-accent" />Block when evidence is unknown</label>
        <div className="sm:col-span-2"><Field label="Reason for change"><TextArea name="rationale" required maxLength={4096} /></Field></div>
        <div className="sm:col-span-2"><Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create draft"}</Button></div>
      </form> : null}
      <MutationMessage error={create.error} success={success} />
    </section>
  );
}

export function CisoPolicyDetail() {
  const { policyId } = useParams({ strict: false }) as { policyId: string };
  const query = useQuery({ queryKey: ["policy", policyId], queryFn: () => api.policy(policyId) });
  const policy = query.data;
  const active = useMemo(() => policy?.versions.find((version) => version.version === policy.active_version), [policy]);

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <PageHeader section="CISO / Policies" title={policy?.name ?? "Policy"} meta={<Link to="/ciso/policies" className="text-accent hover:underline">Back to policy register</Link>} />
      <div className="flex flex-1 flex-col gap-5 overflow-auto p-4 sm:p-6">
        {query.isPending ? <Loading label="Loading policy…" /> : query.isError || !policy ? <ErrorState error={query.error ?? new Error("Policy unavailable")} retry={() => void query.refetch()} /> : <>
          <section className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow)]">
            <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><StatusBadge status={policy.status} /><Badge tone="neutral">{policy.scope}</Badge></div><p className="mt-3 font-mono text-xs text-slate">{policy.id}</p></div><div className="text-right"><p className="font-mono text-[10px] tracking-widest text-slate">ACTIVE VERSION</p><p className="font-serif text-3xl font-semibold text-ink">{policy.active_version || "—"}</p></div></div>
          </section>
          <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]">
            <section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Version history</h2><p className="mt-1 text-sm text-slate">Effective controls and proposed changes, newest first.</p><ol className="mt-5 space-y-4">{[...policy.versions].reverse().map((version) => <li key={version.version} className="rounded-xl border border-line-2 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-serif text-xl font-semibold text-ink">Version {version.version}</span><StatusBadge status={version.state} /></div><p className="mt-1 text-xs text-slate">Created by {version.created_by_name} · {timestamp(version.created_at)}</p></div><SeverityBadge severity={version.severity_threshold} /></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">CONTROL</p><p className="mt-1 text-sm text-ink">{version.block_on_unknown ? "Block unknown evidence" : "Allow unknown evidence"}</p><p className="mt-1 text-xs text-slate">Denied licenses: {version.denied_licenses.join(", ") || "none"}</p></div><div className="rounded-lg bg-surface-2 p-3"><p className="font-mono text-[10px] tracking-widest text-slate">CHANGE FROM ACTIVE</p>{difference(version, active).map((item) => <p key={item} className="mt-1 text-xs text-ink">{item}</p>)}</div></div><p className="mt-3 text-sm leading-relaxed text-slate">{version.rationale}</p><p className="mt-3 break-all font-mono text-[10px] text-slate">SHA-256 {version.content_digest}</p><VersionActions policy={policy} version={version} /></li>)}</ol></section>
            <div className="space-y-5"><NewVersion policy={policy} /><section className="rounded-2xl border border-line bg-surface p-5"><h2 className="font-serif text-lg font-semibold text-ink">Recorded actions</h2><ol className="mt-4 space-y-3">{[...policy.events].reverse().map((event) => <li key={event.id} className="border-l-2 border-accent-soft pl-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-ink">{event.action.replace(/^policy\./, "").replace(/_/g, " ")}</p><span className="font-mono text-[10px] text-slate">{timestamp(event.at)}</span></div><p className="mt-1 text-xs text-slate">{event.actor_name} · {event.rationale}</p></li>)}</ol></section></div>
          </div>
          <GovernanceEvidencePanel kind="policy" id={policy.id} />
        </>}
      </div>
    </main>
  );
}
