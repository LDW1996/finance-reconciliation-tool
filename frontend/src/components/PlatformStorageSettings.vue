<template><main class="platform-storage"><h1>保存设置</h1><MailStorageSettings v-if="capabilities.includes('mail')"/><IlogRetentionSettings v-if="capabilities.includes('ilog')"/></main></template>
<script setup lang="ts">
import MailStorageSettings from './MailStorageSettings.vue'
import IlogRetentionSettings from './IlogRetentionSettings.vue'
defineProps<{capabilities:string[]}>()
</script>
<style scoped>.platform-storage{padding:24px}</style>
