<script setup lang="ts">
import { computed } from 'vue';
import { Button } from '@/components/ui/button';
import { useTaskStore } from '@/entities/task';

const store = useTaskStore();

const isActiveTab = computed(() => store.statusFilter === 'active');
const isDoneTab = computed(() => store.statusFilter === 'done');
const isFailedTab = computed(() => store.statusFilter === 'all');

function selectActive() {
  if (store.statusFilter === 'active') return;
  store.statusFilter = 'active';
  void store.loadTasks();
}

function selectDone() {
  if (store.statusFilter === 'done') return;
  store.statusFilter = 'done';
  void store.loadTasks();
}

function selectFailed() {
  if (store.statusFilter === 'all') return;
  store.statusFilter = 'all';
  void store.loadTasks();
}
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <Button
      size="sm"
      :variant="isActiveTab ? 'default' : 'secondary'"
      class="rounded-full"
      @click="selectActive"
    >
      Active
      <span
        v-if="store.activeCount > 0"
        class="ml-1 rounded-full bg-info/90 px-1.5 py-0.5 text-[10px] leading-none text-white"
      >
        {{ store.activeCount > 99 ? '99+' : store.activeCount }}
      </span>
    </Button>
    <Button
      size="sm"
      :variant="isDoneTab ? 'default' : 'secondary'"
      class="rounded-full"
      @click="selectDone"
    >
      Done
      <span
        v-if="store.doneTasks.length > 0"
        class="ml-1 rounded-full bg-emerald-500/90 px-1.5 py-0.5 text-[10px] leading-none text-white"
      >
        {{ store.doneTasks.length }}
      </span>
    </Button>
    <Button
      size="sm"
      :variant="isFailedTab ? 'default' : 'secondary'"
      class="rounded-full"
      @click="selectFailed"
    >
      Failed
      <span
        v-if="store.failedTasks.length > 0"
        class="ml-1 rounded-full bg-destructive/90 px-1.5 py-0.5 text-[10px] leading-none text-white"
      >
        {{ store.failedTasks.length }}
      </span>
    </Button>
  </div>
</template>
