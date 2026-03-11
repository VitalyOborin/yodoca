<script setup lang="ts">
import { Bot, Sparkles } from 'lucide-vue-next';
import type { Message } from '../model/types';
import { formatMessageTime } from '@/shared/lib';

defineProps<{
  message: Message;
}>();
</script>

<template>
  <div :class="['py-2 sm:py-3', message.role === 'user' ? 'flex justify-end' : '']">
    <div
      v-if="message.role === 'user'"
      class="max-w-[88%] rounded-[1.6rem] rounded-br-md border border-primary/20 bg-primary px-5 py-4 text-sm leading-7 text-primary-foreground shadow-[0_18px_40px_rgb(255_196_82_/_0.18)] sm:max-w-[72%]"
    >
      <div class="mb-2 flex items-center justify-between gap-3">
        <span class="inline-flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.24em] text-primary-foreground/[0.7]">
          <Sparkles class="h-3.5 w-3.5" />
          You
        </span>
        <span class="text-[11px] text-primary-foreground/[0.64]">
          {{ formatMessageTime(message.createdAt) }}
        </span>
      </div>
      <p class="whitespace-pre-wrap" v-text="message.content" />
    </div>
    <div
      v-else
      class="glass-panel max-w-[92%] rounded-[1.8rem] rounded-tl-md border border-white/10 px-5 py-4 text-card-foreground sm:max-w-[78%]"
    >
      <div class="mb-3 flex items-center justify-between gap-3">
        <span class="inline-flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.24em] text-accent">
          <Bot class="h-3.5 w-3.5" />
          Yodoca
        </span>
        <span class="text-[11px] text-foreground/[0.42]">{{ formatMessageTime(message.createdAt) }}</span>
      </div>
      <p class="whitespace-pre-wrap text-sm leading-7 text-foreground" v-text="message.content" />
      <div class="mt-4 flex flex-wrap gap-2">
        <span class="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-foreground/[0.5]">
          Source-aware
        </span>
        <span class="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-foreground/[0.5]">
          Draft output
        </span>
        <span class="rounded-full border border-primary/20 bg-primary/[0.12] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-primary">
          Ready for follow-up
        </span>
      </div>
    </div>
  </div>
</template>
