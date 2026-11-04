<template>
  <section class="receipt-evidence" aria-label="付款凭证预览">
    <header><h3>凭证原件与识别依据</h3><el-button link type="primary" :disabled="loading" @click="load">重新加载凭证</el-button></header>
    <el-alert v-if="error" :title="error" type="warning" :closable="false"/>
    <div class="receipt-evidence-grid" v-loading="loading"><div class="original-evidence"><img v-if="url" :src="url" :alt="candidate.attachment_name || '付款凭证原件'"/><p v-else-if="!loading" class="muted">{{ candidate.attachment_id==='latest_body' ? '付款信息来自邮件正文，请对照识别文字。' : '暂未取得可预览原件，可查看邮件详情或原邮件。' }}</p></div><div class="recognized-evidence"><h4>识别文字</h4><pre>{{ text || '暂无识别文字，请核对原件。' }}</pre></div></div>
  </section>
</template>
<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import {mailApi} from '../services/mail'
import {matchingError,type MatchingCandidate} from '../services/mailMatching'
const props=defineProps<{candidate:MatchingCandidate}>()
const url=ref(''),text=ref(''),error=ref(''),loading=ref(false)
let request=0,alive=true
function clear(){if(url.value)URL.revokeObjectURL(url.value);url.value=''}
async function load(){const token=++request;clear();text.value='';error.value='';loading.value=true;const current=props.candidate;try{const mail=await mailApi.message(current.mail_id);if(!alive||token!==request)return;if(current.attachment_id==='latest_body'){text.value=mail.latest_body || '';return}const asset=mail.attachment_details?.find(a=>a.id===current.attachment_id);const page=asset?.result?.pages?.find(p=>p.number===current.page);text.value=page?.text || '';if(asset){const blob=await mailApi.preview(current.mail_id,current.attachment_id,current.page);if(alive&&token===request)url.value=URL.createObjectURL(blob)}}catch(cause){if(alive&&token===request)error.value=matchingError(cause)}finally{if(alive&&token===request)loading.value=false}}
watch(()=>[props.candidate.mail_id,props.candidate.attachment_id,props.candidate.page],load,{immediate:true});onBeforeUnmount(()=>{alive=false;++request;clear()})
</script>
<style scoped>
.receipt-evidence{margin:24px 0;border:1px solid var(--border);border-radius:8px;padding:16px}.receipt-evidence header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}.receipt-evidence h3{font-size:14px}.receipt-evidence-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;min-height:180px}.original-evidence{overflow:auto;max-height:320px}.original-evidence img{width:100%;height:auto;display:block}.recognized-evidence{min-width:0;max-height:320px;overflow:auto;border-left:1px solid var(--border);padding-left:16px}.recognized-evidence h4{margin:0 0 10px;font-size:13px}.recognized-evidence pre{font:12px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.muted{font-size:13px;color:var(--ink-muted);line-height:1.7}@media(max-width:700px){.receipt-evidence-grid{grid-template-columns:1fr}.recognized-evidence{border-left:0;border-top:1px solid var(--border);padding:12px 0 0}}
</style>
