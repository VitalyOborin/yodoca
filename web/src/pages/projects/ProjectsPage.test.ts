import { describe, expect, it, vi, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import ProjectsPage from './ProjectsPage.vue';

const push = vi.fn();

const projectStore = {
  sortedProjects: [
    {
      id: 'proj_1',
      name: 'Alpha',
      description: 'Alpha description',
      icon: '🚀',
      instructions: null,
      agent_config: {},
      created_at: 1,
      updated_at: 2,
      files: ['README.md'],
      links: ['https://example.com'],
    },
  ],
  loading: false,
  saving: false,
  error: null,
  loadProjects: vi.fn(),
  createProject: vi.fn(),
};

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}));

vi.mock('@/entities/project', () => ({
  useProjectStore: () => projectStore,
}));

vi.mock('@/shared/lib', () => ({
  formatRelativeTimeFromEpoch: () => '2h ago',
}));

describe('ProjectsPage', () => {
  beforeEach(() => {
    push.mockReset();
    projectStore.loadProjects.mockReset();
  });

  it('loads projects and navigates on card click', async () => {
    const wrapper = mount(ProjectsPage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          Button: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    });

    expect(projectStore.loadProjects).toHaveBeenCalled();
    expect(wrapper.text()).toContain('Alpha');

    await wrapper.find('button.surface-panel').trigger('click');
    expect(push).toHaveBeenCalledWith({
      name: 'project-detail',
      params: { projectId: 'proj_1' },
    });
  });
});
