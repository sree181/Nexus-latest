import type { ReactNode } from "react";

import type { Capability } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";
import { StateFrame } from "./WorkflowVisual";

interface RequiresCapabilityProps {
  capability: Capability;
  children: ReactNode;
  title?: string;
  detail?: string;
}

/**
 * Keeps route rendering aligned with the capabilities returned by `/api/me`.
 * The API remains the security boundary; this guard avoids starting protected
 * queries before the server has established who the caller is and what they may
 * do.
 */
export function RequiresCapability({
  capability,
  children,
  title = "This workspace is not available to your role",
  detail = "Your verified identity does not include the capability required for this view.",
}: RequiresCapabilityProps) {
  const identity = useIdentity();

  if (identity.loading) return <main className="workflow-page grid place-items-center p-6"><StateFrame kind="loading" title="Verifying workspace access" detail="No protected query is started until the server confirms your capabilities." /></main>;

  if (identity.failed || !identity.me) return <main className="workflow-page grid place-items-center p-6"><div className="w-full max-w-xl rounded-2xl border border-risk bg-surface shadow-[var(--workflow-shadow)]"><StateFrame kind="error" title="Workspace access could not be verified" detail="meshAgent could not confirm your role or capabilities. No workspace has been opened. Retry when the identity service is available." /></div></main>;

  if (!identity.hasCapability(capability)) return <main className="workflow-page grid place-items-center p-6"><div className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-[var(--workflow-shadow)]"><StateFrame kind="permission" title={title} detail={`${detail} Required capability: ${capability}.`} /></div></main>;

  return <>{children}</>;
}

export function canAccess(capabilities: readonly Capability[], capability: Capability): boolean {
  return capabilities.includes(capability);
}
