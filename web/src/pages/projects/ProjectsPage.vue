<script setup lang="ts">
import { AppNavigationSidebar } from '@/widgets/navigation';
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />
      <section class="surface-panel min-w-0 min-h-0 flex-1 rounded-none" />
    </div>
  </div>
</template>
