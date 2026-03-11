<script setup lang="ts">
import { ThreadSidebar } from '@/widgets/sidebar';
import { ChatPanel } from '@/widgets/chat-panel';
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4 lg:p-5">
    <div
      class="glass-panel noise-overlay flex h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-[2rem] border border-white/10 lg:h-[calc(100vh-2.5rem)] lg:flex-row"
    >
      <ThreadSidebar />
      <ChatPanel />
    </div>
  </div>
</template>
