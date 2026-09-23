import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { AuditEntry } from "../lib/api";
import { api } from "../lib/api";
import { Async } from "../components/Async";
import { PageHeader } from "../components/PageHeader";
import { timestamp } from "../lib/format";

/** Destroying evidence is the action an auditor asks about first, so it
 *  reads differently from the rest. */
const tone: Record<string, "risk" | "warn" | "accent" | "neutral"> = {
  "run.forget": "risk",
  "access.denied": "warn",
  "recommendation.apply": "accent",
  "run.create": "neutral",
  "gate.block": "risk",
  // an install that went through unchecked is not a clean result, and it
  // should not read like one
  "gate.unknown": "warn",
  "device.mint": "accent",
  "device.approve": "accent",
  "device.revoke": "neutral",
};

const label: Record<string, string> = {
  "run.forget": "Forgot memory",
  "access.denied": "Refused",
  "recommendation.apply": "Applied recommendation",
  "run.create": "Started run",
  "gate.block": "Blocked install",
  "gate.unknown": "Install not checked",
  "device.mint": "Registered device",
  "device.approve": "Approved device",
  "device.revoke": "Revoked device",
};

function Entry({ entry }: { entry: AuditEntry }) {
  return (
    <li className="flex flex-col gap-1.5 border-b border-rule px-5 py-4 last:border-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <Badge tone={tone[entry.action] ?? "neutral"}>
          {label[entry.action] ?? entry.action}
        </Badge>
        <span className="text-[13.5px] text-ink">{entry.actor_name}</span>
        <span className="font-mono text-[11.5px] text-slate">
          {entry.actor} · {entry.role}
        </span>
        {/* an asserted identity is worth less than an authenticated one, and
            an audit line must not hide the difference */}
        {!entry.verified && <Badge tone="warn">identity asserted</Badge>}
        <span className="ml-auto font-mono text-[11.5px] text-slate">
          {timestamp(entry.at)}
        </span>
      </div>
      <p className="font-mono text-[12px] text-slate">{entry.target}</p>
      {entry.detail && (
        <p className="text-[13px] leading-snug text-ink">{entry.detail}</p>
      )}
    </li>
  );
}

export function Audit() {
  const log = useQuery({ queryKey: ["audit"], queryFn: api.audit });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        section="Governance"
        title="Action log"
        meta="What people did to governed memory — who deleted it, who was refused it"
      />
      <Async
        query={log}
        label="Reading the action log"
        emptyTitle="Nothing has been done yet"
        emptyDetail="Deletions, run starts and applied recommendations appear here as they happen."
        isEmpty={(d) => d.entries.length === 0}
      >
        {(data) => (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2.5">
              <Badge tone={data.intact ? "ok" : "risk"}>
                {data.intact ? "chain intact" : "chain broken"}
              </Badge>
              {!data.intact && (
                <span className="text-[13px] text-ink">
                  Entry {data.broken_at} does not follow the one before it:
                  this file has been edited or truncated since it was written.
                </span>
              )}
              {/* a log that dies with the process is not an audit trail */}
              {!data.durable && (
                <Badge tone="warn">not durable — lost on restart</Badge>
              )}
            </div>

            <ul className="rounded-2xl border border-rule bg-paper">
              {[...data.entries].reverse().map((e) => (
                <Entry key={e.digest} entry={e} />
              ))}
            </ul>

            <p className="text-[13px] leading-snug text-slate">
              {data.covers} Each entry commits to the digest of the one before
              it, so an entry cannot be removed or altered without breaking the
              chain. The chain is not signed, so anyone able to rewrite this
              file could recompute it wholesale.
            </p>
          </div>
        )}
      </Async>
    </div>
  );
}
