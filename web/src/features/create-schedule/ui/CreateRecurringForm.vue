<script setup lang="ts">
import { computed, reactive } from 'vue';
import cronstrue from 'cronstrue';
import { Button } from '@/components/ui/button';
import type {
  CreateRecurringRequest,
  ScheduleItem,
  ScheduleTopic,
  UpdateRecurringRequest,
} from '@/shared/api';

const props = defineProps<{
  loading?: boolean;
  editItem?: ScheduleItem | null;
}>();

const emit = defineEmits<{
  submitCreate: [payload: CreateRecurringRequest];
  submitUpdate: [id: number, payload: UpdateRecurringRequest];
}>();

const form = reactive({
  topic: (props.editItem?.topic ?? 'system.user.notify') as ScheduleTopic,
  message: props.editItem?.message ?? '',
  channelId: props.editItem?.channel_id ?? '',
  mode: props.editItem?.cron_expr ? 'cron' as const : 'interval' as const,
  cron: props.editItem?.cron_expr ?? '0 9 * * *',
  everySeconds: props.editItem?.every_seconds ?? 3600,
  untilIso: props.editItem?.until_iso ?? '',
});

const cronText = computed(() => {
  if (!form.cron.trim()) return '';
  try {
    return cronstrue.toString(form.cron.trim());
  } catch {
    return 'Invalid cron expression';
  }
});

const canSubmit = computed(() => {
  if (!form.message.trim()) return false;
  if (form.mode === 'cron') return Boolean(form.cron.trim());
  return form.everySeconds > 0;
});

function onSubmit() {
  if (!canSubmit.value) return;

  if (props.editItem) {
    const payload: UpdateRecurringRequest = {
      until_iso: form.untilIso.trim() || null,
    };
    if (form.mode === 'cron') {
      payload.cron = form.cron.trim();
      payload.every_seconds = null;
    } else {
      payload.every_seconds = form.everySeconds;
      payload.cron = null;
    }
    emit('submitUpdate', props.editItem.id, payload);
    return;
  }

  const payload: CreateRecurringRequest = {
    topic: form.topic,
    message: form.message.trim(),
    channel_id: form.channelId.trim() || undefined,
    until_iso: form.untilIso.trim() || undefined,
  };

  if (form.mode === 'cron') {
    payload.cron = form.cron.trim();
  } else {
    payload.every_seconds = form.everySeconds;
  }
  emit('submitCreate', payload);
}
</script>

<template>
  <form class="space-y-4" @submit.prevent="onSubmit">
    <template v-if="!editItem">
      <label class="block space-y-2">
        <span class="text-sm text-muted-foreground">Topic</span>
        <select
          v-model="form.topic"
          class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
        >
          <option value="system.user.notify">Notification</option>
          <option value="system.agent.task">Agent task</option>
          <option value="system.agent.background">Agent background task</option>
        </select>
      </label>

      <label class="block space-y-2">
        <span class="text-sm text-muted-foreground">Message</span>
        <textarea
          v-model="form.message"
          rows="4"
          class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
          placeholder="Reminder text or agent prompt"
        />
      </label>

      <label class="block space-y-2">
        <span class="text-sm text-muted-foreground">Channel ID (optional)</span>
        <input
          v-model="form.channelId"
          type="text"
          class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
          placeholder="telegram_channel"
        >
      </label>
    </template>

    <div class="grid gap-3 sm:grid-cols-2">
      <label class="flex items-center gap-2 rounded-xl border border-border bg-background/50 p-3 text-sm">
        <input v-model="form.mode" type="radio" value="cron">
        Cron
      </label>
      <label class="flex items-center gap-2 rounded-xl border border-border bg-background/50 p-3 text-sm">
        <input v-model="form.mode" type="radio" value="interval">
        Interval
      </label>
    </div>

    <label v-if="form.mode === 'cron'" class="block space-y-2">
      <span class="text-sm text-muted-foreground">Cron expression</span>
      <input
        v-model="form.cron"
        type="text"
        class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 font-mono text-sm"
        placeholder="0 9 * * *"
      >
      <p class="text-xs text-muted-foreground">{{ cronText }}</p>
    </label>

    <label v-else class="block space-y-2">
      <span class="text-sm text-muted-foreground">Every (seconds)</span>
      <input
        v-model.number="form.everySeconds"
        type="number"
        min="1"
        class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
      >
    </label>

    <label class="block space-y-2">
      <span class="text-sm text-muted-foreground">Until (optional)</span>
      <input
        v-model="form.untilIso"
        type="datetime-local"
        class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
      >
    </label>

    <div class="flex justify-end">
      <Button class="rounded-full px-5" :disabled="loading || !canSubmit">
        {{ editItem ? 'Save changes' : 'Create recurring schedule' }}
      </Button>
    </div>
  </form>
</template>
