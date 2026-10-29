<template>
  <section class="mail-storage" aria-labelledby="mail-storage-title" v-loading="loading">
    <div class="storage-heading"><h2 id="mail-storage-title">邮件存储</h2><el-button :loading="loading" :disabled="running" @click="load">刷新占用</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="stats">
      <el-alert v-if="stats.blocked_reason || stats.warning" :title="stats.blocked_reason || '邮件存储已使用超过 80%，请关注容量。'" type="warning" :closable="false" />
      <div class="storage-metrics">
        <div><span>本地邮件</span><strong>{{ stats.mail_count.toLocaleString() }} 封</strong></div>
        <div><span>邮件总占用（含预览和清理备份）</span><strong>{{ bytes(stats.used_bytes) }}</strong></div>
        <div><span>原始附件 · {{ stats.attachment_count.toLocaleString() }} 个来源</span><strong>{{ bytes(stats.attachment_bytes) }}</strong></div>
        <div><span>识别结果与历史预览</span><strong>{{ bytes(stats.recognition_bytes) }}</strong></div>
        <div><span>临时预览缓存（最多 {{ stats.settings.preview_cache_mb }} MB）</span><strong>{{ bytes(stats.preview_cache_bytes) }}</strong></div>
        <div><span>邮件清理恢复备份</span><strong>{{ bytes(stats.cleanup_backup_bytes || 0) }}</strong></div>
        <div><span>磁盘剩余空间</span><strong>{{ bytes(stats.free_bytes) }}</strong></div>
        <div><span>数据库文件占用</span><strong>{{ bytes(stats.database_bytes) }}</strong></div>
        <div><span>等待识别</span><strong>{{ pending.toLocaleString() }} 封</strong></div>
      </div>
      <el-progress :percentage="Math.min(100, Math.round(stats.used_bytes / (stats.settings.max_gb * 1024 ** 3) * 100))" :status="stats.blocked_reason ? 'exception' : undefined" />
      <el-form label-position="top" class="storage-fields" :disabled="saving || running">
        <el-form-item label="邮件容量上限（GB）"><el-input-number v-model="form.max_gb" :min="1" :max="1000" controls-position="right" /></el-form-item>
        <el-form-item label="至少保留磁盘空间（GB）"><el-input-number v-model="form.min_free_gb" :min="1" :max="100" controls-position="right" /></el-form-item>
        <el-form-item label="识别积压暂停线（封）"><el-input-number v-model="form.queue_limit" :min="100" :max="5000" :step="100" controls-position="right" /></el-form-item>
        <el-form-item label="临时预览缓存上限（MB）"><el-input-number v-model="form.preview_cache_mb" :min="32" :max="2048" :step="32" controls-position="right" /></el-form-item>
      </el-form>
      <p class="storage-note">相同原件共用存储，预览在查看时生成，加密缓存最多保留 7 天并按容量淘汰。不删除邮件和原始凭证。</p>
      <el-button type="primary" :loading="saving" :disabled="running" @click="save">保存邮件规则</el-button>
      <div class="storage-maintenance">
        <h3>历史数据优化与空间回收</h3>
        <p class="storage-note">先关闭自动读取，再预览。优化会暂缓识别队列，等待正在识别的附件完成；结束后恢复队列。优化只移除可重建的预览与重复存储，保留原件、识别文字及已确认关联。</p>
        <div class="storage-actions">
          <el-button :disabled="running || saving" @click="startPreview">预览存储优化</el-button>
          <el-checkbox v-model="compact" :disabled="running">优化后整理数据库，释放磁盘空间</el-checkbox>
          <el-button type="primary" :disabled="running || job?.mode !== 'preview' || job?.status !== 'completed'" @click="optimize">确认优化</el-button>
        </div>
        <div v-if="job?.id" class="job-progress" aria-live="polite">
          <strong>{{ job.phase || '存储检查' }}</strong>
          <span v-if="running"> · {{ job.processed || 0 }} / {{ job.total || 0 }} {{ job.mode?.startsWith('cleanup') ? '封邮件' : '份附件' }}</span>
          <el-progress v-if="running" :percentage="job.total ? Math.min(99,Math.round((job.processed || 0)/job.total*100)) : 0" />
          <el-alert v-if="job.message" :title="job.message" type="warning" :closable="false" />
          <template v-if="job.status === 'completed' && job.result">
            <p v-if="job.mode === 'preview'">可移除 {{ count('preview_pages') }} 页历史预览；重复附件预计减少 {{ bytes(count('duplicate_bytes')) }}。预计可释放约 {{ bytes(count('estimated_reclaim_bytes')) }}，实际以优化和数据库整理结果为准。</p>
            <p v-else-if="job.mode === 'optimize'">已处理 {{ count('processed') }} 份附件，移除 {{ count('preview_pages') }} 页可重建预览；数据库文件已减少 {{ bytes(count('database_released_bytes')) }}，临时缓存已清理 {{ bytes(count('cache_released_bytes')) }}。</p>
            <p v-if="count('protected_preview_pages')">{{ count('protected_preview_pages') }} 页预览缺少可重建原件，已保留。</p>
            <el-alert v-if="!job.mode?.startsWith('cleanup') && count('errors')" :title="`${count('errors')} 份附件未完成优化，原数据已保留，请检查后重试。`" type="warning" :closable="false" />
            <el-alert v-if="job.result.compact_note" :title="String(job.result.compact_note)" type="warning" :closable="false" />
          </template>
          <p v-if="running" class="storage-note">大库可能需要较长时间。请保持程序运行，数据库整理期间不要关闭程序。</p>
        </div>
        <details v-if="history.length"><summary>最近优化记录</summary><p v-for="item in history" :key="item.id">{{ date(item.finished_at) }} · {{ item.mode === 'preview' ? '预览' : '优化' }} · {{ item.status === 'completed' ? '完成' : '未完成' }}<span v-if="item.mode === 'optimize' && item.result"> · 文件减少 {{ bytes(Number(item.result.database_released_bytes || 0)) }}</span></p></details>
      </div>
      <div class="storage-maintenance">
        <h3>谨慎清理无付款线索的邮件</h3>
        <p class="storage-note">未匹配邮件仍可能用于未来对账。这里只列出完整读取、无附件、无付款及业务线索的候选；有附件、异常或不确定内容的保留。请逐封核对，默认不选。附件空间请使用上方去重优化。</p>
        <el-button :disabled="running || saving" @click="cleanupPreview">预览邮件清理</el-button>
        <template v-if="job.mode === 'cleanup_preview' && job.status === 'completed'">
          <p>候选 {{ candidates.length }} 封（每批最多200封）；保留 {{ count('protected') }} 封，检查异常保留 {{ count('errors') }} 封。候选也不代表确定无用。</p>
          <el-checkbox-group v-model="selected" class="cleanup-list">
            <div v-for="item in candidates" :key="item.id"><el-checkbox :value="item.id">{{ item.subject }} · {{ item.sender }}</el-checkbox><details><summary>核对邮件正文</summary><pre class="cleanup-body">{{ item.body }}</pre></details></div>
          </el-checkbox-group>
          <el-button type="danger" :disabled="!selected.length || running" @click="cleanupExecute">备份并清理所选 {{ selected.length }} 封</el-button>
        </template>
        <p v-if="job.mode?.startsWith('cleanup') && job.result?.note">{{ job.result.note }}</p>
        <p v-if="job.mode === 'cleanup' && job.status === 'completed'">已清理 {{ count('deleted') }} 封；加密备份 {{ bytes(count('backup_bytes')) }}，备份继续占用磁盘。不会立即缩小数据库文件。</p>
        <p v-if="job.mode === 'cleanup_restore' && job.status === 'completed'">已恢复 {{ count('restored') }} 封；{{ count('conflicts') }} 封与后来数据冲突，已保留现有数据。</p>
        <div v-for="item in cleanupBackups" :key="item.backup_id" class="storage-actions">
          <span>{{ date(item.at) }} · 加密恢复备份 {{ bytes(item.bytes) }}</span>
          <el-button :disabled="running" @click="restoreCleanup(item.backup_id)">从加密备份恢复</el-button>
        </div>
        <p class="storage-note">Outlook原邮件、所有附件、已确认关联和读取进度保留。清理备份随数据目录保存且不自动删除；重新扫描可能再次读入。空间收益取决于候选数量，优先使用附件去重。</p>
      </div>
      <details><summary>数据位置与升级说明</summary><p class="storage-path">{{ stats.data_directory }}</p><p>数据独立于程序解压目录。同一电脑、同一用户关闭旧程序后打开新版，可沿用邮件、账号及读取进度。备份应在关闭程序后复制完整数据目录，包含密钥文件。</p><p>数据库内部可复用空间 {{ bytes(stats.reusable_bytes) }}，已计入数据库文件占用。清理后需要整理数据库，文件才会缩小；空间不足时会跳过整理并提示。原始附件统计按实际加密存储计算，共享后可小于各邮件附件大小之和。</p></details>
    </template>
  </section>
</template>
<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
type Rules={max_gb:number;min_free_gb:number;queue_limit:number;preview_cache_mb:number}
type Stats={settings:Rules;mail_count:number;attachment_count:number;used_bytes:number;database_bytes:number;attachment_bytes:number;recognition_bytes:number;preview_cache_bytes:number;cleanup_backup_bytes?:number;free_bytes:number;reusable_bytes:number;data_directory:string;warning:boolean;blocked_reason:string}
type Job={id?:string;mode?:string;status?:string;phase?:string;processed?:number;total?:number;message?:string;finished_at?:string;result?:Record<string,any>}
const stats=ref<Stats|null>(null),pending=ref(0),loading=ref(false),saving=ref(false),error=ref('')
const job=ref<Job>({}),history=ref<Job[]>([]),compact=ref(true)
const selected=ref<string[]>([])
const candidates=computed<Array<{id:string;subject:string;sender:string;body:string}>>(()=>job.value.result?.candidates || [])
const cleanupBackups=ref<Array<{backup_id:string;bytes:number;at:string}>>([])
const running=computed(()=>job.value.status==='running')
const form=ref<Rules>({max_gb:10,min_free_gb:5,queue_limit:500,preview_cache_mb:256})
const client=axios.create({baseURL:'/api/mail',headers:{'X-Finance-Local':'1'},timeout:30000})
const bytes=(value:number)=> value>=1024**3 ? `${(value/1024**3).toFixed(2)} GB` : `${(value/1024**2).toFixed(1)} MB`
const count=(key:string)=>Number(job.value.result?.[key]||0)
const date=(value?:string)=>value?new Date(value).toLocaleString('zh-CN'):''
let timer:ReturnType<typeof setTimeout>|undefined,alive=true
function failure(cause:unknown,fallback:string){return axios.isAxiosError(cause) && typeof cause.response?.data?.detail==='string'?cause.response.data.detail:fallback}
async function load(){
  loading.value=true;error.value=''
  try{
    const [storage,status,task,records,backups]=await Promise.allSettled([client.get<Stats>('/storage'),client.get('/status'),client.get<Job>('/storage/maintenance'),client.get<Job[]>('/storage/maintenance/history'),client.get('/storage/cleanup/backups')])
    if(!alive)return
    if(storage.status==='rejected') throw storage.reason
    stats.value=storage.value.data;form.value={...storage.value.data.settings}
    if(status.status==='fulfilled') pending.value=status.value.data.analysis_pending||0
    else error.value='邮件占用已读取，但队列状态暂时不可用，请稍后刷新。'
    if(task.status==='fulfilled'){job.value=task.value.data;if(running.value)schedule()}
    else if(!error.value)error.value='邮件占用已读取，但优化任务状态暂不可用，请勿重复启动优化。'
    if(records.status==='fulfilled')history.value=records.value.data
    if(backups.status==='fulfilled')cleanupBackups.value=backups.value.data
    else if(!error.value)error.value='恢复备份列表暂时不可用，请稍后刷新。'
  }
  catch(cause:any){const detail=cause.response?.data?.detail;error.value=typeof detail==='string' ? detail : cause.code==='ECONNABORTED' ? '存储占用查询超过30秒，数据库可能繁忙；后台任务不一定已停止，请稍后刷新。' : '无法连接邮件存储查询服务，请检查程序运行状态后重试。'}finally{loading.value=false}
}
function schedule(){clearTimeout(timer);if(alive)timer=setTimeout(poll,1500)}
async function poll(){
  try{const response=await client.get<Job>('/storage/maintenance');if(!alive)return;job.value=response.data;if(running.value)schedule();else await load()}
  catch{if(alive){error.value='暂时无法读取进度，任务可能仍在运行，请勿重复执行或关闭程序。';schedule()}}
}
async function startPreview(){
  error.value=''
  try{job.value=(await client.post<Job>('/storage/maintenance/preview')).data;schedule()}
  catch(cause){error.value=failure(cause,'无法开始存储预览，请稍后重试。')}
}
async function optimize(){
  try{await ElMessageBox.confirm('将去重原件并移除可重建的历史预览，保留邮件、原始凭证、识别文字及确认关联。整理大数据库需要临时磁盘空间，请保持程序运行。','确认优化存储',{confirmButtonText:'开始优化',cancelButtonText:'取消',type:'warning'})}catch{return}
  error.value=''
  try{job.value=(await client.post<Job>('/storage/maintenance/optimize',{confirmed:true,preview_id:job.value.id,compact:compact.value})).data;schedule()}
  catch(cause){error.value=failure(cause,'存储优化未启动，请重新预览后重试。')}
}
async function cleanupPreview(){
  selected.value=[];error.value=''
  try{job.value=(await client.post<Job>('/storage/cleanup/preview')).data;schedule()}
  catch(cause){error.value=failure(cause,'邮件清理预览未启动。')}
}
async function cleanupExecute(){
  const previewId=job.value.id, ids=[...selected.value]
  try{await ElMessageBox.confirm(`请确认已逐封核对这 ${ids.length} 封邮件。程序先保存加密恢复备份，再清理本地记录；Outlook原件和所有附件保留。备份继续占用空间，数据库不会立即缩小。`,'确认本地邮件清理',{confirmButtonText:'备份并清理',cancelButtonText:'取消',type:'warning'})}catch{return}
  error.value=''
  try{job.value=(await client.post<Job>('/storage/cleanup/execute',{confirmed:true,preview_id:previewId,selected:ids})).data;selected.value=[];schedule()}
  catch(cause){error.value=failure(cause,'清理未启动，请重新预览。')}
}
async function restoreCleanup(backupId:string){
  error.value=''
  try{job.value=(await client.post<Job>('/storage/cleanup/restore',{backup_id:backupId})).data;schedule()}
  catch(cause){error.value=failure(cause,'备份恢复未启动。')}
}
async function save(){
  saving.value=true;error.value=''
  try{stats.value=(await client.put<Stats>('/storage',form.value)).data;ElMessage.success('邮件保存规则已更新')}
  catch(cause){error.value=failure(cause,'保存失败，请检查数值和本地服务后重试。')}finally{saving.value=false}
}
onMounted(load)
onBeforeUnmount(()=>{alive=false;clearTimeout(timer)})
</script>
<style scoped>
.mail-storage{margin-bottom:40px;padding-bottom:32px;border-bottom:1px solid var(--el-border-color-light)}
.storage-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}
.storage-heading h2{margin:0}.storage-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:24px;margin:24px 0}
.storage-metrics span{display:block;color:var(--el-text-color-secondary);font-size:14px;margin-bottom:8px}.storage-metrics strong{font-size:22px;font-weight:600}
.storage-fields{display:flex;flex-wrap:wrap;gap:24px;margin-top:24px}.storage-note,details{font-size:14px;color:var(--el-text-color-secondary);line-height:1.7}.storage-note{margin:0 0 20px}details{margin-top:20px}.storage-path{overflow-wrap:anywhere}summary{cursor:pointer}
.storage-maintenance{margin-top:28px;border-top:1px solid var(--el-border-color-light);padding-top:20px}.storage-actions{display:flex;flex-wrap:wrap;align-items:center;gap:16px}.job-progress{margin-top:20px;line-height:1.7}.cleanup-body{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.cleanup-list details{margin:4px 0 16px 24px}.cleanup-list{max-height:320px;overflow:auto;margin:16px 0}.cleanup-list :deep(.el-checkbox){height:auto;white-space:normal;overflow-wrap:anywhere}.job-progress :deep(.el-progress){margin-top:12px}
@media(max-width:900px){.storage-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.storage-fields{display:block}}
</style>
