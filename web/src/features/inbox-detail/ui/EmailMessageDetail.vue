<script setup lang="ts">
const props = defineProps<{
  payload: Record<string, unknown>;
}>();

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}
</script>

<template>
  <section class="space-y-4">
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <p class="text-xs uppercase tracking-wide text-muted-foreground">From</p>
        <p class="mt-1 text-sm text-foreground">{{ asString(props.payload.from) || '—' }}</p>
      </div>
      <div>
        <p class="text-xs uppercase tracking-wide text-muted-foreground">To</p>
        <p class="mt-1 text-sm text-foreground">{{ asString(props.payload.to) || '—' }}</p>
      </div>
    </div>

    <div>
      <p class="text-xs uppercase tracking-wide text-muted-foreground">Subject</p>
      <p class="mt-1 text-sm font-medium text-foreground">{{ asString(props.payload.subject) || '—' }}</p>
    </div>

    <div v-if="asString(props.payload.snippet)">
      <p class="text-xs uppercase tracking-wide text-muted-foreground">Snippet</p>
      <p class="mt-1 text-sm text-muted-foreground">{{ asString(props.payload.snippet) }}</p>
    </div>

    <div>
      <p class="text-xs uppercase tracking-wide text-muted-foreground">Body</p>
      <pre class="mt-2 max-h-[320px] overflow-auto rounded-xl border border-border/70 bg-black/20 p-3 text-xs text-foreground/90 whitespace-pre-wrap">{{ asString(props.payload.body) || asString(props.payload.text) || 'No body provided.' }}</pre>
    </div>
  </section>
</template>
