<script setup lang="ts">
import { computed } from 'vue';
import { MessageSquarePlus, Search } from 'lucide-vue-next';
import { useThreadStore, ThreadItem } from '@/entities/thread';
import type { Thread } from '@/entities/thread';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';

const threadStore = useThreadStore();

const groupedThreads = computed(() => {
  const now = Date.now();
  const groups = [
    { label: 'Today', items: [] as Thread[] },
    { label: 'Yesterday', items: [] as Thread[] },
    { label: 'Earlier', items: [] as Thread[] },
  ];

  for (const thread of threadStore.sortedThreads) {
    const diff = now - thread.updatedAt.getTime();

    if (diff < 86_400_000) {
      groups[0]?.items.push(thread);
      continue;
    }

    if (diff < 172_800_000) {
      groups[1]?.items.push(thread);
      continue;
    }

    groups[2]?.items.push(thread);
  }

  return groups.filter((group) => group.items.length > 0);
});
</script>

<template>
  <aside class="surface-panel hidden h-full w-[320px] shrink-0 flex-col border-r border-border lg:flex">
    <div class="border-b border-border px-4 py-4">
      <div class="flex items-center justify-between gap-2">
        <div>
          <p class="text-[11px] uppercase tracking-[0.22em] text-muted-foreground">Threads</p>
          <h2 class="mt-1 text-base font-semibold text-foreground">Conversation inbox</h2>
        </div>

        <Button
          variant="secondary"
          size="icon"
          class="focus-ring"
          aria-label="New thread"
          @click="threadStore.createThread()"
        >
          <MessageSquarePlus class="h-4 w-4" />
        </Button>
      </div>

      <div class="mt-3 flex items-center gap-2 rounded-md border border-border bg-secondary/40 px-3 py-2 text-sm text-muted-foreground">
        <Search class="h-4 w-4" />
        <span>Search threads...</span>
      </div>
    </div>

    <ScrollArea class="min-h-0 flex-1 px-3 py-3">
      <nav class="space-y-4">
        <section v-for="group in groupedThreads" :key="group.label" class="space-y-2">
          <p class="px-1 text-[10px] uppercase tracking-[0.22em] text-subtle-foreground">{{ group.label }}</p>

          <ThreadItem
            v-for="thread in group.items"
            :key="thread.id"
            :thread="thread"
            :active="thread.id === threadStore.activeThreadId"
            @select="threadStore.selectThread(thread.id)"
            @rename="threadStore.renameThread(thread.id, $event)"
            @delete="threadStore.deleteThread(thread.id)"
          />
        </section>
      </nav>
    </ScrollArea>
  </aside>
</template>
