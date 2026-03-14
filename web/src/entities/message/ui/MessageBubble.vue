<script setup lang="ts">
import { computed } from 'vue';
import type { Message } from '../model/types';
import { renderMarkdown } from '@/shared/lib';

const props = defineProps<{
  message: Message;
}>();

const isPendingAssistantMessage = computed(
  () => props.message.role === 'assistant' && !props.message.content,
);
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
      <div
        v-if="isPendingAssistantMessage"
        class="flex min-h-6 items-center"
        aria-hidden="true"
      >
        <span class="agent-pending-caret" />
      </div>
      <div
        v-else
        class="chat-markdown leading-6 text-foreground"
        v-html="renderedMarkdown"
      />
    </article>
  </div>
</template>
