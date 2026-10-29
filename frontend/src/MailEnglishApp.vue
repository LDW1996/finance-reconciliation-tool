<template>
  <header class="product-bar" :data-build-commit="buildCommit">
    <div class="product-brand"><el-icon><Tickets /></el-icon><strong>Mail Reconciliation</strong></div>
    <nav aria-label="Main navigation">
      <button type="button" :class="{ selected: page === 'mail' }" @click="page = 'mail'">Mail receipts</button>
      <button type="button" :class="{ selected: page === 'storage' }" @click="page = 'storage'">Storage settings</button>
    </nav>
  </header>
  <MailView v-if="page === 'mail'" />
  <MailStorageSettings v-else />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { Tickets } from '@element-plus/icons-vue'
import MailView from './views/MailView.vue'
import MailStorageSettings from './components/MailStorageSettings.vue'
const page = ref('mail')
const buildCommit = (import.meta.env.VITE_BUILD_COMMIT || 'local').slice(0,8)
</script>
<style>
.product-bar { flex-wrap: wrap; }
.el-button { white-space: normal; min-height: 32px; height: auto; padding-top: 8px; padding-bottom: 8px; }
.el-form-item__label { height: auto; }
</style>
