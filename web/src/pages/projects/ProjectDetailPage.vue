<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  ArrowLeft,
  FileText,
  Globe,
  Link2,
  Trash2,
} from 'lucide-vue-next';
import { useAgentStore } from '@/entities/agent';
import { useMessageStore } from '@/entities/message';
import { useProjectStore } from '@/entities/project';
import { useThreadStore } from '@/entities/thread';
import { ThreadComposer, sendPromptToThread } from '@/features/send-message';
import { formatRelativeTimeFromEpoch } from '@/shared/lib';
import { AppNavigationSidebar } from '@/widgets/navigation';
import { Button } from '@/components/ui/button';
import { GlassModal } from '@/components/ui/glass-modal';
import { ScrollArea } from '@/components/ui/scroll-area';

interface ProjectDraft {
  name: string;
  description: string;
  icon: string;
  instructions: string;
  files: string;
  links: string;
  agentConfig: string;
}

const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();
const threadStore = useThreadStore();
const agentStore = useAgentStore();
const messageStore = useMessageStore();

const launchError = ref<string | null>(null);
const saveError = ref<string | null>(null);
const isLaunching = ref(false);
const isProjectConfigModalOpen = ref(false);
const activeEditor = ref<
  'name' | 'description' | 'files' | 'links' | null
>(null);
const nameInput = ref<HTMLInputElement | null>(null);
const descriptionInput = ref<HTMLTextAreaElement | null>(null);
const filesInput = ref<HTMLTextAreaElement | null>(null);
const linksInput = ref<HTMLTextAreaElement | null>(null);

const draft = reactive<ProjectDraft>({
  name: '',
  description: '',
  icon: '',
  instructions: '',
  files: '',
  links: '',
  agentConfig: '{}',
});

const projectId = computed(() => {
  const param = route.params.projectId;
  return Array.isArray(param) ? (param[0] ?? '') : String(param ?? '');
});

const project = computed(() => {
  if (
    projectStore.activeProject &&
    projectStore.activeProject.id === projectId.value
  ) {
    return projectStore.activeProject;
  }
  return projectStore.getProjectById(projectId.value);
});

const projectThreads = computed(() =>
  threadStore.sortedThreads.filter(
    (thread) => thread.project_id === projectId.value,
  ),
);

function normalizeLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function hydrateDraft() {
  if (!project.value) return;
  draft.name = project.value.name;
  draft.description = project.value.description ?? '';
  draft.icon = project.value.icon ?? '';
  draft.instructions = project.value.instructions ?? '';
  draft.files = project.value.files.join('\n');
  draft.links = project.value.links.join('\n');
  draft.agentConfig = JSON.stringify(project.value.agent_config ?? {}, null, 2);
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

async function saveProjectPatch(
  patch:
    | {
        name?: string | null;
        description?: string | null;
        instructions?: string | null;
        files?: string[];
        links?: string[];
        agent_config?: Record<string, unknown> | null;
      },
) {
  if (!project.value) return;
  saveError.value = null;
  try {
    await projectStore.updateProject(project.value.id, patch);
  } catch (cause) {
    saveError.value =
      cause instanceof Error ? cause.message : 'Failed to update project.';
  }
}

function startEdit(
  field:
    | 'name'
    | 'description'
    | 'files'
    | 'links',
) {
  activeEditor.value = field;
}

function cancelEdit(field: typeof activeEditor.value) {
  hydrateDraft();
  saveError.value = null;
  if (activeEditor.value === field) {
    activeEditor.value = null;
  }
}

async function commitName() {
  const name = draft.name.trim();
  if (!project.value) return;
  if (!name) {
    saveError.value = 'Project name is required.';
    draft.name = project.value.name;
    activeEditor.value = null;
    return;
  }
  if (name !== project.value.name) {
    await saveProjectPatch({ name });
  }
  activeEditor.value = null;
}

async function commitDescription() {
  if (!project.value) return;
  const nextDescription = draft.description.trim() || null;
  if (nextDescription !== project.value.description) {
    await saveProjectPatch({ description: nextDescription });
  }
  activeEditor.value = null;
}

async function commitFiles() {
  if (!project.value) return;
  const nextFiles = normalizeLines(draft.files);
  if (JSON.stringify(nextFiles) !== JSON.stringify(project.value.files)) {
    await saveProjectPatch({ files: nextFiles });
  }
  activeEditor.value = null;
}

async function commitLinks() {
  if (!project.value) return;
  const nextLinks = normalizeLines(draft.links);
  if (JSON.stringify(nextLinks) !== JSON.stringify(project.value.links)) {
    await saveProjectPatch({ links: nextLinks });
  }
  activeEditor.value = null;
}

async function saveProjectConfigModal() {
  if (!project.value) return;
  try {
    const nextConfig = parseAgentConfig(draft.agentConfig);
    const patch: {
      instructions?: string | null;
      agent_config?: Record<string, unknown>;
    } = {};
    const nextInstructions = draft.instructions.trim() || null;
    if (nextInstructions !== project.value.instructions) {
      patch.instructions = nextInstructions;
    }
    if (
      JSON.stringify(nextConfig) !== JSON.stringify(project.value.agent_config ?? {})
    ) {
      patch.agent_config = nextConfig;
    }
    if (Object.keys(patch).length > 0) {
      await saveProjectPatch(patch);
    }
    isProjectConfigModalOpen.value = false;
  } catch (cause) {
    saveError.value =
      cause instanceof Error ? cause.message : 'Failed to update project.';
  }
}

function openProjectConfigModal() {
  hydrateDraft();
  saveError.value = null;
  isProjectConfigModalOpen.value = true;
}

async function deleteProject() {
  if (!project.value) return;
  const confirmed = window.confirm(
    `Delete project "${project.value.name}"? Threads will stay available in chat.`,
  );
  if (!confirmed) return;

  try {
    await projectStore.removeProject(project.value.id);
    void router.push({ name: 'projects' });
  } catch (cause) {
    saveError.value =
      cause instanceof Error ? cause.message : 'Failed to delete project.';
  }
}

async function startProjectThread(content: string) {
  if (!project.value) return;

  launchError.value = null;
  isLaunching.value = true;
  try {
    const thread = await threadStore.createThread({
      project_id: project.value.id,
    });
    threadStore.selectThread(thread.id);
    void router.push({ name: 'chat-thread', params: { threadId: thread.id } });
    await sendPromptToThread({
      threadStore,
      messageStore,
      agentStore,
      content,
      threadId: thread.id,
    });
  } catch (cause) {
    launchError.value =
      cause instanceof Error ? cause.message : 'Failed to start project thread.';
  } finally {
    isLaunching.value = false;
  }
}

async function loadProjectData() {
  if (!projectId.value) return;
  saveError.value = null;
  launchError.value = null;
  try {
    await Promise.all([
      projectStore.loadProject(projectId.value),
      threadStore.loadThreads(),
    ]);
  } catch {
    // store state drives the UI
  }
}

watch(
  projectId,
  () => {
    void loadProjectData();
  },
  { immediate: true },
);

watch(
  project,
  (value) => {
    if (value) hydrateDraft();
  },
  { immediate: true },
);

watch(activeEditor, async (field) => {
  if (!field) return;
  await nextTick();
  const focusMap = {
    name: nameInput.value,
    description: descriptionInput.value,
    files: filesInput.value,
    links: linksInput.value,
  } as const;
  focusMap[field]?.focus();
  if (field === 'name') {
    nameInput.value?.select();
  }
});

onMounted(() => {
  if (!threadStore.threads.length) {
    void threadStore.loadThreads();
  }
});
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <ScrollArea class="min-h-0 flex-1">
        <div class="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-5 sm:px-6 lg:px-8">
          <div class="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
            <div class="flex min-w-0 items-center gap-3">
              <button
                type="button"
                class="focus-ring inline-flex items-center gap-2 rounded-full border border-border bg-secondary/40 px-3 py-1.5 transition-colors hover:bg-secondary/70 hover:text-foreground"
                @click="router.push({ name: 'projects' })"
              >
                <ArrowLeft class="h-4 w-4" />
                <span>Projects</span>
              </button>
              <span>/</span>
              <span class="truncate text-foreground/90">
                {{ project?.name ?? 'Project' }}
              </span>
            </div>

            <div v-if="project" class="flex flex-wrap items-center gap-2">
              <Button
                variant="destructive"
                class="rounded-full"
                :disabled="projectStore.saving"
                @click="deleteProject"
              >
                <Trash2 class="h-4 w-4" />
                Delete
              </Button>
            </div>
          </div>

          <div
            v-if="projectStore.loading && !project"
            class="surface-panel rounded-3xl px-6 py-12 text-center text-muted-foreground"
          >
            Loading project...
          </div>

          <div
            v-else-if="!project"
            class="surface-panel rounded-3xl px-6 py-12 text-center"
          >
            <p class="text-lg font-semibold text-foreground">Project not found</p>
            <p class="mt-2 text-sm text-muted-foreground">
              {{ projectStore.error ?? 'The project could not be loaded.' }}
            </p>
          </div>

          <template v-else>
            <section class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
              <div class="surface-panel rounded-[2rem] border border-border/80 p-6 sm:p-8">
                <div class="flex flex-wrap items-start justify-between gap-4">
                  <div class="min-w-0">
                    <div class="mb-5 flex items-center gap-4 sm:gap-5">
                      <div class="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-3xl shadow-inner shadow-black/20">
                      {{ project.icon || '📁' }}
                      </div>
                      <div class="min-w-0 flex-1">
                        <template v-if="activeEditor === 'name'">
                          <input
                            ref="nameInput"
                            v-model="draft.name"
                            type="text"
                            maxlength="120"
                            class="block w-full border-0 bg-transparent m-0 p-0 text-3xl font-semibold tracking-tight leading-tight text-foreground outline-none sm:text-4xl"
                            placeholder="Project name"
                            @blur="commitName"
                            @keydown.enter.prevent="commitName"
                            @keydown.esc.prevent="cancelEdit('name')"
                          />
                        </template>
                        <template v-else>
                          <button
                            type="button"
                            class="block w-full text-left"
                            @click="startEdit('name')"
                          >
                            <h1 class="m-0 text-3xl font-semibold tracking-tight leading-tight text-foreground sm:text-4xl">
                              {{ project.name }}
                            </h1>
                          </button>
                        </template>
                      </div>
                    </div>

                    <div class="mt-4">
                      <template v-if="activeEditor === 'description'">
                        <textarea
                          ref="descriptionInput"
                          v-model="draft.description"
                          rows="3"
                          class="min-h-[96px] w-full resize-none border-0 bg-transparent p-0 text-base leading-7 text-foreground/90 outline-none sm:text-lg"
                          placeholder="Short project description"
                          @blur="commitDescription"
                          @keydown.esc.prevent="cancelEdit('description')"
                        />
                      </template>
                      <button
                        v-else
                        type="button"
                        class="block max-w-3xl text-left"
                        @click="startEdit('description')"
                      >
                        <p class="text-base leading-7 text-foreground/90 sm:text-lg">
                          {{ project.description || 'Add a short description for this project.' }}
                        </p>
                      </button>
                    </div>
                  </div>
                </div>

                <div class="mt-8 space-y-4">
                  <div>
                    <p class="mb-2 text-xs font-semibold uppercase tracking-[0.24em] text-subtle-foreground">
                      Start a new thread
                    </p>
                    <ThreadComposer
                      :disabled="isLaunching || agentStore.phase === 'thinking'"
                      :phase="agentStore.phase"
                      :current-step="agentStore.currentStep"
                      @send="startProjectThread"
                    />
                    <p v-if="launchError" class="mt-3 text-sm text-destructive">
                      {{ launchError }}
                    </p>
                  </div>

                  <p v-if="saveError" class="text-sm text-destructive">
                    {{ saveError }}
                  </p>
                </div>

                <section class="mt-10 border-t border-border/80 pt-8">
                  <div>
                    <p class="text-xs font-semibold uppercase tracking-[0.24em] text-subtle-foreground">
                      Project threads
                    </p>
                    <h2 class="mt-2 text-2xl font-semibold text-foreground">
                      Previous conversations
                    </h2>
                  </div>

                  <div
                    v-if="projectThreads.length === 0"
                    class="mt-6 rounded-3xl border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground"
                  >
                    No threads yet. Start the first request above.
                  </div>

                  <div v-else class="mt-6 space-y-3">
                    <button
                      v-for="thread in projectThreads"
                      :key="thread.id"
                      type="button"
                      class="focus-ring flex w-full items-start justify-between gap-4 rounded-3xl border border-border bg-secondary/20 px-5 py-4 text-left transition-colors hover:bg-secondary/35"
                      @click="router.push({ name: 'chat-thread', params: { threadId: thread.id } })"
                    >
                      <div class="min-w-0">
                        <p class="truncate text-base font-semibold text-foreground">
                          {{ thread.title || 'New conversation' }}
                        </p>
                        <p class="mt-1 text-sm text-muted-foreground">
                          Open this thread in chat to continue the discussion.
                        </p>
                      </div>
                      <span class="shrink-0 text-xs uppercase tracking-[0.2em] text-subtle-foreground">
                        {{ formatRelativeTimeFromEpoch(thread.last_active_at) }}
                      </span>
                    </button>
                  </div>
                </section>
              </div>

              <aside class="space-y-4">
                <section class="surface-panel rounded-[2rem] border border-border/80 p-5">
                  <div class="space-y-6">
                    <div class="space-y-4">
                      <div class="flex items-center gap-2">
                        <FileText class="h-4 w-4 text-muted-foreground" />
                        <h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-subtle-foreground">
                          Files
                        </h2>
                      </div>
                        <p class="text-sm leading-6 text-muted-foreground">
                          Files and references available as reusable project context.
                        </p>
                        <textarea
                          v-if="activeEditor === 'files'"
                          ref="filesInput"
                          v-model="draft.files"
                          rows="6"
                          class="min-h-[140px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground outline-none"
                          placeholder="One file path per line"
                          @blur="commitFiles"
                          @keydown.esc.prevent="cancelEdit('files')"
                        />
                      <button
                        v-else
                        type="button"
                        class="block w-full text-left"
                        @click="startEdit('files')"
                      >
                      <ul class="space-y-2">
                        <li
                          v-for="file in project.files"
                          :key="file"
                          class="rounded-xl border border-border/80 bg-secondary/20 px-3 py-2 text-sm text-foreground/85"
                        >
                          {{ file }}
                        </li>
                        <li
                          v-if="project.files.length === 0"
                          class="text-sm text-muted-foreground"
                        >
                          No files added.
                        </li>
                      </ul>
                      </button>
                    </div>

                    <div class="border-t border-border/80 pt-6">
                      <div class="space-y-4">
                        <div class="flex items-center gap-2">
                          <Link2 class="h-4 w-4 text-muted-foreground" />
                          <h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-subtle-foreground">
                            Links
                          </h2>
                        </div>
                        <p class="text-sm leading-6 text-muted-foreground">
                          External resources that should stay attached to this project.
                        </p>
                        <textarea
                          v-if="activeEditor === 'links'"
                          ref="linksInput"
                          v-model="draft.links"
                          rows="6"
                          class="min-h-[140px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground outline-none"
                          placeholder="One URL per line"
                          @blur="commitLinks"
                          @keydown.esc.prevent="cancelEdit('links')"
                        />
                        <button
                          v-else
                          type="button"
                          class="block w-full text-left"
                          @click="startEdit('links')"
                        >
                        <ul class="space-y-2">
                          <li v-for="link in project.links" :key="link">
                            <a
                              :href="link"
                              target="_blank"
                              rel="noreferrer"
                              class="focus-ring flex items-center gap-2 rounded-xl border border-border/80 bg-secondary/20 px-3 py-2 text-sm text-info transition-colors hover:bg-secondary/35"
                            >
                              <Globe class="h-4 w-4" />
                              <span class="truncate">{{ link }}</span>
                            </a>
                          </li>
                          <li
                            v-if="project.links.length === 0"
                            class="text-sm text-muted-foreground"
                          >
                            No links added.
                          </li>
                        </ul>
                        </button>
                      </div>
                    </div>

                    <div class="border-t border-border/80 pt-6">
                      <div class="space-y-4">
                        <button
                          type="button"
                          class="text-sm font-medium text-info transition-colors hover:text-info/80"
                          @click="openProjectConfigModal"
                        >
                          Редактировать инструкции
                        </button>
                      </div>
                    </div>
                  </div>
                </section>
              </aside>
            </section>
          </template>
        </div>
      </ScrollArea>
    </div>

    <GlassModal
      :open="isProjectConfigModalOpen"
      title="Project instructions"
      description="Edit the long-form instructions and raw agent config for this project."
      @close="isProjectConfigModalOpen = false"
    >
      <div class="space-y-5">
        <div>
          <p class="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-subtle-foreground">
            Instructions
          </p>
          <textarea
            v-model="draft.instructions"
            rows="10"
            class="min-h-[220px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground outline-none"
            placeholder="Detailed instructions for this project"
          />
        </div>

        <div>
          <p class="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-subtle-foreground">
            Agent Config
          </p>
          <textarea
            v-model="draft.agentConfig"
            rows="10"
            class="min-h-[220px] w-full rounded-2xl border border-border bg-background/70 px-4 py-3 font-mono text-xs leading-6 text-foreground outline-none"
            placeholder='{"model":"yodoca"}'
          />
        </div>

        <p v-if="saveError" class="text-sm text-destructive">
          {{ saveError }}
        </p>

        <div class="flex justify-end gap-3">
          <Button
            variant="secondary"
            class="rounded-full"
            @click="isProjectConfigModalOpen = false"
          >
            Cancel
          </Button>
          <Button
            class="rounded-full px-5"
            :disabled="projectStore.saving"
            @click="saveProjectConfigModal"
          >
            Save
          </Button>
        </div>
      </div>
    </GlassModal>
  </div>
</template>
