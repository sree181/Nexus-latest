import type { ReactNode } from "react";

import type { Capability } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";

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
  detail = "Your identity is valid, but it does not include the capability required for this view.",
}: RequiresCapabilityProps) {
  const identity = useIdentity();

  if (identity.loading) {
    return (
      <main className="grid min-h-0 flex-1 place-items-center bg-paper p-8">
        <p role="status" className="font-mono text-sm text-slate">
          Verifying workspace access…
        </p>
      </main>
    );
  }

  if (identity.failed || !identity.me) {
    return (
      <main className="grid min-h-0 flex-1 place-items-center bg-paper p-8">
        <section role="alert" className="w-full max-w-xl rounded-2xl border border-risk bg-surface p-6 shadow-[var(--shadow)]">
          <p className="font-mono text-[11px] tracking-widest text-risk">IDENTITY UNAVAILABLE</p>
          <h1 className="mt-2 font-serif text-2xl font-semibold text-ink">Workspace access could not be verified</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate">
            MeshAgent could not confirm your role or capabilities. No workspace has been opened. Retry when the identity service is available.
          </p>
        </section>
      </main>
    );
  }

  if (!identity.hasCapability(capability)) {
    return (
      <main className="grid min-h-0 flex-1 place-items-center bg-paper p-8">
        <section className="w-full max-w-xl rounded-2xl border border-line bg-surface p-6 shadow-[var(--shadow)]">
          <p className="font-mono text-[11px] tracking-widest text-slate">ACCESS LIMITED</p>
          <h1 className="mt-2 font-serif text-2xl font-semibold text-ink">{title}</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate">{detail}</p>
          <p className="mt-4 font-mono text-[11px] text-slate">Required capability: {capability}</p>
        </section>
      </main>
    );
  }

  return <>{children}</>;
}

export function canAccess(capabilities: readonly Capability[], capability: Capability): boolean {
  return capabilities.includes(capability);
}
