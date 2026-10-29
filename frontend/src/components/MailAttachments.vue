<template>
  <section class="attachments" aria-label="附件识别">
    <header><h3>付款附件</h3><el-button :icon="Refresh" link :loading="busy" :disabled="!assets.length || busy" @click="$emit('analyze')">识别附件</el-button></header>
    <el-alert v-if="warning" :title="warning" type="warning" :closable="false" />
    <p v-if="!assets.length" class="muted">{{ legacyNames.length ? '此前仅保存了附件名称，请重新导入或读取邮件以识别附件。' : '无附件' }}</p>
    <el-collapse v-model="opened">
      <el-collapse-item v-for="asset in assets" :key="asset.id" :name="asset.id">
        <template #title><span class="filename">{{ asset.name }}</span><el-tag :type="statusType(asset.status)" size="small">{{ labels[asset.status] }}</el-tag></template>
        <p v-if="asset.error" role="alert">{{ asset.error }}</p>
        <template v-if="asset.result">
          <el-alert v-for="message in asset.result.warnings" :key="message" :title="message" type="warning" :closable="false" />
          <el-table :data="fieldRows(asset)" size="small" empty-text="未提取到付款字段">
            <el-table-column prop="label" label="项目" width="85" />
            <el-table-column label="识别内容" min-width="155"><template #default="{row}">{{ row.item ? `${row.item.currency || ''} ${row.item.value}`.trim() : '未识别' }}</template></el-table-column>
            <el-table-column label="来源" width="85"><template #default="{row}"><el-button v-if="row.item" link type="primary" @click="show(asset,row.item.page,row.item)">第 {{ row.item.page }} 页</el-button></template></el-table-column>
          </el-table>
          <div class="pages"><el-button v-for="page in asset.result.pages" :key="page.number" :icon="View" @click="show(asset,page.number)">第 {{ page.number }} 页 · {{ page.method === 'ocr' ? '图片识别' : 'PDF 文字' }}</el-button></div>
        </template>
      </el-collapse-item>
    </el-collapse>
    <el-dialog v-model="visible" :title="`${previewName} · 第 ${page?.number || 1} 页`" width="min(1100px, 94vw)" destroy-on-close>
      <template v-if="page">
        <p v-if="evidence" class="quote">原文：{{ evidence.quote }}<span v-if="evidence.confidence !== null"> · 文字识别置信度 {{ Math.round(evidence.confidence * 100) }}%</span></p>
        <div class="evidence-layout">
          <div class="preview-scroll" v-loading="previewLoading"><el-alert v-if="previewError" :title="previewError" type="warning" :closable="false" /><div v-if="previewUrl" class="image-wrap"><img :src="previewUrl" :alt="`${previewName} 第 ${page.number} 页`" /><span v-if="evidence?.box" class="highlight" :style="highlightStyle(evidence.box)" /></div></div>
          <section class="text-scroll"><h3>识别文字</h3><pre>{{ page.text || '未识别到文字，请查看原图。' }}</pre></section>
        </div>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { Refresh, View } from '@element-plus/icons-vue'
import { mailApi, type MailAttachment, type MailEvidence } from '../services/mail'

const props=defineProps<{ mailId:string; assets: MailAttachment[]; legacyNames: string[]; busy: boolean; warning?: string }>()
defineEmits<{analyze: []}>()
const opened=ref<string[]>([]), visible=ref(false), previewName=ref('')
const previewUrl=ref(''),previewLoading=ref(false),previewError=ref('')
let previewRequest=0
function clearPreview(){if(previewUrl.value)URL.revokeObjectURL(previewUrl.value);previewUrl.value=''}
const page=ref<NonNullable<MailAttachment['result']>['pages'][number]>()
const evidence=ref<MailEvidence>()
const labels={pending:'等待识别',running:'正在识别',done:'已识别 · 待核对',failed:'识别失败',unsupported:'暂不支持',skipped:'已跳过'}
const statusType=(status: string)=>status==='failed' ? 'danger' : status==='done' ? 'info' : 'warning'
const names: Record<string,string>={payer:'付款人',date:'付款日期',amount:'付款金额',fee:'手续费',identifiers:'关联号码'}
function fieldRows(asset: MailAttachment) {
  return Object.entries(names).flatMap<{label: string; item: MailEvidence | undefined}>(([key,label])=>{
    const items=asset.result?.fields[key] || []
    return items.length ? items.map(item=>({label,item})) : [{label,item:undefined}]
  })
}
async function show(asset: MailAttachment, number: number, item?: MailEvidence) {
  const request=++previewRequest
  clearPreview();previewError.value='';previewLoading.value=true
  page.value=asset.result?.pages.find(p=>p.number===number)
  previewName.value=asset.name;evidence.value=item;visible.value=true
  try{const blob=await mailApi.preview(props.mailId,asset.id,number);if(request===previewRequest && visible.value)previewUrl.value=URL.createObjectURL(blob)}
  catch{if(request===previewRequest)previewError.value='原件预览暂不可用。识别文字仍在，请稍后重试或检查原始附件。'}
  finally{if(request===previewRequest)previewLoading.value=false}
}
watch(visible,(value)=>{if(!value){previewRequest++;clearPreview();previewLoading.value=false}})
onBeforeUnmount(()=>{previewRequest++;clearPreview()})
function highlightStyle(box: number[][]) {
  const xs=box.map(p=>p[0]),ys=box.map(p=>p[1])
  return {left:`${Math.min(...xs)*100}%`,top:`${Math.min(...ys)*100}%`,width:`${(Math.max(...xs)-Math.min(...xs))*100}%`,height:`${(Math.max(...ys)-Math.min(...ys))*100}%`}
}
watch(()=>props.assets.map(a=>a.id).join(','),()=>{opened.value=props.assets.slice(0,1).map(a=>a.id);visible.value=false},{immediate:true})
</script>

<style scoped>
.attachments{margin:24px 0}.attachments header{display:flex;justify-content:space-between;align-items:center;gap:12px}.attachments h3{margin:0 0 12px}.filename{flex:1;min-width:0;overflow-wrap:anywhere;line-height:1.5;padding:8px 12px 8px 0}.muted{color:var(--text-secondary)}.attachments :deep(.el-collapse-item__header){height:auto;min-height:48px}.attachments :deep(.el-alert){margin:8px 0}.pages{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.pages :deep(.el-button){margin:0}.quote{overflow-wrap:anywhere;margin:0 0 16px;color:var(--text-secondary)}.evidence-layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:20px}.preview-scroll,.text-scroll{max-height:65vh;overflow:auto;min-width:0}.image-wrap{position:relative;line-height:0}.image-wrap img{width:100%;height:auto}.highlight{position:absolute;border:2px solid var(--el-color-primary);background:rgb(0 122 255 / 12%);box-sizing:border-box;pointer-events:none}.text-scroll pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:13px/1.7 ui-monospace,monospace}.text-scroll h3{font-size:15px}@media(max-width:700px){.evidence-layout{grid-template-columns:minmax(0,1fr)}.preview-scroll,.text-scroll{max-height:42vh}}
</style>
