<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import {
  Clock3,
  FileText,
  FolderOpen,
  Link2,
  Plus,
  Sparkles,
} from 'lucide-vue-next';
import { useProjectStore } from '@/entities/project';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { formatRelativeTimeFromEpoch } from '@/shared/lib';
import { AppNavigationSidebar } from '@/widgets/navigation';

interface CreateProjectDraft {
  name: string;
  description: string;
  icon: string;
  instructions: string;
  files: string;
  links: string;
  agentConfig: string;
}

const router = useRouter();
const projectStore = useProjectStore();

const showCreateDialog = ref(false);
const createError = ref<string | null>(null);

const draft = reactive<CreateProjectDraft>({
  name: '',
  description: '',
  icon: '✨',
  instructions: '',
  files: '',
  links: '',
  agentConfig: '{}',
});

const projects = computed(() => projectStore.sortedProjects);

function normalizeLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function resetDraft() {
  draft.name = '';
  draft.description = '';
  draft.icon = '✨';
  draft.instructions = '';
  draft.files = '';
  draft.links = '';
  draft.agentConfig = '{}';
  createError.value = null;
}

function openCreateDialog() {
  resetDraft();
  showCreateDialog.value = true;
}

function parseAgentConfig(raw: string): Record<string, unknown> {
  const normalized = raw.trim();
  if (!normalized) return {};

  const parsed = JSON.parse(normalized) as unknown;
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Agent config must be a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

async function submitCreateProject() {
  const name = draft.name.trim();
  if (!name) {
    createError.value = 'Project name is required.';
    return;
  }

  createError.value = null;
  try {
    const project = await projectStore.createProject({
      name,
      description: draft.description.trim() || null,
      icon: draft.icon.trim() || null,
      instructions: draft.instructions.trim() || null,
      files: normalizeLines(draft.files),
      links: normalizeLines(draft.links),
      agent_config: parseAgentConfig(draft.agentConfig),
    });
    showCreateDialog.value = false;
    void router.push({
      name: 'project-detail',
      params: { projectId: project.id },
    });
  } catch (cause) {
    createError.value =
      cause instanceof Error ? cause.message : 'Failed to create project.';
  }
}

function openProject(projectId: string) {
  void router.push({ name: 'project-detail', params: { projectId } });
}

onMounted(() => {
  void projectStore.loadProjects();
});
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <ScrollArea class="min-h-0 flex-1">
        <section class="mx-auto flex w-full max-w-7xl flex-col gap-8 px-4 py-6 sm:px-6 lg:px-8">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="max-w-2xl">
              <p class="text-xs font-semibold uppercase tracking-[0.26em] text-subtle-foreground">
                Spaces
              </p>
              <h1 class="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                Projects
              </h1>
              <p class="mt-3 text-sm leading-7 text-muted-foreground sm:text-base">
                Organize threads, instructions, files, and links by topic. Open
                a project to edit its context and continue work in dedicated
                conversations.
              </p>
            </div>

            <Button class="rounded-full px-5" @click="openCreateDialog">
              <Plus class="h-4 w-4" />
              New Project
            </Button>
          </div>

          <div
            v-if="projects.length > 0"
            class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
          >
            <button
              v-for="project in projects"
              :key="project.id"
              type="button"
              class="surface-panel group min-h-[240px] rounded-[0.875rem] border border-border/80 p-5 text-left transition-colors duration-200 hover:border-white/10 hover:bg-white/[0.03]"
              @click="openProject(project.id)"
            >
              <div class="flex h-full flex-col">
                <div class="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-3xl shadow-inner shadow-black/20">
                  {{ project.icon || '📁' }}
                </div>

                <div class="mt-auto">
                  <h2 class="truncate text-xl font-semibold text-foreground">
                    {{ project.name }}
                  </h2>
                  <p class="mt-3 line-clamp-3 text-sm leading-6 text-muted-foreground">
                    {{ project.description || project.instructions || 'No description yet.' }}
                  </p>

                  <div class="mt-5 flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span class="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/30 px-2.5 py-1">
                      <Clock3 class="h-3.5 w-3.5" />
                      {{ formatRelativeTimeFromEpoch(project.updated_at) }}
                    </span>
                    <span class="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/30 px-2.5 py-1">
                      <FileText class="h-3.5 w-3.5" />
                      {{ project.files.length }}
                    </span>
                    <span class="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/30 px-2.5 py-1">
                      <Link2 class="h-3.5 w-3.5" />
                      {{ project.links.length }}
                    </span>
                  </div>
                </div>
              </div>
            </button>
          </div>

          <div
            v-else-if="projectStore.loading"
            class="surface-panel rounded-[2rem] px-6 py-14 text-center text-muted-foreground"
          >
            Loading projects...
          </div>

          <div
            v-else
            class="surface-panel rounded-[2rem] border border-dashed border-border px-6 py-14 text-center"
          >
            <div class="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-2xl text-foreground/80">
              <FolderOpen class="h-7 w-7" />
            </div>
            <h2 class="mt-4 text-xl font-semibold text-foreground">
              No projects yet
            </h2>
            <p class="mx-auto mt-2 max-w-md text-sm leading-7 text-muted-foreground">
              Create your first project to keep related threads, instructions,
              files, and links in one place.
            </p>
            <Button class="mt-6 rounded-full px-5" @click="openCreateDialog">
              <Sparkles class="h-4 w-4" />
              Create first project
            </Button>
            <p v-if="projectStore.error" class="mt-4 text-sm text-destructive">
              {{ projectStore.error }}
            </p>
          </div>
        </section>
      </ScrollArea>
    </div>

    <div
      v-if="showCreateDialog"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm"
      @click.self="showCreateDialog = false"
    >
      <div class="surface-panel w-full max-w-3xl rounded-[2rem] border border-border/90 p-6 shadow-2xl">
        <div class="flex items-start justify-between gap-4">
          <div>
            <p class="text-xs font-semibold uppercase tracking-[0.24em] text-subtle-foreground">
              New project
            </p>
            <h2 class="mt-2 text-2xl font-semibold text-foreground">
              Create a new workspace
            </h2>
          </div>
          <Button
            variant="ghost"
            size="icon"
            class="rounded-full"
            @click="showCreateDialog = false"
          >
            <Plus class="h-4 w-4 rotate-45" />
          </Button>
        </div>

        <div class="mt-6 grid gap-4 sm:grid-cols-[120px_minmax(0,1fr)]">
          <input
            v-model="draft.icon"
            type="text"
            maxlength="8"
            class="focus-ring rounded-2xl border border-border bg-background/70 px-4 py-3 text-center text-3xl text-foreground"
            placeholder="✨"
          />
          <div class="space-y-4">
            <input
              v-model="draft.name"
              type="text"
              maxlength="120"
              class="focus-ring w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-lg font-medium text-foreground"
              placeholder="Project name"
            />
            <textarea
              v-model="draft.description"
              rows="3"
              class="focus-ring min-h-[96px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground"
              placeholder="Short description"
            />
          </div>
        </div>

        <div class="mt-4 grid gap-4 lg:grid-cols-2">
          <textarea
            v-model="draft.instructions"
            rows="7"
            class="focus-ring min-h-[180px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground"
            placeholder="Project instructions"
          />
          <textarea
            v-model="draft.agentConfig"
            rows="7"
            class="focus-ring min-h-[180px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 font-mono text-xs leading-6 text-foreground"
            placeholder='{"model":"yodoca"}'
          />
        </div>

        <div class="mt-4 grid gap-4 lg:grid-cols-2">
          <textarea
            v-model="draft.files"
            rows="5"
            class="focus-ring min-h-[140px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground"
            placeholder="Files: one path per line"
          />
          <textarea
            v-model="draft.links"
            rows="5"
            class="focus-ring min-h-[140px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground"
            placeholder="Links: one URL per line"
          />
        </div>

        <p v-if="createError" class="mt-4 text-sm text-destructive">
          {{ createError }}
        </p>

        <div class="mt-6 flex flex-wrap justify-end gap-3">
          <Button
            variant="secondary"
            class="rounded-full"
            @click="showCreateDialog = false"
          >
            Cancel
          </Button>
          <Button
            class="rounded-full px-5"
            :disabled="projectStore.saving"
            @click="submitCreateProject"
          >
            Create Project
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
