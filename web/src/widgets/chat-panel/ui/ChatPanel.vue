<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Bot, Compass, LibraryBig, Orbit, Waves } from 'lucide-vue-next';
import { useThreadStore } from '@/entities/thread';
import { useMessageStore, MessageBubble } from '@/entities/message';
import { SendMessageForm } from '@/features/send-message';

const threadStore = useThreadStore();
const messageStore = useMessageStore();
const scrollContainer = ref<HTMLElement | null>(null);

const currentMessages = computed(() => {
  if (!threadStore.activeThreadId) return [];
  return messageStore.getThreadMessages(threadStore.activeThreadId);
});

const heroTags = ['Streaming-ready', 'Editorial UI', 'Mock intelligence'];
const capabilityChips = ['Research mode', 'Workspace memory', 'Artifact preview'];
const followUpPrompts = [
  'Turn this into a step-by-step plan',
  'Generate a concise implementation brief',
  'Compare two alternative approaches',
];

function scrollToBottom() {
  nextTick(() => {
    const el = scrollContainer.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

watch(
  () => currentMessages.value.length,
  () => scrollToBottom(),
);

watch(
  () => threadStore.activeThreadId,
  () => scrollToBottom(),
);

function handleSend(content: string) {
  if (!threadStore.activeThreadId) return;
  messageStore.addMessage(threadStore.activeThreadId, 'user', content);

  setTimeout(() => {
    if (!threadStore.activeThreadId) return;
    messageStore.addMessage(
      threadStore.activeThreadId,
      'agent',
      "I'm a mock response. In the real application, this will be powered by the AG-UI protocol streaming real agent responses.",
    );
  }, 800);
}
</script>

<template>
  <main class="relative flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden">
    <header class="shrink-0 border-b border-white/10 px-4 py-4 sm:px-6 lg:px-8">
      <div class="mb-4 flex flex-wrap gap-2">
        <span
          v-for="chip in capabilityChips"
          :key="chip"
          class="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[11px] uppercase tracking-[0.22em] text-foreground/[0.58]"
        >
          {{ chip }}
        </span>
      </div>

      <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <p class="mb-2 text-[11px] uppercase tracking-[0.32em] text-foreground/[0.42]">
            Thread focus
          </p>
          <h2 class="text-2xl font-semibold tracking-[-0.04em] text-foreground sm:text-3xl">
            {{ threadStore.activeThread?.title ?? 'Select a chat' }}
          </h2>
        </div>

        <div class="flex flex-wrap gap-2">
          <span
            v-for="tag in heroTags"
            :key="tag"
            class="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-[11px] uppercase tracking-[0.22em] text-foreground/[0.58]"
          >
            {{ tag }}
          </span>
        </div>
      </div>
    </header>

    <div
      ref="scrollContainer"
      class="min-h-0 flex-1 overflow-y-auto px-4 pt-4 pb-56 sm:px-6 sm:pt-6 lg:px-8"
    >
      <div
        class="mesh-card mb-6 rounded-[1.8rem] border border-white/10 p-5 text-foreground sm:p-6"
      >
        <div class="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.85fr)]">
          <div>
            <p class="mb-3 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/[0.1] px-3 py-1 text-[10px] font-medium uppercase tracking-[0.28em] text-accent">
              <Waves class="h-3.5 w-3.5" />
              Agent workspace
            </p>
            <h3 class="display-title text-4xl leading-none text-white sm:text-5xl">
              A chat surface built for action, not only dialogue.
            </h3>
            <p class="mt-4 max-w-xl text-sm leading-7 text-foreground/[0.7] sm:text-[15px]">
              The strongest AI interfaces now mix conversation with workspace memory, visible
              operating mode and artifact-style outputs. This mock now leans into that direction.
            </p>

            <div class="mt-6 grid gap-3 sm:grid-cols-3">
              <div class="glass-panel rounded-[1.4rem] border border-white/10 p-4">
                <Bot class="mb-3 h-5 w-5 text-primary" />
                <p class="text-xs uppercase tracking-[0.24em] text-foreground/[0.4]">Agent mode</p>
                <p class="mt-2 text-lg font-semibold tracking-[-0.03em] text-white">Prototype</p>
              </div>
              <div class="glass-panel rounded-[1.4rem] border border-white/10 p-4">
                <LibraryBig class="mb-3 h-5 w-5 text-accent" />
                <p class="text-xs uppercase tracking-[0.24em] text-foreground/[0.4]">Context</p>
                <p class="mt-2 text-lg font-semibold tracking-[-0.03em] text-white">Attached</p>
              </div>
              <div class="glass-panel rounded-[1.4rem] border border-white/10 p-4">
                <Orbit class="mb-3 h-5 w-5 text-primary" />
                <p class="text-xs uppercase tracking-[0.24em] text-foreground/[0.4]">Style</p>
                <p class="mt-2 text-lg font-semibold tracking-[-0.03em] text-white">Bold</p>
              </div>
            </div>
          </div>

          <div class="glass-panel rounded-[1.7rem] border border-white/10 p-4">
            <div class="mb-4 flex items-center justify-between gap-3">
              <div>
                <p class="text-[10px] uppercase tracking-[0.28em] text-foreground/[0.38]">
                  Live artifact
                </p>
                <p class="mt-2 text-lg font-semibold tracking-[-0.03em] text-white">
                  Working memory panel
                </p>
              </div>
              <Compass class="h-[18px] w-[18px] text-accent" />
            </div>

            <div class="rounded-[1.3rem] border border-white/10 bg-black/[0.16] p-4">
              <div class="mb-3 flex items-center gap-2">
                <span class="h-2.5 w-2.5 rounded-full bg-primary" />
                <span class="h-2.5 w-2.5 rounded-full bg-accent" />
                <span class="h-2.5 w-2.5 rounded-full bg-white/40" />
              </div>
              <p class="text-sm font-semibold text-white">Architecture review board</p>
              <div class="mt-4 space-y-3 text-sm text-foreground/[0.66]">
                <div class="rounded-2xl border border-white/10 bg-white/[0.04] px-3 py-2">
                  Focus: event bus boundaries
                </div>
                <div class="rounded-2xl border border-white/10 bg-white/[0.04] px-3 py-2">
                  Context packets: 3 files attached
                </div>
                <div class="rounded-2xl border border-primary/20 bg-primary/[0.12] px-3 py-2 text-primary">
                  Suggested next output: implementation brief
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="mx-auto flex w-full max-w-4xl flex-col pb-4">
        <div class="mb-5 flex flex-wrap gap-2">
          <button
            v-for="prompt in followUpPrompts"
            :key="prompt"
            type="button"
            class="rounded-full border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-foreground/[0.66] transition-colors hover:bg-white/[0.1] hover:text-white"
          >
            {{ prompt }}
          </button>
        </div>

        <div
          v-if="currentMessages.length === 0"
          class="glass-panel flex min-h-[280px] items-center justify-center rounded-[2rem] border border-white/10 px-6 text-center text-muted-foreground"
        >
          <div>
            <p class="display-title text-4xl text-white">No dialogue yet.</p>
            <p class="mt-3 text-[15px] leading-7 text-foreground/[0.62]">
              Start a conversation and the mock agent will answer with placeholder intelligence.
            </p>
          </div>
        </div>
        <MessageBubble v-for="message in currentMessages" :key="message.id" :message="message" />
      </div>
    </div>

    <div class="pointer-events-none absolute inset-x-0 bottom-0 px-4 pb-4 pt-12 sm:px-6 lg:px-8"
      style="background: linear-gradient(to bottom, transparent, rgb(16 20 32 / 0.85) 40%, rgb(16 20 32 / 0.95))"
    >
      <div class="pointer-events-auto mx-auto max-w-4xl">
        <SendMessageForm @send="handleSend" />
      </div>
    </div>
  </main>
</template>
