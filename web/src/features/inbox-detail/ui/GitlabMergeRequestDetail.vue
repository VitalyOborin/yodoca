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
    <div class="flex flex-wrap items-center gap-2">
      <span class="rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-muted-foreground">
        Status: {{ asString(props.payload.state) || 'unknown' }}
      </span>
      <span class="rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-muted-foreground">
        Author: {{ asString(props.payload.author) || asString(props.payload.author_username) || 'unknown' }}
      </span>
    </div>

    <div>
      <p class="text-xs uppercase tracking-wide text-muted-foreground">Title</p>
      <p class="mt-1 text-sm font-medium text-foreground">
        {{ asString(props.payload.title) || asString(props.payload.name) || 'Merge request' }}
      </p>
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <p class="text-xs uppercase tracking-wide text-muted-foreground">Source branch</p>
        <p class="mt-1 text-sm text-foreground">{{ asString(props.payload.source_branch) || '—' }}</p>
      </div>
      <div>
        <p class="text-xs uppercase tracking-wide text-muted-foreground">Target branch</p>
        <p class="mt-1 text-sm text-foreground">{{ asString(props.payload.target_branch) || '—' }}</p>
      </div>
    </div>

    <div v-if="asString(props.payload.web_url)">
      <p class="text-xs uppercase tracking-wide text-muted-foreground">Link</p>
      <a
        class="mt-1 inline-flex text-sm text-info underline underline-offset-4"
        :href="asString(props.payload.web_url)"
        target="_blank"
        rel="noopener noreferrer"
      >
        {{ asString(props.payload.web_url) }}
      </a>
    </div>
  </section>
</template>
