<script setup lang="ts">
import { Bell, Github, GitPullRequest, Mail } from 'lucide-vue-next';
import type { InboxItem as InboxItemType } from '@/shared/api';
import { formatRelative } from '@/shared/lib';

const props = defineProps<{
  item: InboxItemType;
  active?: boolean;
}>();

const emit = defineEmits<{
  select: [id: number];
}>();

function sourceIcon(sourceType: string) {
  if (sourceType === 'mail') return Mail;
  if (sourceType === 'gitlab') return GitPullRequest;
  if (sourceType === 'github') return Github;
  return Bell;
}
</script>

<template>
  <button
    type="button"
    class="surface-panel w-full rounded-xl border p-3 text-left transition-colors"
    :class="[
      active
        ? 'border-primary/40 bg-primary/10'
        : 'border-border/70 hover:border-white/20 hover:bg-white/[0.02]',
    ]"
    @click="emit('select', item.id)"
  >
    <div class="flex items-start gap-3">
      <div class="mt-0.5 rounded-lg border border-border bg-secondary/50 p-1.5 text-muted-foreground">
        <component :is="sourceIcon(item.source_type)" class="h-4 w-4" />
      </div>

      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 text-xs text-muted-foreground">
          <span class="truncate">{{ item.source_type }}</span>
          <span aria-hidden="true">·</span>
          <span class="truncate">{{ item.source_account || 'unknown' }}</span>
        </div>

        <p
          class="mt-1 truncate text-sm"
          :class="item.is_read ? 'font-medium text-foreground/90' : 'font-semibold text-foreground'"
        >
          {{ item.title || item.entity_type }}
        </p>

        <div class="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
          <span v-if="!item.is_read" class="inline-block h-2 w-2 rounded-full bg-info" />
          <span class="truncate">{{ formatRelative(new Date(item.occurred_at * 1000)) }}</span>
        </div>
      </div>
    </div>
  </button>
</template>
