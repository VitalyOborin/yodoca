<script setup lang="ts">
import type { InboxItem } from '@/shared/api';
import InboxItemCard from './InboxItem.vue';
import InboxSourceTabs from './InboxSourceTabs.vue';
import InboxEmptyState from './InboxEmptyState.vue';

const props = defineProps<{
  loading?: boolean;
  items: InboxItem[];
  tabs: string[];
  activeSource: string;
  unreadBySource: Record<string, number>;
  selectedId: number | null;
}>();

const emit = defineEmits<{
  selectSource: [source: string];
  selectItem: [id: number];
}>();
</script>

<template>
  <section class="space-y-4">
    <InboxSourceTabs
      :tabs="props.tabs"
      :active="props.activeSource"
      :unread-by-source="props.unreadBySource"
      @select="emit('selectSource', $event)"
    />

    <div v-if="props.loading" class="surface-panel rounded-xl border border-border/70 px-4 py-6 text-sm text-muted-foreground">
      Loading inbox...
    </div>

    <div v-else-if="props.items.length > 0" class="space-y-2">
      <InboxItemCard
        v-for="item in props.items"
        :key="item.id"
        :item="item"
        :active="item.id === props.selectedId"
        @select="emit('selectItem', $event)"
      />
    </div>

    <InboxEmptyState v-else />
  </section>
</template>
