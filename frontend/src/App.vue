<template>
  <header class="product-bar">
    <div class="product-brand"><el-icon><Tickets /></el-icon><strong>财务对账工具</strong></div>
    <nav aria-label="功能导航">
      <button :class="{ selected: page === 'excel' }" @click="navigate('excel')">Excel对账</button>
      <button :class="{ selected: page === 'unlock' }" @click="navigate('unlock')">iLog批量解锁</button>
      <button :class="{ selected: page === 'accounts' }" @click="navigate('accounts')">账号管理</button>
    </nav>
    <span class="product-local">本机工作台</span>
  </header>
  <div v-show="page === 'excel'"><ReconciliationView /></div>
  <UnlockView v-show="page !== 'excel'" :view="page === 'accounts' ? 'accounts' : 'unlock'" @navigate="navigate" />
</template>
<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import { Tickets } from '@element-plus/icons-vue'
import ReconciliationView from './views/ReconciliationView.vue'
import UnlockView from './views/UnlockView.vue'
type Page = 'excel' | 'unlock' | 'accounts'
const fromHash = (): Page => ['excel', 'accounts'].includes(location.hash.slice(1)) ? location.hash.slice(1) as Page : 'unlock'
const page = ref<Page>(fromHash())
function navigate(value: Page) { page.value = value; location.hash = value }
function syncHash() { page.value = fromHash() }
window.addEventListener('hashchange', syncHash)
onBeforeUnmount(() => window.removeEventListener('hashchange', syncHash))
</script>
