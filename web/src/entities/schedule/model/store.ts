import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  createOnceSchedule,
  createRecurringSchedule,
  deleteSchedule as apiDeleteSchedule,
  fetchSchedules,
  updateRecurringSchedule,
  type CreateOnceRequest,
  type CreateRecurringRequest,
  type ScheduleItem,
  type ScheduleStatus,
  type ScheduleType,
  type UpdateRecurringRequest,
} from '@/shared/api';

export type ScheduleTab = 'once' | 'recurring';

export const useScheduleStore = defineStore('schedules', () => {
  const schedules = ref<ScheduleItem[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);
  const activeTab = ref<ScheduleTab>('once');

  const activeOnce = computed(() =>
    schedules.value
      .filter((item) => item.type === 'one_shot' && item.status === 'scheduled')
      .sort(
        (a, b) =>
          new Date(a.fires_at_iso).getTime() - new Date(b.fires_at_iso).getTime(),
      ),
  );

  const activeRecurring = computed(() =>
    schedules.value.filter(
      (item) => item.type === 'recurring' && item.status === 'active',
    ),
  );

  const history = computed(() =>
    schedules.value
      .filter((item) => item.status === 'fired' || item.status === 'cancelled')
      .sort((a, b) => b.created_at - a.created_at),
  );

  async function loadSchedules(status?: ScheduleStatus) {
    loading.value = true;
    error.value = null;
    try {
      schedules.value = await fetchSchedules(status);
    } catch (cause) {
      schedules.value = [];
      error.value =
        cause instanceof Error ? cause.message : 'Failed to load schedules';
    } finally {
      loading.value = false;
    }
  }

  async function addOnce(payload: CreateOnceRequest) {
    saving.value = true;
    error.value = null;
    try {
      await createOnceSchedule(payload);
      await loadSchedules();
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to create schedule';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  async function addRecurring(payload: CreateRecurringRequest) {
    saving.value = true;
    error.value = null;
    try {
      await createRecurringSchedule(payload);
      await loadSchedules();
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to create schedule';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  async function remove(type: ScheduleType, id: number) {
    saving.value = true;
    error.value = null;
    const snapshot = [...schedules.value];
    schedules.value = schedules.value.filter(
      (schedule) => !(schedule.type === type && schedule.id === id),
    );
    try {
      await apiDeleteSchedule(type, id);
      await loadSchedules();
    } catch (cause) {
      schedules.value = snapshot;
      error.value =
        cause instanceof Error ? cause.message : 'Failed to delete schedule';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  async function update(id: number, payload: UpdateRecurringRequest) {
    saving.value = true;
    error.value = null;
    try {
      await updateRecurringSchedule(id, payload);
      await loadSchedules();
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to update schedule';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  function pause(id: number) {
    return update(id, { status: 'paused' });
  }

  function resume(id: number) {
    return update(id, { status: 'active' });
  }

  return {
    schedules,
    loading,
    saving,
    error,
    activeTab,
    activeOnce,
    activeRecurring,
    history,
    loadSchedules,
    addOnce,
    addRecurring,
    remove,
    update,
    pause,
    resume,
  };
});
