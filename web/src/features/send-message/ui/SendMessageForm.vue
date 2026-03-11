<script setup lang="ts">
import { ref } from 'vue';
import { ArrowUp, Mic, Plus } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';

defineProps<{
  disabled?: boolean;
}>();

const emit = defineEmits<{
  send: [content: string];
}>();

const inputText = ref('');
const textareaEl = ref<HTMLTextAreaElement | null>(null);
const settingChips = ['Думаю', 'Средний', 'Контекст IDE'];

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
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
}
</script>

<template>
  <form @submit.prevent="handleSend">
    <div class="glass-panel rounded-xl border border-border/90 bg-background/90 p-2.5">
      <div class="rounded-lg border border-border/60 bg-background/70">
        <textarea
          ref="textareaEl"
          v-model="inputText"
          placeholder="Опишите задачу для агента..."
          rows="1"
          class="app-scrollbar max-h-[180px] min-h-[64px] w-full resize-none border-0 bg-transparent px-3 pt-3 pb-2 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground"
          @keydown="handleKeydown"
          @input="handleInput"
        />
        <div class="flex items-center justify-between gap-2 px-2 py-1.5">
          <div class="flex min-w-0 items-center gap-1.5">
            <Button type="button" variant="ghost" size="icon-sm" class="focus-ring h-7 w-7 rounded-full">
              <Plus class="h-4 w-4" />
            </Button>
            <div class="flex min-w-0 items-center gap-1.5 overflow-x-auto">
              <button
                v-for="chip in settingChips"
                :key="chip"
                type="button"
                class="focus-ring shrink-0 rounded-full border border-border/70 bg-secondary/35 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground"
              >
                {{ chip }}
              </button>
            </div>
          </div>
          <div class="flex items-center gap-1.5">
            <Button type="button" variant="ghost" size="icon-sm" class="focus-ring h-7 w-7 rounded-full">
              <Mic class="h-3.5 w-3.5" />
            </Button>
            <Button
              type="submit"
              size="icon-sm"
              class="focus-ring h-7 w-7 rounded-full"
              :disabled="disabled || !inputText.trim()"
            >
              <ArrowUp class="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  </form>
</template>
