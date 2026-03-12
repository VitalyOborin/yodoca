import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { Message } from './types';

export const useMessageStore = defineStore('messages', () => {
  const messages = ref<Message[]>([]);

  const messagesByThread = computed(() => {
    const grouped = new Map<string, Message[]>();
    for (const msg of messages.value) {
      const list = grouped.get(msg.threadId) ?? [];
      list.push(msg);
      grouped.set(msg.threadId, list);
    }
    for (const list of grouped.values()) {
      list.sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime());
    }
    return grouped;
  });

  function getThreadMessages(threadId: string): Message[] {
    return messagesByThread.value.get(threadId) ?? [];
  }

function extractContent(raw: unknown): string {
  if (typeof raw === 'string') return raw;
  if (Array.isArray(raw)) {
    return raw
      .filter((p): p is { type: string; text: string } =>
        p != null && typeof p === 'object' && typeof p.text === 'string',
      )
      .map((p) => p.text)
      .join('\n');
  }
  return '';
}

function setThreadMessages(
  threadId: string,
  msgs: Array<Record<string, unknown>>,
) {
  const existing = messages.value.filter((m) => m.threadId !== threadId);

  const converted: Message[] = [];
  let index = 0;
  for (const m of msgs) {
    const role = m.role as string | undefined;
    if (role !== 'user' && role !== 'assistant') continue;

    const content = extractContent(m.content);
    if (!content.trim()) continue;

    converted.push({
      id: typeof m.id === 'string' ? m.id : `msg-${threadId}-${index}`,
      threadId,
      role: role === 'assistant' ? 'assistant' : 'user',
      content,
      createdAt: new Date(),
    });
    index++;
  }

  messages.value = [...existing, ...converted];
}

  function addMessage(threadId: string, role: Message['role'], content: string): string {
    const id = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    messages.value.push({ id, threadId, role, content, createdAt: new Date() });
    return id;
  }

  function appendMessageDelta(id: string, delta: string) {
    const msg = messages.value.find((m) => m.id === id);
    if (msg) msg.content += delta;
  }

  function clearThread(threadId: string) {
    messages.value = messages.value.filter((m) => m.threadId !== threadId);
  }

  return {
    messages,
    messagesByThread,
    getThreadMessages,
    setThreadMessages,
    addMessage,
    appendMessageDelta,
    clearThread,
  };
});
