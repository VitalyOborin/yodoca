import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  ApiRequestError,
  cancelTask,
  fetchTask,
  fetchTasks,
  type TaskItem,
  type TaskStatusFilter,
} from '@/shared/api';

const ACTIVE_STATUSES: TaskStatusFilter[] = [
  'pending',
  'running',
  'blocked',
  'retry_scheduled',
  'waiting_subtasks',
  'human_review',
];

export const useTaskStore = defineStore('task', () => {
  const items = ref<TaskItem[]>([]);
  const total = ref(0);
  const activeCount = ref(0);
  const loading = ref(false);
  const cancelling = ref<string | null>(null);
  const error = ref<string | null>(null);
  const lastErrorStatus = ref<number | null>(null);
  const statusFilter = ref<TaskStatusFilter>('active');
  const selectedId = ref<string | null>(null);

  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const selectedItem = computed(
    () => items.value.find((task) => task.task_id === selectedId.value) ?? null,
  );

  const isUnavailable = computed(() => lastErrorStatus.value === 503);

  const activeTasks = computed(() =>
    items.value.filter((task) => ACTIVE_STATUSES.includes(task.status as TaskStatusFilter)),
  );

  const doneTasks = computed(() => items.value.filter((task) => task.status === 'done'));

  const failedTasks = computed(() =>
    items.value.filter((task) => task.status === 'failed' || task.status === 'cancelled'),
  );

  function upsertItem(item: TaskItem) {
    const idx = items.value.findIndex((task) => task.task_id === item.task_id);
    if (idx === -1) {
      items.value.unshift(item);
      total.value += 1;
      return;
    }
    items.value[idx] = item;
  }

  async function loadTasks() {
    loading.value = true;
    error.value = null;
    lastErrorStatus.value = null;
    try {
      const data = await fetchTasks(statusFilter.value);
      items.value = data.tasks;
      total.value = data.total;
      if (statusFilter.value === 'active') {
        activeCount.value = data.total;
      } else {
        const active = await fetchTasks('active');
        activeCount.value = active.total;
      }
      if (selectedId.value && !items.value.some((task) => task.task_id === selectedId.value)) {
        selectedId.value = items.value[0]?.task_id ?? null;
      }
    } catch (cause) {
      items.value = [];
      total.value = 0;
      activeCount.value = 0;
      selectedId.value = null;
      lastErrorStatus.value = cause instanceof ApiRequestError ? cause.status : null;
      error.value = cause instanceof Error ? cause.message : 'Failed to load tasks';
    } finally {
      loading.value = false;
    }
  }

  async function selectTask(taskId: string) {
    selectedId.value = taskId;
    try {
      const item = await fetchTask(taskId);
      upsertItem(item);
    } catch {
      // Keep item from list if detail endpoint is unavailable.
    }
  }

  async function cancel(taskId: string, reason = '') {
    cancelling.value = taskId;
    error.value = null;
    try {
      await cancelTask(taskId, { reason });
      await loadTasks();
      await selectTask(taskId);
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Failed to cancel task';
    } finally {
      cancelling.value = null;
    }
  }

  function startPolling(intervalMs = 3000) {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      if (activeCount.value > 0 || statusFilter.value === 'active') {
        void loadTasks();
      }
    }, intervalMs);
  }

  function stopPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  }

  async function bootstrap() {
    await loadTasks();
    startPolling();
  }

  return {
    items,
    total,
    loading,
    cancelling,
    error,
    lastErrorStatus,
    statusFilter,
    selectedId,
    selectedItem,
    isUnavailable,
    activeTasks,
    doneTasks,
    failedTasks,
    activeCount,
    loadTasks,
    selectTask,
    cancel,
    startPolling,
    stopPolling,
    bootstrap,
  };
});
