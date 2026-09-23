import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { DeviceOut, PairPending } from "../lib/api";
import { ApiError, api } from "../lib/api";
import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { timestamp } from "../lib/format";

/** What a grant actually lets the machine do, said in words rather than left
 *  as an API string nobody outside this repo can interpret. */
const GRANTS: Record<string, string> = {
  "recorder.write": "record what its agent writes, as you",
  "gate.check": "ask whether a package is allowed",
};

/** Approving a waiting `meshagent login`.
 *
 *  The code is typed rather than clicked from a list on purpose. The standing
 *  attack on every device flow is to start a pairing elsewhere and talk
 *  somebody into approving it, so the person approving has to have the code
 *  in front of them, and has to see what they are agreeing to first. */
function Approve() {
  const [code, setCode] = useState("");
  const [looked, setLooked] = useState<PairPending | null>(null);
  const [problem, setProblem] = useState("");
  const queryClient = useQueryClient();

  const look = useMutation({
    mutationFn: () => api.pendingPair(code.trim().toUpperCase()),
    onSuccess: (p) => {
      setLooked(p);
      setProblem("");
    },
    onError: (e: unknown) => {
      setLooked(null);
      setProblem(
        e instanceof ApiError && e.status === 404
          ? "No login is waiting on that code. Codes expire after ten minutes."
          : String(e),
      );
    },
  });

  const approve = useMutation({
    mutationFn: () => api.approvePair(code.trim().toUpperCase()),
    onSuccess: () => {
      setLooked(null);
      setCode("");
      void queryClient.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (e: unknown) => setProblem(String(e)),
  });

  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-rule bg-paper p-5">
      <div>
        <h2 className="font-serif text-[17px] text-ink">
          Approve a machine
        </h2>
        <p className="mt-0.5 text-[13px] leading-snug text-slate">
          Someone ran <code className="font-mono text-[12px]">meshagent
          login</code> and is waiting. Only approve a code you are looking at
          on your own screen.
        </p>
      </div>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          look.mutate();
        }}
      >
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="user-code"
            className="font-mono text-[10.5px] tracking-wide text-slate"
          >
            CODE
          </label>
          <input
            id="user-code"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="ABCD-2345"
            className="w-[180px] rounded-lg border border-line bg-wash px-3 py-2 font-mono text-[13px] text-ink"
          />
        </div>
        <button
          type="submit"
          disabled={!code.trim() || look.isPending}
          className="rounded-lg border border-line bg-surface px-4 py-2 text-[13px] text-ink disabled:opacity-50"
        >
          {look.isPending ? "Looking…" : "Look up"}
        </button>
      </form>

      {problem && (
        <p className="rounded-xl border border-line-2 bg-wash px-4 py-3 text-[13px] leading-relaxed text-ink">
          {problem}
        </p>
      )}

      {looked && (
        <div className="flex flex-col gap-3 rounded-xl border border-line-2 bg-wash px-4 py-4">
          <p className="text-[13px] leading-relaxed text-ink">
            <span className="font-mono">{looked.user_code}</span> was started
            by a machine calling itself{" "}
            <span className="font-medium">{looked.label}</span>, at{" "}
            {timestamp(looked.started_at)}. If that is not a machine of
            yours, do not approve it.
          </p>
          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[10.5px] tracking-wide text-slate">
              IT WILL BE ABLE TO
            </span>
            <ul className="flex flex-col gap-1">
              {looked.grants.map((g) => (
                <li key={g} className="text-[13px] text-ink">
                  · {GRANTS[g] ?? g}
                </li>
              ))}
            </ul>
            <p className="text-[12.5px] leading-snug text-slate">
              It will not be able to read any run, see the fleet, or delete
              anything — including your own.
            </p>
          </div>
          <div>
            <button
              type="button"
              onClick={() => approve.mutate()}
              disabled={approve.isPending || looked.approved}
              className="rounded-lg border border-line bg-surface px-4 py-2 text-[13px] text-ink disabled:opacity-50"
            >
              {looked.approved
                ? "Already approved"
                : approve.isPending
                  ? "Approving…"
                  : `Approve ${looked.label}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function Device({ device }: { device: DeviceOut }) {
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.revokeDevice(device.id),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["devices"] }),
  });

  return (
    <li className="flex flex-col gap-1.5 border-b border-rule px-5 py-4 last:border-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-[13.5px] text-ink">{device.label}</span>
        <span className="font-mono text-[11.5px] text-slate">
          {device.subject}
        </span>
        {/* the same honesty the audit log carries: a token minted by an
            asserted identity records under an asserted name */}
        {!device.verified && <Badge tone="warn">identity asserted</Badge>}
        <button
          type="button"
          onClick={() => revoke.mutate()}
          disabled={revoke.isPending}
          className="ml-auto rounded-lg border border-line px-3 py-1.5 text-[12.5px] text-ink disabled:opacity-50"
        >
          {revoke.isPending ? "Revoking…" : "Revoke"}
        </button>
      </div>
      <p className="font-mono text-[11.5px] text-slate">
        added {timestamp(device.created_at)} ·{" "}
        {device.last_used
          ? `last recorded ${timestamp(device.last_used)}`
          : "has not recorded anything yet"}
      </p>
    </li>
  );
}

export function Devices() {
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        section="Governance"
        title="Devices"
        meta="Machines allowed to record as you — the editor hooks that have no browser to sign in with"
      />

      <Approve />

      <Async
        query={devices}
        label="Reading registered machines"
        emptyTitle="No machine is registered"
        emptyDetail="Until one is, hooks post under a name asserted in a header, and the coverage table says so rather than naming anyone it cannot stand behind."
        isEmpty={(d) => d.length === 0}
      >
        {(data) => (
          <div className="flex flex-col gap-4">
            <ul className="rounded-2xl border border-rule bg-paper">
              {data.map((d) => (
                <Device key={d.id} device={d} />
              ))}
            </ul>
            <p className="text-[13px] leading-snug text-slate">
              A device token records and nothing else — it cannot read a run,
              see the fleet, or delete memory. Revoking takes effect on the
              machine's next write; there is no lifetime to wait out.
            </p>
          </div>
        )}
      </Async>
    </div>
  );
}
