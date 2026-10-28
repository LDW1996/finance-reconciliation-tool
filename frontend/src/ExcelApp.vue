<template>
  <header class="product-bar" :data-build-commit="buildCommit">
    <div class="product-brand"><el-icon><Tickets /></el-icon><strong>Excel对账工具</strong></div>
  </header>
  <ReconciliationView />
</template>
<script setup lang="ts">
import { Tickets } from '@element-plus/icons-vue'
import ReconciliationView from './views/ReconciliationView.vue'
const buildCommit=(import.meta.env.VITE_BUILD_COMMIT || 'local').slice(0,8)
</script>
