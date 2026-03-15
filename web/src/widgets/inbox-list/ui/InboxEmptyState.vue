<script setup lang="ts">
import { Inbox } from 'lucide-vue-next';
</script>

<template>
  <div class="surface-panel rounded-2xl border border-dashed border-border p-8 text-center">
    <div class="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/5 text-foreground/80">
      <Inbox class="h-6 w-6" />
    </div>
    <h3 class="mt-4 text-lg font-semibold text-foreground">Inbox is empty</h3>
    <p class="mt-2 text-sm text-muted-foreground">
      New notifications from Mail, GitLab, GitHub, and scheduler will appear here.
    </p>
  </div>
</template>
