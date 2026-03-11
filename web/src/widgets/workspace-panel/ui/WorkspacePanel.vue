<script setup lang="ts">
import { computed } from 'vue';
import { CircleCheck, CircleX, LoaderCircle, OctagonX, PauseCircle, PlayCircle } from 'lucide-vue-next';
import { useAgentStore } from '@/entities/agent';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { formatMessageTime } from '@/shared/lib';

const agentStore = useAgentStore();

const phaseLabel = computed(() => {
  const phaseMap = {
    idle: 'Idle',
    thinking: 'Thinking',
    acting: 'Executing',
    waiting_input: 'Waiting for input',
    error: 'Error',
    complete: 'Complete',
  } as const;

  return phaseMap[agentStore.phase] ?? 'Idle';
});

const phaseClass = computed(() => {
  const classMap = {
    idle: 'text-muted-foreground',
    thinking: 'text-primary',
    acting: 'text-[hsl(var(--info))]',
    waiting_input: 'text-[hsl(var(--warning))]',
    error: 'text-destructive',
    complete: 'text-[hsl(var(--success))]',
  } as const;

  return classMap[agentStore.phase] ?? 'text-muted-foreground';
});

function statusIcon(status: 'pending' | 'running' | 'done' | 'error') {
  if (status === 'running') return LoaderCircle;
  if (status === 'done') return CircleCheck;
  if (status === 'error') return CircleX;
  return PauseCircle;
}
</script>

<template>
  <aside class="surface-panel hidden min-w-0 flex-1 flex-col rounded-xl xl:flex xl:max-w-[58%]">
    <header class="border-b border-border px-5 py-4">
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-xs uppercase tracking-[0.2em] text-muted-foreground">Agent status</p>
          <div class="mt-2 flex items-center gap-3">
            <span class="agent-orb h-2.5 w-2.5 rounded-full" />
            <p class="text-base font-semibold text-foreground">{{ phaseLabel }}</p>
            <span class="text-sm" :class="phaseClass">{{ agentStore.currentStep }}</span>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <div class="min-w-24 rounded-md border border-border bg-secondary/60 px-2.5 py-1.5 text-xs">
            <p class="text-muted-foreground">Confidence</p>
            <p class="mt-0.5 font-medium text-foreground">{{ agentStore.confidence }}%</p>
          </div>

          <Button
            variant="destructive"
            size="sm"
            class="focus-ring"
            :disabled="!agentStore.canStop"
            @click="agentStore.stopRun"
          >
            <OctagonX class="mr-1 h-4 w-4" />
            Stop
          </Button>
        </div>
      </div>
    </header>

    <div class="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 md:grid-cols-2">
      <section class="surface-panel min-h-0 rounded-lg p-4">
        <h3 class="text-sm font-semibold text-foreground">Intent preview</h3>
        <ol class="mt-3 space-y-2 text-sm text-muted-foreground">
          <li v-for="(step, index) in agentStore.intentPreview" :key="step" class="flex gap-2">
            <span class="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs text-primary">
              {{ index + 1 }}
            </span>
            <span>{{ step }}</span>
          </li>
        </ol>
      </section>

      <section class="surface-panel min-h-0 rounded-lg p-4">
        <div class="mb-3 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-foreground">Editable drafts</h3>
          <Button variant="ghost" size="sm" class="focus-ring text-muted-foreground">
            <PlayCircle class="mr-1 h-4 w-4" />
            Apply
          </Button>
        </div>

        <div class="space-y-2">
          <article
            v-for="draft in agentStore.drafts"
            :key="draft.id"
            class="rounded-md border border-border bg-secondary/40 p-3"
          >
            <p class="text-sm font-medium text-foreground">{{ draft.title }}</p>
            <p class="mt-1 text-xs leading-5 text-muted-foreground">{{ draft.description }}</p>
            <div class="mt-2 flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                class="focus-ring h-7 px-2.5"
                @click="agentStore.updateDraftStatus(draft.id, 'approved')"
              >
                Accept
              </Button>
              <Button
                size="sm"
                variant="outline"
                class="focus-ring h-7 px-2.5"
                @click="agentStore.requireInput('Пользователь редактирует черновик перед запуском')"
              >
                Edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                class="focus-ring h-7 px-2.5 text-destructive"
                @click="agentStore.updateDraftStatus(draft.id, 'rejected')"
              >
                Reject
              </Button>
            </div>
          </article>
        </div>
      </section>

      <section class="surface-panel md:col-span-2 min-h-0 rounded-lg p-4">
        <h3 class="text-sm font-semibold text-foreground">Action audit trail</h3>
        <ScrollArea class="mt-3 h-[260px] rounded-md border border-border bg-secondary/40 p-2">
          <div class="space-y-2">
            <article
              v-for="entry in agentStore.auditTrail"
              :key="entry.id"
              class="animate-enter flex items-start justify-between gap-3 rounded-md border border-border/70 bg-background/60 p-2.5"
            >
              <div class="min-w-0">
                <p class="flex items-center gap-2 text-sm text-foreground">
                  <component
                    :is="statusIcon(entry.status)"
                    :class="[
                      'h-4 w-4 shrink-0',
                      entry.status === 'done'
                        ? 'text-[hsl(var(--success))]'
                        : entry.status === 'error'
                          ? 'text-destructive'
                          : entry.status === 'running'
                            ? 'animate-spin text-primary'
                            : 'text-amber-300',
                    ]"
                  />
                  <span class="truncate">{{ entry.title }}</span>
                </p>
                <p v-if="entry.detail" class="mt-1 text-xs text-muted-foreground">{{ entry.detail }}</p>
              </div>
              <time class="shrink-0 text-xs text-subtle-foreground">{{ formatMessageTime(entry.at) }}</time>
            </article>
          </div>
        </ScrollArea>
      </section>
    </div>
  </aside>
</template>
