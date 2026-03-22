/**
 * Thread type aligned with backend API schema.
 * @see docs/api/openapi.yaml
 */
export interface Thread {
  id: string;
  project_id: string | null;
  title: string | null;
  channel_id: string;
  created_at: number;
  last_active_at: number;
  is_archived: boolean;
}
