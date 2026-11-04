<template>
  <el-dialog :model-value="true" title="银行流水预览" width="min(1100px, 96vw)" class="bank-ledger-preview" append-to-body align-center @close="emit('close')">
    <p class="source">本地已保存的流水，只读查看；原表修改后需重新读取，打开预览不会重新匹配。</p>
    <p v-if="result?.metadata" class="source">{{ result.metadata.sheet }} · {{ result.metadata.entries_count }} 条有效流水 · {{ result.metadata.skipped || 0 }} 行跳过</p>
    <el-radio-group v-model="tab" aria-label="流水预览范围"><el-radio-button value="valid">有效流水（{{ result?.metadata?.entries_count || 0 }}）</el-radio-button><el-radio-button value="skipped">跳过行（{{ result?.metadata?.skipped || 0 }}）</el-radio-button></el-radio-group>
    <form v-if="tab==='valid'" class="bank-preview-search" @submit.prevent="search"><el-input v-model="draft" clearable placeholder="流水号、付款人或银行摘要" aria-label="搜索流水预览" @clear="search"/><el-button native-type="submit" :loading="loading">搜索</el-button></form>
    <el-alert v-if="error" :title="error" type="warning" :closable="false"/>
    <el-table v-if="tab==='valid'" :data="result?.rows || []" max-height="460" v-loading="loading" empty-text="暂无符合条件的流水"><el-table-column prop="row" label="原表行号" width="95"/><el-table-column prop="ref" label="流水号" min-width="170"/><el-table-column prop="date" label="日期" min-width="130"/><el-table-column label="金额" min-width="160"><template #default="{row}">{{ row.currency }} {{ row.amount }}</template></el-table-column><el-table-column prop="payer" label="银行付款人" min-width="180"/><el-table-column prop="description" label="银行摘要" min-width="240"/></el-table>
    <template v-if="tab==='skipped'"><el-table :data="skippedRows" max-height="460" empty-text="没有跳过行提示"><el-table-column prop="row" label="原表行号" width="110"/><el-table-column label="跳过原因"><template #default>日期或金额无效，请在原表核实。</template></el-table-column></el-table><p class="source">历史数据最多保留前 100 行跳过提示；完整内容请查看原始流水表。</p></template>
    <details v-if="result?.metadata?.warnings?.length" class="bank-preview-notes"><summary>查看导入提示</summary><p v-for="warning in result.metadata.warnings" :key="warning">{{ warning }}</p></details>
    <el-pagination v-if="tab==='valid'" :current-page="page" :total="result?.total || 0" :page-size="50" layout="total, prev, pager, next" :pager-count="5" :disabled="loading" @current-change="page=$event"/>
    <template #footer><el-button @click="emit('close')">关闭预览</el-button></template>
  </el-dialog>
</template>
<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref,watch} from 'vue'
import {mailMatchingApi,matchingError,type Receipt,type LedgerMetadata} from '../services/mailMatching'
const emit=defineEmits<{close:[]}>()
const tab=ref('valid')
const result=ref<{rows:Receipt[];total:number;metadata:LedgerMetadata|null}>(),draft=ref(''),keyword=ref(''),page=ref(1),loading=ref(false),error=ref('')
const skippedRows=computed(()=>(result.value?.metadata?.warnings || []).flatMap(w=>{const match=w.match(/(?:\u7b2c\s*|Row\s+)(\d+).*?(?:\u65e0\u6548|invalid)/i);return match?[{row:Number(match[1])}]:[]}))
let request=0,controller:AbortController|undefined,alive=true
async function load(){const token=++request;controller?.abort();controller=new AbortController();loading.value=true;error.value='';try{const data=await mailMatchingApi.ledgerPreview(page.value,keyword.value,controller.signal);if(alive&&token===request)result.value=data}catch(cause){if(alive&&token===request&&!controller.signal.aborted)error.value=matchingError(cause)}finally{if(alive&&token===request)loading.value=false}}
function search(){keyword.value=draft.value.trim();if(page.value!==1)page.value=1;else void load()}
watch(page,()=>void load());onMounted(load);onBeforeUnmount(()=>{alive=false;++request;controller?.abort()})
</script>
<style scoped>
.source{color:var(--ink-muted);font-size:13px;line-height:1.7}.bank-preview-search{display:flex;gap:12px;margin:18px 0}.bank-preview-notes{margin:16px 0;font-size:13px;line-height:1.7}.el-pagination{margin-top:18px;overflow:auto}
</style>

<style scoped>
:global(.bank-ledger-preview){max-height:calc(100dvh - 40px);margin:auto!important}
</style>
