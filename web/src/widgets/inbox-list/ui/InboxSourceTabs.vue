<script setup lang="ts">
import { Button } from '@/components/ui/button';

const props = defineProps<{
  tabs: string[];
  active: string;
  unreadBySource: Record<string, number>;
}>();

const emit = defineEmits<{
  select: [source: string];
}>();

function tabLabel(source: string): string {
  if (source === 'all') return 'All';
  return source.slice(0, 1).toUpperCase() + source.slice(1);
}
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <Button
      v-for="source in props.tabs"
      :key="source"
      size="sm"
      :variant="props.active === source ? 'default' : 'secondary'"
      class="rounded-full"
      @click="emit('select', source)"
    >
      {{ tabLabel(source) }}
      <span
        v-if="(props.unreadBySource[source] ?? 0) > 0"
        class="ml-1 rounded-full bg-destructive/90 px-1.5 py-0.5 text-[10px] leading-none text-white"
      >
        {{ props.unreadBySource[source] ?? 0 }}
      </span>
    </Button>
  </div>
</template>
