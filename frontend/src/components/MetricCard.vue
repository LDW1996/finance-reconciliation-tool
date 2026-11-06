<template>
  <div class="ds-metric" :class="tone ? `ds-metric--${tone}` : ''">
    <p class="ds-metric__label"><slot name="label">{{ label }}</slot></p>
    <p class="ds-metric__value ds-num"><slot>{{ value }}</slot></p>
    <p v-if="hint || $slots.hint" class="ds-metric__hint"><slot name="hint">{{ hint }}</slot></p>
  </div>
</template>

<script setup lang="ts">
// Presentation only. Numeric values are never formatted here: the caller owns
// the exact business value and its locale. Styles live in style.css (.ds-*).
withDefaults(
  defineProps<{
    label?: string
    value?: string | number
    hint?: string
    tone?: 'success' | 'warning' | 'danger' | ''
  }>(),
  { label: '', value: '', hint: '', tone: '' },
)
</script>
