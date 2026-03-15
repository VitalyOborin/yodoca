import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import SchedulePage from './SchedulePage.vue';

const scheduleStore = {
  schedules: [],
  loading: false,
  saving: false,
  error: null as string | null,
  activeTab: 'once' as 'once' | 'recurring',
  activeOnce: [],
  activeRecurring: [],
  history: [],
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
  });

  it('loads schedules and opens create dialog', async () => {
    const wrapper = mount(SchedulePage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          ScheduleList: {
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
});
