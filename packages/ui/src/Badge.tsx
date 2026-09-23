import type { ReactNode } from "react";
import { cn } from "./cn";

type Tone = "neutral" | "ok" | "warn" | "risk" | "accent";

const tones: Record<Tone, string> = {
  neutral: "text-[var(--slate)] bg-[var(--surface-2)] border-[var(--line-2)]",
  ok: "text-[var(--ok)] bg-[var(--ok-soft)] border-[color-mix(in_srgb,var(--ok)_35%,white)]",
  warn: "text-[var(--warn)] bg-[var(--warn-soft)] border-[color-mix(in_srgb,var(--warn)_35%,white)]",
  risk: "text-[var(--risk)] bg-[var(--risk-soft)] border-[color-mix(in_srgb,var(--risk)_35%,white)]",
  accent: "text-[var(--accent)] bg-[var(--accent-soft)] border-[color-mix(in_srgb,var(--accent)_35%,white)]",
};

/** Status chip. Always render a word inside -- color never carries meaning alone. */
export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] leading-none",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}
