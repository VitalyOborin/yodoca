<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { Bot, CalendarClock, FolderKanban, Inbox, MessageSquareText, Settings, PanelLeftClose, PanelLeftOpen } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

const STORAGE_KEY = 'yodoca.nav.expanded';
const expanded = ref(false);

const navItems = [
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'inbox', label: 'Inbox', icon: Inbox },
  { id: 'projects', label: 'Projects', icon: FolderKanban },
  { id: 'schedule', label: 'Schedule', icon: CalendarClock },
  { id: 'agents', label: 'Agents', icon: Bot },
] as const;

onMounted(() => {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === '1') expanded.value = true;
  if (saved === '0') expanded.value = false;
});

watch(expanded, (value) => {
  localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
});
</script>

<template>
  <aside
    :class="[
      'glass-panel hidden h-full shrink-0 flex-col border-r border-white/10 p-3 transition-[width] duration-200 lg:flex',
      expanded ? 'w-[240px]' : 'w-[68px]',
    ]"
  >
    <div class="mb-4 flex items-center" :class="expanded ? 'justify-between' : 'justify-center'">
      <span v-if="expanded" class="text-sm font-semibold tracking-wide text-white">Yodoca</span>
      <Button
        variant="ghost"
        size="icon"
        class="focus-ring h-9 w-9 rounded-xl text-foreground/80 hover:bg-white/10 hover:text-white"
        @click="expanded = !expanded"
      >
        <PanelLeftClose v-if="expanded" class="h-4 w-4" />
        <PanelLeftOpen v-else class="h-4 w-4" />
      </Button>
    </div>

    <TooltipProvider :delay-duration="80">
      <nav class="flex flex-col gap-1">
        <Tooltip v-for="item in navItems" :key="item.id">
          <TooltipTrigger as-child>
            <button
              type="button"
              :class="[
                'focus-ring flex h-10 items-center gap-3 rounded-lg px-2.5 text-sm transition-colors',
                item.id === 'chat'
                  ? 'bg-black/45 text-white'
                  : 'text-foreground/80 hover:bg-white/10 hover:text-white',
                expanded ? 'justify-start' : 'justify-center',
              ]"
            >
              <component :is="item.icon" class="h-4 w-4 shrink-0" />
              <span v-if="expanded" class="truncate">{{ item.label }}</span>
            </button>
          </TooltipTrigger>
          <TooltipContent v-if="!expanded" side="right">{{ item.label }}</TooltipContent>
        </Tooltip>
      </nav>
    </TooltipProvider>

    <TooltipProvider :delay-duration="80">
      <div class="mt-auto">
        <Tooltip>
          <TooltipTrigger as-child>
            <button
              type="button"
              :class="[
                'focus-ring flex h-10 items-center gap-3 rounded-lg px-2.5 text-sm text-foreground/80 transition-colors hover:bg-white/10 hover:text-white',
                expanded ? 'w-full justify-start' : 'w-full justify-center',
              ]"
            >
              <Settings class="h-4 w-4 shrink-0" />
              <span v-if="expanded" class="truncate">Settings</span>
            </button>
          </TooltipTrigger>
          <TooltipContent v-if="!expanded" side="right">Settings</TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  </aside>
</template>
