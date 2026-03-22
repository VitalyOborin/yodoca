import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import type { ScheduleItem } from '@/shared/api';
import SchedulePage from './SchedulePage.vue';

const scheduleStore = {
  schedules: [],
  loading: false,
  saving: false,
  error: null as string | null,
  lastErrorStatus: null as number | null,
  activeTab: 'once' as 'once' | 'recurring',
  activeOnce: [] as ScheduleItem[],
  activeRecurring: [] as ScheduleItem[],
  history: [] as ScheduleItem[],
  loadSchedules: vi.fn(),
  addOnce: vi.fn(),
  addRecurring: vi.fn(),
  update: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  remove: vi.fn(),
};

vi.mock('@/entities/schedule', () => ({
  useScheduleStore: () => scheduleStore,
}));

vi.mock('@vueuse/core', () => ({
  useIntervalFn: vi.fn(),
}));

describe('SchedulePage', () => {
  beforeEach(() => {
    scheduleStore.loadSchedules.mockReset();
    scheduleStore.resume.mockReset();
    scheduleStore.activeTab = 'once';
    scheduleStore.activeOnce = [];
    scheduleStore.activeRecurring = [];
    scheduleStore.history = [];
  });

  it('loads schedules and opens create dialog', async () => {
    const wrapper = mount(SchedulePage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          ScheduleList: {
            props: ['activeRecurring', 'history'],
            template: '<button class="create-btn" @click="$emit(\'create\')">Create</button>',
          },
          CreateScheduleDialog: {
            props: ['open'],
            template: '<div class="dialog" :data-open="open" />',
          },
        },
      },
    });

    expect(scheduleStore.loadSchedules).toHaveBeenCalled();
    expect(wrapper.find('.dialog').attributes('data-open')).toBe('false');

    await wrapper.find('.create-btn').trigger('click');
    expect(wrapper.find('.dialog').attributes('data-open')).toBe('true');
  });

  it('keeps paused recurring in active and allows resume', async () => {
    scheduleStore.activeTab = 'recurring';
    scheduleStore.activeRecurring = [
      {
        id: 77,
        type: 'recurring',
        topic: 'system.agent.task',
        message: 'Paused recurring',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-16T09:00:00',
        status: 'paused',
        cron_expr: '*/5 * * * *',
        every_seconds: null,
        until_iso: null,
        created_at: 10,
      },
    ];
    scheduleStore.history = [
      {
        id: 88,
        type: 'recurring',
        topic: 'system.agent.task',
        message: 'Cancelled recurring',
        channel_id: null,
        payload: {},
        fires_at_iso: '2026-03-15T09:00:00',
        status: 'cancelled',
        cron_expr: '*/5 * * * *',
        every_seconds: null,
        until_iso: null,
        created_at: 9,
      },
    ];

    const wrapper = mount(SchedulePage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          ScheduleList: {
            props: ['activeRecurring', 'history'],
            emits: ['resume'],
            template: [
              '<div>',
              '<div class="active-count">{{ activeRecurring.length }}</div>',
              '<div class="history-count">{{ history.length }}</div>',
              '<button class="resume-btn" @click="$emit(\'resume\', activeRecurring[0].id)">Resume</button>',
              '</div>',
            ].join(''),
          },
          CreateScheduleDialog: {
            props: ['open'],
            template: '<div class="dialog" :data-open="open" />',
          },
        },
      },
    });

    expect(wrapper.find('.active-count').text()).toBe('1');
    expect(wrapper.find('.history-count').text()).toBe('1');

    await wrapper.find('.resume-btn').trigger('click');
    expect(scheduleStore.resume).toHaveBeenCalledWith(77);
  });
});
