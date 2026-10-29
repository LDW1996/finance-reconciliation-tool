<template>
  <el-dialog :model-value="true" title="客户台账预览" class="customer-preview-dialog" modal-class="customer-preview-overlay" append-to-body align-center :lock-scroll="true" width="min(1100px, 96vw)" @close="emit('close')">
    <div class="snapshot-info"><strong :title="`${result?.metadata.name || '当前本地台账'} · 读取于 ${time(result?.metadata.imported_at)}`">{{ result?.metadata.name || '当前本地台账' }}</strong><span>{{ result?.metadata.sheet || '—' }}</span><span class="snapshot-time">读取于 {{ time(result?.metadata.imported_at) }}</span><el-link v-if="result?.metadata.online" :href="result.metadata.online.spreadsheet_url" target="_blank" rel="noopener noreferrer">打开原表</el-link><el-tag v-if="result?.metadata.entries_count" :type="result.metadata.enabled ? 'success' : 'info'">{{ result.metadata.enabled ? '参与匹配' : '已暂停使用' }}</el-tag></div>
    <p class="preview-note preview-intro">展示当前实际使用的本地快照，不会重新读取 Google 表格。线上修改需要回到来源卡片手动读取后才会更新。</p>
    <el-alert v-if="result?.notice" :title="'旧快照未保存未导入行明细，请手动重新读取客户台账后补全。'" type="warning" :closable="false" />
    <el-radio-group v-model="status" aria-label="台账预览分类" class="preview-tabs"><el-radio-button value="valid">有效映射（{{ result?.metadata.entries_count || 0 }}）</el-radio-button><el-radio-button value="excluded">未导入行（{{ excludedCount }}）</el-radio-button></el-radio-group>
    <form class="preview-search" @submit.prevent="search"><el-input v-model="draft" maxlength="200" clearable aria-label="搜索客户台账" placeholder="客户、付款人、邮箱、行号或未导入原因" @clear="search" /><el-button native-type="submit" :loading="loading">搜索</el-button><el-button :disabled="loading" @click="load">刷新本地预览</el-button></form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div class="preview-table-area"><el-table v-loading="loading" :data="result?.rows || []" height="100%" :empty-text="emptyText" class="preview-table">
      <el-table-column prop="row" label="原表行号" width="100" />
      <el-table-column label="客户公司" min-width="200"><template #default="{row}">{{ row.customer || '—' }}</template></el-table-column>
      <el-table-column label="银行付款人" min-width="180"><template #default="{row}">{{ row.bank_name || '—' }}</template></el-table-column>
      <el-table-column label="登记邮箱" min-width="230"><template #default="{row}">{{ row.emails?.join('；') || row.email || '—' }}</template></el-table-column>
      <el-table-column v-if="status==='valid'" label="关系提示" min-width="180"><template #default="{row}"><el-tag v-for="flag in row.flags" :key="flag" :type="flag==='未登记邮箱' ? 'info' : 'warning'" size="small">{{ flag }}</el-tag><span v-if="!row.flags?.length">—</span></template></el-table-column>
      <el-table-column v-else label="未导入原因" min-width="220"><template #default="{row}"><span>{{ row.reasons?.join('；') }}</span><span v-if="row.duplicate_of">（保留第 {{ row.duplicate_of }} 行）</span><span v-if="row.truncated"> · 原内容过长，仅展示前1000字</span></template></el-table-column>
    </el-table></div>
    <p v-if="!loading && !error" class="preview-note preview-summary">{{ result?.total || 0 }} 条{{ keyword ? '搜索结果' : '记录' }}<span v-if="status==='valid'" class="preview-rule"> · 共享邮箱和一对多关系仅作为辅助线索，仍需核对付款凭证。</span></p>
    <el-pagination v-if="!error" :current-page="page" :page-size="50" :total="result?.total || 0" layout="prev, pager, next" :pager-count="5" :disabled="loading" @current-change="page=$event" />
    <template #footer><el-button @click="emit('close')">关闭预览</el-button></template>
  </el-dialog>
</template>
<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref,watch} from 'vue'
import {mailMatchingApi,matchingError,type CustomerMappingPreviewResult} from '../services/mailMatching'
const emit=defineEmits<{close:[]}>()
const status=ref<'valid'|'excluded'>('valid'),draft=ref(''),keyword=ref(''),page=ref(1),loading=ref(false),error=ref(''),result=ref<CustomerMappingPreviewResult>()
const excludedCount=computed(()=>Number(result.value?.metadata.skipped || 0)+Number(result.value?.metadata.duplicates_count || 0))
const emptyText=computed(()=>error.value?'预览未加载，请重试':loading.value?'正在加载…':status.value==='excluded' && result.value?.diagnostics_available===false && excludedCount.value?'此旧快照未保存未导入行明细':'暂无符合条件的记录')
const time=(value?:string)=>value?new Date(value).toLocaleString('zh-CN'):'未知（历史数据）'
let alive=true,sequence=0,controller:AbortController|undefined
async function load(){
  controller?.abort();controller=new AbortController();const token=++sequence
  loading.value=true;error.value=''
  if(result.value)result.value={...result.value,rows:[]}
  try{const data=await mailMatchingApi.customerMappingPreview({status:status.value,keyword:keyword.value,page:page.value,page_size:50},controller.signal);if(alive && token===sequence){result.value=data;const last=Math.max(1,Math.ceil(data.total/50));if(page.value>last)page.value=last}}
  catch(cause){if(alive && token===sequence && !controller.signal.aborted)error.value=matchingError(cause)}
  finally{if(alive && token===sequence)loading.value=false}
}
function search(){const changed=keyword.value!==draft.value.trim();keyword.value=draft.value.trim();if(page.value!==1)page.value=1;else if(changed || !loading.value)void load()}
watch(status,()=>{if(page.value!==1)page.value=1;else void load()})
watch(page,()=>void load())
onMounted(()=>void load())
onBeforeUnmount(()=>{alive=false;controller?.abort()})
</script>
<style scoped>
.snapshot-info,.preview-search{display:flex;flex-wrap:wrap;align-items:center;gap:12px}.snapshot-info{font-size:14px;overflow-wrap:anywhere}.preview-note{font-size:13px;line-height:1.7;color:var(--el-text-color-secondary)}.preview-tabs{margin:16px 0}.preview-search{margin-bottom:16px}.preview-search .el-input{flex:1;min-width:160px}.preview-table :deep(.cell){overflow-wrap:anywhere}.preview-table .el-tag{margin:3px}.el-pagination{max-width:100%;overflow-x:auto}@media(max-width:600px){.preview-search .el-input{flex-basis:100%}.snapshot-info{align-items:flex-start}}

:global(html:has(.customer-preview-overlay)){overflow:hidden;overscroll-behavior:none}
:global(.customer-preview-overlay .el-overlay-dialog){overflow:hidden;overscroll-behavior:none;padding:16px 0}
:global(.customer-preview-dialog){height:min(900px,calc(100dvh - 32px));max-height:calc(100dvh - 32px);display:flex;flex-direction:column;overflow:hidden;margin:0 auto;padding:0}
:global(.customer-preview-dialog .el-dialog__header){flex:0 0 auto;margin:0;padding:16px 48px 16px 20px;border-bottom:1px solid var(--el-border-color-light)}
:global(.customer-preview-dialog .el-dialog__body){flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:12px 20px}
:global(.customer-preview-dialog .el-dialog__footer){flex:0 0 auto;padding:12px 20px;border-top:1px solid var(--el-border-color-light)}
.snapshot-info,.preview-intro,.preview-tabs,.preview-search,.preview-summary,.el-alert,.el-pagination{flex-shrink:0}
.snapshot-info strong{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.preview-table-area{flex:1;min-height:0;overflow:hidden}.preview-table{height:100%}.preview-table :deep(.el-scrollbar__wrap){overscroll-behavior:contain}
@media(max-height:700px){:global(.customer-preview-dialog .el-dialog__header){padding-top:10px;padding-bottom:10px}:global(.customer-preview-dialog .el-dialog__footer){padding-top:8px;padding-bottom:8px}:global(.customer-preview-dialog .el-dialog__body){padding:8px 14px}.snapshot-info{font-size:12px;gap:8px}.snapshot-time,.preview-intro,.preview-rule{display:none}.snapshot-info strong{max-width:44%}.preview-tabs{margin:8px 0}.preview-search{gap:6px;margin-bottom:8px}.preview-summary{margin:6px 0;font-size:12px;line-height:16px}.el-alert{padding:8px}.el-alert :deep(.el-alert__title){font-size:12px;line-height:18px}}
</style>
