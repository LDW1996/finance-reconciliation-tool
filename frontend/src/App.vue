<template>
  <header class="product-bar" :data-build-commit="buildCommit">
    <div class="product-brand"><el-icon><Tickets /></el-icon><strong>财务对账工具</strong></div>
    <nav aria-label="功能导航">
      <button type="button" :class="{ selected: page === 'excel' }" :aria-current="page === 'excel' ? 'page' : undefined" @click="navigate('excel')">Excel对账</button>
      <button type="button" :class="{ selected: page === 'unlock' }" :aria-current="page === 'unlock' ? 'page' : undefined" @click="navigate('unlock')">iLog批量解锁</button>
      <button type="button" :class="{ selected: page === 'mail' }" :aria-current="page === 'mail' ? 'page' : undefined" @click="navigate('mail')">邮件收款</button>
      <button type="button" :class="{ selected: isManagement }" :aria-current="isManagement ? 'page' : undefined" @click="navigate('accounts')">管理</button>
    </nav>
  </header>
  <div v-show="page === 'excel'"><ReconciliationView /></div>
  <MailView v-if="page === 'mail'" />
  <div v-show="page !== 'excel' && page !== 'mail'" :class="{ 'management-layout': isManagement }">
    <aside v-if="isManagement" class="management-sidebar">
      <h1>管理</h1>
      <nav aria-label="管理导航">
        <button v-for="item in managementItems" :key="item.page" type="button" :class="{ selected: page === item.page }" :aria-current="page === item.page ? 'page' : undefined" @click="navigate(item.page)"><el-icon><component :is="item.icon" /></el-icon>{{ item.label }}</button>
      </nav>
    </aside>
    <UnlockView :class="{ 'management-content': isManagement }" :view="page === 'excel' || page === 'mail' ? 'unlock' : page" @navigate="navigate" />
  </div>
</template>
<script setup lang="ts">
import { computed, ref, onBeforeUnmount } from 'vue'
import { Tickets, User, Clock, Setting } from '@element-plus/icons-vue'
import ReconciliationView from './views/ReconciliationView.vue'
import UnlockView from './views/UnlockView.vue'
import MailView from './views/MailView.vue'
const buildCommit = (import.meta.env.VITE_BUILD_COMMIT || 'local').slice(0,8)
type Page = 'excel' | 'unlock' | 'mail' | 'accounts' | 'history' | 'retention'
const fromHash = (): Page => ['excel', 'unlock', 'mail', 'accounts', 'history', 'retention'].includes(location.hash.slice(1)) ? location.hash.slice(1) as Page : 'excel'
const page = ref<Page>(fromHash())
const isManagement = computed(() => ['accounts', 'history', 'retention'].includes(page.value))
const managementItems = [{page:'accounts',label:'账号管理',icon:User},{page:'history',label:'解锁历史',icon:Clock},{page:'retention',label:'保存设置',icon:Setting}] as const
function navigate(value: Page) { page.value = value; location.hash = value }
function syncHash() { page.value = fromHash() }
window.addEventListener('hashchange', syncHash)
onBeforeUnmount(() => window.removeEventListener('hashchange', syncHash))
</script>

<style scoped>
@media (max-width: 480px) {
  .product-bar nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
