<script setup lang="ts">
import { ref, watch } from 'vue';
import { Check, Pencil, Trash2, X } from 'lucide-vue-next';
import type { Thread } from '../model/types';
import { formatRelativeTime } from '@/shared/lib';
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
const draftTitle = ref(props.thread.title);

watch(
  () => props.thread.title,
  (title) => {
    if (!isEditing.value) draftTitle.value = title;
  },
);

function startEditing() {
  isEditing.value = true;
  draftTitle.value = props.thread.title;
}

function cancelEditing() {
  isEditing.value = false;
  draftTitle.value = props.thread.title;
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
          ? 'border-primary/50 bg-primary/12 text-foreground'
          : 'border-border bg-secondary/35 text-foreground hover:border-primary/35 hover:bg-secondary/70',
      )
    "
  >
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0 flex-1">
        <div v-if="isEditing" class="flex items-center gap-1.5">
          <input
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
            class="h-7 w-7"
            :disabled="!draftTitle.trim()"
            @click.stop="saveTitle"
          >
            <Check class="h-4 w-4 text-[hsl(var(--success))]" />
          </Button>
          <Button type="button" variant="ghost" size="icon-sm" class="h-7 w-7" @click.stop="cancelEditing">
            <X class="h-4 w-4 text-muted-foreground" />
          </Button>
        </div>

        <button v-else type="button" class="block w-full cursor-pointer text-left" @click="$emit('select')">
          <p class="truncate text-sm font-medium text-foreground">{{ thread.title }}</p>
          <p class="mt-1 text-xs text-subtle-foreground">{{ formatRelativeTime(thread.updatedAt) }}</p>
        </button>
      </div>

      <div class="flex items-start gap-1">
        <div
          :class="
            cn(
              'flex items-center gap-1 transition-opacity',
              isEditing ? 'pointer-events-none opacity-0' : 'opacity-0 group-hover:opacity-100',
            )
          "
        >
          <Button type="button" variant="ghost" size="icon-sm" class="h-7 w-7" @click.stop="startEditing">
            <Pencil class="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-7 w-7 text-destructive"
            @click.stop="$emit('delete')"
          >
            <Trash2 class="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>

    <button v-if="!isEditing" type="button" class="mt-2 block w-full cursor-pointer text-left" @click="$emit('select')">
      <p class="line-clamp-2 text-xs leading-5 text-muted-foreground">{{ thread.lastMessagePreview || 'No messages yet' }}</p>
    </button>
    <p v-else class="mt-2 text-xs text-subtle-foreground">Enter to save, Esc to cancel.</p>
  </article>
</template>
