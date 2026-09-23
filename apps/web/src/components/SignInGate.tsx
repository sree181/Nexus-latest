import { useEffect, useState } from "react";

import {
  clearSignInArtifacts,
  completeSignIn,
  localIdentity,
  oidcEnabled,
  setLocalIdentity,
  signIn,
  signedIn,
} from "../lib/auth";

/** Stands between the app and anyone who has not identified themselves.
 *
 *  With a provider configured that means a real sign-in. Without one there
 *  is nothing to sign in to, so it steps aside — and the sidebar says in as
 *  many words that the identity is asserted rather than verified. The one
 *  thing it must never do is imply a login happened when none did.
 */
export function SignInGate({ children }: { children: React.ReactNode }) {
  const [error, setError] = useState<string | null>(null);
  const [returning, setReturning] = useState(
    () => {
      const params = new URLSearchParams(window.location.search);
      return params.has("code") || params.has("error");
    },
  );

  useEffect(() => {
    if (!returning) return;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const providerError = params.get("error");
    if (providerError) {
      const description = params.get("error_description");
      clearSignInArtifacts();
      setError(
        description
          ? `The identity provider did not complete sign-in: ${description}`
          : `The identity provider did not complete sign-in (${providerError}).`,
      );
      setReturning(false);
      window.history.replaceState({}, document.title, "/");
      return;
    }
    if (!code) {
      clearSignInArtifacts();
      setError("The sign-in response did not include an authorization code.");
      setReturning(false);
      window.history.replaceState({}, document.title, "/");
      return;
    }
    completeSignIn(code, params.get("state"))
      .then((back) => window.location.replace(back))
      .catch((exc: unknown) => {
        setError(exc instanceof Error ? exc.message : String(exc));
        setReturning(false);
        window.history.replaceState({}, document.title, "/");
      });
  }, [returning]);

  if (returning) return <Waiting>Completing sign-in…</Waiting>;

  if (oidcEnabled && !signedIn()) {
    return (
      <Panel
        title="Sign in to MeshAgent"
        body="Memory here is attributed to the person whose agent wrote it, so
              the API will not serve anything until it knows who you are."
        error={error}
      >
        <button
          type="button"
          onClick={() => void signIn()}
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
    <div className="flex h-screen items-center justify-center bg-paper px-6">
      <div className="w-full max-w-md">
        <h1 className="font-serif text-2xl font-semibold text-ink">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-dim">{body}</p>
        {error ? (
          <p role="alert" className="mt-4 rounded-lg bg-danger-wash px-3 py-2 text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="mt-6">{children}</div>
      </div>
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
        setLocalIdentity(
          String(form.get("user") ?? "").trim() || "dev@localhost",
          form.get("role") === "analyst" ? "analyst" : "developer",
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
          <option value="analyst">Security office</option>
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
