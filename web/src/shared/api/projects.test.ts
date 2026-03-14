import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createProject,
  deleteProject,
  fetchProject,
  fetchProjects,
  updateProject,
} from './projects';

describe('project api client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches project list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          projects: [
            {
              id: 'proj_1',
              name: 'Alpha',
              description: 'Desc',
              icon: '🚀',
              instructions: null,
              agent_config: {},
              created_at: 1,
              updated_at: 2,
              files: [],
              links: [],
            },
          ],
        }),
      }),
    );

    await expect(fetchProjects()).resolves.toEqual([
      expect.objectContaining({ id: 'proj_1', icon: '🚀' }),
    ]);
  });

  it('sends CRUD requests to project endpoints', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          project: {
            id: 'proj_1',
            name: 'Alpha',
            description: null,
            icon: null,
            instructions: null,
            agent_config: {},
            created_at: 1,
            updated_at: 1,
            files: [],
            links: [],
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          project: {
            id: 'proj_1',
            name: 'Updated',
            description: null,
            icon: null,
            instructions: null,
            agent_config: {},
            created_at: 1,
            updated_at: 2,
            files: [],
            links: [],
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({ success: true }),
      })
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({
          project: {
            id: 'proj_1',
            name: 'Updated',
            description: null,
            icon: null,
            instructions: null,
            agent_config: {},
            created_at: 1,
            updated_at: 2,
            files: [],
            links: [],
          },
        }),
      });
    vi.stubGlobal('fetch', fetchMock);

    await createProject({ name: 'Alpha' });
    await updateProject('proj_1', { name: 'Updated' });
    await deleteProject('proj_1');
    await fetchProject('proj_1');

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/projects',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/projects/proj_1',
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/projects/proj_1',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/projects/proj_1',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    );
  });
});
