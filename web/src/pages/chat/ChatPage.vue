<script setup lang="ts">
import { computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ThreadSidebar } from '@/widgets/sidebar';
import { ChatPanel } from '@/widgets/chat-panel';
import { AppNavigationSidebar } from '@/widgets/navigation';
import { useThreadStore } from '@/entities/thread';

const route = useRoute();
const router = useRouter();
const threadStore = useThreadStore();

const routeThreadId = computed(() => {
  const param = route.params.threadId;
  return Array.isArray(param) ? (param[0] ?? null) : (param ?? null);
});

watch(
  routeThreadId,
  (id) => {
    if (id !== threadStore.activeThreadId) {
      threadStore.selectThread(id);
    }
  },
  { immediate: true },
);

watch(
  () => route.name,
  (name) => {
    if (name === 'chat' && threadStore.activeThreadId !== null) {
      threadStore.selectThread(null);
    }
  },
  { immediate: true },
);

watch(
  () => threadStore.activeThreadId,
  (id) => {
    const currentParam = routeThreadId.value;

    if (id && id !== currentParam) {
      router.replace({ name: 'chat-thread', params: { threadId: id } });
    } else if (!id && route.name !== 'chat') {
      router.replace({ name: 'chat' });
    }
  }
);
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
