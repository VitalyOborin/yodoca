import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  createProject as apiCreateProject,
  deleteProject as apiDeleteProject,
  fetchProject,
  fetchProjects,
  updateProject as apiUpdateProject,
  type CreateProjectRequest,
  type Project,
  type UpdateProjectRequest,
} from '@/shared/api';

export const useProjectStore = defineStore('projects', () => {
  const projects = ref<Project[]>([]);
  const activeProject = ref<Project | null>(null);
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);

  const sortedProjects = computed(() =>
    [...projects.value].sort((a, b) => b.updated_at - a.updated_at),
  );

  function upsertProject(project: Project) {
    const index = projects.value.findIndex((item) => item.id === project.id);
    if (index >= 0) {
      projects.value[index] = project;
    } else {
      projects.value.unshift(project);
    }
  }

  async function loadProjects() {
    loading.value = true;
    error.value = null;
    try {
      projects.value = await fetchProjects();
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to load projects';
      projects.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function loadProject(id: string) {
    loading.value = true;
    error.value = null;
    try {
      const project = await fetchProject(id);
      activeProject.value = project;
      upsertProject(project);
      return project;
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to load project';
      activeProject.value = null;
      throw cause;
    } finally {
      loading.value = false;
    }
  }

  async function createProject(payload: CreateProjectRequest) {
    saving.value = true;
    error.value = null;
    try {
      const project = await apiCreateProject(payload);
      upsertProject(project);
      activeProject.value = project;
      return project;
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to create project';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  async function updateProject(id: string, payload: UpdateProjectRequest) {
    saving.value = true;
    error.value = null;
    try {
      const project = await apiUpdateProject(id, payload);
      upsertProject(project);
      if (activeProject.value?.id === id) {
        activeProject.value = project;
      }
      return project;
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to update project';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  async function removeProject(id: string) {
    saving.value = true;
    error.value = null;
    try {
      await apiDeleteProject(id);
      projects.value = projects.value.filter((project) => project.id !== id);
      if (activeProject.value?.id === id) {
        activeProject.value = null;
      }
    } catch (cause) {
      error.value =
        cause instanceof Error ? cause.message : 'Failed to delete project';
      throw cause;
    } finally {
      saving.value = false;
    }
  }

  function getProjectById(id: string) {
    return projects.value.find((project) => project.id === id) ?? null;
  }

  return {
    projects,
    activeProject,
    loading,
    saving,
    error,
    sortedProjects,
    loadProjects,
    loadProject,
    createProject,
    updateProject,
    removeProject,
    getProjectById,
  };
});
