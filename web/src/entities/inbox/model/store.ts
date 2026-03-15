import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  ApiRequestError,
  deleteInboxItem,
  fetchInbox,
  fetchInboxItem,
  markAllInboxRead,
  markInboxRead,
  useInboxStream,
  type InboxItem,
  type InboxListQuery,
  type InboxStatusFilter,
  type InboxStreamEvent,
} from '@/shared/api';

export const useInboxStore = defineStore('inbox', () => {
  const items = ref<InboxItem[]>([]);
  const total = ref(0);
  const unreadCount = ref(0);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);
  const lastErrorStatus = ref<number | null>(null);
  const selectedId = ref<number | null>(null);

  const sourceFilter = ref<string>('all');
  const entityTypeFilter = ref<string>('');
  const statusFilter = ref<InboxStatusFilter>('active');
  const unreadOnly = ref(false);

  const streamActive = ref(false);
  let stopStream: (() => void) | null = null;

  const sourceTabs = computed(() => {
    const uniq = new Set<string>();
    for (const item of items.value) {
      uniq.add(item.source_type);
    }
    return ['all', ...Array.from(uniq).sort((a, b) => a.localeCompare(b))];
  });

  const sourceUnread = computed(() => {
    const counts: Record<string, number> = { all: 0 };
    for (const item of items.value) {
      if (item.status !== 'active' || item.is_read) continue;
      const allCount = counts.all ?? 0;
      counts.all = allCount + 1;
      counts[item.source_type] = (counts[item.source_type] ?? 0) + 1;
    }
    return counts;
  });

  const selectedItem = computed(() => {
    if (selectedId.value === null) return null;
    return items.value.find((item) => item.id === selectedId.value) ?? null;
  });

  const isUnavailable = computed(
    () => lastErrorStatus.value === 503 || error.value?.includes('unavailable') === true,
  );

  function currentQuery(): InboxListQuery {
    return {
      source_type: sourceFilter.value === 'all' ? undefined : sourceFilter.value,
      entity_type: entityTypeFilter.value || undefined,
      status: statusFilter.value,
      unread: unreadOnly.value ? true : undefined,
      limit: 50,
      offset: 0,
    };
  }

  function upsertItem(item: InboxItem) {
    const idx = items.value.findIndex((entry) => entry.id === item.id);
    if (idx === -1) {
      items.value.unshift(item);
      total.value += 1;
      return;
    }
    items.value[idx] = item;
  }

  function removeItem(id: number) {
    const idx = items.value.findIndex((entry) => entry.id === id);
    if (idx === -1) return;
    items.value.splice(idx, 1);
    total.value = Math.max(0, total.value - 1);
    if (selectedId.value === id) {
      selectedId.value = items.value[0]?.id ?? null;
    }
  }

  async function refreshUnreadCount() {
    try {
      const data = await fetchInbox({ status: 'active', limit: 1, offset: 0 });
      unreadCount.value = data.unread_count;
    } catch {
      // keep existing unread count
    }
  }

  async function loadInbox() {
    loading.value = true;
    error.value = null;
    lastErrorStatus.value = null;
    try {
      const data = await fetchInbox(currentQuery());
      items.value = data.items;
      total.value = data.total;
      unreadCount.value = data.unread_count;
      if (
        selectedId.value !== null
        && items.value.find((item) => item.id === selectedId.value)
      ) {
        return;
      }
      selectedId.value = items.value[0]?.id ?? null;
    } catch (cause) {
      items.value = [];
      total.value = 0;
      selectedId.value = null;
      lastErrorStatus.value =
        cause instanceof ApiRequestError ? cause.status : null;
      error.value = cause instanceof Error ? cause.message : 'Failed to load inbox';
    } finally {
      loading.value = false;
    }
  }

  async function selectItem(id: number) {
    selectedId.value = id;
    try {
      const item = await fetchInboxItem(id);
      upsertItem(item);
      if (!item.is_read) {
        await readItem(id);
      }
    } catch {
      // keep selected item from list if detail unavailable
    }
  }

  async function readItem(id: number) {
    saving.value = true;
    error.value = null;
    try {
      await markInboxRead(id);
      const item = items.value.find((entry) => entry.id === id);
      if (item && !item.is_read) {
        item.is_read = true;
        unreadCount.value = Math.max(0, unreadCount.value - 1);
      }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Failed to mark as read';
    } finally {
      saving.value = false;
    }
  }

  async function readAll(sourceType?: string) {
    saving.value = true;
    error.value = null;
    try {
      await markAllInboxRead(sourceType ? { source_type: sourceType } : {});
      await loadInbox();
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Failed to mark all as read';
    } finally {
      saving.value = false;
    }
  }

  async function softDelete(id: number) {
    saving.value = true;
    error.value = null;
    try {
      await deleteInboxItem(id);
      removeItem(id);
      await refreshUnreadCount();
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Failed to delete item';
    } finally {
      saving.value = false;
    }
  }

  async function handleStreamEvent(event: InboxStreamEvent) {
    if (event.event !== 'inbox.item.ingested') return;
    try {
      const item = await fetchInboxItem(event.inbox_id);
      const sourceMatches = sourceFilter.value === 'all' || item.source_type === sourceFilter.value;
      const entityMatches = !entityTypeFilter.value || item.entity_type === entityTypeFilter.value;
      const statusMatches = statusFilter.value === 'all' || item.status === statusFilter.value;
      const unreadMatches = !unreadOnly.value || !item.is_read;

      if (sourceMatches && entityMatches && statusMatches && unreadMatches) {
        upsertItem(item);
      } else {
        removeItem(item.id);
      }
      await refreshUnreadCount();
    } catch {
      await loadInbox();
    }
  }

  function startStream() {
    if (streamActive.value) return;
    stopStream = useInboxStream({
      onEvent: (event) => {
        void handleStreamEvent(event);
      },
      onError: () => {
        // Browser EventSource handles reconnect; we keep store state untouched.
      },
    });
    streamActive.value = true;
  }

  function stopInboxStream() {
    stopStream?.();
    stopStream = null;
    streamActive.value = false;
  }

  async function bootstrap() {
    if (!streamActive.value) {
      startStream();
    }
    await refreshUnreadCount();
  }

  return {
    items,
    total,
    unreadCount,
    loading,
    saving,
    error,
    lastErrorStatus,
    selectedId,
    selectedItem,
    sourceFilter,
    entityTypeFilter,
    statusFilter,
    unreadOnly,
    sourceTabs,
    sourceUnread,
    isUnavailable,
    loadInbox,
    selectItem,
    readItem,
    readAll,
    softDelete,
    handleStreamEvent,
    startStream,
    stopInboxStream,
    bootstrap,
    refreshUnreadCount,
  };
});
