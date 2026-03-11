import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { Message } from './types';

function hours(h: number): number {
  return h * 3_600_000;
}
function mins(m: number): number {
  return m * 60_000;
}

const now = Date.now();
const MARKDOWN_DEMO_MESSAGE = `# Markdown demo for Yodoca

## Formatting
This paragraph contains **bold text**, *italic text*, and inline code: \`context.emit("user.message", payload)\`.

> Quote: Transparent agent actions build user trust.

## Lists
- Event-driven architecture
- Durable event journal
- Progressive authorization

1. Analyze request
2. Preview intent
3. Execute safely

## Links
Read [Architecture docs](https://example.com/docs/architecture) and [Event bus guide](https://example.com/docs/event-bus).

## Python block
\`\`\`python
from datetime import datetime, timezone

def emit_event(topic: str, payload: dict) -> dict:
    return {
        "topic": topic,
        "payload": payload,
        "at": datetime.now(timezone.utc).isoformat(),
    }
\`\`\`

## JSON block
\`\`\`json
{
  "event": "task.created",
  "thread_id": "thread-1",
  "priority": "high",
  "meta": {
    "source": "chat",
    "attempt": 1
  }
}
\`\`\`

## Text block
\`\`\`text
TASK STATUS BOARD
- planning: done
- execution: running
- review: pending
\`\`\`
`;

const MOCK_MESSAGES: Message[] = [
  {
    id: 'msg-1',
    threadId: 'thread-1',
    role: 'user',
    content: 'Can you review the current architecture and suggest improvements?',
    createdAt: new Date(now - hours(2)),
  },
  {
    id: 'msg-2',
    threadId: 'thread-1',
    role: 'agent',
    content:
      "I've analyzed the codebase structure. Here are my recommendations:\n\n1. **Split the monolith service** into domain-bounded modules\n2. **Introduce an event bus** for cross-module communication\n3. **Add a caching layer** for frequently accessed data\n4. **Improve error handling** with typed result objects\n\nWould you like me to elaborate on any of these points?",
    createdAt: new Date(now - hours(2) + mins(1)),
  },
  {
    id: 'msg-3',
    threadId: 'thread-1',
    role: 'user',
    content: 'Tell me more about the event bus approach. How would it work with our current setup?',
    createdAt: new Date(now - hours(1) - mins(30)),
  },
  {
    id: 'msg-4',
    threadId: 'thread-1',
    role: 'agent',
    content:
      "Great question! Here's how the event bus would integrate:\n\n**Architecture:**\n- A durable SQLite-backed event journal\n- Topic-based publish/subscribe pattern\n- Each module subscribes to relevant topics\n\n**Benefits:**\n- Loose coupling between modules\n- Built-in audit trail via the journal\n- Easy to add new subscribers without modifying publishers\n\n**Implementation steps:**\n1. Define core event topics\n2. Create the EventBus service with SQLite persistence\n3. Migrate direct calls to event-based communication\n4. Add monitoring and dead-letter handling",
    createdAt: new Date(now - hours(1) - mins(28)),
  },
  {
    id: 'msg-5',
    threadId: 'thread-1',
    role: 'user',
    content: 'That sounds solid. Can you start with step 1 and define the core topics?',
    createdAt: new Date(now - mins(45)),
  },
  {
    id: 'msg-6',
    threadId: 'thread-1',
    role: 'agent',
    content:
      "I recommend splitting the module into smaller services with clear boundaries. Here are the core event topics I'd suggest:\n\n- `user.message` — incoming user messages\n- `agent.response` — agent replies\n- `system.error` — error notifications\n- `task.created` / `task.completed` — task lifecycle\n- `memory.updated` — knowledge base changes\n\nEach topic follows the `domain.action` naming convention for consistency.",
    createdAt: new Date(now - mins(43)),
  },
  {
    id: 'msg-7',
    threadId: 'thread-1',
    role: 'user',
    content: 'Can you provide a markdown-rich example message for docs and UI testing?',
    createdAt: new Date(now - mins(39)),
  },
  {
    id: 'msg-8',
    threadId: 'thread-1',
    role: 'agent',
    content: MARKDOWN_DEMO_MESSAGE,
    createdAt: new Date(now - mins(38)),
  },
  {
    id: 'msg-9',
    threadId: 'thread-1',
    role: 'user',
    content: 'Looks good. Add a short rollout checklist for this UI update.',
    createdAt: new Date(now - mins(33)),
  },
  {
    id: 'msg-13',
    threadId: 'thread-1',
    role: 'agent',
    content:
      'Rollout checklist:\n- Enable markdown rendering in chat\n- Verify syntax highlighting for python/json/text\n- Validate mobile scroll and composer overlap behavior',
    createdAt: new Date(now - mins(31)),
  },
  {
    id: 'msg-14',
    threadId: 'thread-1',
    role: 'user',
    content: 'Great, ship this in the next frontend patch.',
    createdAt: new Date(now - mins(28)),
  },
  {
    id: 'msg-15',
    threadId: 'thread-1',
    role: 'agent',
    content: 'Acknowledged. I will include this in the next patch and keep backward compatibility for plain text messages.',
    createdAt: new Date(now - mins(27)),
  },
  {
    id: 'msg-10',
    threadId: 'thread-2',
    role: 'user',
    content: "I'm seeing increasing memory usage in the worker process. It grows by ~50MB/hour.",
    createdAt: new Date(now - hours(5)),
  },
  {
    id: 'msg-11',
    threadId: 'thread-2',
    role: 'agent',
    content:
      "Let me help diagnose this. Common causes of memory leaks in worker processes:\n\n1. **Unreleased event listeners** — check if listeners are cleaned up on disconnect\n2. **Accumulating buffers** — large payloads being stored in memory\n3. **Closure references** — closures holding references to large objects\n\nCan you share the worker's initialization code?",
    createdAt: new Date(now - hours(5) + mins(2)),
  },
  {
    id: 'msg-12',
    threadId: 'thread-2',
    role: 'user',
    content: 'The issue is in the event listener cleanup, I think. Here is the code...',
    createdAt: new Date(now - hours(4)),
  },
  {
    id: 'msg-20',
    threadId: 'thread-3',
    role: 'user',
    content: 'I need comprehensive test coverage for the authentication module.',
    createdAt: new Date(now - hours(24)),
  },
  {
    id: 'msg-21',
    threadId: 'thread-3',
    role: 'agent',
    content:
      'Here are the test cases for the login flow:\n\n- Valid credentials → returns token\n- Invalid password → returns 401\n- Locked account → returns 423\n- Expired token refresh → issues new token\n- Rate limiting → returns 429 after threshold',
    createdAt: new Date(now - hours(24) + mins(3)),
  },
];

export const useMessageStore = defineStore('messages', () => {
  const messages = ref<Message[]>(MOCK_MESSAGES);

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

  function addMessage(threadId: string, role: 'user' | 'agent', content: string) {
    messages.value.push({
      id: `msg-${Date.now()}`,
      threadId,
      role,
      content,
      createdAt: new Date(),
    });
  }

  return { messages, messagesByThread, getThreadMessages, addMessage };
});
