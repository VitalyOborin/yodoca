<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue';
import { useTaskStore } from '@/entities/task';
import TaskCard from './TaskCard.vue';
import TaskTabs from './TaskTabs.vue';

const store = useTaskStore();

const visibleItems = computed(() => {
  if (store.statusFilter === 'active') return store.activeTasks;
  if (store.statusFilter === 'done') return store.doneTasks;
  if (store.statusFilter === 'all') return store.failedTasks;
  return store.items;
});

onMounted(() => {
  void store.bootstrap();
});

onUnmounted(() => {
  store.stopPolling();
});
</script>

<template>
  <section class="space-y-4">
    <TaskTabs />

    <div
      v-if="store.loading && visibleItems.length === 0"
      class="surface-panel rounded-xl border border-border/70 px-4 py-6 text-sm text-muted-foreground"
    >
      Loading tasks...
    </div>

    <div
      v-else-if="store.isUnavailable"
      class="surface-panel rounded-xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground"
    >
      Task Engine extension is unavailable.
    </div>

    <div
      v-else-if="visibleItems.length === 0"
      class="surface-panel rounded-xl border border-border/70 px-4 py-6 text-sm text-muted-foreground"
    >
      No tasks.
    </div>

    <div v-else class="space-y-2">
      <TaskCard
        v-for="task in visibleItems"
        :key="task.task_id"
        :task="task"
        :active="store.selectedId === task.task_id"
        :cancelling="store.cancelling === task.task_id"
        @cancel="store.cancel(task.task_id, $event)"
        @select="store.selectTask(task.task_id)"
      />
    </div>
  </section>
</template>
