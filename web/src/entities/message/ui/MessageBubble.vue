<script setup lang="ts">
import type { Message } from '../model/types';

defineProps<{
  message: Message;
}>();
</script>

<template>
  <div :class="['animate-enter py-2', message.role === 'user' ? 'flex justify-end' : '']">
    <article
      v-if="message.role === 'user'"
      class="max-w-[88%] rounded-2xl rounded-br-sm border border-primary/30 bg-[rgb(124_106_255_/_0.1)] px-4 py-3 text-sm text-foreground sm:max-w-[78%]"
    >
      <p class="whitespace-pre-wrap leading-6">{{ message.content }}</p>
    </article>

    <article
      v-else
      class="max-w-full px-0 py-1 text-card-foreground"
    >
      <p class="whitespace-pre-wrap leading-6 text-foreground">{{ message.content }}</p>
    </article>
  </div>
</template>
