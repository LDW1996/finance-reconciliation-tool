<template>
  <header class="product-bar" :data-build-commit="buildCommit">
    <div class="product-brand"><el-icon><Tickets /></el-icon><strong>Mail Reconciliation</strong></div>
    <nav aria-label="Main navigation">
      <button type="button" :class="{ selected: page === 'mail' }" @click="page = 'mail'">Mail receipts</button>
      <button type="button" :class="{ selected: page === 'storage' }" @click="page = 'storage'">Storage settings</button>
    </nav>
  </header>
  <MailView v-if="page === 'mail'" />
  <main v-else class="mail-storage-page"><MailStorageSettings standalone /></main>
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
.mail-storage-page{max-width:1600px;margin:0 auto;padding:32px 36px 48px;background:white;min-height:calc(100vh - 76px)}
.mail-storage-page>h1{font-size:28px;margin:0 0 26px}
@media(max-width:680px){.mail-storage-page{padding:24px 16px}.mail-storage-page>h1{font-size:24px}}

.el-button { white-space: normal; min-height: 32px; height: auto; padding-top: 8px; padding-bottom: 8px; }
.el-form-item__label { height: auto; }
</style>
