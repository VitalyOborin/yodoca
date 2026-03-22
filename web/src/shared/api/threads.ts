/**
 * Thread API client.
 * Types align with docs/api/openapi.yaml.
 */

import { apiFetch } from './http';

export interface Thread {
  id: string;
  project_id: string | null;
  title: string | null;
  channel_id: string;
  created_at: number;
  last_active_at: number;
  is_archived: boolean;
}

export interface ThreadsResponse {
  threads: Thread[];
}

export interface ThreadDetailResponse {
  thread: Thread;
  history: Record<string, unknown>[];
}

export interface CreateThreadRequest {
  id?: string | null;
  project_id?: string | null;
  title?: string | null;
}

export interface UpdateThreadRequest {
  title?: string | null;
  project_id?: string | null;
  is_archived?: boolean | null;
}

export interface CreateThreadResponse {
  thread: Thread;
}

export interface UpdateThreadResponse {
  thread: Thread;
}

export interface OperationResult {
  success: boolean;
  message?: string | null;
}

export async function fetchThreads(): Promise<Thread[]> {
  const data = await apiFetch<ThreadsResponse>('/api/threads');
  return data.threads;
}

export async function fetchThread(id: string): Promise<ThreadDetailResponse> {
  return apiFetch<ThreadDetailResponse>(`/api/threads/${encodeURIComponent(id)}`);
}

export async function createThread(
  opts?: CreateThreadRequest,
): Promise<Thread> {
  const data = await apiFetch<CreateThreadResponse>('/api/threads', {
    method: 'POST',
    body: JSON.stringify(opts ?? {}),
  });
  return data.thread;
}

export async function updateThread(
  id: string,
  patch: UpdateThreadRequest,
): Promise<Thread> {
  const data = await apiFetch<UpdateThreadResponse>(
    `/api/threads/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(patch),
    },
  );
  return data.thread;
}

export async function deleteThread(id: string): Promise<OperationResult> {
  return apiFetch<OperationResult>(
    `/api/threads/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
}
