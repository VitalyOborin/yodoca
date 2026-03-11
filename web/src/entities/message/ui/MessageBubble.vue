<script setup lang="ts">
import { computed } from 'vue';
import type { Message } from '../model/types';
import { renderMarkdown } from '@/shared/lib';

const props = defineProps<{
  message: Message;
}>();

const renderedMarkdown = computed(() => renderMarkdown(props.message.content));
</script>

<template>
  <div :class="['animate-enter py-2', message.role === 'user' ? 'flex justify-end' : '']">
    <article
      v-if="message.role === 'user'"
      class="max-w-[88%] rounded-xl border border-primary/30 bg-primary/15 px-4 py-3 text-sm text-foreground sm:max-w-[78%]"
    >
      <p class="whitespace-pre-wrap leading-6">{{ message.content }}</p>
    </article>

    <article
      v-else
      class="max-w-full px-0 py-1 text-card-foreground"
    >
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div class="chat-markdown leading-6 text-foreground" v-html="renderedMarkdown" />
    </article>
  </div>
</template>
