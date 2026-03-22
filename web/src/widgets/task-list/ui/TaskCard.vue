<script setup lang="ts">
import { Ban, CheckCircle2, CircleAlert, LoaderCircle } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';
import { formatRelative } from '@/shared/lib';
import type { TaskItem } from '@/shared/api';

const props = defineProps<{
  task: TaskItem;
  cancelling?: boolean;
  active?: boolean;
}>();

const emit = defineEmits<{
  cancel: [reason: string];
  select: [];
}>();

function progressPercent(task: TaskItem): number {
  const max = Math.max(1, task.max_steps);
  return Math.max(0, Math.min(100, Math.round((task.step / max) * 100)));
}

function isCancellable(task: TaskItem): boolean {
  return (
    task.status === 'pending' ||
    task.status === 'blocked' ||
    task.status === 'running' ||
    task.status === 'retry_scheduled' ||
    task.status === 'waiting_subtasks' ||
    task.status === 'human_review'
  );
}

function requestCancel() {
  const reason = window.prompt('Cancellation reason (optional):', '') ?? '';
  emit('cancel', reason);
}

function statusClass(status: TaskItem['status']): string {
  if (status === 'done') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  if (status === 'failed' || status === 'cancelled') {
    return 'bg-destructive/15 text-destructive border-destructive/40';
  }
  return 'bg-info/10 text-info border-info/30';
}

function statusIcon(status: TaskItem['status']) {
  if (status === 'done') return CheckCircle2;
  if (status === 'failed' || status === 'cancelled') return CircleAlert;
  return LoaderCircle;
}
</script>

<template>
  <article
    class="surface-panel rounded-xl border p-4 transition-colors"
    :class="[
      props.active
        ? 'border-primary/40 bg-primary/10'
        : 'border-border/70 hover:border-white/20 hover:bg-white/[0.02]',
    ]"
  >
    <div class="flex items-start justify-between gap-3">
      <button type="button" class="min-w-0 flex-1 text-left" @click="emit('select')">
        <p class="truncate text-sm font-semibold text-foreground">{{ props.task.goal }}</p>
        <p class="mt-1 truncate text-xs text-muted-foreground">
          {{ props.task.agent_id }} · {{ props.task.task_id }}
        </p>
      </button>
      <span
        class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium"
        :class="statusClass(props.task.status)"
      >
        <component :is="statusIcon(props.task.status)" class="h-3 w-3" />
        {{ props.task.status }}
      </span>
    </div>

    <div class="mt-3 space-y-1">
      <div class="h-1.5 overflow-hidden rounded-full bg-secondary/70">
        <div
          class="h-full rounded-full bg-info transition-[width] duration-300"
          :style="{ width: `${progressPercent(props.task)}%` }"
        />
      </div>
      <div class="flex items-center justify-between text-xs text-muted-foreground">
        <span>Step {{ props.task.step }} / {{ props.task.max_steps }}</span>
        <span>{{ progressPercent(props.task) }}%</span>
      </div>
    </div>

    <p
      v-if="props.task.partial_result"
      class="mt-3 line-clamp-2 text-xs leading-5 text-foreground/80"
    >
      {{ props.task.partial_result }}
    </p>
    <p v-if="props.task.error" class="mt-2 line-clamp-2 text-xs leading-5 text-destructive">
      {{ props.task.error }}
    </p>

    <div class="mt-3 flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">
        Updated {{ formatRelative(new Date(props.task.updated_at * 1000)) }}
      </span>
      <Button
        v-if="isCancellable(props.task)"
        variant="outline"
        size="sm"
        class="rounded-full px-3"
        :disabled="props.cancelling"
        @click="requestCancel"
      >
        <Ban class="mr-1 h-3.5 w-3.5" />
        {{ props.cancelling ? 'Cancelling...' : 'Cancel' }}
      </Button>
    </div>
  </article>
</template>
