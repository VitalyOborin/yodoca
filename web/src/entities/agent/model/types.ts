export type AgentPhase = 'idle' | 'thinking' | 'acting' | 'waiting_input' | 'error' | 'complete';

export interface AgentAuditItem {
  id: string;
  at: Date;
  status: 'pending' | 'running' | 'done' | 'error';
  title: string;
  detail?: string;
}

export interface AgentDraft {
  id: string;
  title: string;
  description: string;
  status: 'pending' | 'approved' | 'rejected';
}
