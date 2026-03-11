<script setup lang="ts">
import { ref } from 'vue';
import { CornerDownLeft, Paperclip, SendHorizonal, WandSparkles } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';

const emit = defineEmits<{
  send: [content: string];
}>();

const inputText = ref('');
const textareaEl = ref<HTMLTextAreaElement | null>(null);
const quickModes = ['Ask', 'Research', 'Plan', 'Build'];

function handleSend() {
  const text = inputText.value.trim();
  if (!text) return;
  emit('send', text);
  inputText.value = '';
  if (textareaEl.value) textareaEl.value.style.height = 'auto';
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    handleSend();
  }
}

function handleInput() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
}
</script>

<template>
  <form @submit.prevent="handleSend">
    <div
      class="glass-panel rounded-[1.8rem] border border-white/10 p-3 sm:p-4"
    >
      <div class="mb-3 flex flex-col gap-3 px-1 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex flex-wrap items-center gap-2">
          <div class="flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-foreground/[0.46]">
            <WandSparkles class="h-3.5 w-3.5 text-primary" />
            Prompt composer
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="mode in quickModes"
              :key="mode"
              type="button"
              class="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-foreground/[0.56] transition-colors hover:bg-white/[0.1] hover:text-white"
            >
              {{ mode }}
            </button>
          </div>
        </div>
        <div class="hidden items-center gap-2 text-[11px] text-foreground/[0.4] sm:flex">
          <CornerDownLeft class="h-3.5 w-3.5" />
          Enter to send, Shift+Enter for a new line
        </div>
      </div>

      <div
        class="flex items-end gap-3 rounded-[1.4rem] border border-white/10 bg-black/[0.12] px-3 py-3 transition-colors focus-within:border-primary/50"
      >
        <Button
          type="button"
          variant="ghost"
          size="icon"
          class="mb-1 hidden h-10 w-10 rounded-2xl border border-white/10 bg-white/[0.06] text-foreground/[0.66] hover:bg-white/[0.1] sm:inline-flex"
        >
          <Paperclip class="h-4 w-4" />
        </Button>
        <textarea
          ref="textareaEl"
          v-model="inputText"
          placeholder="Shape the next move..."
          rows="1"
          class="max-h-[200px] flex-1 resize-none border-0 bg-transparent px-1 text-sm leading-7 text-foreground outline-none placeholder:text-foreground/[0.32]"
          @keydown="handleKeydown"
          @input="handleInput"
        />
        <Button
          type="submit"
          variant="default"
          size="icon"
          class="h-12 w-12 shrink-0 rounded-2xl bg-primary text-primary-foreground shadow-[0_14px_32px_rgb(255_196_82_/_0.24)] hover:bg-primary/90"
          :disabled="!inputText.trim()"
        >
          <SendHorizonal class="h-4 w-4" />
        </Button>
      </div>
    </div>
  </form>
</template>
