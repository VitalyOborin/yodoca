<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/components/ui/pagination';
import { useInboxStore } from '@/entities/inbox';
import { InboxDetailPanel } from '@/features/inbox-detail';
import { AppNavigationSidebar } from '@/widgets/navigation';
import { InboxList } from '@/widgets/inbox-list';

const inboxStore = useInboxStore();

const activeSource = computed(() => inboxStore.sourceFilter);
const pageError = computed(() => inboxStore.error);
const items = computed(() => inboxStore.items);
const selectedId = computed(() => inboxStore.selectedId);
const selectedItem = computed(() => inboxStore.selectedItem);
const total = computed(() => inboxStore.total);
const pageLimit = computed(() => inboxStore.pageLimit);
const pageOffset = computed(() => inboxStore.pageOffset);

watch(
  () => [inboxStore.sourceFilter, inboxStore.entityTypeFilter, inboxStore.statusFilter, inboxStore.unreadOnly],
  () => {
    inboxStore.resetPagination();
    void inboxStore.loadInbox();
  },
);

function onSelectSource(source: string) {
  inboxStore.sourceFilter = source;
}

function onSelectItem(id: number) {
  void inboxStore.selectItem(id);
}

function onMarkAllRead() {
  void inboxStore.readAll();
}

function onReadItem(id: number) {
  void inboxStore.readItem(id);
}

function onDeleteItem(id: number) {
  void inboxStore.softDelete(id);
}

function onPageOffsetChange(offset: number) {
  inboxStore.setOffset(offset);
}

onMounted(() => {
  void inboxStore.bootstrap();
  void inboxStore.loadInbox();
});
</script>

<template>
  <div class="h-screen w-full overflow-hidden p-3 sm:p-4">
    <div class="glass-panel flex h-full min-h-0 overflow-hidden rounded-2xl">
      <AppNavigationSidebar />

      <section class="min-w-0 min-h-0 flex-1 overflow-auto">
        <div class="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 sm:px-6 lg:px-8">
          <header class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 class="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">Inbox</h1>
              <p class="mt-2 text-sm text-muted-foreground">
                Unified feed from mail, GitLab, GitHub, scheduler, and other extensions.
              </p>
            </div>
            <Button
              size="sm"
              class="rounded-full px-4"
              :disabled="inboxStore.saving || inboxStore.unreadCount === 0 || inboxStore.isUnavailable"
              @click="onMarkAllRead"
            >
              Mark all read
            </Button>
          </header>

          <p v-if="pageError" class="text-sm text-destructive">{{ pageError }}</p>

          <div
            v-if="inboxStore.isUnavailable"
            class="surface-panel rounded-2xl border border-dashed border-border px-6 py-10 text-center text-sm text-muted-foreground"
          >
            Inbox extension is unavailable. Start the extension to use this page.
          </div>

          <div v-else class="grid min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
            <div class="space-y-3">
              <InboxList
                :loading="inboxStore.loading"
                :items="items"
                :tabs="inboxStore.sourceTabs"
                :active-source="activeSource"
                :unread-by-source="inboxStore.sourceUnread"
                :selected-id="selectedId"
                @select-source="onSelectSource"
                @select-item="onSelectItem"
              />
              <Pagination
                :total="total"
                :limit="pageLimit"
                :offset="pageOffset"
                :disabled="inboxStore.loading || inboxStore.saving"
                @update-offset="onPageOffsetChange"
              />
            </div>
            <InboxDetailPanel
              :item="selectedItem"
              :busy="inboxStore.saving"
              @read="onReadItem"
              @delete="onDeleteItem"
            />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
