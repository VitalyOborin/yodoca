<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onClickOutside } from '@vueuse/core';
import { AlertCircle, CheckCircle2, Ellipsis, LoaderCircle } from 'lucide-vue-next';
import { useThreadStore } from '@/entities/thread';
import { useMessageStore, MessageBubble } from '@/entities/message';
import { useAgentStore } from '@/entities/agent';
import { SendMessageForm, sendPromptToThread } from '@/features/send-message';
import { ScrollArea } from '@/components/ui/scroll-area';

const threadStore = useThreadStore();
const messageStore = useMessageStore();
const agentStore = useAgentStore();

const scrollContainer = ref<HTMLElement | null>(null);
const composerFooter = ref<HTMLElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const menuOpen = ref(false);
const messagesBottomInset = ref(176);
const loadingHistory = ref(false);
const isSending = ref(false);
const pendingHistoryThreadId = ref<string | null>(null);
let latestHistoryRequestId = 0;
let footerResizeObserver: ResizeObserver | null = null;

const currentMessages = computed(() => {
  if (!threadStore.activeThreadId) return [];
  return messageStore.getThreadMessages(threadStore.activeThreadId);
});

function scrollToBottom() {
  nextTick(() => {
    const el = scrollContainer.value;
    if (!el) return;
    const viewport = el.closest('[data-slot="scroll-area-viewport"]') as HTMLElement | null;
    const target = viewport ?? el;
    target.scrollTop = target.scrollHeight;
  });
}

function updateMessagesInset() {
  const footerHeight = composerFooter.value?.offsetHeight ?? 0;
  messagesBottomInset.value = Math.max(176, footerHeight + 16);
}

async function archiveCurrentThread() {
  const activeThread = threadStore.activeThread;
  if (!activeThread) return;
  await threadStore.archiveThread(activeThread.id);
  menuOpen.value = false;
}

async function deleteCurrentThread() {
  if (!threadStore.activeThreadId) return;
  await threadStore.removeThread(threadStore.activeThreadId);
  menuOpen.value = false;
}

async function loadThreadHistory(threadId: string) {
  const requestId = ++latestHistoryRequestId;
  loadingHistory.value = true;

  try {
    const { history } = await threadStore.loadThread(threadId);
    if (requestId !== latestHistoryRequestId || threadStore.activeThreadId !== threadId) {
      return;
    }
    const localMessages = messageStore.getThreadMessages(threadId);
    if (history.length === 0 && localMessages.length > 0) {
      return;
    }
    messageStore.setThreadMessages(threadId, history);
  } catch {
    if (requestId !== latestHistoryRequestId || threadStore.activeThreadId !== threadId) {
      return;
    }
    const localMessages = messageStore.getThreadMessages(threadId);
    if (localMessages.length > 0) {
      return;
    }
    messageStore.setThreadMessages(threadId, []);
  } finally {
    if (requestId === latestHistoryRequestId) {
      loadingHistory.value = false;
      scrollToBottom();
    }
  }
}

function flushPendingHistoryLoad() {
  const threadId = pendingHistoryThreadId.value;
  if (!threadId || isSending.value) return;
  if (
    agentStore.phase === 'thinking' &&
    messageStore.getThreadMessages(threadId).length > 0
  ) {
    pendingHistoryThreadId.value = null;
    loadingHistory.value = false;
    return;
  }

  pendingHistoryThreadId.value = null;
  void loadThreadHistory(threadId);
}

async function handleSend(content: string) {
  agentStore.resetPhase();
  isSending.value = true;
  try {
    await sendPromptToThread({
      threadStore,
      messageStore,
      agentStore,
      content,
    });
  } finally {
    isSending.value = false;
    flushPendingHistoryLoad();
  }
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

watch(
  () => threadStore.activeThreadId,
  (id) => {
    if (!id) return;
    pendingHistoryThreadId.value = id;
    flushPendingHistoryLoad();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
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
          <h1 class="truncate text-lg font-semibold text-foreground">
            {{ threadStore.activeThread?.title ?? 'New conversation' }}
          </h1>
        </div>

        <div v-if="threadStore.activeThreadId" ref="menuRef" class="relative">
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
              Archive
            </button>
            <button
              type="button"
              class="focus-ring block w-full rounded-md px-3 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive/15"
              @click="deleteCurrentThread"
            >
              Delete
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
          v-if="loadingHistory"
          class="rounded-xl border border-border bg-secondary/40 px-4 py-6 text-center"
        >
          <p class="text-sm text-muted-foreground">Loading conversation...</p>
        </div>

        <div
          v-else-if="currentMessages.length === 0"
          class="rounded-xl border border-border bg-secondary/40 px-4 py-6 text-center"
        >
          <p class="text-sm text-muted-foreground">
            {{ threadStore.activeThreadId ? 'Start the conversation. Type your message below.' : 'Type your message below to start a new conversation.' }}
          </p>
        </div>

        <section v-else aria-live="polite" aria-atomic="false">
          <MessageBubble v-for="message in currentMessages" :key="message.id" :message="message" />
        </section>
      </div>
    </ScrollArea>

    <footer ref="composerFooter" class="pointer-events-none absolute inset-x-0 bottom-0 px-4 pb-4">
      <div class="pointer-events-auto mx-auto w-full max-w-[760px]">
        <div class="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderCircle v-if="agentStore.phase === 'thinking'" class="h-3.5 w-3.5 animate-spin" />
          <AlertCircle v-else-if="agentStore.phase === 'error'" class="h-3.5 w-3.5 text-destructive" />
          <CheckCircle2 v-else-if="agentStore.phase === 'complete'" class="h-3.5 w-3.5 text-[hsl(var(--success))]" />
          <span v-if="agentStore.currentStep">{{ agentStore.currentStep }}</span>
        </div>
        <SendMessageForm :disabled="agentStore.phase === 'thinking'" @send="handleSend" />
      </div>
    </footer>
  </main>
</template>
