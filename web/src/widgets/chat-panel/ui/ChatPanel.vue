<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onClickOutside } from '@vueuse/core';
import { AlertCircle, CheckCircle2, Ellipsis, LoaderCircle, PauseCircle } from 'lucide-vue-next';
import { useThreadStore } from '@/entities/thread';
import { useMessageStore, MessageBubble } from '@/entities/message';
import { useAgentStore } from '@/entities/agent';
import { SendMessageForm } from '@/features/send-message';
import { ScrollArea } from '@/components/ui/scroll-area';

const threadStore = useThreadStore();
const messageStore = useMessageStore();
const agentStore = useAgentStore();

const scrollContainer = ref<HTMLElement | null>(null);
const composerFooter = ref<HTMLElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const menuOpen = ref(false);
const messagesBottomInset = ref(176);
const pendingTimers = new Set<number>();
let footerResizeObserver: ResizeObserver | null = null;

const currentMessages = computed(() => {
  if (!threadStore.activeThreadId) return [];
  return messageStore.getThreadMessages(threadStore.activeThreadId);
});

function queueTimeout(callback: () => void, delay: number): Promise<void> {
  return new Promise((resolve) => {
    const id = window.setTimeout(() => {
      pendingTimers.delete(id);
      callback();
      resolve();
    }, delay);
    pendingTimers.add(id);
  });
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollContainer.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

function updateMessagesInset() {
  const footerHeight = composerFooter.value?.offsetHeight ?? 0;
  messagesBottomInset.value = Math.max(176, footerHeight + 16);
}

function archiveCurrentThread() {
  const activeThread = threadStore.activeThread;
  if (!activeThread) return;

  if (!activeThread.title.startsWith('Archived: ')) {
    threadStore.renameThread(activeThread.id, `Archived: ${activeThread.title}`);
  }
  menuOpen.value = false;
}

function deleteCurrentThread() {
  if (!threadStore.activeThreadId) return;
  threadStore.deleteThread(threadStore.activeThreadId);
  menuOpen.value = false;
}

onClickOutside(menuRef, () => {
  menuOpen.value = false;
});

watch(
  () => currentMessages.value.length,
  () => scrollToBottom(),
);

watch(
  () => threadStore.activeThreadId,
  () => scrollToBottom(),
);

async function handleSend(content: string) {
  if (!threadStore.activeThreadId) return;

  messageStore.addMessage(threadStore.activeThreadId, 'user', content);
  threadStore.touchThread(threadStore.activeThreadId, content);
  agentStore.startRun(content);

  await queueTimeout(() => {
    agentStore.beginExecution();
  }, 420);

  const failRequest = /error|ошибк|fail/i.test(content);
  if (failRequest) {
    await queueTimeout(() => {
      agentStore.failRun('Не удалось завершить действие: конфликт валидации данных.');
      if (!threadStore.activeThreadId) return;
      messageStore.addMessage(
        threadStore.activeThreadId,
        'agent',
        'Не удалось завершить действие из-за конфликта данных. Проверьте входные параметры и повторите.',
      );
      threadStore.touchThread(
        threadStore.activeThreadId,
        'Не удалось завершить действие из-за конфликта данных.',
      );
    }, 700);
    return;
  }

  await queueTimeout(() => {
    if (!threadStore.activeThreadId) return;
    messageStore.addMessage(
      threadStore.activeThreadId,
      'agent',
      'Готово. Я сформировал план, обновил action log и подготовил черновик для подтверждения в правой панели.',
    );
    threadStore.touchThread(
      threadStore.activeThreadId,
      'Готово. Я сформировал план, обновил action log и подготовил черновик.',
    );
    agentStore.completeRun('Ответ отправлен в чат, workspace обновлен.');
  }, 760);
}

onBeforeUnmount(() => {
  for (const timer of pendingTimers) {
    clearTimeout(timer);
  }
  pendingTimers.clear();
  footerResizeObserver?.disconnect();
  footerResizeObserver = null;
});

onMounted(() => {
  nextTick(() => {
    updateMessagesInset();
    if (!composerFooter.value) return;

    footerResizeObserver = new ResizeObserver(() => {
      updateMessagesInset();
    });
    footerResizeObserver.observe(composerFooter.value);
  });
});
</script>

<template>
  <main class="relative min-w-0 min-h-0 flex flex-1 flex-col border-r border-border xl:border-r-0">
    <header class="border-b border-border px-4 py-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="text-xs uppercase tracking-[0.2em] text-muted-foreground">Thread</p>
          <h1 class="truncate text-lg font-semibold text-foreground">
            {{ threadStore.activeThread?.title ?? 'Select a conversation' }}
          </h1>
        </div>

        <div ref="menuRef" class="relative">
          <button
            type="button"
            class="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md text-foreground/80 transition-colors hover:bg-white/10 hover:text-white"
            aria-label="Thread actions"
            @click="menuOpen = !menuOpen"
          >
            <Ellipsis class="h-4 w-4" />
          </button>

          <div
            v-if="menuOpen"
            class="glass-panel absolute top-10 right-0 z-20 min-w-40 rounded-lg border border-border bg-card/95 p-1.5"
          >
            <button
              type="button"
              class="focus-ring block w-full rounded-md px-3 py-2 text-left text-sm text-foreground/85 transition-colors hover:bg-white/10 hover:text-white"
              @click="archiveCurrentThread"
            >
              Архивировать
            </button>
            <button
              type="button"
              class="focus-ring block w-full rounded-md px-3 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive/15"
              @click="deleteCurrentThread"
            >
              Удалить
            </button>
          </div>
        </div>
      </div>
    </header>

    <ScrollArea class="min-h-0 flex-1">
      <div
        ref="scrollContainer"
        class="mx-auto w-full max-w-[760px] space-y-1 px-4 pt-4"
        :style="{ paddingBottom: `${messagesBottomInset}px` }"
      >
        <div
          v-if="currentMessages.length === 0"
          class="rounded-xl border border-border bg-secondary/40 px-4 py-6 text-center"
        >
          <p class="text-sm text-muted-foreground">Начните диалог. Агент покажет intent preview и audit trail справа.</p>
        </div>

        <section aria-live="polite" aria-atomic="false">
          <MessageBubble v-for="message in currentMessages" :key="message.id" :message="message" />
        </section>
      </div>
    </ScrollArea>

    <footer ref="composerFooter" class="pointer-events-none absolute inset-x-0 bottom-0 px-4 pb-4">
      <div class="pointer-events-auto mx-auto w-full max-w-[760px]">
        <div class="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderCircle v-if="agentStore.phase === 'thinking' || agentStore.phase === 'acting'" class="h-3.5 w-3.5 animate-spin" />
          <PauseCircle v-else-if="agentStore.phase === 'waiting_input'" class="h-3.5 w-3.5 text-[hsl(var(--warning))]" />
          <AlertCircle v-else-if="agentStore.phase === 'error'" class="h-3.5 w-3.5 text-destructive" />
          <CheckCircle2 v-else-if="agentStore.phase === 'complete'" class="h-3.5 w-3.5 text-[hsl(var(--success))]" />
          <span v-if="agentStore.currentStep">{{ agentStore.currentStep }}</span>
        </div>
        <SendMessageForm :disabled="agentStore.phase === 'thinking' || agentStore.phase === 'acting'" @send="handleSend" />
      </div>
    </footer>
  </main>
</template>
