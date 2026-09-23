import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Badge } from "@meshagent/ui";

import type { DataOrigin, OriginMetadata, WorkflowSeverity } from "../lib/api";
import { timestamp } from "../lib/format";

export const inputClass =
  "w-full rounded-lg border border-line-2 bg-white px-3 py-2.5 text-sm text-ink outline-none transition placeholder:text-slate-2 focus:border-accent focus:ring-2 focus:ring-accent-soft disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-slate";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm text-ink">
      <span className="font-mono text-[10.5px] tracking-widest text-slate">{label.toUpperCase()}</span>
      {children}
      {hint ? <span className="text-xs leading-snug text-slate">{hint}</span> : null}
    </label>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputClass} min-h-24 resize-y ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

export function MutationMessage({ error, success }: { error: unknown; success?: string | null }) {
  if (error) {
    return (
      <p role="alert" className="rounded-lg border border-risk bg-risk-soft px-3 py-2 text-sm text-risk">
        {error instanceof Error ? error.message : "The operation could not be completed."}
      </p>
    );
  }
  if (success) {
    return (
      <p role="status" className="rounded-lg border border-ok bg-ok-soft px-3 py-2 text-sm text-ok">
        {success}
      </p>
    );
  }
  return null;
}

const originTone: Record<DataOrigin, "ok" | "warn" | "neutral"> = {
  live: "ok",
  mixed: "warn",
  sample: "neutral",
};

export function OriginNote({ origin, compact = false }: { origin: OriginMetadata; compact?: boolean }) {
  return (
    <section aria-label="Data provenance" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-surface px-4 py-3">
      <Badge tone={originTone[origin.data_origin]}>{origin.data_origin} data</Badge>
      <span className="font-mono text-[11px] text-slate">
        Source time {origin.source_time > 0 ? timestamp(origin.source_time) : "not available"}
      </span>
      <span className="font-mono text-[11px] text-slate">
        Coverage {origin.coverage.known} known / {origin.coverage.unknown} unknown
      </span>
      {origin.seeded_count > 0 ? (
        <span className="font-mono text-[11px] text-slate">{origin.seeded_count} seeded run{origin.seeded_count === 1 ? "" : "s"}</span>
      ) : null}
      {!compact ? <p className="basis-full text-xs leading-snug text-slate">Basis: {origin.coverage.basis}. Unknown coverage is not treated as a clean result.</p> : null}
    </section>
  );
}

export function SeverityBadge({ severity }: { severity: WorkflowSeverity }) {
  return <Badge tone={severity === "critical" || severity === "high" ? "risk" : severity === "medium" ? "warn" : "neutral"}>{severity}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone = status === "approved" || status === "resolved" || status === "closed" || status === "ready"
    ? "ok"
    : status === "rejected" || status === "overdue" || status === "failed"
      ? "risk"
      : status === "pending" || status === "open"
        ? "warn"
        : "neutral";
  return <Badge tone={tone}>{status.replace(/_/g, " ")}</Badge>;
}

export function Metric({ label, value, detail, tone = "neutral" }: { label: string; value: number | string; detail: string; tone?: "neutral" | "risk" | "warn" | "ok" }) {
  const skin = tone === "risk" ? "border-risk bg-risk-soft" : tone === "warn" ? "border-warn bg-warn-soft" : tone === "ok" ? "border-ok bg-ok-soft" : "border-line bg-surface";
  return (
    <section className={`rounded-2xl border p-5 ${skin}`}>
      <p className="font-mono text-[10.5px] tracking-widest text-slate">{label.toUpperCase()}</p>
      <p className="mt-1 font-serif text-3xl font-semibold text-ink">{value}</p>
      <p className="mt-1 text-xs leading-snug text-slate">{detail}</p>
    </section>
  );
}

export function dateInputToEpoch(value: FormDataEntryValue | null): number {
  return Math.floor(new Date(String(value ?? "")).getTime() / 1000);
}

export function epochToDateInput(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}
