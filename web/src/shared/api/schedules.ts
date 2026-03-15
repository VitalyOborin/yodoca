import { apiFetch } from './http';

export type ScheduleType = 'one_shot' | 'recurring';
export type ScheduleStatus =
  | 'scheduled'
  | 'fired'
  | 'cancelled'
  | 'active'
  | 'paused';
export type ScheduleTopic =
  | 'system.user.notify'
  | 'system.agent.task'
  | 'system.agent.background';

export interface ScheduleItem {
  id: number;
  type: ScheduleType;
  topic: ScheduleTopic;
  message: string | null;
  channel_id: string | null;
  payload: Record<string, unknown>;
  fires_at_iso: string;
  status: ScheduleStatus;
  cron_expr: string | null;
  every_seconds: number | null;
  until_iso: string | null;
  created_at: number;
}

export interface ScheduleListResponse {
  schedules: ScheduleItem[];
  count: number;
}

export interface CreateOnceRequest {
  topic: ScheduleTopic;
  message: string;
  channel_id?: string | null;
  delay_seconds?: number;
  at_iso?: string;
}

export interface CreateRecurringRequest {
  topic: ScheduleTopic;
  message: string;
  channel_id?: string | null;
  cron?: string;
  every_seconds?: number;
  until_iso?: string | null;
}

export interface UpdateRecurringRequest {
  cron?: string | null;
  every_seconds?: number | null;
  until_iso?: string | null;
  status?: 'active' | 'paused';
}

export interface ScheduleOnceResponse {
  success: boolean;
  schedule_id: number;
  topic: string;
  fires_in_seconds: number;
  status: 'scheduled';
  error?: string | null;
}

export interface ScheduleRecurringResponse {
  success: boolean;
  schedule_id: number;
  next_fire_iso: string;
  status: 'created';
  error?: string | null;
}

export interface UpdateScheduleResponse {
  success: boolean;
  schedule_id: number;
  next_fire_iso: string;
  message?: string | null;
  error?: string | null;
}

export interface OperationResult {
  success: boolean;
  message?: string | null;
}

export async function fetchSchedules(status?: ScheduleStatus): Promise<ScheduleItem[]> {
  const url = status ? `/api/schedules?status=${status}` : '/api/schedules';
  const data = await apiFetch<ScheduleListResponse>(url);
  return data.schedules;
}

export async function createOnceSchedule(
  payload: CreateOnceRequest,
): Promise<ScheduleOnceResponse> {
  return apiFetch<ScheduleOnceResponse>('/api/schedules/once', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function createRecurringSchedule(
  payload: CreateRecurringRequest,
): Promise<ScheduleRecurringResponse> {
  return apiFetch<ScheduleRecurringResponse>('/api/schedules/recurring', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deleteSchedule(
  type: ScheduleType,
  id: number,
): Promise<OperationResult> {
  return apiFetch<OperationResult>(`/api/schedules/${type}/${id}`, {
    method: 'DELETE',
  });
}

export async function updateRecurringSchedule(
  id: number,
  payload: UpdateRecurringRequest,
): Promise<UpdateScheduleResponse> {
  return apiFetch<UpdateScheduleResponse>(`/api/schedules/recurring/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}
