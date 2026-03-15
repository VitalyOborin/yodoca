<script setup lang="ts">
import { computed, reactive } from 'vue';
import { Button } from '@/components/ui/button';
import type { CreateOnceRequest, ScheduleTopic } from '@/shared/api';

defineProps<{
  loading?: boolean;
}>();

const emit = defineEmits<{
  submit: [payload: CreateOnceRequest];
}>();

const form = reactive({
  topic: 'system.user.notify' as ScheduleTopic,
  message: '',
  channelId: '',
  mode: 'delay' as 'delay' | 'datetime',
  delayMinutes: 10,
  atIso: '',
});

const canSubmit = computed(() => {
  if (!form.message.trim()) return false;
  if (form.mode === 'delay') return form.delayMinutes > 0;
  return Boolean(form.atIso);
});

function onSubmit() {
  if (!canSubmit.value) return;
  const payload: CreateOnceRequest = {
    topic: form.topic,
    message: form.message.trim(),
    channel_id: form.channelId.trim() || undefined,
  };
  if (form.mode === 'delay') {
    payload.delay_seconds = Math.max(1, Math.round(form.delayMinutes * 60));
  } else {
    payload.at_iso = form.atIso;
  }
  emit('submit', payload);
}
</script>

<template>
  <form class="space-y-4" @submit.prevent="onSubmit">
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
      />
    </label>

    <div class="grid gap-3 sm:grid-cols-2">
      <label class="flex items-center gap-2 rounded-xl border border-border bg-background/50 p-3 text-sm">
        <input v-model="form.mode" type="radio" value="delay">
        In N minutes
      </label>
      <label class="flex items-center gap-2 rounded-xl border border-border bg-background/50 p-3 text-sm">
        <input v-model="form.mode" type="radio" value="datetime">
        At specific date/time
      </label>
    </div>

    <label v-if="form.mode === 'delay'" class="block space-y-2">
      <span class="text-sm text-muted-foreground">Delay (minutes)</span>
      <input
        v-model.number="form.delayMinutes"
        type="number"
        min="1"
        class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
      >
    </label>

    <label v-else class="block space-y-2">
      <span class="text-sm text-muted-foreground">Date and time</span>
      <input
        v-model="form.atIso"
        type="datetime-local"
        class="w-full rounded-xl border border-border bg-background/70 px-3 py-2 text-sm"
      >
    </label>

    <div class="flex justify-end">
      <Button class="rounded-full px-5" :disabled="loading || !canSubmit">
        Create once schedule
      </Button>
    </div>
  </form>
</template>
