<template>
  <section class="mail-storage" aria-labelledby="mail-storage-title" v-loading="loading">
    <div class="storage-heading"><h2 id="mail-storage-title">邮件存储</h2><el-button :loading="loading" @click="load">刷新占用</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="stats">
      <el-alert v-if="stats.blocked_reason || stats.warning" :title="stats.blocked_reason || '邮件存储已使用超过 80%，请关注容量。'" type="warning" :closable="false" />
      <div class="storage-metrics">
        <div><span>本地邮件</span><strong>{{ stats.mail_count.toLocaleString() }} 封</strong></div>
        <div><span>邮件数据库占用</span><strong>{{ bytes(stats.used_bytes) }}</strong></div>
        <div><span>原始附件 · {{ stats.attachment_count.toLocaleString() }} 份</span><strong>{{ bytes(stats.attachment_bytes) }}</strong></div>
        <div><span>附件识别结果与预览</span><strong>{{ bytes(stats.recognition_bytes) }}</strong></div>
        <div><span>磁盘剩余空间</span><strong>{{ bytes(stats.free_bytes) }}</strong></div>
        <div><span>等待识别</span><strong>{{ pending.toLocaleString() }} 封</strong></div>
      </div>
      <el-progress :percentage="Math.min(100, Math.round(stats.used_bytes / (stats.settings.max_gb * 1024 ** 3) * 100))" :status="stats.blocked_reason ? 'exception' : undefined" />
      <el-form label-position="top" class="storage-fields" :disabled="saving">
        <el-form-item label="邮件容量上限（GB）"><el-input-number v-model="form.max_gb" :min="1" :max="1000" controls-position="right" /></el-form-item>
        <el-form-item label="至少保留磁盘空间（GB）"><el-input-number v-model="form.min_free_gb" :min="1" :max="100" controls-position="right" /></el-form-item>
        <el-form-item label="识别积压暂停线（封）"><el-input-number v-model="form.queue_limit" :min="100" :max="5000" :step="100" controls-position="right" /></el-form-item>
      </el-form>
      <p class="storage-note">容量或磁盘空间不足时暂停新增读取；识别积压时暂缓下一批，自动读取会在后续检查时继续。本阶段不自动删除邮件和凭证。</p>
      <el-button type="primary" :loading="saving" @click="save">保存邮件规则</el-button>
      <details><summary>数据位置与升级说明</summary><p class="storage-path">{{ stats.data_directory }}</p><p>数据独立于程序解压目录。同一电脑、同一用户关闭旧程序后打开新版，可沿用邮件、账号及读取进度。备份应在关闭程序后复制完整数据目录，包含密钥文件。</p><p>数据库内部可复用空间 {{ bytes(stats.reusable_bytes) }}，已计入数据库占用。附件统计为加密保存后的大小，识别结果包含预览图片，不等于纯图片体积。</p></details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
type Rules={max_gb:number;min_free_gb:number;queue_limit:number}
type Stats={settings:Rules;mail_count:number;attachment_count:number;used_bytes:number;attachment_bytes:number;recognition_bytes:number;free_bytes:number;reusable_bytes:number;data_directory:string;warning:boolean;blocked_reason:string}
const stats=ref<Stats|null>(null),pending=ref(0),loading=ref(false),saving=ref(false),error=ref('')
const form=ref<Rules>({max_gb:10,min_free_gb:5,queue_limit:500})
const client=axios.create({baseURL:'/api/mail',headers:{'X-Finance-Local':'1'},timeout:30000})
const bytes=(value:number)=> value>=1024**3 ? `${(value/1024**3).toFixed(2)} GB` : `${(value/1024**2).toFixed(1)} MB`
async function load(){
  loading.value=true;error.value=''
  try{
    const [storage,status]=await Promise.allSettled([client.get<Stats>('/storage'),client.get('/status')])
    if(storage.status==='rejected') throw storage.reason
    stats.value=storage.value.data;form.value={...storage.value.data.settings}
    if(status.status==='fulfilled') pending.value=status.value.data.analysis_pending||0
    else error.value='邮件占用已读取，但队列状态暂时不可用，请稍后刷新。'
  }
  catch(cause:any){const detail=cause.response?.data?.detail;error.value=typeof detail==='string' ? detail : cause.code==='ECONNABORTED' ? '存储占用查询超过30秒，数据库可能繁忙；后台任务不一定已停止，请稍后刷新。' : '无法连接邮件存储查询服务，请检查程序运行状态后重试。'}finally{loading.value=false}
}
async function save(){
  saving.value=true;error.value=''
  try{stats.value=(await client.put<Stats>('/storage',form.value)).data;ElMessage.success('邮件保存规则已更新')}
  catch{error.value='保存失败，请检查数值和本地服务后重试。'}finally{saving.value=false}
}
onMounted(load)
</script>
<style scoped>
.mail-storage{margin-bottom:40px;padding-bottom:32px;border-bottom:1px solid var(--el-border-color-light)}
.storage-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}
.storage-heading h2{margin:0}.storage-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}
.storage-metrics span{display:block;color:var(--el-text-color-secondary);font-size:14px;margin-bottom:8px}.storage-metrics strong{font-size:22px;font-weight:600}
.storage-fields{display:flex;flex-wrap:wrap;gap:24px;margin-top:24px}.storage-note,details{font-size:14px;color:var(--el-text-color-secondary);line-height:1.7}.storage-note{margin:0 0 20px}details{margin-top:20px}.storage-path{overflow-wrap:anywhere}summary{cursor:pointer}
@media(max-width:700px){.storage-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.storage-fields{display:block}}
</style>
