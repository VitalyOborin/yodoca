export type MessageRole = 'user' | 'assistant';

export interface Message {
  id: string;
  threadId: string;
  role: MessageRole;
  content: string;
  createdAt: Date;
}
