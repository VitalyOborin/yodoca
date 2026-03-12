<script setup lang="ts">
import { ThreadSidebar } from '@/widgets/sidebar';
import { ChatPanel } from '@/widgets/chat-panel';
import { AppNavigationSidebar } from '@/widgets/navigation';
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <section class="min-w-0 min-h-0 flex flex-1 overflow-hidden">
        <div class="surface-panel flex min-w-0 flex-1 rounded-none">
          <ThreadSidebar />
          <ChatPanel />
        </div>
      </section>
    </div>
  </div>
</template>
