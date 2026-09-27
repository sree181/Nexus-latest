import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";
import { IntegrityRef, StateFrame, WorkflowHero } from "../components/WorkflowVisual";

const CODE_PATTERN = /^[A-Z2-9]{4}-[A-Z2-9]{4}$/;

export function enrollmentCodeFromLocation(search: string) {
  const candidate = new URLSearchParams(search).get("code")?.trim().toUpperCase() ?? "";
  return CODE_PATTERN.test(candidate) ? candidate : "";
}

export function RecorderEnrollment() {
  const identity = useIdentity();
  const initialCode = enrollmentCodeFromLocation(window.location.search);
  const [entry, setEntry] = useState(initialCode);
  const [code, setCode] = useState(initialCode);
  const pending = useQuery({
    queryKey: ["recorder-enrollment", code],
    queryFn: () => api.pendingRecorderEnrollment(code),
    enabled: Boolean(code), retry: false,
  });
  const approval = useMutation({ mutationFn: () => api.approveRecorderEnrollment(code), onSuccess: () => void pending.refetch() });

  return (
    <main className="recorder-enrollment-page">
      <WorkflowHero eyebrow="Connect managed recorder" title="Approve this device" description="The code binds a local P-256 key to your company identity. It does not grant access to your account or source." tone="accent" />
      <section className="recorder-enrollment-card">
        {!code ? <form onSubmit={(event) => { event.preventDefault(); setCode(entry.trim().toUpperCase()); }}><label htmlFor="recorder-enrollment-code">Code shown by meshagent-recorder</label><input id="recorder-enrollment-code" value={entry} onChange={(event) => setEntry(event.target.value.toUpperCase())} placeholder="ABCD-2345" pattern="[A-Z2-9]{4}-[A-Z2-9]{4}" maxLength={9} autoComplete="one-time-code" /><button type="submit" disabled={!CODE_PATTERN.test(entry)}>Review device</button></form> : pending.isPending ? <StateFrame kind="loading" title="Checking the device code…" /> : pending.isError ? <StateFrame kind="error" title="This code could not be verified" detail={pending.error.message} action={<button type="button" onClick={() => setCode("")}>Enter another code</button>} /> : pending.data.approved || approval.isSuccess ? <StateFrame kind="empty" title="Recorder approved" detail="Return to the installer. It will prove possession of the local key and finish setup." /> : <div className="recorder-enrollment-review"><header><p className="workflow-eyebrow">Confirm local device</p><h2>{pending.data.label}</h2><p>{pending.data.platform} {pending.data.architecture} · recorder {pending.data.recorder_version}</p></header><dl><div><dt>Deployment</dt><dd>{pending.data.deployment}</dd></div><div><dt>Signing in as</dt><dd>{identity.me?.name ?? "Verified company user"}</dd></div><div><dt>Attestation</dt><dd>{pending.data.attestation_format === "none" ? "Not supplied" : pending.data.attestation_format}</dd></div></dl><IntegrityRef label="Device key" value={pending.data.key_thumbprint} /><aside><strong>What this grants</strong><p>Recording and package-gate checks only. The recorder cannot read your meshAgent workspace.</p></aside>{approval.isError ? <p role="alert">{approval.error.message}</p> : null}<div className="recorder-enrollment-actions"><button type="button" onClick={() => setCode("")}>Cancel</button><button type="button" onClick={() => approval.mutate()} disabled={approval.isPending}>Approve recorder</button></div></div>}
      </section>
    </main>
  );
}
