/**
 * Agent API client using AG-UI protocol (POST /agent, SSE).
 * Uses HttpAgent.runAgent() and returns full assistant text from newMessages.
 */

import { HttpAgent } from '@ag-ui/client';
import type { Message } from '@ag-ui/core';

const AGENT_URL = '/agent';

function getAuthToken(): string {
  return import.meta.env.VITE_API_KEY ?? '';
}

function randomUUID(): string {
  return crypto.randomUUID?.() ?? `run-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

function toAguiMessage(m: ChatMessage): Message {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
  } as Message;
}

function extractTextContent(msg: { content?: string | Array<{ type?: string; text?: string }> }): string {
  if (typeof msg.content === 'string') return msg.content;
  if (Array.isArray(msg.content)) {
    return msg.content
      .filter((p): p is { type: string; text: string } => p?.type === 'text' && typeof p?.text === 'string')
      .map((p) => p.text)
      .join('\n');
  }
  return '';
}

/**
 * Runs the agent with the given thread and messages.
 * Returns the full assistant text when the run completes.
 * Rejects on RUN_ERROR.
 */
export async function runAgent(
  threadId: string,
  messages: ChatMessage[],
): Promise<string> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'X-Thread-Id': threadId,
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const agent = new HttpAgent({
    url: AGENT_URL,
    headers,
    threadId,
    initialMessages: messages.map(toAguiMessage),
  });

  const { newMessages } = await agent.runAgent({ runId: randomUUID() });

  const lastAssistant = [...newMessages]
    .reverse()
    .find((m) => m.role === 'assistant');
  if (lastAssistant) {
    return extractTextContent(lastAssistant);
  }

  return '';
}
