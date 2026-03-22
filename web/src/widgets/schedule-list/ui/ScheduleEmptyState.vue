<script setup lang="ts">
import { CalendarClock } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';

defineProps<{
  title?: string;
  description?: string;
}>();

const emit = defineEmits<{
  create: [];
}>();
</script>

<template>
  <div class="surface-panel rounded-3xl border border-dashed border-border px-6 py-12 text-center">
    <div class="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/5">
      <CalendarClock class="h-6 w-6 text-foreground/80" />
    </div>
    <h3 class="mt-4 text-lg font-semibold text-foreground">
      {{ title ?? 'No schedules yet' }}
    </h3>
    <p class="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
      {{ description ?? 'Create your first schedule to automate reminders and agent tasks.' }}
    </p>
    <Button class="mt-6 rounded-full px-5" @click="emit('create')">
      Create first schedule
    </Button>
  </div>
</template>
