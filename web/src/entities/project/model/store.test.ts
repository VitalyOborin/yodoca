import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import * as api from '@/shared/api';
import { useProjectStore } from './store';

describe('useProjectStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it('loads and sorts projects', async () => {
    vi.spyOn(api, 'fetchProjects').mockResolvedValue([
      {
        id: 'older',
        name: 'Older',
        description: null,
        icon: null,
        instructions: null,
        agent_config: {},
        created_at: 1,
        updated_at: 10,
        files: [],
        links: [],
      },
      {
        id: 'newer',
        name: 'Newer',
        description: null,
        icon: null,
        instructions: null,
        agent_config: {},
        created_at: 1,
        updated_at: 20,
        files: [],
        links: [],
      },
    ]);

    const store = useProjectStore();
    await store.loadProjects();

    expect(store.sortedProjects.map((project) => project.id)).toEqual([
      'newer',
      'older',
    ]);
  });

  it('creates, updates, and removes a project', async () => {
    const created = {
      id: 'proj_1',
      name: 'Alpha',
      description: 'Desc',
      icon: '🚀',
      instructions: null,
      agent_config: {},
      created_at: 1,
      updated_at: 1,
      files: [],
      links: [],
    };
    const updated = { ...created, name: 'Beta', updated_at: 2 };

    vi.spyOn(api, 'createProject').mockResolvedValue(created);
    vi.spyOn(api, 'updateProject').mockResolvedValue(updated);
    vi.spyOn(api, 'deleteProject').mockResolvedValue({ success: true });

    const store = useProjectStore();
    await store.createProject({ name: 'Alpha' });
    await store.updateProject('proj_1', { name: 'Beta' });
    await store.removeProject('proj_1');

    expect(store.activeProject).toBeNull();
    expect(store.projects).toEqual([]);
  });
});
