<script setup lang="ts">
import { computed } from 'vue';
import { Bell, Github, GitPullRequest, Mail, Trash2 } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';
import type { InboxItem } from '@/shared/api';
import { formatDateTime, formatRelative } from '@/shared/lib';
import EmailMessageDetail from './EmailMessageDetail.vue';
import GitlabMergeRequestDetail from './GitlabMergeRequestDetail.vue';
import GenericPayloadDetail from './GenericPayloadDetail.vue';

const props = defineProps<{
  item: InboxItem | null;
  busy?: boolean;
}>();

const emit = defineEmits<{
  read: [id: number];
  delete: [id: number];
}>();

const detailKind = computed(() => {
  const item = props.item;
  if (!item) return 'none';
  if (item.entity_type === 'email.message') return 'email';
  if (item.source_type === 'gitlab' && item.entity_type.includes('merge_request')) {
    return 'gitlab-merge-request';
  }
  return 'generic';
});

const sourceLabel = computed(() => {
  const item = props.item;
  if (!item) return '';
  return item.source_type || 'unknown';
});

const sourceIcon = computed(() => {
  const item = props.item;
  if (!item) return Bell;
  if (item.source_type === 'mail') return Mail;
  if (item.source_type === 'gitlab') return GitPullRequest;
  if (item.source_type === 'github') return Github;
  return Bell;
});
</script>

<template>
  <aside class="surface-panel flex min-h-0 flex-col rounded-2xl border border-border/80">
    <template v-if="item">
      <header class="border-b border-border/70 p-4">
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="inline-flex items-center gap-2 rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-muted-foreground">
              <component :is="sourceIcon" class="h-3.5 w-3.5" />
              {{ sourceLabel }}
            </div>
            <h2 class="mt-3 text-lg font-semibold text-foreground">{{ item.title || item.entity_type }}</h2>
            <p class="mt-1 text-xs text-muted-foreground">
              {{ formatDateTime(new Date(item.occurred_at * 1000), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}
              · {{ formatRelative(new Date(item.occurred_at * 1000)) }}
            </p>
          </div>
          <div class="flex items-center gap-2">
            <Button
              v-if="!item.is_read"
              size="sm"
              variant="secondary"
              :disabled="busy"
              @click="emit('read', item.id)"
            >
              Mark as read
            </Button>
            <Button size="icon-sm" variant="ghost" :disabled="busy" @click="emit('delete', item.id)">
              <Trash2 class="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>

      <div class="min-h-0 flex-1 overflow-auto p-4">
        <EmailMessageDetail v-if="detailKind === 'email'" :payload="item.payload" />
        <GitlabMergeRequestDetail
          v-else-if="detailKind === 'gitlab-merge-request'"
          :payload="item.payload"
        />
        <GenericPayloadDetail v-else :payload="item.payload" />
      </div>
    </template>

    <div v-else class="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
      Select an item to inspect full payload.
    </div>
  </aside>
</template>
