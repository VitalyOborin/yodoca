<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { AlertCircle, CheckCircle2, LoaderCircle, PauseCircle } from 'lucide-vue-next';
import { useThreadStore } from '@/entities/thread';
import { useMessageStore, MessageBubble } from '@/entities/message';
import { useAgentStore } from '@/entities/agent';
import { SendMessageForm } from '@/features/send-message';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';

const threadStore = useThreadStore();
const messageStore = useMessageStore();
const agentStore = useAgentStore();

const scrollContainer = ref<HTMLElement | null>(null);
const pendingTimers = new Set<number>();

const currentMessages = computed(() => {
  if (!threadStore.activeThreadId) return [];
  return messageStore.getThreadMessages(threadStore.activeThreadId);
});

const phaseBadgeClass = computed(() => {
  const map = {
    idle: 'text-muted-foreground border-border bg-secondary/50',
    thinking: 'text-primary border-primary/35 bg-primary/10',
    acting: 'text-cyan-300 border-cyan-400/30 bg-cyan-500/10',
    waiting_input: 'text-amber-300 border-amber-400/30 bg-amber-500/10',
    error: 'text-destructive border-destructive/35 bg-destructive/10',
    complete: 'text-emerald-300 border-emerald-400/30 bg-emerald-500/10',
  } as const;
  return map[agentStore.phase];
});

function phaseLabel() {
  const map = {
    idle: 'Idle',
    thinking: 'Thinking',
    acting: 'Executing',
    waiting_input: 'Needs input',
    error: 'Error',
    complete: 'Complete',
  } as const;
  return map[agentStore.phase];
}

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

        <div class="flex items-center gap-2">
          <span class="rounded-full border px-2.5 py-1 text-xs" :class="phaseBadgeClass">{{ phaseLabel() }}</span>

          <Button
            variant="ghost"
            size="sm"
            class="focus-ring"
            :disabled="agentStore.phase !== 'waiting_input'"
            @click="agentStore.setPhase('idle')"
          >
            Resume
          </Button>
        </div>
      </div>
    </header>

    <ScrollArea class="min-h-0 flex-1">
      <div ref="scrollContainer" class="mx-auto w-full max-w-[760px] space-y-1 px-4 pt-4 pb-44">
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

    <footer class="pointer-events-none absolute inset-x-0 bottom-0 px-4 pb-4">
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
