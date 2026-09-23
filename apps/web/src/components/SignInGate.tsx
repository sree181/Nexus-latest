import { useEffect, useState } from "react";

import {
  browserSession,
  localIdentity,
  oidcEnabled,
  setLocalIdentity,
  signIn,
} from "../lib/auth";

/** Stands between the app and anyone who has not identified themselves.
 *
 *  With a provider configured that means a real sign-in. Without one there
 *  is nothing to sign in to, so it steps aside — and the sidebar says in as
 *  many words that the identity is asserted rather than verified. The one
 *  thing it must never do is imply a login happened when none did.
 */
export function SignInGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<"checking" | "signed-in" | "signed-out">(
    oidcEnabled ? "checking" : "signed-in",
  );
  const [error, setError] = useState<string | null>(() => {
    const value = new URLSearchParams(window.location.search).get("auth_error");
    return value ? "The company sign-in could not be completed. Start a new attempt or contact your identity administrator." : null;
  });

  useEffect(() => {
    if (!oidcEnabled) return;
    browserSession()
      .then((session) => setStatus(session.authenticated ? "signed-in" : "signed-out"))
      .catch((exc: unknown) => {
        setError(exc instanceof Error ? exc.message : "MeshAgent could not verify the browser session.");
        setStatus("signed-out");
      });
    if (window.location.search.includes("auth_error=")) {
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  if (status === "checking") return <Waiting>Checking company session…</Waiting>;

  if (oidcEnabled && status === "signed-out") {
    return (
      <Panel
        title="Sign in to MeshAgent"
        body="Use your company identity. Your role and permissions are confirmed by the API before a workspace opens."
        error={error}
      >
        <button
          type="button"
          onClick={signIn}
          className="rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-bright"
        >
          Sign in with your company account
        </button>
      </Panel>
    );
  }

  return <>{children}</>;
}

function Waiting({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen items-center justify-center bg-paper">
      <p role="status" className="font-mono text-sm text-ink-dim">
        {children}
      </p>
    </div>
  );
}

function Panel({
  title,
  body,
  error,
  children,
}: {
  title: string;
  body: string;
  error: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-paper p-4 sm:p-7 lg:p-10">
      <main className="mx-auto grid min-h-[calc(100vh-2rem)] max-w-6xl overflow-hidden rounded-[24px] border border-line bg-surface shadow-[0_24px_80px_rgba(19,26,34,0.12)] sm:min-h-[calc(100vh-3.5rem)] lg:grid-cols-[0.92fr_1.08fr]">
        <section className="relative flex flex-col justify-between overflow-hidden bg-rail p-7 text-rail-ink sm:p-10 lg:p-12">
          <div aria-hidden="true" className="absolute -right-24 -top-20 h-72 w-72 rounded-full border border-accent-bright/20 bg-accent/10 blur-2xl" />
          <div className="relative">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent shadow-lg shadow-black/20">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /><path d="M12 7v4M12 11l-6 6M12 11l6 6" /></svg>
              </div>
              <div><p className="font-serif text-xl font-semibold">MeshAgent</p><p className="font-mono text-[10px] tracking-[0.18em] text-rail-ink-faint">SECURITY WORKBENCH</p></div>
            </div>
            <h2 className="mt-16 max-w-md font-serif text-3xl font-semibold leading-tight sm:text-4xl">One verified entry point for engineering evidence and security decisions.</h2>
            <p className="mt-4 max-w-md text-sm leading-relaxed text-rail-ink-dim">The workspace shown after sign-in is determined by server-issued capabilities, not by the browser.</p>
          </div>
          <dl className="relative mt-12 grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            {[
              ["Identity", "OIDC + PKCE"],
              ["Access", "Role scoped"],
              ["Actions", "Audit recorded"],
            ].map(([label, value]) => <div key={label} className="rounded-xl border border-rail-line bg-white/[0.035] px-4 py-3"><dt className="font-mono text-[10px] tracking-widest text-rail-ink-faint">{label.toUpperCase()}</dt><dd className="mt-1 text-sm text-rail-ink">{value}</dd></div>)}
          </dl>
        </section>
        <section className="flex items-center p-7 sm:p-12 lg:p-16">
          <div className="w-full max-w-md">
            <p className="font-mono text-[10.5px] tracking-[0.18em] text-accent">COMPANY ACCESS</p>
            <h1 className="mt-3 font-serif text-3xl font-semibold text-ink sm:text-4xl">{title}</h1>
            <p className="mt-3 text-sm leading-relaxed text-slate">{body}</p>
            {error ? (
              <p role="alert" className="mt-5 rounded-xl border border-risk bg-risk-soft px-4 py-3 text-sm text-risk">{error}</p>
            ) : null}
            <div className="mt-7">{children}</div>
            <div className="mt-8 border-t border-line pt-5">
              <p className="text-xs leading-relaxed text-slate">Only authorized Developer, Analyst, and CISO roles are accepted. Privileged group conflicts are denied rather than resolved automatically.</p>
            </div>
          </div>
        </section>
      </main>
      </div>
  );
}

/** Switches which local identity this browser asserts. Only rendered when
 *  no provider is configured — with one, roles come from signed claims and
 *  are not the client's to choose. */
export function LocalIdentityPicker() {
  const { user, role } = localIdentity();
  if (oidcEnabled) return null;
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const form = new FormData(e.currentTarget);
        const requestedRole = String(form.get("role") ?? "developer");
        setLocalIdentity(
          String(form.get("user") ?? "").trim() || "dev@localhost",
          requestedRole === "analyst" || requestedRole === "ciso"
            ? requestedRole
            : "developer",
        );
      }}
    >
      <div className="flex flex-col gap-1">
        <label htmlFor="local-user" className="font-mono text-[11px] tracking-widest text-ink-faint">
          WORKING AS
        </label>
        <input
          id="local-user"
          name="user"
          defaultValue={user}
          className="rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="local-role" className="font-mono text-[11px] tracking-widest text-ink-faint">
          ROLE
        </label>
        <select
          id="local-role"
          name="role"
          defaultValue={role}
          className="rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
        >
          <option value="developer">Developer</option>
          <option value="analyst">Security analyst</option>
          <option value="ciso">CISO</option>
        </select>
      </div>
      <button
        type="submit"
        className="rounded-lg border border-line px-3 py-2 text-sm text-ink transition hover:bg-wash focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        Switch
      </button>
    </form>
  );
}
