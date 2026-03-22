<script setup lang="ts">
import { ref, watch, nextTick } from 'vue';
import { Check, Pencil, Trash2 } from 'lucide-vue-next';
import type { Thread } from '../model/types';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

const props = defineProps<{
  thread: Thread;
  active: boolean;
}>();

const emit = defineEmits<{
  select: [];
  rename: [title: string];
  delete: [];
}>();

const isEditing = ref(false);
const draftTitle = ref(props.thread.title ?? '');
const inputEl = ref<HTMLInputElement | null>(null);

watch(
  () => props.thread.title,
  (title) => {
    if (!isEditing.value) draftTitle.value = title ?? '';
  },
);

async function startEditing() {
  draftTitle.value = props.thread.title ?? '';
  isEditing.value = true;
  await nextTick();
  inputEl.value?.focus();
  inputEl.value?.select();
}

function cancelEditing() {
  isEditing.value = false;
  draftTitle.value = props.thread.title ?? '';
}

function saveTitle() {
  const title = draftTitle.value.trim();
  if (!title) {
    cancelEditing();
    return;
  }
  emit('rename', title);
  isEditing.value = false;
}
</script>

<template>
  <article
    :class="
      cn(
        'group rounded-lg border px-3 py-2 transition-colors',
        props.active
          ? 'border-primary/40 bg-primary/10 text-foreground'
          : 'border-transparent text-foreground hover:bg-secondary/50',
      )
    "
  >
    <div class="flex items-center justify-between gap-2">
      <div class="min-w-0 flex-1">
        <div v-if="isEditing" class="flex items-center gap-1">
          <input
            ref="inputEl"
            v-model="draftTitle"
            type="text"
            maxlength="80"
            class="focus-ring min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground"
            @click.stop
            @keydown.enter.prevent="saveTitle"
            @keydown.esc.prevent="cancelEditing"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-7 w-7 shrink-0"
            :disabled="!draftTitle.trim()"
            @click.stop="saveTitle"
          >
            <Check class="h-4 w-4 text-[hsl(var(--success))]" />
          </Button>
        </div>

        <button v-else type="button" class="block w-full cursor-pointer text-left" @click="$emit('select')">
          <p class="truncate text-sm font-medium text-foreground">{{ thread.title ?? 'New conversation' }}</p>
        </button>
      </div>

      <div
        v-if="!isEditing"
        :class="cn('flex items-center gap-1 transition-opacity opacity-0 group-hover:opacity-100')"
      >
        <Button type="button" variant="ghost" size="icon-sm" class="h-7 w-7" @click.stop="startEditing">
          <Pencil class="h-3.5 w-3.5" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          class="h-7 w-7 text-destructive hover:!bg-destructive hover:!text-white dark:hover:!bg-destructive"
          @click.stop="$emit('delete')"
        >
          <Trash2 class="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  </article>
</template>
