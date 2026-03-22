import { apiFetch } from './http';

export interface Project {
  id: string;
  name: string;
  description: string | null;
  icon: string | null;
  instructions: string | null;
  agent_config: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  files: string[];
  links: string[];
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface ProjectResponse {
  project: Project;
}

export interface CreateProjectRequest {
  name: string;
  description?: string | null;
  icon?: string | null;
  instructions?: string | null;
  agent_config?: Record<string, unknown> | null;
  files?: string[];
  links?: string[];
}

export interface UpdateProjectRequest {
  name?: string | null;
  description?: string | null;
  icon?: string | null;
  instructions?: string | null;
  agent_config?: Record<string, unknown> | null;
  files?: string[] | null;
  links?: string[] | null;
}

export interface OperationResult {
  success: boolean;
  message?: string | null;
}

export async function fetchProjects(): Promise<Project[]> {
  const data = await apiFetch<ProjectsResponse>('/api/projects');
  return data.projects;
}

export async function fetchProject(id: string): Promise<Project> {
  const data = await apiFetch<ProjectResponse>(
    `/api/projects/${encodeURIComponent(id)}`,
  );
  return data.project;
}

export async function createProject(
  payload: CreateProjectRequest,
): Promise<Project> {
  const data = await apiFetch<ProjectResponse>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return data.project;
}

export async function updateProject(
  id: string,
  payload: UpdateProjectRequest,
): Promise<Project> {
  const data = await apiFetch<ProjectResponse>(
    `/api/projects/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  );
  return data.project;
}

export async function deleteProject(id: string): Promise<OperationResult> {
  return apiFetch<OperationResult>(`/api/projects/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}
