// In-memory bearer token storage. Tokens are intentionally not persisted in
// localStorage/sessionStorage; a page reload requires a fresh login.
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string): void {
  accessToken = token;
}

export function clearAccessToken(): void {
  accessToken = null;
}
