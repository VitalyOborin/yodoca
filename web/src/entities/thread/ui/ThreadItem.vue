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
    if (!isEditing.value) {
      draftTitle.value = title;
    }
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
  <div
    :class="
      cn(
        'group rounded-[1.4rem] border border-white/[0.06] px-4 py-3 transition-all duration-300',
        props.active
          ? 'glass-panel -translate-y-0.5 text-sidebar-accent-foreground shadow-[0_18px_40px_rgb(0_0_0_/_0.22)]'
          : 'bg-white/[0.03] text-sidebar-foreground/[0.72] hover:-translate-y-0.5 hover:border-white/[0.12] hover:bg-white/[0.08]',
      )
    "
  >
    <div class="mb-3 flex items-start justify-between gap-3">
      <div class="min-w-0 flex-1">
        <div v-if="isEditing" class="flex items-center gap-2">
          <input
            v-model="draftTitle"
            type="text"
            maxlength="80"
            class="min-w-0 flex-1 rounded-xl border border-white/10 bg-black/[0.18] px-3 py-2 text-sm font-semibold tracking-[-0.02em] text-sidebar-foreground outline-none focus:border-primary/50"
            @click.stop
            @keydown.enter.prevent="saveTitle"
            @keydown.esc.prevent="cancelEditing"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-8 w-8 rounded-xl border border-white/10 bg-white/[0.06] text-primary hover:bg-white/[0.12]"
            :disabled="!draftTitle.trim()"
            @click.stop="saveTitle"
          >
            <Check class="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-8 w-8 rounded-xl border border-white/10 bg-white/[0.06] text-sidebar-foreground/[0.68] hover:bg-white/[0.12]"
            @click.stop="cancelEditing"
          >
            <X class="h-4 w-4" />
          </Button>
        </div>

        <button
          v-else
          type="button"
          class="block w-full cursor-pointer text-left"
          @click="$emit('select')"
        >
          <span class="block truncate pr-2 text-sm font-semibold tracking-[-0.02em] text-sidebar-foreground">
            {{ thread.title }}
          </span>
          <span class="mt-1 block text-[11px] uppercase tracking-[0.24em] text-sidebar-foreground/[0.38]">
            {{ formatRelativeTime(thread.updatedAt) }}
          </span>
        </button>
      </div>

      <div class="flex shrink-0 items-start gap-1">
        <span
          :class="
            cn(
              'rounded-full border px-2 py-1 text-[10px] font-medium uppercase tracking-[0.18em] transition-opacity duration-200',
              props.active
                ? 'border-primary/30 bg-primary/[0.18] text-primary'
                : 'border-white/10 bg-white/[0.06] text-sidebar-foreground/[0.52]',
              isEditing ? 'opacity-0' : 'opacity-100 group-hover:opacity-0',
            )
          "
        >
          {{ thread.messageCount }}
        </span>

        <div
          :class="
            cn(
              'flex items-center gap-1 transition-all duration-200',
              isEditing
                ? 'pointer-events-none absolute opacity-0'
                : 'pointer-events-none w-0 overflow-hidden opacity-0 group-hover:pointer-events-auto group-hover:w-auto group-hover:opacity-100',
            )
          "
        >
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-8 w-8 rounded-xl border border-white/10 bg-white/[0.06] text-sidebar-foreground/[0.66] hover:bg-white/[0.12]"
            @click.stop="startEditing"
          >
            <Pencil class="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            class="h-8 w-8 rounded-xl border border-white/10 bg-white/[0.06] text-destructive hover:bg-destructive/15"
            @click.stop="$emit('delete')"
          >
            <Trash2 class="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>

    <button
      v-if="!isEditing"
      type="button"
      class="block w-full cursor-pointer text-left"
      @click="$emit('select')"
    >
      <p class="m-0 max-h-12 overflow-hidden text-sm leading-6 text-sidebar-foreground/[0.6]">
        {{ thread.lastMessagePreview }}
      </p>
    </button>
    <p v-else class="m-0 max-h-12 overflow-hidden text-sm leading-6 text-sidebar-foreground/[0.42]">
      Press Enter to save the title, or Esc to cancel.
    </p>
  </div>
</template>
