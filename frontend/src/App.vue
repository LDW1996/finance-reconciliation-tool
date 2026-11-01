<template>
  <div v-if="loading" role="status">正在连接本地服务…</div>
  <div v-else-if="startupError"><el-alert :title="startupError" type="error" :closable="false"/><el-button @click="initialize">重试</el-button></div>
  <PlatformLogin v-else-if="!session.authenticated" @authenticated="authenticated" />
  <template v-else>
    <header class="product-bar" :data-build-commit="buildCommit"><div class="product-brand"><strong>财务综合业务平台</strong></div><nav aria-label="功能导航"><button v-for="item in items" :key="item.page" :class="{selected:page===item.page}" @click="navigate(item.page)">{{item.label}}</button></nav><span>{{session.username}} · {{session.role}}</span><label>语言 / Language <select v-model="language" @change="changeLanguage"><option value="zh-CN">中文</option><option value="en">English</option></select></label><el-button :loading="loggingOut" @click="logout">退出登录</el-button></header>
    <ReconciliationView v-if="page==='excel' && can('excel')"/>
    <MailView v-else-if="page==='mail' && can('mail')"/>
    <PlatformStorageSettings v-else-if="page==='retention'" :capabilities="session.capabilities"/>
    <UnlockView v-else-if="can('ilog') && (page==='unlock'||page==='accounts'||page==='history')" :view="page" @navigate="navigate"/>
  </template>
</template>
<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import ReconciliationView from './views/ReconciliationView.vue'
import MailView from './views/MailView.vue'
import UnlockView from './views/UnlockView.vue'
import PlatformLogin from './components/PlatformLogin.vue'
import PlatformStorageSettings from './components/PlatformStorageSettings.vue'
import { getPlatformSession, logoutPlatform, type PlatformSession } from './services/platformAuth'
const buildCommit=(import.meta.env.VITE_BUILD_COMMIT || 'local').slice(0,8)
const language=ref(localStorage.getItem('finance-platform-language') || 'zh-CN')
function changeLanguage(){localStorage.setItem('finance-platform-language',language.value);window.dispatchEvent(new Event('platform-language-change'))}
type Page='excel'|'mail'|'unlock'|'accounts'|'history'|'retention'
const session=ref<PlatformSession>({authenticated:false,capabilities:[]}),loading=ref(true),startupError=ref(''),loggingOut=ref(false),page=ref<Page>('excel')
const can=(capability:string)=>session.value.capabilities.includes(capability)
const items=computed(()=>[...(can('excel')?[{page:'excel' as Page,label:'Excel对账'}]:[]),...(can('mail')?[{page:'mail' as Page,label:'邮件收款'}]:[]),...(can('ilog')?[{page:'unlock' as Page,label:'iLog批量解锁'},{page:'accounts' as Page,label:'iLog业务账号'},{page:'history' as Page,label:'解锁历史'}]:[]),...(can('mail')||can('ilog')?[{page:'retention' as Page,label:'保存设置'}]:[])])
function navigate(value:Page){page.value=items.value.some(i=>i.page===value)?value:(items.value[0]?.page || 'excel');location.hash=page.value}
function syncHash(){if(session.value.authenticated)navigate(location.hash.slice(1) as Page)}
function authenticated(value:PlatformSession){session.value=value;syncHash()}
async function initialize(){loading.value=true;startupError.value='';try{authenticated(await getPlatformSession())}catch(error:any){if(error.response?.status===401)session.value={authenticated:false,capabilities:[]};else startupError.value='无法连接本地服务'}finally{loading.value=false}}
async function logout(){loggingOut.value=true;try{await logoutPlatform();session.value={authenticated:false,capabilities:[]}}catch(error:any){ElMessage.error(error.response?.data?.detail || '退出失败，请等待当前任务完成后重试')}finally{loggingOut.value=false}}
function expired(){session.value={authenticated:false,capabilities:[]}}
window.addEventListener('hashchange',syncHash);window.addEventListener('platform-session-expired',expired)
onBeforeUnmount(()=>{window.removeEventListener('hashchange',syncHash);window.removeEventListener('platform-session-expired',expired)})
void initialize()
</script>
<style scoped>@media(max-width:700px){.product-bar{flex-wrap:wrap}.product-bar nav{display:flex;flex-wrap:wrap}}</style>
