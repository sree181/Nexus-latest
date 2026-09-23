import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type Variant = "primary" | "ghost" | "danger";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const base =
  "inline-flex items-center justify-center gap-2 rounded-[10px] px-[18px] py-3 text-sm font-medium transition disabled:opacity-50 disabled:pointer-events-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]";

const variants: Record<Variant, string> = {
  primary: "bg-[var(--accent)] text-white hover:brightness-110",
  ghost: "bg-surface text-ink border border-[var(--line-2)] hover:bg-[var(--surface-2)]",
  danger: "bg-[var(--risk-soft)] text-[var(--risk)] border border-[var(--risk)] hover:brightness-105",
};

/** Real <button>, keyboard-focusable, accessible by default. */
export function Button({ variant = "primary", className, ...rest }: Props) {
  return <button className={cn(base, variants[variant], className)} {...rest} />;
}
