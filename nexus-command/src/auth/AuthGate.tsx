import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { beginOperatorSignIn, beginOperatorSignOut, completeOperatorSignIn, loadAuthConfig, type AuthConfig } from './oidcClient';
import { clearSession, getAccessToken } from './session';

interface AuthContextValue {
  config: AuthConfig | null;
  signedIn: boolean;
  signOut: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  config: null,
  signedIn: false,
  signOut: () => undefined,
});

export function AuthGate({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [signedIn, setSignedIn] = useState(Boolean(getAccessToken()));
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await loadAuthConfig();
        if (cancelled) return;
        setConfig(next);
        const params = new URLSearchParams(window.location.search);
        const oauthError = params.get('error_description') || params.get('error');
        if (oauthError) {
          setError(oauthError);
          window.history.replaceState({}, document.title, window.location.pathname);
          return;
        }
        const code = params.get('code');
        const state = params.get('state');
        if (code && state && next.loginRequired) {
          await completeOperatorSignIn(next, code, state);
          window.history.replaceState({}, document.title, window.location.pathname);
          setSignedIn(true);
        } else {
          setSignedIn(!next.loginRequired || Boolean(getAccessToken()));
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Identity configuration failed');
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async () => {
    if (!config) return;
    setError(null);
    try {
      await beginOperatorSignIn(config);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign-in could not start');
    }
  }, [config]);

  const signOut = useCallback(() => {
    clearSession();
    if (config) beginOperatorSignOut(config);
    else window.location.assign('/');
  }, [config]);

  const value = useMemo(() => ({ config, signedIn, signOut }), [config, signedIn, signOut]);

  if (busy) {
    return (
      <main className="system-screen">
        <div className="system-panel">
          <div className="loading-mark" />
          <h1>Checking who you are</h1>
          <p>A named person has to sign in before the desk can open.</p>
        </div>
      </main>
    );
  }

  if (config && config.loginRequired && !signedIn) {
    return (
      <main className="system-screen">
        <div className="system-panel">
          <span className="system-code">Sign in</span>
          <h1>Named operator required</h1>
          <p>
            Decisions are recorded against a real person. Sign in with your assigned account. This is not a
            simulation.
          </p>
          {error && <div className="form-error" role="alert">{error}</div>}
          {!config.configured && (
            <p>The identity provider is not configured on this deployment. Set OIDC issuer, audience, JWKS, and client ID on nexus-api.</p>
          )}
          <button className="button button--approve" type="button" onClick={() => void signIn()} disabled={!config.configured}>
            Sign in
          </button>
        </div>
      </main>
    );
  }

  if (error && !signedIn) {
    return (
      <main className="system-screen">
        <div className="system-panel system-panel--error">
          <span className="system-code">Unavailable</span>
          <h1>We could not verify who you are</h1>
          <p>{error}</p>
        </div>
      </main>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
