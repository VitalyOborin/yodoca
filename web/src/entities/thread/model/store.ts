import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { Thread } from './types';
import {
  fetchThreads,
  fetchThread,
  createThread as apiCreateThread,
  updateThread,
  deleteThread as apiDeleteThread,
} from '@/shared/api';

export const useThreadStore = defineStore('threads', () => {
  const threads = ref<Thread[]>([]);
  const activeThreadId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const activeThread = computed(() =>
    threads.value.find((t) => t.id === activeThreadId.value),
  );

  const sortedThreads = computed(() =>
    [...threads.value]
      .filter((t) => !t.is_archived)
      .sort((a, b) => b.last_active_at - a.last_active_at),
  );

  async function loadThreads() {
    loading.value = true;
    error.value = null;
    try {
      threads.value = await fetchThreads();
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load threads';
      threads.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function loadThread(id: string) {
    return fetchThread(id);
  }

  function selectThread(id: string | null) {
    activeThreadId.value = id;
  }

  async function createThread(): Promise<Thread> {
    const thread = await apiCreateThread();
    threads.value.unshift(thread);
    activeThreadId.value = thread.id;
    return thread;
  }

  async function ensureThread(): Promise<string> {
    if (activeThreadId.value) return activeThreadId.value;
    const thread = await createThread();
    return thread.id;
  }

  async function renameThread(id: string, title: string) {
    const normalizedTitle = title.trim();
    if (!normalizedTitle) return;

    const updated = await updateThread(id, { title: normalizedTitle });
    const idx = threads.value.findIndex((t) => t.id === id);
    if (idx >= 0) threads.value[idx] = updated;
  }

  async function archiveThread(id: string) {
    await updateThread(id, { is_archived: true });
    const t = threads.value.find((x) => x.id === id);
    if (t) t.is_archived = true;
  }

  async function removeThread(id: string) {
    await apiDeleteThread(id);
    const index = threads.value.findIndex((t) => t.id === id);
    if (index >= 0) threads.value.splice(index, 1);
    if (activeThreadId.value === id) {
      const next = sortedThreads.value[0];
      activeThreadId.value = next?.id ?? null;
    }
  }

  return {
    threads,
    activeThreadId,
    activeThread,
    sortedThreads,
    loading,
    error,
    loadThreads,
    loadThread,
    selectThread,
    createThread,
    ensureThread,
    renameThread,
    archiveThread,
    removeThread,
  };
});
