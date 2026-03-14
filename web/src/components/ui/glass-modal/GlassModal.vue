<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue';
import { X } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';

const props = defineProps<{
  open: boolean;
  title?: string;
  description?: string;
  widthClass?: string;
}>();

const emit = defineEmits<{
  close: [];
}>();

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) {
    emit('close');
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      window.addEventListener('keydown', handleKeydown);
      document.body.style.overflow = 'hidden';
    } else {
      window.removeEventListener('keydown', handleKeydown);
      document.body.style.overflow = '';
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  document.body.style.overflow = '';
});
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm"
    @click.self="$emit('close')"
  >
    <div
      :class="[
        'glass-panel w-full rounded-[1.5rem] border border-white/10 bg-background/85 p-3 shadow-2xl',
        widthClass ?? 'max-w-3xl',
      ]"
    >
      <div class="rounded-[1.2rem] border border-border/80 bg-background/90 p-5 sm:p-6">
        <div class="mb-5 flex items-start justify-between gap-4">
          <div>
            <h2 v-if="title" class="text-xl font-semibold text-foreground">
              {{ title }}
            </h2>
            <p v-if="description" class="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              {{ description }}
            </p>
          </div>

          <Button
            variant="ghost"
            size="icon"
            class="rounded-full"
            @click="$emit('close')"
          >
            <X class="h-4 w-4" />
          </Button>
        </div>

        <slot />
      </div>
    </div>
  </div>
</template>
