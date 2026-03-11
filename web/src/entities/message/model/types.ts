export type MessageRole = 'user' | 'agent';

export interface Message {
  id: string;
  threadId: string;
  role: MessageRole;
  content: string;
  createdAt: Date;
}
