<template>
  <span class="ds-confirm-trigger">
    <slot name="trigger" :open="open" />
    <el-dialog
      v-model="visible"
      :title="title"
      width="min(460px, 94vw)"
      :close-on-click-modal="false"
      append-to-body
      class="ds-confirm-dialog"
    >
      <p class="ds-confirm__desc"><slot name="description">{{ description }}</slot></p>
      <div v-if="detail" class="ds-confirm__detail">{{ detail }}</div>
      <slot />
      <template #footer>
        <el-button :disabled="loading" @click="cancel"><slot name="cancel">{{ cancelText }}</slot></el-button>
        <el-button
          :type="danger ? 'danger' : 'primary'"
          :plain="danger"
          :loading="loading"
          @click="confirm"
        ><slot name="confirm">{{ confirmText }}</slot></el-button>
      </template>
    </el-dialog>
  </span>
</template>

<script setup lang="ts">
import { ref } from 'vue'

// Self-contained visibility state: callers do not need a new ref in their
// <script setup>, so business logic stays byte-for-byte unchanged.
// Copy comes from slots/props; defaults are ASCII to keep catalog checks clean.
withDefaults(
  defineProps<{
    title?: string
    description?: string
    detail?: string
    confirmText?: string
    cancelText?: string
    danger?: boolean
    loading?: boolean
  }>(),
  { title: '', description: '', detail: '', confirmText: 'Confirm', cancelText: 'Cancel', danger: false, loading: false },
)

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const visible = ref(false)
const open = () => { visible.value = true }
const close = () => { visible.value = false }
const cancel = () => { close(); emit('cancel') }
const confirm = () => { close(); emit('confirm') }
defineExpose({ open, close })
</script>
