import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { Thread } from './types';

const MOCK_THREADS: Thread[] = [
  {
    id: 'thread-1',
    title: 'Project architecture review',
    lastMessagePreview: 'I recommend splitting the module into smaller services...',
    updatedAt: new Date(Date.now() - 300_000),
    messageCount: 12,
  },
  {
    id: 'thread-2',
    title: 'Debug: memory leak in worker',
    lastMessagePreview: 'The issue is in the event listener cleanup...',
    updatedAt: new Date(Date.now() - 3_600_000),
    messageCount: 8,
  },
  {
    id: 'thread-3',
    title: 'Write unit tests for auth',
    lastMessagePreview: 'Here are the test cases for the login flow...',
    updatedAt: new Date(Date.now() - 86_400_000),
    messageCount: 5,
  },
  {
    id: 'thread-4',
    title: 'Deploy pipeline setup',
    lastMessagePreview: 'The CI config should include these stages...',
    updatedAt: new Date(Date.now() - 172_800_000),
    messageCount: 15,
  },
  {
    id: 'thread-5',
    title: 'API design discussion',
    lastMessagePreview: 'REST vs GraphQL — here are the trade-offs...',
    updatedAt: new Date(Date.now() - 604_800_000),
    messageCount: 22,
  },
];

export const useThreadStore = defineStore('threads', () => {
  const threads = ref<Thread[]>(MOCK_THREADS);
  const activeThreadId = ref<string | null>('thread-1');

  const activeThread = computed(() => threads.value.find((t) => t.id === activeThreadId.value));

  const sortedThreads = computed(() =>
    [...threads.value].sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime()),
  );

  function selectThread(id: string) {
    if (!threads.value.some((thread) => thread.id === id)) return;
    activeThreadId.value = id;
  }

  function createThread() {
    const newThread: Thread = {
      id: `thread-${Date.now()}`,
      title: 'New conversation',
      lastMessagePreview: '',
      updatedAt: new Date(),
      messageCount: 0,
    };
    threads.value.unshift(newThread);
    activeThreadId.value = newThread.id;
  }

  function renameThread(id: string, title: string) {
    const normalizedTitle = title.trim();
    if (!normalizedTitle) return;

    const thread = threads.value.find((item) => item.id === id);
    if (!thread) return;

    thread.title = normalizedTitle;
    thread.updatedAt = new Date();
  }

  function deleteThread(id: string) {
    const index = threads.value.findIndex((thread) => thread.id === id);
    if (index === -1) return;

    threads.value.splice(index, 1);

    if (activeThreadId.value !== id) return;

    const nextActiveThread = sortedThreads.value[0] ?? null;
    activeThreadId.value = nextActiveThread?.id ?? null;
  }

  return {
    threads,
    activeThreadId,
    activeThread,
    sortedThreads,
    selectThread,
    createThread,
    renameThread,
    deleteThread,
  };
});
