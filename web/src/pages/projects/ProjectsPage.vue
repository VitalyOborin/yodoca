<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Clock3, FileText, FolderOpen, Link2, Plus, Sparkles } from 'lucide-vue-next';
import { useProjectStore } from '@/entities/project';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { formatRelativeTimeFromEpoch } from '@/shared/lib';
import { AppNavigationSidebar } from '@/widgets/navigation';

const router = useRouter();
const projectStore = useProjectStore();

const createError = ref<string | null>(null);

const projects = computed(() => projectStore.sortedProjects);

async function submitCreateProject() {
  createError.value = null;
  try {
    const project = await projectStore.createProject({
      name: 'Новый проект',
    });
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
              <h1 class="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                Projects
              </h1>
              <p class="mt-3 text-sm leading-7 text-muted-foreground sm:text-base">
                Organize threads, instructions, files, and links by topic. Open
                a project to edit its context and continue work in dedicated
                conversations.
              </p>
            </div>

            <Button
              class="rounded-full px-5"
              :disabled="projectStore.saving"
              @click="submitCreateProject"
            >
              <Plus class="h-4 w-4" />
              New Project
            </Button>
          </div>

          <p v-if="createError" class="text-sm text-destructive">
            {{ createError }}
          </p>

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
            <Button
              class="mt-6 rounded-full px-5"
              :disabled="projectStore.saving"
              @click="submitCreateProject"
            >
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
  </div>
</template>
