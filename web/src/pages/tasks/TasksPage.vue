<script setup lang="ts">
import { AppNavigationSidebar } from '@/widgets/navigation';
import { TaskList } from '@/widgets/task-list';
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <section class="min-w-0 min-h-0 flex-1 overflow-auto">
        <div class="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 sm:px-6 lg:px-8">
          <header class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 class="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                Tasks
              </h1>
              <p class="mt-2 text-sm text-muted-foreground">
                Monitor background Task Engine jobs, inspect progress, and cancel active work.
              </p>
            </div>
          </header>

          <TaskList />
        </div>
      </section>
    </div>
  </div>
</template>
