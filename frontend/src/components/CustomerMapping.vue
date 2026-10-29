<template>
  <section class="mapping-panel" aria-label="客户映射台账">
    <div class="mapping-heading"><h3>客户映射台账</h3><el-tag :type="metadata.enabled ? 'success' : 'info'">{{ metadata.entries_count ? (metadata.enabled ? '已启用' : '已停用') : '未导入' }}</el-tag></div>
    <p>用银行付款人关联客户公司和完整发件邮箱，辅助匹配个人代付或凭证付款人缺失的邮件；不会替换凭证原文或自动确认关联。</p>
    <div class="mapping-actions">
      <label>工作表（可选）<el-input v-model="sheet" aria-label="客户台账工作表" placeholder="默认第一个工作表" :disabled="disabled || working" /></label>
      <input ref="fileInput" type="file" accept=".xlsx,.csv" aria-label="选择客户映射台账" :disabled="disabled || working" @change="importFile" />
      <el-button :disabled="disabled || working || !metadata.entries_count" @click="toggle">{{ metadata.enabled ? '停用映射' : '启用映射' }}</el-button>
    </div>
    <p v-if="metadata.entries_count" role="status">{{ metadata.name }} · {{ metadata.sheet }} · {{ metadata.entries_count }} 条映射<span v-if="metadata.skipped"> · {{ metadata.skipped }} 行信息不完整，未导入</span></p>
    <details><summary>台账格式与匹配说明</summary><p>请将 Google 客户表导出为 XLSX 或 CSV（最大5MB），保留 Customer Name、Customer Bank Name Reff、Email 列。每行至少有客户名称和银行付款人，邮箱可留空；电话号码和备注不参与匹配。</p><p>只有金额相同、币种明确且一致、日期在允许范围内时，映射才补充分数。直接姓名匹配优先；共享邮箱或一对多关系降低分值，并提示人工核对。重新导入会替换当前映射，相关确认会复核。</p></details>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
  </section>
</template>
<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref} from 'vue'
import {ElMessageBox} from 'element-plus'
import {mailMatchingApi,matchingError,type CustomerMappingMetadata} from '../services/mailMatching'
const props=defineProps<{disabled:boolean}>()
const emit=defineEmits<{'changed':[];'busy-change':[value:boolean]}>()
const metadata=ref<CustomerMappingMetadata>({}),sheet=ref(''),error=ref(''),working=ref(false),fileInput=ref<HTMLInputElement>()
let alive=true
const controller=new AbortController()
async function mutate(action:()=>Promise<CustomerMappingMetadata>){
  if(working.value || props.disabled)return
  working.value=true;emit('busy-change',true);error.value=''
  try{const result=await action();if(alive){metadata.value=result;emit('busy-change',false);emit('changed')}}
  catch(cause){if(alive)error.value=matchingError(cause)}
  finally{working.value=false;if(alive)emit('busy-change',false)}
}
async function importFile(event:Event){
  const input=event.target as HTMLInputElement,file=input.files?.[0];input.value=''
  if(!file || working.value || props.disabled)return
  if(metadata.value.entries_count){
    try{await ElMessageBox.confirm('将替换当前客户映射台账并重新计算候选。凭证原文和邮件数据保留；依赖旧映射的确认将复核。','替换客户台账',{confirmButtonText:'导入并替换',cancelButtonText:'取消',type:'warning'})}catch{return}
  }
  await mutate(()=>mailMatchingApi.importCustomerMapping(file,sheet.value,controller.signal))
}
async function toggle(){await mutate(()=>mailMatchingApi.enableCustomerMapping(!metadata.value.enabled,controller.signal))}
onMounted(async()=>{try{const result=await mailMatchingApi.customerMapping(controller.signal);if(alive)metadata.value=result}catch(cause){if(alive)error.value=matchingError(cause)}})
onBeforeUnmount(()=>{alive=false;controller.abort()})
</script>
<style scoped>
.mapping-panel{padding:20px 0;border-bottom:1px solid var(--el-border-color-light);margin-bottom:20px}.mapping-heading,.mapping-actions{display:flex;align-items:center;flex-wrap:wrap;gap:14px}.mapping-heading h3{margin:0}.mapping-panel p,.mapping-panel details{font-size:14px;color:var(--el-text-color-secondary);line-height:1.7;overflow-wrap:anywhere}.mapping-actions label{display:grid;gap:6px;font-size:14px}.mapping-actions input[type=file]{max-width:100%}.mapping-panel details{margin:12px 0}.mapping-panel summary{cursor:pointer}
</style>
