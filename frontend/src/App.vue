<template>
  <div v-if="loading" role="status">正在连接本地服务…</div>
  <div v-else-if="startupError"><el-alert :title="startupError" type="error" :closable="false"/><el-button @click="initialize">重试</el-button></div>
  <PlatformLogin v-else-if="!session.authenticated" @authenticated="authenticated" />
  <template v-else>
    <header class="product-bar platform-header" :data-build-commit="buildCommit">
      <div class="product-brand platform-brand"><el-icon aria-hidden="true"><OfficeBuilding /></el-icon><strong>财务综合业务平台</strong></div>
      <nav class="platform-nav" aria-label="功能导航"><button v-for="item in items" :key="item.page" :class="{selected:item.page==='unlock' ? isIlog : page===item.page}" :aria-current="(item.page==='unlock' ? isIlog : page===item.page) ? 'page' : undefined" @click="navigate(item.page)">{{item.label}}</button></nav>
      <div class="platform-session">
        <span class="platform-account" :title="session.username"><el-icon aria-hidden="true"><User /></el-icon><span>{{session.username}}</span></span>
        <el-button class="platform-logout" :icon="SwitchButton" :loading="loggingOut" @click="logout">退出登录</el-button>
      </div>
    </header>
    <ReconciliationView v-if="page==='excel' && can('excel')"/>
    <MailView v-else-if="page==='mail' && can('mail')"/>
    <div v-else-if="can('ilog') && isIlog" class="ilog-workspace"><ModuleTitle class="ilog-title" :icon="Unlock">iLog</ModuleTitle><nav class="ilog-tabs" aria-label="iLog功能导航"><button v-for="tab in ilogTabs" :key="tab.page" type="button" :class="{selected:page===tab.page}" :aria-current="page===tab.page ? 'page' : undefined" @click="navigate(tab.page)">{{tab.label}}</button></nav>
    <UnlockView v-if="can('ilog') && (page==='unlock'||page==='accounts'||page==='history')" :view="page" @navigate="navigate"/><IlogRetentionSettings v-if="page==='retention'"/></div>
  </template>
</template>
<script setup lang="ts">
import ModuleTitle from './components/ModuleTitle.vue'
import { ref, computed, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { OfficeBuilding, User, SwitchButton, Unlock } from '@element-plus/icons-vue'
import ReconciliationView from './views/ReconciliationView.vue'
import MailView from './views/MailView.vue'
import UnlockView from './views/UnlockView.vue'
import PlatformLogin from './components/PlatformLogin.vue'
import IlogRetentionSettings from './components/IlogRetentionSettings.vue'
import { getPlatformSession, logoutPlatform, type PlatformSession } from './services/platformAuth'
const buildCommit=(import.meta.env.VITE_BUILD_COMMIT || 'local').slice(0,8)
type Page='excel'|'mail'|'unlock'|'accounts'|'history'|'retention'
const session=ref<PlatformSession>({authenticated:false,capabilities:[]}),loading=ref(true),startupError=ref(''),loggingOut=ref(false),page=ref<Page>('excel')
const can=(capability:string)=>session.value.capabilities.includes(capability)
const ilogTabs=[{page:'unlock',label:'批量解锁'},{page:'accounts',label:'业务账号'},{page:'history',label:'解锁历史'},{page:'retention',label:'保存设置'}] as const
const isIlog=computed(()=>ilogTabs.some(tab=>tab.page===page.value))
const items=computed(()=>[...(can('excel')?[{page:'excel' as Page,label:'Excel对账'}]:[]),...(can('mail')?[{page:'mail' as Page,label:'邮件收款'}]:[]),...(can('ilog')?[{page:'unlock' as Page,label:'iLog'}]:[])])
function navigate(value:Page){page.value=(items.value.some(i=>i.page===value)||(can('ilog')&&ilogTabs.some(tab=>tab.page===value)))?value:(items.value[0]?.page || 'excel');location.hash=page.value}
function syncHash(){if(session.value.authenticated)navigate(location.hash.slice(1) as Page)}
function authenticated(value:PlatformSession){session.value=value;syncHash()}
async function initialize(){loading.value=true;startupError.value='';try{authenticated(await getPlatformSession())}catch(error:any){if(error.response?.status===401)session.value={authenticated:false,capabilities:[]};else startupError.value='无法连接本地服务'}finally{loading.value=false}}
async function logout(){loggingOut.value=true;try{await logoutPlatform();session.value={authenticated:false,capabilities:[]}}catch(error:any){ElMessage.error(error.response?.data?.detail || '退出失败，请等待当前任务完成后重试')}finally{loggingOut.value=false}}
function expired(){session.value={authenticated:false,capabilities:[]}}
window.addEventListener('hashchange',syncHash);window.addEventListener('platform-session-expired',expired)
onBeforeUnmount(()=>{window.removeEventListener('hashchange',syncHash);window.removeEventListener('platform-session-expired',expired)})
void initialize()
</script>
<style scoped>
.ilog-workspace{padding:32px;min-width:0}.ilog-workspace>.ilog-title{margin:0 0 24px;font-size:28px}
.ilog-tabs{display:flex;gap:32px;border-bottom:1px solid var(--border);margin-bottom:24px;overflow-x:auto}
.ilog-tabs button{border:0;border-bottom:3px solid transparent;background:transparent;padding:12px 0 16px;white-space:nowrap;color:inherit;font:inherit;cursor:pointer}
.ilog-tabs button.selected{color:var(--el-color-primary);border-bottom-color:var(--el-color-primary);font-weight:600}
.ilog-tabs button:focus-visible{outline:2px solid var(--el-color-primary)}
.ilog-workspace :deep(.unlock-shell){padding:0;margin:0;max-width:none}
@media(max-width:700px){.ilog-workspace{padding:24px 16px}}

.platform-header.product-bar { min-height: 72px; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; column-gap: 32px; padding: 0 32px; }
.platform-brand.product-brand { gap: 10px; font-size: 19px; letter-spacing: -.025em; color: #16334b; }
.platform-brand strong { font-weight: 600; }
.platform-brand .el-icon { flex-shrink: 0; font-size: 26px; color: #0066cc; }
.platform-nav { min-width: 0; gap: 4px !important; align-self: center !important; overflow-x: auto; scrollbar-width: thin; }
.platform-header .platform-nav button { min-height: 38px; flex-shrink: 0; padding: 8px 13px; border-radius: 6px; font-size: 14px; font-weight: 500; line-height: 22px; white-space: nowrap; }
.platform-header .platform-nav button.selected { background: #edf5fd; color: #0066cc; font-weight: 600; }
.platform-session { display: flex; align-items: center; justify-content: flex-end; gap: 20px; padding-left: 24px; border-left: 1px solid #e5e8ed; white-space: nowrap; }
.platform-account { display: inline-flex; align-items: center; gap: 7px; color: #636b78; font-size: 13px; }
.platform-account .el-icon { color: #8b94a1; font-size: 17px; }
.platform-account > span { overflow: hidden; max-width: 100px; text-overflow: ellipsis; }
.platform-logout.el-button { height: 34px; margin: 0; padding: 0 12px; border-color: #e1e5eb; color: #505a68; font-size: 13px; border-radius: 6px; }
.platform-logout.el-button:hover { border-color: #bed3ea; background: #f6f9fd; color: #0066cc; }
@media (max-width: 1250px) {
  .platform-header.product-bar { grid-template-columns: minmax(0, 1fr) auto; row-gap: 0; padding: 16px 24px 0; }
  .platform-header .platform-nav { grid-column: 1 / -1; grid-row: 2; margin-top: 12px; padding: 0 0 12px; }
  .platform-session { grid-column: 2; grid-row: 1; }
}
@media (max-width: 600px) {
  .platform-header.product-bar { padding: 14px 16px 0; column-gap: 16px; }
  .platform-brand.product-brand { width: auto; min-width: 0; font-size: 16px; gap: 7px; white-space: normal; line-height: 1.4; }
  .platform-brand .el-icon { font-size: 23px; }
  .platform-session { gap: 12px; padding-left: 0; border: 0; }
  .platform-account .el-icon { display: none; }
  .platform-account { font-size: 12px; }
  .platform-account > span { max-width: 48px; }
  .platform-logout.el-button { font-size: 12px; height: 34px; padding: 0 8px; }
  .platform-header .platform-nav { width: auto; max-width: 100%; margin-top: 10px; }
  .platform-header .platform-nav button { flex: 0 0 auto; font-size: 13px; padding: 7px 11px; min-height: 36px; }
}
</style>
