import { apiFetch } from './http';

export type TaskStatus =
  | 'pending'
  | 'blocked'
  | 'running'
  | 'retry_scheduled'
  | 'waiting_subtasks'
  | 'human_review'
  | 'done'
  | 'failed'
  | 'cancelled';

export type TaskStatusFilter = TaskStatus | 'active' | 'all';

export interface TaskItem {
  task_id: string;
  status: TaskStatus;
  agent_id: string;
  goal: string;
  step: number;
  max_steps: number;
  attempt_no: number;
  partial_result: string | null;
  error: string | null;
  chain_id: string | null;
  chain_order: number | null;
  created_at: number;
  updated_at: number;
}

export interface TaskListResponse {
  tasks: TaskItem[];
  total: number;
}

export interface CancelTaskRequest {
  reason?: string;
}

export interface CancelTaskResponse {
  task_id: string;
  status: 'cancelled' | 'not_found';
  message: string;
}

export async function fetchTasks(status: TaskStatusFilter = 'active'): Promise<TaskListResponse> {
  return apiFetch<TaskListResponse>(`/api/tasks?status=${status}`);
}

export async function fetchTask(taskId: string): Promise<TaskItem> {
  return apiFetch<TaskItem>(`/api/tasks/${taskId}`);
}

export async function cancelTask(
  taskId: string,
  payload: CancelTaskRequest = {},
): Promise<CancelTaskResponse> {
  return apiFetch<CancelTaskResponse>(`/api/tasks/${taskId}/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
