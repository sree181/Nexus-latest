import { clearPkceChallenge, expectedPkceState, getPkceVerifier, rememberReturnTo, setAccessToken, setPkceChallenge } from './session';

export interface AuthConfig {
  configured: boolean;
  loginRequired: boolean;
  issuer: string | null;
  clientId: string | null;
  audience: string | null;
  scopes: string;
}

function randomUrlValue(bytes = 32): string {
  const values = new Uint8Array(bytes);
  crypto.getRandomValues(values);
  return btoa(String.fromCharCode(...values)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function issuerOrigin(issuer: string): string {
  return new URL(issuer).origin;
}

function authorizationEndpoint(issuer: string): string {
  return `${issuer.trim().replace(/\/+$/, '')}/authorize`;
}

function tokenEndpoint(issuer: string): string {
  return `${issuer.trim().replace(/\/+$/, '')}/oauth/token`;
}

function logoutEndpoint(issuer: string): string {
  return `${issuer.trim().replace(/\/+$/, '')}/v2/logout`;
}

export async function loadAuthConfig(): Promise<AuthConfig> {
  const response = await fetch('/api/v1/auth/config', { cache: 'no-store' });
  const payload = await response.json() as { data: AuthConfig } | { error: { message: string } };
  if (!response.ok || 'error' in payload) {
    throw new Error('error' in payload ? payload.error.message : 'Unable to load identity configuration');
  }
  return payload.data;
}

export async function beginOperatorSignIn(config: AuthConfig): Promise<void> {
  if (!config.issuer || !config.clientId || !config.audience) {
    throw new Error('OIDC issuer, client ID, and audience are required');
  }
  const state = randomUrlValue(16);
  const verifier = randomUrlValue(32);
  const challenge = await sha256Base64Url(verifier);
  setPkceChallenge(state, verifier);
  rememberReturnTo(window.location.pathname);
  const params = new URLSearchParams({
    client_id: config.clientId,
    redirect_uri: window.location.origin,
    response_type: 'code',
    scope: config.scopes,
    audience: config.audience,
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  window.location.assign(`${authorizationEndpoint(config.issuer)}?${params.toString()}`);
}

export async function completeOperatorSignIn(config: AuthConfig, code: string, state: string): Promise<void> {
  if (!config.issuer || !config.clientId) throw new Error('OIDC client is not configured');
  if (state !== expectedPkceState()) throw new Error('Sign-in state did not match this browser session');
  const verifier = getPkceVerifier();
  if (!verifier) throw new Error('Sign-in verifier is missing; start sign-in again');
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    code,
    redirect_uri: window.location.origin,
    code_verifier: verifier,
  });
  const response = await fetch(tokenEndpoint(config.issuer), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const payload = await response.json() as { access_token?: string; error_description?: string };
  if (!response.ok || !payload.access_token) {
    throw new Error(payload.error_description || 'Identity provider did not return an access token');
  }
  setAccessToken(payload.access_token);
  clearPkceChallenge();
}

export function beginOperatorSignOut(config: AuthConfig): void {
  const returnTo = window.location.origin;
  if (config.issuer && config.clientId) {
    const params = new URLSearchParams({ client_id: config.clientId, returnTo });
    window.location.assign(`${logoutEndpoint(config.issuer)}?${params.toString()}`);
    return;
  }
  window.location.assign(returnTo);
}

export function issuerConnectOrigin(issuer: string | null): string | null {
  if (!issuer) return null;
  try {
    return issuerOrigin(issuer);
  } catch {
    return null;
  }
}
