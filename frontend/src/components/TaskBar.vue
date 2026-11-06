<template>
  <div class="ds-task" :class="{ 'ds-task--paused': state === 'paused' }">
    <div class="ds-task__head">
      <span class="ds-task__phase"><slot name="phase">{{ phase }}</slot></span>
      <span class="ds-task__count ds-num">{{ current }} / {{ total }}</span>
    </div>
    <div
      class="ds-task__bar"
      role="progressbar"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="ariaLabel"
    >
      <div class="ds-task__fill" :style="{ width: `${percent}%` }"></div>
    </div>
    <div class="ds-task__foot">
      <span class="ds-task__note"><slot name="note">{{ note }}</slot></span>
      <div class="ds-task__actions"><slot name="actions" /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

// Presentation only. The percentage is always derived from the caller's
// counters; it never writes back to task state. Styles live in style.css.
const props = withDefaults(
  defineProps<{
    phase?: string
    current?: number
    total?: number
    state?: 'idle' | 'running' | 'paused' | 'done'
    note?: string
    ariaLabel?: string
  }>(),
  { phase: '', current: 0, total: 0, state: 'idle', note: '', ariaLabel: 'Progress' },
)

const percent = computed(() => {
  const total = Number(props.total) || 0
  if (total <= 0) return 0
  const current = Math.max(0, Number(props.current) || 0)
  return Math.min(100, Math.round((current / total) * 100))
})
</script>
