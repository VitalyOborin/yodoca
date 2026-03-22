<script setup lang="ts">
import { ChevronLeft, ChevronRight } from 'lucide-vue-next';
import { computed } from 'vue';
import { Button } from '@/components/ui/button';

const props = withDefaults(
  defineProps<{
    total: number;
    limit: number;
    offset: number;
    disabled?: boolean;
    siblingCount?: number;
  }>(),
  {
    disabled: false,
    siblingCount: 1,
  },
);

const emit = defineEmits<{
  updateOffset: [offset: number];
}>();

const totalPages = computed(() => {
  if (props.total <= 0 || props.limit <= 0) return 1;
  return Math.max(1, Math.ceil(props.total / props.limit));
});

const currentPage = computed(() => {
  if (props.limit <= 0) return 1;
  const page = Math.floor(props.offset / props.limit) + 1;
  return Math.min(Math.max(1, page), totalPages.value);
});

const pageWindow = computed(() => {
  const pages: number[] = [];
  const left = Math.max(1, currentPage.value - props.siblingCount);
  const right = Math.min(totalPages.value, currentPage.value + props.siblingCount);

  if (left > 1) {
    pages.push(1);
    if (left > 2) {
      pages.push(-1);
    }
  }

  for (let page = left; page <= right; page += 1) {
    pages.push(page);
  }

  if (right < totalPages.value) {
    if (right < totalPages.value - 1) {
      pages.push(-1);
    }
    pages.push(totalPages.value);
  }

  return pages;
});

const canGoPrev = computed(() => currentPage.value > 1);
const canGoNext = computed(() => currentPage.value < totalPages.value);

const from = computed(() => {
  if (props.total <= 0) return 0;
  return props.offset + 1;
});

const to = computed(() => {
  if (props.total <= 0) return 0;
  return Math.min(props.offset + props.limit, props.total);
});

function setPage(page: number) {
  if (props.disabled) return;
  if (page < 1 || page > totalPages.value) return;
  const nextOffset = (page - 1) * props.limit;
  if (nextOffset === props.offset) return;
  emit('updateOffset', nextOffset);
}
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/70 bg-card/40 px-3 py-2">
    <p class="text-xs text-muted-foreground">
      {{ from }}-{{ to }} of {{ props.total }}
    </p>

    <div class="flex items-center gap-1">
      <Button
        size="icon-sm"
        variant="outline"
        :disabled="props.disabled || !canGoPrev"
        @click="setPage(currentPage - 1)"
      >
        <ChevronLeft class="h-4 w-4" />
      </Button>

      <template v-for="page in pageWindow" :key="page">
        <span
          v-if="page === -1"
          class="px-2 text-xs text-muted-foreground"
        >
          ...
        </span>
        <Button
          v-else
          size="sm"
          :variant="page === currentPage ? 'default' : 'ghost'"
          class="min-w-8 px-2"
          :disabled="props.disabled"
          @click="setPage(page)"
        >
          {{ page }}
        </Button>
      </template>

      <Button
        size="icon-sm"
        variant="outline"
        :disabled="props.disabled || !canGoNext"
        @click="setPage(currentPage + 1)"
      >
        <ChevronRight class="h-4 w-4" />
      </Button>
    </div>
  </div>
</template>
