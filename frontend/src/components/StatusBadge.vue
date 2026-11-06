<template>
  <span class="ds-badge" :class="`ds-badge--${tone}`" role="status">
    <i v-if="dot" class="ds-badge__dot" aria-hidden="true"></i>
    <slot>{{ label }}</slot>
  </span>
</template>

<script setup lang="ts">
// Presentation only. Copy is supplied by the caller so the platform language
// catalogs stay the single source of truth. Styles live in style.css (.ds-*).
withDefaults(
  defineProps<{
    label?: string
    tone?: 'success' | 'warning' | 'danger' | 'info' | 'primary' | 'processing' | 'neutral'
    dot?: boolean
  }>(),
  { label: '', tone: 'neutral', dot: true },
)
</script>
