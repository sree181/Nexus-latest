import type { ErrorComponentProps } from "@tanstack/react-router";
import { Link } from "@tanstack/react-router";
import { Button } from "@meshagent/ui";

import { StateFrame } from "./WorkflowVisual";

function Frame({ children }: { children: React.ReactNode }) {
  return <main className="workflow-page grid place-items-center overflow-auto p-6"><div className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-[var(--workflow-shadow)]">{children}</div></main>;
}

export function RouteNotFound() {
  return <Frame><StateFrame kind="empty" title="That screen is not here" detail="The address may be outdated, or this deployment does not expose that view for this identity. No governed data was changed." action={<Link to="/" className="workflow-primary-action max-w-[220px]">Go to workspace</Link>} /></Frame>;
}

export function RouteError({ error, reset }: ErrorComponentProps) {
  const message = error instanceof Error ? error.message : "An unexpected route error occurred.";
  return <Frame><StateFrame kind="error" title="This screen could not be shown" detail={message} action={<div className="flex flex-wrap justify-center gap-3"><Button variant="ghost" onClick={reset}>Try this screen again</Button><Link to="/" className="workflow-primary-action w-auto">Go to workspace</Link></div>} /></Frame>;
}
