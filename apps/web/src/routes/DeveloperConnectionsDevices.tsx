import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { DevIcon } from "../components/DeveloperIcons";
import { EmptyVisual, SidePanel, Status } from "../components/DeveloperVisual";
import { ApiError, api, type DeviceOut, type PairPending } from "../lib/api";
import { relativeTimeMs, timestamp } from "../lib/format";

function PairDevice() {
  const [code, setCode] = useState("");
  const [pending, setPending] = useState<PairPending | null>(null);
  const [problem, setProblem] = useState("");
  const client = useQueryClient();
  const lookup = useMutation({
    mutationFn: () => api.pendingPair(code.trim().toUpperCase()),
    onSuccess: (value) => { setPending(value); setProblem(""); },
    onError: (error: unknown) => {
      setPending(null);
      setProblem(error instanceof ApiError && error.status === 404 ? "Code expired" : "Try again");
    },
  });
  const approve = useMutation({
    mutationFn: () => api.approvePair(code.trim().toUpperCase()),
    onSuccess: () => { setPending(null); setCode(""); void client.invalidateQueries({ queryKey: ["devices"] }); },
    onError: () => setProblem("Try again"),
  });
  return (
    <section className="dev-surface">
      <header className="dev-panel-heading"><h2>Pair</h2>{problem ? <Status label={problem} tone="danger" icon="warning" /> : null}</header>
      <form className="flex flex-wrap items-end gap-2 p-4" onSubmit={(event) => { event.preventDefault(); lookup.mutate(); }}>
        <div className="dev-field"><label htmlFor="pair-code">Code</label><input id="pair-code" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} placeholder="ABCD-2345" className="dev-mono" /></div>
        <button type="submit" disabled={!code.trim() || lookup.isPending} className="dev-action">Check</button>
      </form>
      {pending ? <div className="dev-compact-row"><DevIcon name="device" /><span className="dev-compact-row-main"><strong>{pending.label}</strong><small>{timestamp(pending.started_at)} · {pending.grants.length} permissions</small></span><button type="button" className="dev-action dev-action-primary" disabled={approve.isPending || pending.approved} onClick={() => approve.mutate()}>{pending.approved ? "Approved" : "Approve"}</button></div> : null}
    </section>
  );
}

function DevicePanel({ device, close }: { device: DeviceOut; close: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const client = useQueryClient();
  const revoke = useMutation({ mutationFn: () => api.revokeDevice(device.id), onSuccess: () => { void client.invalidateQueries({ queryKey: ["devices"] }); close(); } });
  return (
    <SidePanel title={device.label} icon="device" onClose={close} footer={confirming ? <div className="flex gap-2"><button type="button" className="dev-action dev-action-danger flex-1" disabled={revoke.isPending} onClick={() => revoke.mutate()}>Revoke</button><button type="button" className="dev-action" onClick={() => setConfirming(false)}>Cancel</button></div> : <button type="button" className="dev-action dev-action-danger w-full" onClick={() => setConfirming(true)}>Revoke device</button>}>
      <dl className="dev-kv"><dt>Identity</dt><dd>{device.verified ? "Verified" : "Local"}</dd><dt>Owner</dt><dd>{device.name}</dd><dt>Added</dt><dd>{timestamp(device.created_at)}</dd><dt>Last activity</dt><dd>{device.last_used ? timestamp(device.last_used) : "Not used"}</dd><dt>Device</dt><dd className="dev-mono">{device.id}</dd><dt>Access</dt><dd>Record and check packages</dd></dl>
      {revoke.isError ? <div className="dev-compact-row dev-tone-danger"><DevIcon name="warning" /><span>Try again</span></div> : null}
    </SidePanel>
  );
}

export function DeveloperConnectionsDevices() {
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices, refetchInterval: 10_000 });
  const [selected, setSelected] = useState<DeviceOut | null>(null);
  return (
    <div className="dev-scroll dev-stack">
      <PairDevice />
      <section className="dev-surface">
        <header className="dev-panel-heading"><h2>Devices</h2><Status label={`${devices.data?.length ?? 0}`} tone="neutral" icon="device" /></header>
        {devices.isPending ? <EmptyVisual icon="live" title="Loading" /> : devices.isError ? <EmptyVisual icon="warning" title="Try again" action={<button type="button" className="dev-action" onClick={() => void devices.refetch()}>Retry</button>} /> : devices.data.length ? devices.data.map((device) => <button key={device.id} type="button" className="dev-compact-row w-full border-x-0 border-t-0 bg-transparent text-left" onClick={() => setSelected(device)}><DevIcon name="device" /><span className="dev-compact-row-main"><strong>{device.label}</strong><small>{device.last_used ? `Active ${relativeTimeMs(device.last_used * 1_000)}` : "Not used"}</small></span><Status label={device.verified ? "Verified" : "Local"} tone={device.verified ? "success" : "warning"} icon="user" /></button>) : <EmptyVisual icon="device" title="No devices" />}
      </section>
      {selected ? <DevicePanel device={selected} close={() => setSelected(null)} /> : null}
    </div>
  );
}
