import { getCurrentInstance, onBeforeUnmount } from 'vue';
import { apiFetch } from './http';

export type InboxStatus = 'active' | 'deleted';
export type InboxStatusFilter = InboxStatus | 'all';

export interface InboxItem {
  id: number;
  source_type: string;
  source_account: string | null;
  entity_type: string;
  external_id: string | null;
  title: string | null;
  occurred_at: number;
  ingested_at: number;
  status: InboxStatus;
  is_read: boolean;
  payload: Record<string, unknown>;
}

export interface InboxListResponse {
  items: InboxItem[];
  total: number;
  unread_count: number;
  limit: number;
  offset: number;
}

export interface InboxListQuery {
  source_type?: string;
  entity_type?: string;
  status?: InboxStatusFilter;
  unread?: boolean;
  limit?: number;
  offset?: number;
}

export interface InboxReadAllRequest {
  source_type?: string;
}

export interface OperationResult {
  success: boolean;
  message?: string | null;
}

export interface InboxStreamEvent {
  event: 'inbox.item.ingested';
  inbox_id: number;
  source_type: string;
  entity_type: string;
  title: string;
  change_type: 'created' | 'updated' | 'deleted' | 'duplicate';
  ingested_at: number;
}

function toQueryString(query: InboxListQuery): string {
  const params = new URLSearchParams();
  if (query.source_type) params.set('source_type', query.source_type);
  if (query.entity_type) params.set('entity_type', query.entity_type);
  if (query.status) params.set('status', query.status);
  if (typeof query.unread === 'boolean') {
    params.set('unread', query.unread ? 'true' : 'false');
  }
  if (typeof query.limit === 'number') params.set('limit', String(query.limit));
  if (typeof query.offset === 'number') params.set('offset', String(query.offset));
  const str = params.toString();
  return str ? `?${str}` : '';
}

export async function fetchInbox(query: InboxListQuery = {}): Promise<InboxListResponse> {
  return apiFetch<InboxListResponse>(`/api/inbox${toQueryString(query)}`);
}

export async function fetchInboxItem(id: number): Promise<InboxItem> {
  return apiFetch<InboxItem>(`/api/inbox/${id}`);
}

export async function markInboxRead(id: number): Promise<OperationResult> {
  return apiFetch<OperationResult>(`/api/inbox/${id}/read`, {
    method: 'POST',
  });
}

export async function markAllInboxRead(
  payload: InboxReadAllRequest = {},
): Promise<OperationResult> {
  return apiFetch<OperationResult>('/api/inbox/read-all', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deleteInboxItem(id: number): Promise<OperationResult> {
  return apiFetch<OperationResult>(`/api/inbox/${id}`, {
    method: 'DELETE',
  });
}

export interface UseInboxStreamOptions {
  onEvent: (event: InboxStreamEvent) => void;
  onError?: (error: Event) => void;
}

export function useInboxStream(options: UseInboxStreamOptions): () => void {
  const source = new EventSource('/api/inbox/stream');

  source.onmessage = (event) => {
    try {
      const parsed = JSON.parse(event.data) as InboxStreamEvent;
      if (parsed?.event === 'inbox.item.ingested') {
        options.onEvent(parsed);
      }
    } catch {
      // ignore malformed event frames
    }
  };

  source.onerror = (event) => {
    options.onError?.(event);
  };

  const stop = () => {
    source.close();
  };

  if (getCurrentInstance()) {
    onBeforeUnmount(stop);
  }

  return stop;
}
