<script setup lang="ts">
import { Pause, Pencil, Play, Trash2 } from 'lucide-vue-next';
import { Button } from '@/components/ui/button';

defineProps<{
  type: 'one_shot' | 'recurring';
  status: 'scheduled' | 'fired' | 'cancelled' | 'active' | 'paused';
  disabled?: boolean;
}>();

const emit = defineEmits<{
  edit: [];
  pause: [];
  resume: [];
  cancel: [];
}>();
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <Button
      v-if="type === 'recurring'"
      variant="secondary"
      size="sm"
      :disabled="disabled || status === 'cancelled'"
      @click="emit('edit')"
    >
      <Pencil class="h-3.5 w-3.5" />
      Edit
    </Button>

    <Button
      v-if="type === 'recurring' && status === 'active'"
      variant="secondary"
      size="sm"
      :disabled="disabled"
      @click="emit('pause')"
    >
      <Pause class="h-3.5 w-3.5" />
      Pause
    </Button>

    <Button
      v-if="type === 'recurring' && status === 'paused'"
      variant="secondary"
      size="sm"
      class="bg-lime-400 text-black hover:bg-lime-300"
      :disabled="disabled"
      @click="emit('resume')"
    >
      <Play class="h-3.5 w-3.5" />
      Resume
    </Button>

    <Button
      v-if="status === 'scheduled' || type === 'recurring'"
      variant="destructive"
      size="sm"
      :disabled="disabled"
      @click="emit('cancel')"
    >
      <Trash2 class="h-3.5 w-3.5" />
      Cancel
    </Button>
  </div>
</template>
