import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";

import { api, type RecorderDetail, type RecorderSummary } from "../lib/api";
import { ConflictRecovery, IntegrityRef, StateFrame } from "../components/WorkflowVisual";
import { useIdentity } from "../lib/useIdentity";

function relative(epoch: number): string {
  if (!epoch) return "Never seen";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function statusTone(item: RecorderSummary): string {
  if (item.trust_state === "revoked" || item.delivery_state === "blocked") return "danger";
  if (item.trust_state === "quarantined" || item.delivery_state === "offline" || item.config_status === "stale") return "warning";
  if (item.credential_kind === "legacy_bearer" || item.delivery_state === "delayed") return "pending";
  return "active";
}

export function RecorderTrustActions({ recorder, authorized = true }: { recorder: RecorderDetail; authorized?: boolean }) {
  const client = useQueryClient();
  const [action, setAction] = useState<"quarantine" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: (choice: "quarantine" | "revoke") => choice === "quarantine"
      ? api.quarantineRecorder(recorder.id, { expected_version: recorder.version, reason })
      : api.revokeRecorder(recorder.id, { expected_version: recorder.version, reason }),
    onSuccess: async () => {
      setAction(null);
      setReason("");
      await Promise.all([
        client.invalidateQueries({ queryKey: ["recorders"] }),
        client.invalidateQueries({ queryKey: ["recorder", recorder.id] }),
        client.invalidateQueries({ queryKey: ["recorder-onboarding"] }),
      ]);
    },
  });
  const canQuarantine = recorder.trust_state === "active";
  const canRevoke = recorder.trust_state === "active" || recorder.trust_state === "quarantined";

  if (!authorized) {
    return <div className="admin-read-only"><strong>Read-only access</strong><span>Your verified capabilities do not permit recorder trust changes.</span></div>;
  }
  if (!canQuarantine && !canRevoke) {
    return <div className="admin-trust-actions"><p className="admin-action-quiet">This recorder is revoked. New credentials and evidence delivery are denied.</p><div className="admin-action-row"><Link to="/admin/onboarding/recorder">Re-enroll with a new key</Link></div></div>;
  }
  return (
    <div className="admin-trust-actions">
      <div className="admin-action-row">
        {recorder.trust_state === "quarantined" ? <Link to="/admin/onboarding/recorder">Re-enroll with a new key</Link> : null}
        {canQuarantine ? <button type="button" onClick={() => setAction("quarantine")}>Quarantine</button> : null}
        {canRevoke ? <button type="button" className="is-danger" onClick={() => setAction("revoke")}>Revoke</button> : null}
      </div>
      {action ? (
        <form onSubmit={(event) => { event.preventDefault(); mutation.mutate(action); }}>
          <label htmlFor="recorder-trust-reason">Reason for {action}</label>
          <textarea id="recorder-trust-reason" value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} placeholder="Record the operational reason." />
          <p>{action === "quarantine" ? "New tokens and delivery stop immediately. Queued evidence stays encrypted on the device." : "Revocation is terminal for this device identity. Re-enrollment creates a new recorder."}</p>
          <div>
            <button type="button" onClick={() => setAction(null)}>Cancel</button>
            <button type="submit" className={action === "revoke" ? "is-danger" : ""} disabled={reason.trim().length < 3 || mutation.isPending}>Confirm {action}</button>
          </div>
        </form>
      ) : null}
      <ConflictRecovery error={mutation.error} draft={reason} onReload={() => { mutation.reset(); void client.invalidateQueries({ queryKey: ["recorder", recorder.id] }); }} />
      {mutation.isError && !(mutation.error instanceof Error && "status" in mutation.error && mutation.error.status === 412) ? <p role="alert" className="admin-action-error">{mutation.error.message}</p> : null}
    </div>
  );
}

export function RecorderInspector({ recorder, canWriteTrust }: { recorder: RecorderDetail; canWriteTrust: boolean }) {
  return (
    <section className="admin-recorder-inspector">
      <header>
        <div><p className="workflow-eyebrow">Recorder detail</p><h2>{recorder.label}</h2><p>{recorder.name} · {recorder.deployment}</p></div>
        <span className={`admin-status is-${statusTone(recorder)}`}>{recorder.trust_state.replace("_", " ")}</span>
      </header>
      <dl className="admin-recorder-facts">
        <div><dt>Credential</dt><dd>{recorder.credential_kind === "dpop" ? "DPoP-bound" : "Legacy bearer"}</dd></div>
        <div><dt>Last heartbeat</dt><dd>{relative(recorder.last_seen_at)}</dd></div>
        <div><dt>Delivery</dt><dd>{recorder.delivery_state}</dd></div>
        <div><dt>Local queue</dt><dd>{recorder.queue_batches} batches · {Math.round(recorder.queue_bytes / 1024)} KB</dd></div>
        <div><dt>Recorder</dt><dd>{recorder.recorder_version} · {recorder.platform} {recorder.architecture}</dd></div>
        <div><dt>Desired configuration</dt><dd>v{recorder.config_version}</dd></div>
        <div><dt>Applied configuration</dt><dd>{recorder.reported_config_version ? `v${recorder.reported_config_version}` : "No heartbeat"} · {recorder.config_status}</dd></div>
      </dl>
      <div className="admin-integrity-row">
        <IntegrityRef label="Key" value={recorder.key_thumbprint} />
        <IntegrityRef label="Config" value={recorder.config_digest} />
      </div>
      {recorder.config_status === "stale" ? <p className="admin-config-drift" role="status">A newer signed policy is verified but not active. Restart the recorder through the managed service to apply it safely.</p> : null}
      <details className="admin-history" open={recorder.history.length > 0}>
        <summary>Trust history <span>{recorder.history.length}</span></summary>
        {recorder.history.length ? <ol>{recorder.history.map((entry) => <li key={entry.receipt_id}><span className={`admin-history-mark is-${entry.next_state}`} /><div><strong>{entry.action}</strong><p>{entry.reason}</p><small>{entry.actor_name} · {new Date(entry.created_at * 1000).toLocaleString()}</small><IntegrityRef label="Receipt" value={entry.receipt_id} /></div></li>)}</ol> : <p>No trust changes have been recorded.</p>}
      </details>
      <aside className="admin-responsibility">
        <p className="workflow-eyebrow">Your responsibility</p>
        <h3>Keep this recorder trusted or stop it.</h3>
        <p>This view intentionally excludes prompts, source, repositories, packages, and developer sessions.</p>
        <RecorderTrustActions recorder={recorder} authorized={canWriteTrust} />
      </aside>
    </section>
  );
}

export function AdminRecorders() {
  const identity = useIdentity();
  const [filter, setFilter] = useState<"all" | "attention" | "legacy">("all");
  const params = useParams({ strict: false });
  const routeDevice = "deviceId" in params ? String(params.deviceId) : null;
  const fleet = useQuery({ queryKey: ["recorders"], queryFn: api.recorders, refetchInterval: 15_000 });
  const rows = useMemo(() => (fleet.data?.recorders ?? []).filter((item) => filter === "all" || (filter === "legacy" ? item.credential_kind === "legacy_bearer" : statusTone(item) !== "active")), [fleet.data, filter]);
  const selected = routeDevice ?? rows[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["recorder", selected], queryFn: () => api.recorder(selected as string), enabled: Boolean(selected), refetchInterval: 15_000 });

  if (fleet.isPending) return <main className="admin-page"><StateFrame kind="loading" title="Loading recorder trust…" /></main>;
  if (fleet.isError) return <main className="admin-page"><StateFrame kind="error" title="Recorder fleet is unavailable" detail={fleet.error.message} action={<button type="button" onClick={() => void fleet.refetch()}>Try again</button>} /></main>;
  return (
    <main className="admin-fleet-page">
      <header className="admin-fleet-header"><div><p className="workflow-eyebrow">Enterprise recorder fleet</p><h1>{fleet.data.total} managed recorder{fleet.data.total === 1 ? "" : "s"}</h1><p>Trust and delivery health only. Developer work remains outside this role.</p></div><span className={`admin-status is-${fleet.data.action_required ? "warning" : "active"}`}>{fleet.data.action_required ? `${fleet.data.action_required} need attention` : "Fleet healthy"}</span></header>
      <div className="admin-fleet-workspace">
        <section className="admin-recorder-list" aria-label="Recorder fleet">
          <div className="admin-filter-row" aria-label="Recorder filters">{(["all", "attention", "legacy"] as const).map((item) => <button key={item} type="button" aria-pressed={filter === item} onClick={() => setFilter(item)}>{item}</button>)}</div>
          {rows.length ? <ul>{rows.map((item) => <li key={item.id}><Link to="/admin/recorders/$deviceId" params={{ deviceId: item.id }} className={selected === item.id ? "is-selected" : ""}><span className={`admin-recorder-dot is-${statusTone(item)}`} aria-hidden="true" /><span><strong>{item.label}</strong><small>{item.name} · {item.platform}</small></span><span><b>{item.trust_state}</b><small>{relative(item.last_seen_at)}</small></span></Link></li>)}</ul> : <StateFrame kind="empty" title="No recorders in this view" detail="Change the filter or enroll the pilot device." />}
        </section>
        <section className="admin-recorder-focus">{!selected ? <StateFrame kind="empty" title="Select a recorder" /> : detail.isPending ? <StateFrame kind="loading" title="Loading recorder…" /> : detail.isError ? <StateFrame kind="error" title="Recorder detail is unavailable" detail={detail.error.message} action={<button type="button" onClick={() => void detail.refetch()}>Try again</button>} /> : <RecorderInspector recorder={detail.data} canWriteTrust={identity.hasCapability("recorder.trust.write")} />}</section>
      </div>
    </main>
  );
}
