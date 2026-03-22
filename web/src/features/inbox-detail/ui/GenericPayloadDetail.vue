<script setup lang="ts">
const props = defineProps<{
  payload: Record<string, unknown>;
}>();
</script>

<template>
  <section>
    <p class="text-xs uppercase tracking-wide text-muted-foreground">Raw payload</p>
    <pre class="mt-2 max-h-[380px] overflow-auto rounded-xl border border-border/70 bg-black/20 p-3 text-xs text-foreground/90">{{ JSON.stringify(props.payload, null, 2) }}</pre>
  </section>
</template>
