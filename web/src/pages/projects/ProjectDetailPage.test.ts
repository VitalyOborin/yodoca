import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import ProjectDetailPage from './ProjectDetailPage.vue';

const { push, confirmMock, sendPromptToThread } = vi.hoisted(() => ({
  push: vi.fn(),
  confirmMock: vi.fn(() => true),
  sendPromptToThread: vi.fn(),
}));

const activeProject = {
  id: 'proj_1',
  name: 'Alpha',
  description: 'Alpha description',
  icon: '🚀',
  instructions: 'Project instructions',
  agent_config: { model: 'yodoca' },
  created_at: 1,
  updated_at: 2,
  files: ['README.md'],
  links: ['https://example.com'],
};

const projectStore = {
  activeProject,
  loading: false,
  saving: false,
  error: null,
  getProjectById: vi.fn(() => activeProject),
  loadProject: vi.fn().mockResolvedValue(activeProject),
  updateProject: vi.fn().mockResolvedValue(activeProject),
  removeProject: vi.fn().mockResolvedValue(undefined),
};

const threadStore = {
  threads: [
    { id: 't1', title: 'Alpha thread', project_id: 'proj_1', last_active_at: 10, channel_id: 'web', created_at: 1, is_archived: false },
    { id: 't2', title: 'Other thread', project_id: 'proj_2', last_active_at: 8, channel_id: 'web', created_at: 1, is_archived: false },
  ],
  sortedThreads: [
    { id: 't1', title: 'Alpha thread', project_id: 'proj_1', last_active_at: 10, channel_id: 'web', created_at: 1, is_archived: false },
    { id: 't2', title: 'Other thread', project_id: 'proj_2', last_active_at: 8, channel_id: 'web', created_at: 1, is_archived: false },
  ],
  loadThreads: vi.fn().mockResolvedValue(undefined),
  createThread: vi.fn().mockResolvedValue({
    id: 'new-thread',
    title: null,
    project_id: 'proj_1',
    last_active_at: 11,
    channel_id: 'web',
    created_at: 11,
    is_archived: false,
  }),
  selectThread: vi.fn(),
};

const agentStore = {
  phase: 'idle',
};

const messageStore = {};

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ params: { projectId: 'proj_1' } }),
}));

vi.mock('@/entities/project', () => ({
  useProjectStore: () => projectStore,
}));

vi.mock('@/entities/thread', () => ({
  useThreadStore: () => threadStore,
}));

vi.mock('@/entities/agent', () => ({
  useAgentStore: () => agentStore,
}));

vi.mock('@/entities/message', () => ({
  useMessageStore: () => messageStore,
}));

vi.mock('@/features/send-message', () => ({
  SendMessageForm: {
    template: '<button class="send-trigger" @click="$emit(\'send\', \'Hello project\')">send</button>',
  },
  sendPromptToThread,
}));

vi.mock('@/shared/lib', () => ({
  formatRelativeTimeFromEpoch: () => '2h ago',
}));

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    push.mockReset();
    sendPromptToThread.mockReset();
    projectStore.loadProject.mockClear();
    threadStore.loadThreads.mockClear();
    threadStore.createThread.mockClear();
    threadStore.selectThread.mockClear();
    projectStore.removeProject.mockClear();
    vi.stubGlobal('confirm', confirmMock);
  });

  it('loads the project and renders only matching threads', async () => {
    const wrapper = mount(ProjectDetailPage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          Button: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    });

    await Promise.resolve();

    expect(projectStore.loadProject).toHaveBeenCalledWith('proj_1');
    expect(threadStore.loadThreads).toHaveBeenCalled();
    expect(wrapper.text()).toContain('Alpha thread');
    expect(wrapper.text()).not.toContain('Other thread');
  });

  it('starts a new project thread and navigates to chat', async () => {
    sendPromptToThread.mockResolvedValue('new-thread');

    const wrapper = mount(ProjectDetailPage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          Button: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    });

    await wrapper.find('.send-trigger').trigger('click');

    expect(threadStore.createThread).toHaveBeenCalledWith({
      project_id: 'proj_1',
    });
    expect(push).toHaveBeenCalledWith({
      name: 'chat-thread',
      params: { threadId: 'new-thread' },
    });
    expect(sendPromptToThread).toHaveBeenCalled();
  });

  it('deletes the project and returns to the projects list', async () => {
    const wrapper = mount(ProjectDetailPage, {
      global: {
        stubs: {
          AppNavigationSidebar: { template: '<div />' },
          ScrollArea: { template: '<div><slot /></div>' },
          Button: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    });

    const deleteButton = wrapper.findAll('button').find((item) =>
      item.text().includes('Delete'),
    );
    expect(deleteButton).toBeTruthy();

    await deleteButton?.trigger('click');

    expect(projectStore.removeProject).toHaveBeenCalledWith('proj_1');
    expect(push).toHaveBeenCalledWith({ name: 'projects' });
  });
});
