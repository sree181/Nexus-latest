import { useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";

import type { Me, Role } from "../lib/api";
import { useIdentity } from "../lib/useIdentity";
import { StartTask } from "./StartTask";

export const roleDestination: Record<Role, "/" | "/analyst/queue" | "/ciso/overview"> = {
  developer: "/",
  analyst: "/analyst/queue",
  ciso: "/ciso/overview",
};

export function landingForIdentity(me: Pick<Me, "role" | "primary_role">): string | null {
  if (me.role !== me.primary_role) return null;
  return roleDestination[me.primary_role] ?? null;
}

export function RoleLanding() {
  const identity = useIdentity();
  const navigate = useNavigate();
  const destination = identity.me ? landingForIdentity(identity.me) : null;

  useEffect(() => {
    if (destination) void navigate({ to: destination, replace: true });
  }, [destination, navigate]);

  if (identity.me && destination === "/") return <StartTask />;

  if (identity.loading || destination) {
    return (
      <main className="grid min-h-0 flex-1 place-items-center bg-paper p-8">
        <p role="status" className="font-mono text-sm text-slate">
          Opening your workspace…
        </p>
      </main>
    );
  }

  return (
    <main className="grid min-h-0 flex-1 place-items-center bg-paper p-8">
      <section role="alert" className="w-full max-w-xl rounded-2xl border border-risk bg-surface p-6 shadow-[var(--shadow)]">
        <p className="font-mono text-[11px] tracking-widest text-risk">IDENTITY NOT ACCEPTED</p>
        <h1 className="mt-2 font-serif text-2xl font-semibold text-ink">No workspace was opened</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate">
          {identity.failed
            ? "MeshAgent could not retrieve your server-managed role and capabilities. Retry after the identity service is available."
            : "The identity response did not contain one consistent supported role. Ask an administrator to review your role mapping."}
        </p>
      </section>
    </main>
  );
}
