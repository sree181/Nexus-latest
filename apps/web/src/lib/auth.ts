/** Browser authentication for MeshAgent.
 *
 * In production, the API owns OIDC discovery, PKCE state, code exchange, and
 * the access token. The browser receives only an opaque HttpOnly SameSite
 * application-session cookie. Without an issuer/client build configuration the
 * development UI uses explicitly unverified local headers.
 */

const ISSUER = (import.meta.env.VITE_OIDC_ISSUER ?? "").replace(/\/$/, "");
const CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID ?? "";

/** True when the static product was built for company identity. */
export const oidcEnabled = Boolean(ISSUER && CLIENT_ID);

const LOCAL_USER = "meshagent.local.user";
const LOCAL_ROLE = "meshagent.local.role";

export interface BrowserSessionStatus {
  authenticated: boolean;
  expires_at: number | null;
  reauth_required: boolean;
}

function currentReturnTo(): string {
  const target =
    window.location.pathname + window.location.search + window.location.hash;
  return target.startsWith("/") && !target.startsWith("//") ? target : "/";
}

/** Start the API-owned Authorization Code + PKCE flow. */
export function signIn(): void {
  const url = new URL("/auth/login", window.location.origin);
  url.searchParams.set("return_to", currentReturnTo());
  window.location.assign(url.toString());
}

/** Ask the same-origin BFF whether its opaque session is usable. */
export async function browserSession(): Promise<BrowserSessionStatus> {
  const response = await fetch("/auth/session", {
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  if (response.status === 401) {
    return { authenticated: false, expires_at: null, reauth_required: true };
  }
  if (!response.ok) {
    throw new Error("MeshAgent could not verify the browser session.");
  }
  return response.json() as Promise<BrowserSessionStatus>;
}

export async function signOut(): Promise<void> {
  try {
    await fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
  } finally {
    window.location.assign("/");
  }
}

/** A REST 401 means the server session is no longer authoritative. Reloading
 * makes the sign-in boundary check `/auth/session`; a 403 never calls this. */
export function handleUnauthorized(): void {
  if (oidcEnabled) window.location.reload();
}

// -- local, explicitly unverified development identity -----------------------

export type LocalRole = "developer" | "analyst" | "ciso";

export function localIdentity(): { user: string; role: LocalRole } {
  const role = localStorage.getItem(LOCAL_ROLE);
  return {
    user: localStorage.getItem(LOCAL_USER) ?? "dev@localhost",
    role: role === "analyst" || role === "ciso" ? role : "developer",
  };
}

export function setLocalIdentity(user: string, role: LocalRole): void {
  localStorage.setItem(LOCAL_USER, user);
  localStorage.setItem(LOCAL_ROLE, role);
  window.location.assign("/");
}

/** Production browser requests rely on the same-origin HttpOnly cookie. */
export function authHeaders(): Record<string, string> {
  if (oidcEnabled) return {};
  const { user, role } = localIdentity();
  return { "X-MeshAgent-User": user, "X-MeshAgent-Role": role };
}

function base64url(bytes: Uint8Array): string {
  let value = "";
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Same-origin production WebSockets automatically carry the HttpOnly cookie.
 * Local mode uses an explicit unverified subprotocol identity. */
export function socketProtocols(): string[] | undefined {
  if (oidcEnabled) return undefined;
  const { user, role } = localIdentity();
  return [LOCAL, base64url(new TextEncoder().encode(user)), role];
}

export const BEARER = "meshagent.bearer";
export const LOCAL = "meshagent.local";
