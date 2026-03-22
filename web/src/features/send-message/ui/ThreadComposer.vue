<script setup lang="ts">
import { AlertCircle, CheckCircle2, LoaderCircle } from 'lucide-vue-next';
import SendMessageForm from './SendMessageForm.vue';

defineProps<{
  disabled?: boolean;
  phase?: 'idle' | 'thinking' | 'complete' | 'error';
  currentStep?: string | null;
}>();

const emit = defineEmits<{
  send: [content: string];
}>();
</script>

<template>
  <div>
    <div class="mb-2 flex min-h-4 items-center gap-2 text-xs text-muted-foreground">
      <LoaderCircle
        v-if="phase === 'thinking'"
        class="h-3.5 w-3.5 animate-spin"
      />
      <AlertCircle
        v-else-if="phase === 'error'"
        class="h-3.5 w-3.5 text-destructive"
      />
      <CheckCircle2
        v-else-if="phase === 'complete'"
        class="h-3.5 w-3.5 text-[hsl(var(--success))]"
      />
      <span v-if="currentStep">{{ currentStep }}</span>
    </div>
    <SendMessageForm
      :disabled="disabled"
      @send="emit('send', $event)"
    />
  </div>
</template>
