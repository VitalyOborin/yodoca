<script setup lang="ts">
import { computed } from 'vue';
import { Compass, Sparkles, SquarePen } from 'lucide-vue-next';
import { useThreadStore, ThreadItem } from '@/entities/thread';
import type { Thread } from '@/entities/thread';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

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
      const group = groups[0];
      if (group) group.items.push(thread);
      continue;
    }
    if (diff < 172_800_000) {
      const group = groups[1];
      if (group) group.items.push(thread);
      continue;
    }
    const group = groups[2];
    if (group) group.items.push(thread);
  }

  return groups.filter((group) => group.items.length > 0);
});
</script>

<template>
  <aside
    class="mesh-card flex shrink-0 flex-col border-b border-white/10 lg:sticky lg:top-0 lg:h-full lg:w-[340px] lg:min-w-[340px] lg:self-start lg:border-r lg:border-b-0"
  >
    <div class="px-4 pt-4 pb-3 sm:px-5 lg:px-6 lg:pt-6">
      <div class="mb-5 flex items-start justify-between gap-4">
        <div>
          <p class="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.08] px-3 py-1 text-[10px] font-medium uppercase tracking-[0.32em] text-primary">
            <Sparkles class="h-3.5 w-3.5" />
            Experimental canvas
          </p>
          <h1 class="text-2xl font-bold tracking-[-0.04em] text-sidebar-foreground">Yodoca</h1>
          <p class="mt-2 max-w-[22ch] text-sm leading-6 text-sidebar-foreground/[0.64]">
            Chat workspace with cinematic density, ambient gradients and bold hierarchy.
          </p>
        </div>
        <TooltipProvider :delay-duration="300">
          <Tooltip>
            <TooltipTrigger as-child>
              <Button
                variant="ghost"
                size="icon"
                class="h-11 w-11 rounded-2xl border border-white/10 bg-white/[0.08] text-sidebar-foreground hover:bg-white/[0.14]"
                @click="threadStore.createThread()"
              >
                <SquarePen class="h-[18px] w-[18px]" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">New chat</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      <div
        class="glass-panel rounded-[1.6rem] border border-white/10 px-4 py-4 text-sidebar-foreground"
      >
        <div class="mb-4 flex items-center justify-between">
          <span class="text-xs uppercase tracking-[0.28em] text-sidebar-foreground/[0.48]">Focus</span>
          <Compass class="h-4 w-4 text-accent" />
        </div>
        <p class="display-title text-3xl leading-none text-white">Design first.</p>
        <p class="mt-3 text-sm leading-6 text-sidebar-foreground/[0.66]">
          Threads feel curated and project-aware, with controls living directly in context.
        </p>
        <div class="mt-5 grid grid-cols-3 gap-2 text-center">
          <div class="rounded-2xl border border-white/[0.08] bg-black/[0.1] px-2 py-3">
            <p class="text-lg font-bold text-white">{{ threadStore.sortedThreads.length }}</p>
            <p class="text-[10px] uppercase tracking-[0.2em] text-sidebar-foreground/[0.42]">Threads</p>
          </div>
          <div class="rounded-2xl border border-white/[0.08] bg-black/[0.1] px-2 py-3">
            <p class="text-lg font-bold text-white">Live</p>
            <p class="text-[10px] uppercase tracking-[0.2em] text-sidebar-foreground/[0.42]">Mock</p>
          </div>
          <div class="rounded-2xl border border-white/[0.08] bg-black/[0.1] px-2 py-3">
            <p class="text-lg font-bold text-white">AG</p>
            <p class="text-[10px] uppercase tracking-[0.2em] text-sidebar-foreground/[0.42]">Flow</p>
          </div>
        </div>
      </div>
    </div>

    <div class="flex items-center justify-between px-4 pb-2 sm:px-5 lg:px-6">
      <span class="text-xs uppercase tracking-[0.28em] text-sidebar-foreground/[0.42]">Threads</span>
      <TooltipProvider :delay-duration="300">
        <Tooltip>
          <TooltipTrigger as-child>
            <Button
              variant="ghost"
              size="sm"
              class="rounded-full border border-white/10 bg-white/[0.05] px-3 text-[11px] uppercase tracking-[0.24em] text-sidebar-foreground/[0.62] hover:bg-white/[0.1]"
              @click="threadStore.createThread()"
            >
              New
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">New chat</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>

    <ScrollArea class="flex-1 px-3 pb-4 sm:px-4 lg:px-5">
      <nav class="flex flex-col gap-4">
        <section v-for="group in groupedThreads" :key="group.label" class="flex flex-col gap-2">
          <div class="px-1">
            <p class="text-[10px] uppercase tracking-[0.28em] text-sidebar-foreground/[0.34]">
              {{ group.label }}
            </p>
          </div>
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
