/**
 * Base fetch helper for API requests.
 * Uses relative URLs (Vite proxy handles /api and /agent in dev).
 */

import { getAuthToken } from './auth';

const API_BASE = '';

export interface ApiError {
  message: string;
  type?: string;
  code?: string;
}

export class ApiRequestError extends Error {
  status: number;
  body?: ApiError;

  constructor(message: string, status: number, body?: ApiError) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  const token = getAuthToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let body: ApiError | undefined;
    try {
      const json = await response.json();
      if (json?.error) {
        body = {
          message: json.error.message ?? response.statusText,
          type: json.error.type,
          code: json.error.code,
        };
      }
    } catch {
      // ignore
    }
    throw new ApiRequestError(
      body?.message ?? response.statusText,
      response.status,
      body,
    );
  }

  const contentType = response.headers.get('Content-Type');
  if (contentType?.includes('application/json')) {
    return response.json() as Promise<T>;
  }

  return undefined as T;
}
