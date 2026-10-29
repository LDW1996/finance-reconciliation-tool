<template>
  <section class="customer-source source-card" aria-label="在线客户台账">
    <div class="source-heading"><h3>客户台账</h3><el-tag :type="config.configured ? 'success' : 'info'">{{ config.configured ? '已配置' : '待配置' }}</el-tag><el-tag v-if="metadata.entries_count" :type="metadata.enabled ? 'success' : 'info'">{{ metadata.enabled ? '参与匹配' : '已暂停使用' }}</el-tag></div>
    <p class="source-summary">{{ metadata.entries_count ? `${metadata.sheet} · ${metadata.entries_count.toLocaleString()} 条有效映射` : '读取客户、银行付款人和邮箱的映射关系' }}<span v-if="metadata.skipped"> · {{ metadata.skipped }} 行未导入</span><span v-if="metadata.duplicates_count"> · {{ metadata.duplicates_count }} 行重复已合并</span></p>
    <p class="source-summary">上次在线读取：{{ time(config.last_read_at) }}<span v-if="metadata.entries_count && !metadata.online"> · 当前使用备用文件</span></p>
    <div class="source-actions"><el-button :disabled="disabled || working" @click="openConfig">配置来源</el-button><el-button type="primary" :loading="operation==='read'" :disabled="disabled || working || !config.configured" @click="readOnline">读取客户台账</el-button><el-button :disabled="disabled || working || !metadata.entries_count" @click="previewOpen=true">查看台账</el-button><el-button v-if="metadata.entries_count" text :disabled="disabled || working" @click="toggle">{{ metadata.enabled ? '暂停使用' : '启用映射' }}</el-button></div>
    <p class="source-note">只读，不修改客户原表。在线表更新后，请重新读取。</p>
    <p v-if="sourceChanged" class="source-note">来源已修改，当前仍使用上次数据；读取成功后才替换。</p>
    <el-alert v-if="error || config.last_error" :title="error || config.last_error || ''" type="error" :closable="false" />
    <p v-if="metadata.entries_count && (readFailed || config.last_error)" class="source-note">读取未完成，仍使用上次成功的数据。</p>
    <CustomerMappingPreview v-if="previewOpen" @close="previewOpen=false" />
    <el-dialog v-model="configOpen" title="配置客户台账来源" width="min(600px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!working" :show-close="!working" @closed="clearCredential">
      <el-form label-position="top" @submit.prevent="saveConfig">
        <el-form-item label="Google Sheets 工作表链接（含 gid）"><el-input v-model="url" aria-label="客户台账工作表链接" :disabled="working" /></el-form-item>
        <el-checkbox v-model="useShared" :disabled="working" @change="clearCredential">共用银行流水的服务账号授权</el-checkbox>
        <el-form-item v-if="!useShared" label="独立服务账号 JSON（已有授权时可留空）"><input ref="credentialInput" type="file" accept=".json,application/json" aria-label="客户台账独立授权文件" :disabled="working" @change="chooseCredential" /></el-form-item>
      </el-form>
      <p class="source-note">请复制目标工作表的完整链接，通过其中的 gid 指定工作表。来源和银行流水分别保存，可使用不同表格。</p>
      <el-alert :title="config.service_email ? `将客户表共享给 ${config.service_email}，授予查看权限即可。授权方式更换后，以保存后显示的账号为准。` : '保存授权后显示服务账号邮箱；客户表只需授予查看权限。'" type="info" :closable="false" />
      <p class="source-note">表头保留 Customer Name、Customer Bank Name Reff、Email（或对应中文列名）。每行至少需要客户名称和银行付款人；不完整行会单独统计。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template #footer><el-button :disabled="working" @click="configOpen=false">关闭</el-button><el-button type="primary" :loading="operation==='config'" :disabled="disabled || working" @click="saveConfig">保存配置</el-button></template>
    </el-dialog>
  </section>
</template>
<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref} from 'vue'
import {ElMessageBox} from 'element-plus'
import CustomerMappingPreview from './CustomerMappingPreview.vue'
import {mailMatchingApi,matchingError,validateSpreadsheetUrl,type CustomerMappingMetadata,type CustomerOnlineConfig} from '../services/mailMatching'
const props=defineProps<{disabled:boolean}>()
const emit=defineEmits<{'changed':[];'busy-change':[value:boolean]}>()
const metadata=ref<CustomerMappingMetadata>({}),config=ref<CustomerOnlineConfig>({configured:false,use_shared:true}),error=ref(''),operation=ref(''),readFailed=ref(false)
const previewOpen=ref(false),configOpen=ref(false),url=ref(''),useShared=ref(true),credential=ref<File>(),credentialInput=ref<HTMLInputElement>()
const working=computed(()=>!!operation.value)
const sourceChanged=computed(()=>metadata.value.online && metadata.value.online.spreadsheet_url!==config.value.spreadsheet_url)
const time=(value?:string|null)=>value?new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'尚未读取'
let alive=true
const controller=new AbortController()
async function work(kind:string,action:()=>Promise<void>,changed=false){
  if(working.value || props.disabled)return
  operation.value=kind;emit('busy-change',true);error.value=''
  try{await action();if(alive && changed){emit('busy-change',false);emit('changed')}}
  catch(cause){if(alive){error.value=kind==='config'?'保存失败，请检查链接和授权方式；独立授权需要有效的JSON文件，共用授权需先配置银行流水账号。':matchingError(cause);if(kind==='read')readFailed.value=true}}
  finally{operation.value='';if(alive)emit('busy-change',false)}
}
function clearCredential(){credential.value=undefined;if(credentialInput.value)credentialInput.value.value=''}
function chooseCredential(event:Event){credential.value=(event.target as HTMLInputElement).files?.[0]}
async function openConfig(){
  if(props.disabled || working.value)return
  error.value='';clearCredential()
  await work('load-config',async()=>{const result=await mailMatchingApi.customerOnlineConfig(controller.signal);if(alive){config.value=result;url.value=result.spreadsheet_url || '';useShared.value=result.use_shared;configOpen.value=true}})
}
async function saveConfig(){
  try{validateSpreadsheetUrl(url.value)}catch(cause){error.value=matchingError(cause);return}
  await work('config',async()=>{
    try{const result=await mailMatchingApi.saveCustomerOnlineConfig(url.value,useShared.value,credential.value,controller.signal);if(alive){config.value=result;configOpen.value=false}}
    finally{clearCredential()}
  })
}
async function readOnline(){
  await work('read',async()=>{
    const result=await mailMatchingApi.refreshCustomerOnline(controller.signal)
    if(alive){metadata.value=result;config.value={...config.value,last_read_at:result.imported_at,last_error:''};readFailed.value=false}
  },true)
}
async function toggle(){await work('toggle',async()=>{const result=await mailMatchingApi.enableCustomerMapping(!metadata.value.enabled,controller.signal);if(alive)metadata.value=result},true)}
async function importBackup(event:Event,sheet=''){
  const input=event.target as HTMLInputElement,file=input.files?.[0];input.value=''
  if(!file || props.disabled || working.value)return
  if(metadata.value.entries_count){try{await ElMessageBox.confirm('备用文件将替换当前客户映射。在线来源配置保留，依赖旧映射的关联将重新核对。','导入备用客户台账',{confirmButtonText:'导入并替换',cancelButtonText:'取消',type:'warning'})}catch{return}}
  await work('file-import',async()=>{const result=await mailMatchingApi.importCustomerMapping(file,sheet,controller.signal);if(alive){metadata.value=result;readFailed.value=false}},true)
}
defineExpose({importBackup})
onMounted(async()=>{
  const results=await Promise.allSettled([mailMatchingApi.customerMapping(controller.signal),mailMatchingApi.customerOnlineConfig(controller.signal)])
  if(!alive)return
  if(results[0].status==='fulfilled')metadata.value=results[0].value
  if(results[1].status==='fulfilled')config.value=results[1].value
  if(results.some(result=>result.status==='rejected'))error.value='客户台账状态暂时不可用，请重新打开配置检查。'
})
onBeforeUnmount(()=>{alive=false;controller.abort()})
</script>
<style scoped>
.customer-source{background:var(--surface,var(--el-bg-color));border:1px solid var(--border,var(--el-border-color-light));border-radius:10px;padding:22px;min-width:0}.source-heading,.source-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px}.source-heading h3{font-size:15px;margin:0}.source-actions{margin:16px 0 10px}.source-actions .el-button{margin:0}.source-summary,.source-note{font-size:13px;color:var(--ink-muted,var(--el-text-color-secondary));line-height:1.7;overflow-wrap:anywhere;margin:8px 0}.customer-source :deep(.el-checkbox){white-space:normal;height:auto;margin-bottom:18px}.customer-source :deep(input[type=file]){max-width:100%}@media(max-width:700px){.customer-source{padding:16px}}
</style>
