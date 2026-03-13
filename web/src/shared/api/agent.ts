/**
 * Agent API client using AG-UI protocol (POST /agent, SSE).
 * Supports streaming via onDelta callback (called with each text chunk).
 * Resolves with the full concatenated assistant text when the run finishes.
 */

import { HttpAgent } from '@ag-ui/client';
import { EventType } from '@ag-ui/core';
import type { Message } from '@ag-ui/core';
import { lastValueFrom } from 'rxjs';
import { tap, finalize } from 'rxjs/operators';
import { getAuthToken } from './auth';

const AGENT_URL = '/agent';

function randomUUID(): string {
  return crypto.randomUUID?.() ?? `run-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

export interface RunAgentOptions {
  /** Called with each incremental text chunk as it arrives. */
  onDelta?: (delta: string) => void;
  /** Called once the run ends (success or error). */
  onDone?: () => void;
}

function toAguiMessage(m: ChatMessage): Message {
  return { id: m.id, role: m.role, content: m.content } as Message;
}

/**
 * Runs the agent with streaming.
 * Each TEXT_MESSAGE_CONTENT delta is forwarded to `options.onDelta`.
 * Resolves with the full assistant text on RUN_FINISHED.
 * Rejects on RUN_ERROR.
 */
export function runAgent(
  threadId: string,
  messages: ChatMessage[],
  options: RunAgentOptions = {},
): Promise<string> {
  const { onDelta, onDone } = options;
  const token = getAuthToken();
  const headers: Record<string, string> = { 'X-Thread-Id': threadId };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const agent = new HttpAgent({
    url: AGENT_URL,
    headers,
    threadId,
    initialMessages: messages.map(toAguiMessage),
  });

  let accumulated = '';
  let hasRunFinished = false;

  const events$ = agent.run({
    threadId,
    runId: randomUUID(),
    messages: messages.map(toAguiMessage),
    tools: [],
    context: [],
  });

  const stream$ = events$.pipe(
    tap((event) => {
      if (event.type === EventType.TEXT_MESSAGE_CONTENT) {
        const delta = (event as { delta?: string }).delta ?? '';
        accumulated += delta;
        onDelta?.(delta);
      }
      if (event.type === EventType.RUN_ERROR) {
        throw new Error((event as { message?: string }).message ?? 'Agent run failed');
      }
      if (event.type === EventType.RUN_FINISHED) {
        hasRunFinished = true;
      }
    }),
    finalize(() => onDone?.()),
  );

  return lastValueFrom(stream$).then(() => {
    if (!hasRunFinished) {
      throw new Error('Agent stream interrupted before completion');
    }
    return accumulated;
  });
}
