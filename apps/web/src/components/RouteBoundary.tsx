import type { ErrorComponentProps } from "@tanstack/react-router";
import { Link } from "@tanstack/react-router";
import { Button } from "@meshagent/ui";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-0 flex-1 place-items-center overflow-auto bg-paper p-6">
      <div className="flex max-w-lg flex-col items-center gap-3 rounded-2xl border border-line bg-surface p-7 text-center shadow-[var(--shadow)]">
        {children}
      </div>
    </main>
  );
}

export function RouteNotFound() {
  return (
    <Frame>
      <p className="font-mono text-[11px] tracking-wide text-slate">404 · ROUTE NOT FOUND</p>
      <h1 className="font-serif text-[24px] text-ink">That screen is not here</h1>
      <p className="text-[13.5px] leading-snug text-slate">
        The address may be outdated, or this deployment does not expose that
        view for this identity. No governed-memory data was changed.
      </p>
      <Link
        to="/"
        className="mt-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright"
      >
        Go to workspace
      </Link>
    </Frame>
  );
}

export function RouteError({ error, reset }: ErrorComponentProps) {
  const message = error instanceof Error ? error.message : "An unexpected route error occurred.";
  return (
    <Frame>
      <p className="font-mono text-[11px] tracking-wide text-risk">SCREEN ERROR</p>
      <h1 className="font-serif text-[24px] text-ink">This screen could not be shown</h1>
      <p role="alert" className="font-mono text-[12px] leading-snug text-slate">{message}</p>
      <div className="mt-2 flex flex-wrap justify-center gap-3">
        <Button variant="ghost" onClick={reset}>Try this screen again</Button>
        <Link
          to="/"
          className="rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright"
        >
          Go to workspace
        </Link>
      </div>
    </Frame>
  );
}
