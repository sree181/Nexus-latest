/** Signing in, and carrying the result on every request.
 *
 *  Two modes, matching the API. With `VITE_OIDC_ISSUER` set this runs a real
 *  Authorization Code flow with PKCE against the company's provider and sends
 *  the access token as a bearer. Without it there is no provider to talk to,
 *  so the client simply asserts who it is and the API marks that identity
 *  unverified — which the UI has to show rather than dress up as a login.
 *
 *  No client secret is involved and none can be: this is a public client in a
 *  browser, which is exactly what PKCE exists for.
 */

const ISSUER = (import.meta.env.VITE_OIDC_ISSUER ?? "").replace(/\/$/, "");
const CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID ?? "";
const SCOPE = import.meta.env.VITE_OIDC_SCOPE ?? "openid profile email";

/** True when a provider is configured and a real sign-in is required. */
export const oidcEnabled = Boolean(ISSUER && CLIENT_ID);

const VERIFIER = "meshagent.pkce.verifier";
const STATE = "meshagent.pkce.state";
const RETURN_TO = "meshagent.pkce.return";
const TOKEN = "meshagent.access.token";

/** Who you claim to be when there is no provider. Local only. */
const LOCAL_USER = "meshagent.local.user";
const LOCAL_ROLE = "meshagent.local.role";

export interface Endpoints {
  authorization_endpoint: string;
  token_endpoint: string;
}

let discovered: Promise<Endpoints> | undefined;

/** The provider's endpoints, from its own discovery document — the paths
 *  differ between Entra, Okta, Google and Keycloak, so none is assumed. */
function endpoints(): Promise<Endpoints> {
  discovered ??= fetch(`${ISSUER}/.well-known/openid-configuration`)
    .then((res) => {
      if (!res.ok) throw new Error(`identity provider returned ${res.status}`);
      return res.json() as Promise<Endpoints>;
    });
  return discovered;
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomVerifier(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64url(new Uint8Array(digest));
}

function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

/** The callback state is short and randomly generated. This comparison avoids
 * an early-exit character comparison, which is the closest useful browser-side
 * equivalent of constant-time validation. The provider still validates the
 * authorization code and PKCE verifier at its token endpoint. */
export function constantTimeStateEquals(
  expected: string | null,
  received: string | null | undefined,
): boolean {
  if (!expected || !received) return false;
  let difference = expected.length ^ received.length;
  // Always walk the expected state length. State is fixed length in this app,
  // so a mismatched input cannot reveal which character first differed.
  for (let index = 0; index < expected.length; index += 1) {
    difference |= expected.charCodeAt(index) ^ (received.charCodeAt(index) || 0);
  }
  return difference === 0;
}

/** Remove transient OAuth artifacts after a completed, rejected, or abandoned
 * authorization. Keeping a verifier or state around invites callback replay
 * and can accidentally bind a later login to an earlier return location. */
export function clearSignInArtifacts(): void {
  sessionStorage.removeItem(VERIFIER);
  sessionStorage.removeItem(STATE);
  sessionStorage.removeItem(RETURN_TO);
}

function safeReturnTo(): string {
  const back = sessionStorage.getItem(RETURN_TO) ?? "/";
  // It was written locally, but keep this boundary strict in case storage was
  // modified: only an in-app absolute path is a valid post-login destination.
  return back.startsWith("/") && !back.startsWith("//") ? back : "/";
}

/** Send the browser to the provider. Returns a promise that never resolves,
 *  because the page is navigating away. */
export async function signIn(): Promise<never> {
  const verifier = randomVerifier();
  const state = base64url(crypto.getRandomValues(new Uint8Array(16)));
  // An interrupted attempt must not leave a usable verifier/state pair behind.
  clearSignInArtifacts();
  sessionStorage.setItem(VERIFIER, verifier);
  sessionStorage.setItem(RETURN_TO, window.location.pathname + window.location.search);
  sessionStorage.setItem(STATE, state);

  const { authorization_endpoint } = await endpoints();
  const url = new URL(authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", CLIENT_ID);
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("scope", SCOPE);
  url.searchParams.set("code_challenge", await challenge(verifier));
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", state);
  window.location.assign(url.toString());
  return new Promise<never>(() => {});
}

/** Exchange the code the provider redirected back with. Returns where the
 *  user was before they were sent to sign in. */
export async function completeSignIn(
  code: string,
  state: string | null | undefined,
): Promise<string> {
  const verifier = sessionStorage.getItem(VERIFIER);
  const expectedState = sessionStorage.getItem(STATE);
  const back = safeReturnTo();

  try {
    if (!verifier) throw new Error("no sign-in was in progress in this tab");
    if (!constantTimeStateEquals(expectedState, state)) {
      throw new Error("sign-in response could not be verified; please try again");
    }

    const { token_endpoint } = await endpoints();
    const res = await fetch(token_endpoint, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        client_id: CLIENT_ID,
        redirect_uri: redirectUri(),
        code_verifier: verifier,
      }),
    });
    if (!res.ok) throw new Error(`the provider rejected the sign-in (${res.status})`);

    const body = (await res.json()) as { access_token?: string };
    if (!body.access_token) throw new Error("the provider returned no access token");

    sessionStorage.setItem(TOKEN, body.access_token);
    return back;
  } finally {
    // Consume PKCE artifacts even if state validation or token exchange fails.
    clearSignInArtifacts();
  }
}

export function accessToken(): string | null {
  return oidcEnabled ? sessionStorage.getItem(TOKEN) : null;
}

export function signedIn(): boolean {
  return !oidcEnabled || accessToken() !== null;
}

export function signOut(): void {
  sessionStorage.removeItem(TOKEN);
  clearSignInArtifacts();
  window.location.assign("/");
}

// -- the local, unauthenticated mode ------------------------------------------

export type LocalRole = "developer" | "analyst";

export function localIdentity(): { user: string; role: LocalRole } {
  const role = localStorage.getItem(LOCAL_ROLE);
  return {
    user: localStorage.getItem(LOCAL_USER) ?? "dev@localhost",
    role: role === "analyst" ? "analyst" : "developer",
  };
}

export function setLocalIdentity(user: string, role: LocalRole): void {
  localStorage.setItem(LOCAL_USER, user);
  localStorage.setItem(LOCAL_ROLE, role);
  window.location.reload();
}

/** Identity headers for a request. A bearer token when signed in; the
 *  asserted identity when there is no provider to sign in to. */
export function authHeaders(): Record<string, string> {
  if (oidcEnabled) {
    const token = accessToken();
    return token ? { authorization: `Bearer ${token}` } : {};
  }
  const { user, role } = localIdentity();
  return { "X-MeshAgent-User": user, "X-MeshAgent-Role": role };
}

/** Identity for a WebSocket, which cannot carry an Authorization header.
 *
 *  It rides in the subprotocol list instead, which — unlike a query
 *  parameter — stays out of access logs. Subprotocol values are HTTP tokens,
 *  so they cannot contain `@`; the asserted user is base64url encoded for
 *  that reason, not for secrecy, of which there is none in local mode. */
export function socketProtocols(): string[] | undefined {
  if (oidcEnabled) {
    const token = accessToken();
    return token ? [BEARER, token] : undefined;
  }
  const { user, role } = localIdentity();
  return [LOCAL, base64url(new TextEncoder().encode(user)), role];
}

export const BEARER = "meshagent.bearer";
export const LOCAL = "meshagent.local";
