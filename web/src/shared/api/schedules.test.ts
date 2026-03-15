import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError } from './http';
import {
  createOnceSchedule,
  createRecurringSchedule,
  deleteSchedule,
  fetchSchedules,
  updateRecurringSchedule,
} from './schedules';

describe('schedules api client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches schedule list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          schedules: [
            {
              id: 1,
              type: 'one_shot',
              topic: 'system.user.notify',
              message: 'Test',
              channel_id: null,
              payload: {},
              fires_at_iso: '2026-03-16T09:00:00',
              status: 'scheduled',
              cron_expr: null,
              every_seconds: null,
              until_iso: null,
              created_at: 1,
            },
          ],
          count: 1,
        }),
      }),
    );

    await expect(fetchSchedules()).resolves.toEqual([
      expect.objectContaining({ id: 1, type: 'one_shot' }),
    ]);
  });

  it('sends create/update/delete requests', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          success: true,
          schedule_id: 10,
          topic: 'system.user.notify',
          fires_in_seconds: 60,
          status: 'scheduled',
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          success: true,
          schedule_id: 11,
          next_fire_iso: '2026-03-16T09:00:00',
          status: 'created',
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          success: true,
          schedule_id: 11,
          next_fire_iso: '2026-03-16T09:00:00',
          message: 'updated',
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({ success: true }),
      });
    vi.stubGlobal('fetch', fetchMock);

    await createOnceSchedule({
      topic: 'system.user.notify',
      message: 'Test',
      delay_seconds: 30,
    });
    await createRecurringSchedule({
      topic: 'system.agent.background',
      message: 'Check',
      every_seconds: 60,
    });
    await updateRecurringSchedule(11, { status: 'paused' });
    await deleteSchedule('recurring', 11);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/schedules/once',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/schedules/recurring',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/schedules/recurring/11',
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/schedules/recurring/11',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('throws ApiRequestError on api error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          error: {
            message: 'Scheduler extension is not loaded or not initialized',
            type: 'service_unavailable',
            code: 'scheduler_unavailable',
          },
        }),
      }),
    );

    await expect(fetchSchedules()).rejects.toBeInstanceOf(ApiRequestError);
  });
});
