import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import * as api from '@/shared/api';
import { useScheduleStore } from './store';

describe('useScheduleStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it('loads and splits schedules by computed groups', async () => {
    vi.spyOn(api, 'fetchSchedules').mockResolvedValue([
      {
        id: 1,
        type: 'one_shot',
        topic: 'system.user.notify',
        message: 'Reminder',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-16T09:00:00',
        status: 'scheduled',
        cron_expr: null,
        every_seconds: null,
        until_iso: null,
        created_at: 1,
      },
      {
        id: 2,
        type: 'recurring',
        topic: 'system.agent.background',
        message: 'Health check',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-16T08:00:00',
        status: 'active',
        cron_expr: '0 * * * *',
        every_seconds: null,
        until_iso: null,
        created_at: 2,
      },
      {
        id: 3,
        type: 'one_shot',
        topic: 'system.user.notify',
        message: 'Done',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-15T08:00:00',
        status: 'fired',
        cron_expr: null,
        every_seconds: null,
        until_iso: null,
        created_at: 3,
      },
    ]);

    const store = useScheduleStore();
    await store.loadSchedules();

    expect(store.activeOnce.map((item) => item.id)).toEqual([1]);
    expect(store.activeRecurring.map((item) => item.id)).toEqual([2]);
    expect(store.history.map((item) => item.id)).toEqual([3]);
  });

  it('rolls back optimistic remove on error', async () => {
    vi.spyOn(api, 'fetchSchedules').mockResolvedValue([
      {
        id: 7,
        type: 'one_shot',
        topic: 'system.user.notify',
        message: 'Reminder',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-16T09:00:00',
        status: 'scheduled',
        cron_expr: null,
        every_seconds: null,
        until_iso: null,
        created_at: 1,
      },
    ]);
    vi.spyOn(api, 'deleteSchedule').mockRejectedValue(new Error('Conflict'));

    const store = useScheduleStore();
    await store.loadSchedules();

    await expect(store.remove('one_shot', 7)).rejects.toThrow('Conflict');
    expect(store.schedules).toHaveLength(1);
    expect(store.schedules[0]?.id).toBe(7);
  });

  it('reloads after add, update and remove', async () => {
    const loadSpy = vi
      .spyOn(api, 'fetchSchedules')
      .mockResolvedValue([]);
    vi.spyOn(api, 'createOnceSchedule').mockResolvedValue({
      success: true,
      schedule_id: 10,
      topic: 'system.user.notify',
      fires_in_seconds: 60,
      status: 'scheduled',
    });
    vi.spyOn(api, 'createRecurringSchedule').mockResolvedValue({
      success: true,
      schedule_id: 11,
      next_fire_iso: '2026-03-16T09:00:00',
      status: 'created',
    });
    vi.spyOn(api, 'updateRecurringSchedule').mockResolvedValue({
      success: true,
      schedule_id: 11,
      next_fire_iso: '2026-03-16T09:00:00',
      message: 'Updated',
    });
    vi.spyOn(api, 'deleteSchedule').mockResolvedValue({ success: true });

    const store = useScheduleStore();
    await store.addOnce({
      topic: 'system.user.notify',
      message: 'Reminder',
      delay_seconds: 60,
    });
    await store.addRecurring({
      topic: 'system.agent.background',
      message: 'Health check',
      every_seconds: 60,
    });
    await store.update(11, { status: 'paused' });
    await store.remove('recurring', 11);

    expect(loadSpy).toHaveBeenCalledTimes(4);
  });
});
