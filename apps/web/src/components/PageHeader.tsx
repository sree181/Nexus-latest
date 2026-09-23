import type { ReactNode } from "react";

/** The 68px screen header every route wears: section / title on the left,
 *  whatever the screen wants to state about its data on the right. */
export function PageHeader({
  section,
  title,
  meta,
}: {
  section: string;
  title: string;
  meta?: ReactNode;
}) {
  return (
    <header className="flex min-h-[68px] shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-4 py-3 sm:px-8">
      <h1 className="min-w-0 text-[15px] font-normal text-slate">
        {section} <span className="text-slate-3">/</span>{" "}
        <span className="font-medium text-ink">{title}</span>
      </h1>
      {meta !== undefined && (
        <div className="flex min-w-0 flex-wrap items-center gap-2 font-mono text-xs text-slate sm:gap-3">
          {meta}
        </div>
      )}
    </header>
  );
}

/** Says out loud where a screen's data came from, so curated sample data is
 *  never mistaken for engine-recorded memory. */
export function SourceNote({ sample }: { sample: boolean | undefined }) {
  if (sample === undefined) return null;
  return (
    <span className="font-mono text-xs text-slate">
      {sample ? "sample data" : "engine memory"}
    </span>
  );
}
