const RUNTIME_TOKEN_KEYS = ['yodoca.api_token', 'yodoca.api_key'] as const;

function readStorageToken(storage: Storage): string {
  for (const key of RUNTIME_TOKEN_KEYS) {
    const token = storage.getItem(key);
    if (token) return token;
  }
  return '';
}

function readRuntimeToken(): string {
  if (typeof window === 'undefined') return '';

  try {
    return readStorageToken(window.sessionStorage) || readStorageToken(window.localStorage);
  } catch {
    return '';
  }
}

/**
 * Local embedded client auth strategy:
 * 1) Runtime token from storage (allows custom clients without rebuild)
 * 2) VITE_API_KEY for local development convenience
 */
export function getAuthToken(): string {
  return readRuntimeToken() || (import.meta.env.VITE_API_KEY ?? '');
}

