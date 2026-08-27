const ACCESS_TOKEN_KEY = 'nexus_access_token';
const PKCE_VERIFIER_KEY = 'nexus_pkce_verifier';
const PKCE_STATE_KEY = 'nexus_pkce_state';

export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setAccessToken(token: string | null): void {
  if (token) sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
  else sessionStorage.removeItem(ACCESS_TOKEN_KEY);
}

export function getPkceVerifier(): string | null {
  return sessionStorage.getItem(PKCE_VERIFIER_KEY);
}

export function setPkceChallenge(state: string, verifier: string): void {
  sessionStorage.setItem(PKCE_STATE_KEY, state);
  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
}

export function expectedPkceState(): string | null {
  return sessionStorage.getItem(PKCE_STATE_KEY);
}

export function clearPkceChallenge(): void {
  sessionStorage.removeItem(PKCE_STATE_KEY);
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);
}

export function clearSession(): void {
  setAccessToken(null);
  clearPkceChallenge();
}
