import type { useAgentStore } from '@/entities/agent';
import type { useMessageStore } from '@/entities/message';
import type { useThreadStore } from '@/entities/thread';
import type { ChatMessage, CreateThreadRequest } from '@/shared/api';

interface SendPromptToThreadOptions {
  threadStore: ReturnType<typeof useThreadStore>;
  messageStore: ReturnType<typeof useMessageStore>;
  agentStore: ReturnType<typeof useAgentStore>;
  content: string;
  threadId?: string | null;
  createThreadRequest?: CreateThreadRequest;
}

function toChatMessages(
  msgs: { id: string; role: string; content: string }[],
): ChatMessage[] {
  return msgs.map((message) => ({
    id: message.id,
    role: message.role as 'user' | 'assistant',
    content: message.content,
  }));
}

export async function sendPromptToThread({
  threadStore,
  messageStore,
  agentStore,
  content,
  threadId,
  createThreadRequest,
}: SendPromptToThreadOptions): Promise<string> {
  const trimmed = content.trim();
  if (!trimmed) {
    throw new Error('Prompt cannot be empty');
  }

  let resolvedThreadId = threadId ?? threadStore.activeThreadId;
  if (!resolvedThreadId) {
    const thread = await threadStore.createThread(createThreadRequest);
    resolvedThreadId = thread.id;
  }

  threadStore.selectThread(resolvedThreadId);

  messageStore.addMessage(resolvedThreadId, 'user', trimmed);
  const allMessages = messageStore.getThreadMessages(resolvedThreadId);
  const chatMessages = toChatMessages(allMessages);
  const assistantMessageId = messageStore.addMessage(
    resolvedThreadId,
    'assistant',
    '',
  );

  try {
    await agentStore.runAgent(resolvedThreadId, chatMessages, {
      onDelta: (delta) => {
        messageStore.appendMessageDelta(assistantMessageId, delta);
      },
    });
  } catch (cause) {
    const message = messageStore.messages.find(
      (item) => item.id === assistantMessageId,
    );
    if (message && !message.content) {
      messageStore.messages.splice(messageStore.messages.indexOf(message), 1);
    }
    throw cause;
  } finally {
    await threadStore.loadThreads();
  }

  return resolvedThreadId;
}
