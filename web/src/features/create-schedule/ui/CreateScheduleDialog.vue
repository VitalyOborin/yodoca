<script setup lang="ts">
import { computed, ref } from 'vue';
import { Button } from '@/components/ui/button';
import { GlassModal } from '@/components/ui/glass-modal';
import type {
  CreateOnceRequest,
  CreateRecurringRequest,
  ScheduleItem,
  UpdateRecurringRequest,
} from '@/shared/api';
import CreateOnceForm from './CreateOnceForm.vue';
import CreateRecurringForm from './CreateRecurringForm.vue';

const props = defineProps<{
  open: boolean;
  loading?: boolean;
  editItem?: ScheduleItem | null;
}>();

const emit = defineEmits<{
  close: [];
  createOnce: [payload: CreateOnceRequest];
  createRecurring: [payload: CreateRecurringRequest];
  updateRecurring: [id: number, payload: UpdateRecurringRequest];
}>();

const type = ref<'once' | 'recurring'>('once');

const title = computed(() =>
  props.editItem ? 'Edit recurring schedule' : 'Create schedule',
);
const description = computed(() =>
  props.editItem
    ? 'Update recurring schedule timing and status.'
    : 'Choose schedule type and configure execution settings.',
);

function closeDialog() {
  if (props.loading) return;
  emit('close');
}

function handleCreateRecurring(payload: CreateRecurringRequest) {
  emit('createRecurring', payload);
}

function handleUpdateRecurring(id: number, payload: UpdateRecurringRequest) {
  emit('updateRecurring', id, payload);
}
</script>

<template>
  <GlassModal
    :open="open"
    :title="title"
    :description="description"
    width-class="max-w-2xl"
    @close="closeDialog"
  >
    <div class="space-y-5">
      <template v-if="!editItem">
        <div class="grid gap-3 sm:grid-cols-2">
          <Button
            :variant="type === 'once' ? 'default' : 'secondary'"
            class="rounded-xl"
            @click="type = 'once'"
          >
            One-shot
          </Button>
          <Button
            :variant="type === 'recurring' ? 'default' : 'secondary'"
            class="rounded-xl"
            @click="type = 'recurring'"
          >
            Recurring
          </Button>
        </div>

        <CreateOnceForm
          v-if="type === 'once'"
          :loading="loading"
          @submit="emit('createOnce', $event)"
        />
        <CreateRecurringForm
          v-else
          :loading="loading"
          @submit-create="handleCreateRecurring"
          @submit-update="handleUpdateRecurring"
        />
      </template>

      <CreateRecurringForm
        v-else
        :loading="loading"
        :edit-item="editItem"
        @submit-create="handleCreateRecurring"
        @submit-update="handleUpdateRecurring"
      />
    </div>
  </GlassModal>
</template>
