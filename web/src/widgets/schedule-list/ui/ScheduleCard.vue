<script setup lang="ts">
import { computed } from 'vue';
import { BellRing, Bot, Clock3, RefreshCcw } from 'lucide-vue-next';
import cronstrue from 'cronstrue';
import { ScheduleActions } from '@/features/manage-schedule';
import type { ScheduleItem } from '@/shared/api';
import { formatScheduleAbsolute, formatScheduleRelative } from '@/shared/lib';

const props = defineProps<{
  item: ScheduleItem;
  busy?: boolean;
}>();

const emit = defineEmits<{
  pause: [id: number];
  resume: [id: number];
  cancel: [type: 'one_shot' | 'recurring', id: number];
  edit: [item: ScheduleItem];
}>();

const topicLabel = computed(() => {
  if (props.item.topic === 'system.user.notify') return 'Notification';
  if (props.item.topic === 'system.agent.task') return 'Agent Task';
  return 'Background Task';
});

const cronLabel = computed(() => {
  if (!props.item.cron_expr) return null;
  try {
    return cronstrue.toString(props.item.cron_expr);
  } catch {
    return props.item.cron_expr;
  }
});

const statusBadgeClass = computed(() => {
  if (props.item.status === 'paused') {
    return 'border-yellow-300 bg-yellow-300 text-black';
  }
  return 'border-border text-muted-foreground';
});
</script>

<template>
  <article class="surface-panel rounded-2xl border border-border/80 p-4">
    <div class="flex items-start justify-between gap-4">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span
            class="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-muted-foreground"
          >
            <Clock3 v-if="item.type === 'one_shot'" class="h-3.5 w-3.5" />
            <RefreshCcw v-else class="h-3.5 w-3.5" />
            {{ item.type === 'one_shot' ? 'Once' : 'Recurring' }}
          </span>
          <span
            class="inline-flex items-center gap-1 rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-muted-foreground"
          >
            <BellRing v-if="item.topic === 'system.user.notify'" class="h-3.5 w-3.5" />
            <Bot v-else class="h-3.5 w-3.5" />
            {{ topicLabel }}
          </span>
          <span
            class="inline-flex rounded-full border px-2.5 py-1 text-xs uppercase"
            :class="statusBadgeClass"
          >
            {{ item.status }}
          </span>
        </div>
        <p class="mt-3 text-base font-medium text-foreground">
          {{ item.message || 'No message' }}
        </p>
        <p class="mt-1 text-sm text-muted-foreground">
          {{ formatScheduleAbsolute(item.fires_at_iso) }} · {{ formatScheduleRelative(item.fires_at_iso) }}
        </p>
        <p v-if="cronLabel" class="mt-2 text-sm text-muted-foreground">
          {{ cronLabel }}
        </p>
        <p v-else-if="item.every_seconds" class="mt-2 text-sm text-muted-foreground">
          Every {{ item.every_seconds }} seconds
        </p>
      </div>
    </div>

    <div class="mt-4">
      <ScheduleActions
        :type="item.type"
        :status="item.status"
        :disabled="busy"
        @pause="emit('pause', item.id)"
        @resume="emit('resume', item.id)"
        @cancel="emit('cancel', item.type, item.id)"
        @edit="emit('edit', item)"
      />
    </div>
  </article>
</template>
