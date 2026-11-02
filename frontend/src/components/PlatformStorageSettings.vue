<template><main class="platform-storage"><ModuleTitle class="storage-title" :icon="Setting">保存设置</ModuleTitle><MailStorageSettings v-if="capabilities.includes('mail')"/><IlogRetentionSettings v-if="capabilities.includes('ilog')"/></main></template>
<script setup lang="ts">
import { Setting } from '@element-plus/icons-vue'
import ModuleTitle from './ModuleTitle.vue'
import MailStorageSettings from './MailStorageSettings.vue'
import IlogRetentionSettings from './IlogRetentionSettings.vue'
defineProps<{capabilities:string[]}>()
</script>
<style scoped>
.platform-storage { padding: 36px 32px 48px; }
.platform-storage > .storage-title { margin: 0 0 28px; line-height: 1.4; }
@media (max-width: 680px) {
  .platform-storage { padding: 28px 16px 36px; }
  .platform-storage > .storage-title { margin-bottom: 24px; }
}
</style>
