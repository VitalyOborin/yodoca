import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import ScheduleActions from './ScheduleActions.vue';

describe('ScheduleActions', () => {
  it('does not show Cancel button for cancelled recurring schedules', () => {
    const wrapper = mount(ScheduleActions, {
      props: {
        type: 'recurring',
        status: 'cancelled',
      },
      global: {
        stubs: {
          Button: { template: '<button><slot /></button>' },
        },
      },
    });

    expect(wrapper.text()).not.toContain('Cancel');
  });

  it('does not show Edit button for fired recurring schedules', () => {
    const wrapper = mount(ScheduleActions, {
      props: {
        type: 'recurring',
        status: 'fired',
      },
      global: {
        stubs: {
          Button: { template: '<button><slot /></button>' },
        },
      },
    });

    expect(wrapper.text()).not.toContain('Edit');
  });
});
