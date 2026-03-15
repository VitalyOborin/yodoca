import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import ScheduleCard from './ScheduleCard.vue';

vi.mock('@/shared/lib', () => ({
  formatScheduleAbsolute: () => 'Mar 15, 13:05',
  formatScheduleRelative: () => 'in 1 hour',
  formatInterval: (seconds: number) => `Every ${seconds / 60} minute`,
}));

describe('ScheduleCard', () => {
  it('renders human-readable interval and until time', () => {
    const wrapper = mount(ScheduleCard, {
      props: {
        item: {
          id: 1,
          type: 'recurring',
          topic: 'system.agent.task',
          message: 'Run report',
          channel_id: null,
          payload: {},
          fires_at_iso: '2026-03-15T13:05:09Z',
          status: 'active',
          cron_expr: null,
          every_seconds: 3_600,
          until_iso: '2026-03-20T13:05:09Z',
          created_at: 1,
        },
      },
      global: {
        stubs: {
          ScheduleActions: true,
        },
      },
    });

    expect(wrapper.text()).toContain('Every 60 minute');
    expect(wrapper.text()).toContain('Until Mar 15, 13:05');
  });
});
