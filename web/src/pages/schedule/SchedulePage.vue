<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useIntervalFn } from '@vueuse/core';
import { AppNavigationSidebar } from '@/widgets/navigation';
import { useScheduleStore } from '@/entities/schedule';
import { CreateScheduleDialog } from '@/features/create-schedule';
import { ScrollArea } from '@/components/ui/scroll-area';
import type {
  CreateOnceRequest,
  CreateRecurringRequest,
  ScheduleItem,
  UpdateRecurringRequest,
} from '@/shared/api';
import { ScheduleList } from '@/widgets/schedule-list';

const scheduleStore = useScheduleStore();
const dialogOpen = ref(false);
const editItem = ref<ScheduleItem | null>(null);
const schedulerUnavailableRetries = ref(0);
const nextAllowedPollAt = ref(0);

const BASE_POLL_INTERVAL_MS = 30_000;
const MAX_POLL_INTERVAL_MS = 5 * 60_000;

const pageError = computed(() => scheduleStore.error);

function openCreateDialog() {
  editItem.value = null;
  dialogOpen.value = true;
}

function openEditDialog(item: ScheduleItem) {
  editItem.value = item;
  dialogOpen.value = true;
}

function closeDialog() {
  dialogOpen.value = false;
  editItem.value = null;
}

async function onCreateOnce(payload: CreateOnceRequest) {
  await scheduleStore.addOnce(payload);
  closeDialog();
}

async function onCreateRecurring(payload: CreateRecurringRequest) {
  await scheduleStore.addRecurring(payload);
  closeDialog();
}

async function onUpdateRecurring(id: number, payload: UpdateRecurringRequest) {
  await scheduleStore.update(id, payload);
  closeDialog();
}

function nextBackoffMs(retries: number): number {
  const multiplier = 2 ** Math.max(0, retries - 1);
  return Math.min(BASE_POLL_INTERVAL_MS * multiplier, MAX_POLL_INTERVAL_MS);
}

async function refreshSchedules() {
  await scheduleStore.loadSchedules();
  if (scheduleStore.lastErrorStatus === 503) {
    schedulerUnavailableRetries.value += 1;
    nextAllowedPollAt.value = Date.now() + nextBackoffMs(schedulerUnavailableRetries.value);
    return;
  }
  schedulerUnavailableRetries.value = 0;
  nextAllowedPollAt.value = Date.now() + BASE_POLL_INTERVAL_MS;
}

onMounted(() => {
  void refreshSchedules();
});

useIntervalFn(() => {
  if (document.visibilityState !== 'visible') return;
  if (Date.now() < nextAllowedPollAt.value) return;
  void refreshSchedules();
}, BASE_POLL_INTERVAL_MS);

function onCancel(type: 'one_shot' | 'recurring', id: number) {
  void scheduleStore.remove(type, id);
}
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <ScrollArea class="min-h-0 flex-1">
        <section class="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h1 class="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                Schedule
              </h1>
              <p class="mt-2 max-w-2xl text-sm leading-7 text-muted-foreground sm:text-base">
                Create one-shot reminders and recurring automations for notifications
                and agent tasks.
              </p>
            </div>
          </div>

          <p v-if="pageError" class="text-sm text-destructive">{{ pageError }}</p>

          <ScheduleList
            :loading="scheduleStore.loading"
            :saving="scheduleStore.saving"
            :active-tab="scheduleStore.activeTab"
            :active-once="scheduleStore.activeOnce"
            :active-recurring="scheduleStore.activeRecurring"
            :history="scheduleStore.history"
            @change-tab="scheduleStore.activeTab = $event"
            @create="openCreateDialog"
            @pause="scheduleStore.pause($event)"
            @resume="scheduleStore.resume($event)"
            @cancel="onCancel"
            @edit="openEditDialog"
          />
        </section>
      </ScrollArea>
    </div>
  </div>

  <CreateScheduleDialog
    :open="dialogOpen"
    :loading="scheduleStore.saving"
    :edit-item="editItem"
    @close="closeDialog"
    @create-once="onCreateOnce"
    @create-recurring="onCreateRecurring"
    @update-recurring="onUpdateRecurring"
  />
</template>
