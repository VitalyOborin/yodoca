<script setup lang="ts">
import { computed } from 'vue';
import { Button } from '@/components/ui/button';
import type { ScheduleItem } from '@/shared/api';
import type { ScheduleTab } from '@/entities/schedule';
import ScheduleCard from './ScheduleCard.vue';
import ScheduleEmptyState from './ScheduleEmptyState.vue';

const props = defineProps<{
  loading?: boolean;
  saving?: boolean;
  activeTab: ScheduleTab;
  activeOnce: ScheduleItem[];
  activeRecurring: ScheduleItem[];
  history: ScheduleItem[];
}>();

const emit = defineEmits<{
  changeTab: [tab: ScheduleTab];
  create: [];
  pause: [id: number];
  resume: [id: number];
  cancel: [type: 'one_shot' | 'recurring', id: number];
  edit: [item: ScheduleItem];
}>();

const activeItems = computed(() =>
  props.activeTab === 'once' ? props.activeOnce : props.activeRecurring,
);

function forwardCancel(type: 'one_shot' | 'recurring', id: number) {
  emit('cancel', type, id);
}
</script>

<template>
  <section class="space-y-6">
    <div class="flex items-center justify-between gap-3">
      <h2 class="text-xl font-semibold text-foreground">Active schedules</h2>
      <Button class="rounded-full px-5" @click="emit('create')">New schedule</Button>
    </div>

    <div class="flex gap-2">
      <Button
        size="sm"
        :variant="activeTab === 'once' ? 'default' : 'secondary'"
        @click="emit('changeTab', 'once')"
      >
        Once
      </Button>
      <Button
        size="sm"
        :variant="activeTab === 'recurring' ? 'default' : 'secondary'"
        @click="emit('changeTab', 'recurring')"
      >
        Recurring
      </Button>
    </div>

    <div v-if="loading" class="surface-panel rounded-2xl px-4 py-8 text-center text-muted-foreground">
      Loading schedules...
    </div>
    <div v-else-if="activeItems.length > 0" class="space-y-3">
      <ScheduleCard
        v-for="item in activeItems"
        :key="`${item.type}-${item.id}`"
        :item="item"
        :busy="saving"
        @pause="emit('pause', $event)"
        @resume="emit('resume', $event)"
        @cancel="forwardCancel"
        @edit="emit('edit', $event)"
      />
    </div>
    <ScheduleEmptyState
      v-else
      title="No active schedules"
      description="Create a schedule to see active reminders and recurring tasks."
      @create="emit('create')"
    />

    <div class="pt-2">
      <h2 class="mb-3 text-xl font-semibold text-foreground">History</h2>
      <div v-if="history.length > 0" class="space-y-3">
        <ScheduleCard
          v-for="item in history"
          :key="`history-${item.type}-${item.id}`"
          :item="item"
          :busy="saving"
          @pause="emit('pause', $event)"
          @resume="emit('resume', $event)"
          @cancel="forwardCancel"
          @edit="emit('edit', $event)"
        />
      </div>
      <p v-else class="text-sm text-muted-foreground">No history yet.</p>
    </div>
  </section>
</template>
