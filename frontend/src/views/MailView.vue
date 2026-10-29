<template>
  <main class="mail-page">
    <header class="mail-heading">
      <div><h1>邮件收款匹配</h1><p>{{ status?.enabled ? '自动读取已开启' : '自动读取未开启' }}<span v-if="status?.analysis_busy"> · 正在识别付款附件</span></p></div>
      <div class="mail-actions">
        <input ref="fileInput" type="file" accept=".eml,.msg" class="mail-file-input" @change="importSample" />
        <el-button v-if="section === 'mail'" :icon="Upload" :loading="working === 'import'" :disabled="!!working" @click="fileInput?.click()">导入验证邮件</el-button>
        <el-button v-if="section === 'mail'" :icon="Download" :disabled="!rows.length" @click="exportRows">导出本页邮件</el-button>
      </div>
    </header>

    <el-tabs v-model="section">
      <el-tab-pane label="收款匹配" name="receipts"><ReceiptMatching ref="receiptMatching" :active="section === 'receipts'" @open-mail="openMatchedMail" /></el-tab-pane>
      <el-tab-pane label="邮箱与邮件" name="mail">
    <el-alert v-if="connectionError" :title="connectionError" type="error" :closable="false" show-icon class="mail-alert" />
    <details class="mail-settings" :open="settingsOpen ?? true" @toggle="settingsOpen = ($event.target as HTMLDetailsElement).open">
      <summary><strong>自动收取邮件</strong><span class="auto-mail-state">{{ !status ? '正在检查环境' : !status.outlook_supported ? '当前 Mac 不支持连接 · Windows 经典版 Outlook 可用' : status.enabled ? '自动读取已开启' : 'Windows 经典版 Outlook' }}</span><span class="auto-mail-config">{{ settingsOpen === false ? '展开设置' : '收起设置' }}</span></summary>
      <div class="mail-connection-line"><p>{{ status && !status.outlook_supported ? '当前电脑可导入 MSG / EML 验证；自动收取需在 Windows 经典版 Outlook 中使用。' : '选择邮件目录和时间范围，读取后自动识别付款附件。' }}</p><el-button link type="primary" :icon="folders.length ? Refresh : Connection" :loading="working === 'connect'" :disabled="connectionLocked" @click="connect">{{ folders.length ? '刷新目录' : '连接 Outlook' }}</el-button></div>
      <div class="mail-setup-grid">
        <section class="mail-source-pane" aria-label="读取范围">
          <h2>读取范围</h2><p class="setup-caption">选择需要读取的邮箱或存档目录。关闭自动读取并等待当前读取完成后可修改，附件识别可继续。</p>
          <MailFolderTree v-model="folderKeys" :nodes="folderTree" :disabled="connectionLocked" />
          <div class="mail-directory-footer"><el-checkbox v-model="includeChildren" :disabled="connectionLocked">包含子文件夹</el-checkbox><span>已选 {{ selectedFolders.length }} 个目录</span></div>
        </section>
        <section class="mail-options-pane" aria-label="读取方式">
          <h2>读取方式</h2><p class="setup-caption">设置邮件时间范围和每批读取数量。</p>
          <el-radio-group v-model="rangeMode" class="mail-range-tabs" :disabled="connectionLocked" aria-label="读取日期方式"><el-radio-button value="recent">最近天数</el-radio-button><el-radio-button value="fixed">指定日期</el-radio-button></el-radio-group>
          <div v-if="rangeMode === 'recent'" class="mail-setting-row"><label for="mail-days">读取最近</label><el-input-number id="mail-days" v-model="days" :min="1" :max="3650" :disabled="connectionLocked" controls-position="right" /><span>天</span></div>
          <div v-else class="mail-fixed-dates"><label>收件日期范围</label><el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" :disabled="connectionLocked" aria-label="收件日期范围" /><span class="setup-caption">北京时间，包含结束日</span></div>
          <div class="mail-setting-row"><label for="mail-limit">每批最多</label><el-input-number id="mail-limit" v-model="limit" :min="1" :max="500" :disabled="connectionLocked" controls-position="right" /><span>封</span></div>
          <div class="mail-auto-options"><h2>自动读取</h2><p class="setup-caption">{{ rangeMode === 'fixed' ? '连续补扫所选范围，完成后自动停止。' : '按设定的间隔检查并读取新邮件。' }}</p>
            <el-switch :model-value="!!status?.enabled" :disabled="!status?.outlook_supported || !!working || (!status?.enabled && (!selectedFolders.length || !rangeValid || status?.busy))" :loading="working === 'poll'" :active-text="rangeMode === 'fixed' ? '自动连续补扫' : '定时读取'" @change="setPolling" />
            <div class="mail-auto-row"><label for="mail-seconds">{{ rangeMode === 'fixed' ? '异常检查间隔' : '检查间隔' }}</label><el-select id="mail-seconds" v-model="seconds" :disabled="connectionLocked" aria-label="检查间隔"><el-option v-for="minutes in [1,5,15,30,60]" :key="minutes" :label="`${minutes} 分钟`" :value="minutes * 60" /></el-select><el-checkbox v-model="resumeOnStart" :disabled="!status?.outlook_supported || !!working || !!status?.enabled || !!status?.busy">启动后恢复</el-checkbox></div>
          </div>
        </section>
      </div>
      <footer class="mail-setup-footer">
        <div class="mail-sync-status" role="status"><span class="mail-status-lead"><el-icon><Clock /></el-icon>{{ status?.busy ? '正在读取…' : status?.last_run ? `上次读取 ${formatDate(status.last_run)} · ${status.read} 封 · 新增 ${status.new} 封` : '尚未读取' }}</span><span v-if="status?.next_run">下次 {{ formatDate(status.next_run) }}</span><span v-if="status?.sync_phase">{{ status.sync_phase === 'history' ? '历史补扫' : '增量同步' }} · 已扫描 {{ status.sync_scanned || 0 }} 封{{ status.sync_complete ? (status.errors.length ? ' · 本轮结束，有异常待补扫' : ' · 本轮完成') : '' }}</span><span>本地已保存 {{ savedTotal }} 封<template v-if="status?.analysis_pending"> · 待识别 {{ status.analysis_pending }} 封</template></span><span v-if="status?.folders_total != null">目录进度 {{ status.folders_done || 0 }} / {{ status.folders_total }}</span><span v-if="status?.sync_start">收件范围 {{ formatDate(status.sync_start) }} — {{ formatDate(status.sync_end || '') }}（结束时间不含）</span></div>
        <div class="mail-setup-actions"><el-button :icon="Refresh" :disabled="!status?.outlook_supported || !!working || !!status?.enabled || !!status?.busy || !selectedFolders.length || !rangeValid" @click="scan(true)">重新补扫</el-button><el-button type="primary" :icon="VideoPlay" :loading="status?.busy" :disabled="!status?.outlook_supported || !selectedFolders.length || !rangeValid || !!working || status?.enabled || status?.busy" @click="scan()">读取一批</el-button></div>
      </footer>
      <details v-if="status?.performance" class="mail-performance">
        <summary>本次启动 · 识别处理 {{ status.performance.active_analysis ?? status.analysis_active ?? 0 }} / {{ status.analysis_workers ?? '—' }} · 最近60秒处理 {{ status.performance.recent.completed_attachments }} 附件</summary>
        <div class="mail-performance-totals">
          <span>运行 {{ performanceDuration(status.performance.elapsed_seconds) }}</span>
          <span>处理 {{ status.performance.counters.completed_attachments }} 附件</span>
          <span>OCR {{ status.performance.counters.ocr_pages }} 页（最近60秒 {{ status.performance.recent.ocr_pages }} 页）</span>
          <span>PDF文本 {{ status.performance.counters.pdf_text_pages }} 页</span>
          <span>复用 {{ status.performance.counters.cache_hits }} 次</span>
          <span>未变邮件跳过 {{ status.performance.counters.unchanged_mail }} 封</span>
          <span>识别失败 {{ status.performance.counters.analysis_failures }} 次</span>
        </div>
        <div class="mail-performance-totals">
          <span>读取 {{ performanceDuration(status.performance.seconds.read_seconds) }}</span>
          <span>附件读取 {{ performanceDuration(status.performance.seconds.attachment_read_seconds) }}</span>
          <span>附件识别 {{ performanceDuration(status.performance.seconds.analysis_seconds) }}</span>
          <span>保存 {{ performanceDuration(status.performance.seconds.save_seconds) }}</span>
          <span>复用等待 {{ performanceDuration(status.performance.seconds.cache_wait_seconds) }}</span>
        </div>
        <div class="mail-performance-footer"><p>耗时为累计工作时间，并发任务可能重叠。最近60秒按秒统计，边界最多相差1秒；重启后重新计数。</p><el-button size="small" :icon="Download" @click="exportPerformance">导出性能 JSON</el-button><el-button size="small" @click="exportDiagnostics(3)">导出近3天日志</el-button><el-button size="small" @click="exportDiagnostics(14)">近14天日志</el-button></div>
      </details>
      <p v-if="status?.queue_paused && !status.analysis_error && !status.error" class="mail-queue-note" role="status">{{ status.enabled ? '正在消化附件队列，积压下降后将自动继续读取。' : '正在消化附件队列，可稍后继续读取。' }}</p>
      <details class="mail-reading-note"><summary>读取说明</summary><p>仅读取 Outlook 中已打开且有权限访问的目录。标识与内容一致的邮件副本合并并保留来源；没有匹配邮件不代表其他目录也没有。</p></details>
    </details>
    <el-alert v-if="status?.error" :title="status.error" :type="status.queue_paused && status.error.includes('队列') ? 'info' : 'error'" :closable="false" show-icon class="mail-alert" />
    <el-alert v-if="status?.analysis_error" :title="status.analysis_error" type="error" :closable="false" show-icon class="mail-alert" />
    <el-alert v-if="status?.partial" :title="status.enabled ? '本批已保存，后台将继续读取下一批。' : '本批已保存，可继续读取下一批，或开启定时读取自动补扫。'" type="info" :closable="false" show-icon class="mail-alert" />
    <el-alert v-for="(error,i) in warnings" :key="i" :title="error" type="warning" :closable="false" show-icon class="mail-alert" />

    <div class="mail-workspace">
      <section class="mail-list" aria-label="已读取邮件">
        <div class="mail-section-heading"><h2>本地邮件 <span>{{ savedTotal }}</span></h2><el-input v-model="filter" clearable :prefix-icon="Search" placeholder="搜索付款人、金额、日期或邮件" aria-label="筛选本地邮件" /></div>
        <div class="mail-filters" aria-label="付款信息筛选"><button v-for="chip in filterChips" :key="chip.value" :aria-pressed="paymentFilter === chip.value" @click="paymentFilter = chip.value">{{ chip.label }}</button></div>
        <div class="mail-cards" aria-label="邮件列表">
          <button v-for="row in filteredRows" :key="row.id" class="mail-card" :class="{selected: selected?.id === row.id}" :aria-pressed="selected?.id === row.id" @click="selectMessage(row)">
            <strong class="mail-card-amount">{{ summaryAmount(row) }}</strong>
            <span class="mail-card-subject">{{ row.subject || '（无主题）' }}</span>
            <span>{{ row.payment_summary?.payers?.join('、') || '付款人待补充' }}</span>
            <span>付款日期：{{ row.payment_summary?.dates?.join('、') || '待补充' }}</span>
            <span class="mail-card-meta">{{ row.sender || '发件人未提供' }} · {{ formatDate(row.received) }}</span>
            <span class="mail-card-status">{{ summaryStatus(row) }}</span>
          </button>
          <p v-if="!filteredRows.length" class="mail-muted">{{ savedTotal ? '没有符合筛选条件的邮件' : '暂无邮件，可导入 MSG / EML 邮件' }}</p>
        </div>
        <div class="mail-list-pagination"><span>筛选 {{ mailTotal }} 封 · 每页 50 封</span><el-pagination v-model:current-page="mailPage" :page-size="50" :total="mailTotal" :pager-count="5" layout="prev, pager, next" small /></div>
        <footer class="mail-list-footer"><span>已保存 {{ savedTotal }} 封 · 支持翻页及全库搜索</span><el-button link type="danger" :icon="Delete" :disabled="locked || !!working || !savedTotal" @click="clearLocal">清空本地记录</el-button></footer>
      </section>
      <section class="mail-detail" aria-label="邮件详情" v-loading="detailLoading">
        <MailPaymentDetail v-if="selected" :key="selected.id" :mail="selected" :related="related" :related-loading="relatedLoading" :related-error="relatedError" :analysis-busy="!!status?.analysis_busy || working === 'analyze'" @analyze="analyzeAttachments" @open-receipt="openRelatedReceipt" @more-receipts="loadRelated(20)" @select-payment="selectPayment" />
        <el-empty v-else description="选择邮件查看详情" :image-size="72" />
      </section>
    </div>
      </el-tab-pane>
    </el-tabs>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Refresh, Upload, Download, Delete, Search, Clock, VideoPlay } from '@element-plus/icons-vue'
import { mailApi, type MailFolder, type MailRow, type MailStatus, type MailRelatedReceipts } from '../services/mail'
import { downloadBlob } from '../services/api'
import MailPaymentDetail from '../components/MailPaymentDetail.vue'
import MailFolderTree from '../components/MailFolderTree.vue'
import ReceiptMatching from '../components/ReceiptMatching.vue'

const performanceDuration=(value:number)=>`${Math.max(0, Number.isFinite(value) ? value : 0).toFixed(1)} 秒`
async function exportDiagnostics(days:number) {
  try { downloadBlob(await mailApi.diagnostics(days),'邮件诊断日志.zip') } catch(error) { ElMessage.error(errorText(error)) }
}
function exportPerformance() {
  if (!status.value?.performance) return
  const payload={generated_at:new Date().toISOString(),build_commit:import.meta.env.VITE_BUILD_COMMIT || 'unknown',performance:status.value.performance}
  downloadBlob(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}),'邮件性能统计.json')
}

const mailPage=ref(1), mailTotal=ref(0), savedTotal=ref(0)
const rows=ref<MailRow[]>([]), folders=ref<MailFolder[]>([]), selected=ref<MailRow|null>(null), status=ref<MailStatus|null>(null)
const folderKeys=ref<string[]>([]), includeChildren=ref(false), rangeMode=ref('recent'), dateRange=ref<[string,string]|null>(null), days=ref(90), limit=ref(100), seconds=ref(300), filter=ref(''), working=ref(''), connectionError=ref(''), section=ref('receipts'), resumeOnStart=ref(false)
const folderWarnings=ref<string[]>([]), detailLoading=ref(false), fileInput=ref<HTMLInputElement>()
const folderValue=(folder:MailFolder)=>JSON.stringify([folder.store,folder.id])
const selectedFolders=computed(()=>folders.value.filter(f=>folderKeys.value.includes(folderValue(f))))
const rangeValid=computed(()=>rangeMode.value==='recent' || !!(dateRange.value?.[0] && dateRange.value?.[1]))
interface FolderNode {value:string;label:string;path:string;disabled:boolean;children:FolderNode[]}
const folderTree=computed(()=>{
  const nodes=new Map<string,FolderNode>()
  for(const f of folders.value) nodes.set(folderValue(f),{value:folderValue(f),label:f.name || f.label,path:f.label,disabled:f.selectable===false,children:[]})
  const roots:FolderNode[]=[]
  for(const f of folders.value){const node=nodes.get(folderValue(f))!;const parent=f.parent ? nodes.get(JSON.stringify([f.store,f.parent])) : undefined;if(parent)parent.children.push(node);else {node.label=f.parent ? f.label : (f.store_label || f.label);roots.push(node)}}
  return roots
})
const locked=computed(()=>!!status.value?.busy || !!status.value?.enabled || !!status.value?.analysis_busy)
const connectionLocked=computed(()=>!!status.value?.busy || !!status.value?.enabled || !!working.value || !status.value?.outlook_supported)
const warnings=computed(()=>[...folderWarnings.value,...(status.value?.errors || [])])
const receiptMatching = ref<{ openReceipt: (id: string, ref: string, preferredMailId?: string,preferredPaymentId?:string) => Promise<void> }>()
const relatedPaymentId=ref('')
const related = ref<MailRelatedReceipts | null>(null), relatedLoading = ref(false), relatedError = ref('')
const settingsOpen = ref<boolean>(), paymentFilter = ref('all')
const filterChips = [{value:'all',label:'全部'},{value:'payment',label:'有付款信息'},{value:'partial',label:'待补充'},{value:'pending',label:'识别中'}]
const summaryStatus = (row: MailRow) => ({pending:'正在识别',ready:'信息齐全 · 待核对',partial:'信息待补充',empty:'暂无适合的付款信息',failed:'识别失败，可重试'}[row.payment_summary?.status || 'pending'])
const summaryAmount = (row: MailRow) => {
  const amounts = row.payment_summary?.amounts || []
  return amounts.length > 1 ? `${amounts.length} 笔金额 · ${row.payment_summary?.count || amounts.length} 条付款信息` : amounts.length ? `${amounts[0]!.currency || '币种待补充'} ${Number.isFinite(Number(amounts[0]!.amount))?Number(amounts[0]!.amount).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):amounts[0]!.amount}` : '付款金额待补充'
}
const filteredRows = computed(() => rows.value)
let messageRequest=0, searchTimer:ReturnType<typeof setTimeout>|undefined
async function loadMessages(){
  const request=++messageRequest
  const query={page:mailPage.value,page_size:50,keyword:filter.value,payment_filter:paymentFilter.value}
  const data=await mailApi.messagePage(query)
  if(disposed || request!==messageRequest || query.page!==mailPage.value || query.keyword!==filter.value || query.payment_filter!==paymentFilter.value)return
  rows.value=data.rows;mailTotal.value=data.total;savedTotal.value=data.total_saved
  if(selected.value && !data.rows.some(row=>row.id===selected.value?.id)) {
    ++detailRequest; ++relatedRequest
    selected.value=null; related.value=null; relatedError.value=''; relatedLoading.value=false
    relatedPaymentId.value=''; detailLoading.value=false
  }
  const last=Math.max(1,Math.ceil(data.total/50));if(mailPage.value>last)mailPage.value=last
}
function scheduleMessages(){clearTimeout(searchTimer);searchTimer=setTimeout(()=>{void loadMessages().catch(error=>{connectionError.value=errorText(error)})},250)}
watch([filter,paymentFilter],()=>{
  ++detailRequest; ++relatedRequest
  selected.value=null; related.value=null; relatedError.value=''; relatedLoading.value=false
  relatedPaymentId.value=''; detailLoading.value=false
  rows.value=[]; mailTotal.value=0; mailPage.value=1; scheduleMessages()
})
watch(mailPage,scheduleMessages)

let timer: ReturnType<typeof setTimeout> | undefined, disposed=false, detailRequest=0, relatedRequest=0, initialized=false
const errorText=(error: any)=>{const detail=error.response?.data?.detail;return typeof detail === 'string' ? detail : detail ? '输入参数无效，请检查天数、封数和文件夹。' : '无法连接本机邮件服务，请检查程序是否正在运行。'}
const formatDate=(value: string)=>{ const date=new Date(value);return !value || Number.isNaN(date.getTime()) ? value || '—' : date.toLocaleString() }
const selection=()=>({store:selectedFolders.value[0]?.store || 'stopped',folder:selectedFolders.value[0]?.id || 'stopped',folders:selectedFolders.value.map(f=>({store:f.store,folder:f.id,label:f.label})),include_children:includeChildren.value,start_date:rangeMode.value==='fixed'?dateRange.value?.[0]:null,end_date:rangeMode.value==='fixed'?dateRange.value?.[1]:null,days:days.value,limit:limit.value})
async function refresh() {
  try {
    status.value=await mailApi.status()
    if(!initialized) {
      seconds.value=status.value.seconds;days.value=status.value.days || 90;limit.value=status.value.limit || 100;resumeOnStart.value=!!status.value.resume_on_start
      includeChildren.value=!!status.value.include_children
      if(status.value.start_date && status.value.end_date){rangeMode.value='fixed';dateRange.value=[status.value.start_date,status.value.end_date]}
      if(status.value.folders?.length){folders.value=status.value.folders.map(f=>({store:f.store,id:f.folder,label:f.label || '上次选择的目录'}));folderKeys.value=folders.value.map(folderValue)}
      else if(status.value.store && status.value.folder){folders.value=[{store:status.value.store,id:status.value.folder,label:status.value.folder_label || '上次选择的 Outlook 文件夹'}];folderKeys.value=folders.value.map(folderValue)}
      initialized=true
    }
    await loadMessages(); connectionError.value=''
    if(selected.value && (status.value.analysis_busy || selected.value.payment_summary?.status === 'pending' || rows.value.find(row => row.id === selected.value?.id)?.payment_summary?.status !== selected.value.payment_summary?.status || selected.value.attachment_details?.some(a=>a.status==='pending' || a.status==='running'))) {
      const identity=selected.value.id, request=detailRequest
      const detail=await mailApi.message(identity)
      if(!disposed && selected.value?.id===identity && request===detailRequest) {
        const changed=JSON.stringify(selected.value.payment_candidates)!==JSON.stringify(detail.payment_candidates) || selected.value.payment_summary?.status!==detail.payment_summary?.status
        selected.value=detail
        if(!detail.payment_candidates?.some(p=>p.id===relatedPaymentId.value))relatedPaymentId.value=detail.payment_candidates?.[0]?.id || ''
        if(changed) void loadRelated()
      }
    }
  } catch(error) { connectionError.value=errorText(error) }
  finally { if(!disposed) timer=setTimeout(refresh,4000) }
}
async function action(name: string, work: ()=>Promise<void>) {
  working.value=name
  try { await work() } catch(error) { ElMessage.error(errorText(error)) }
  finally { working.value='' }
}
async function connect() { await action('connect',async()=>{const data=await mailApi.folders();folders.value=data.folders;folderWarnings.value=data.errors;folderKeys.value=folderKeys.value.filter(key=>data.folders.some(f=>folderValue(f)===key));if(!data.folders.length) ElMessage.warning('未找到可访问的邮箱或存档，请先在经典 Outlook 打开 PST。')}) }
async function scan(reset=false) { if(!rangeValid.value || !selectedFolders.value.length)return; await action('scan',async()=>{status.value=await mailApi.scan({...selection(),reset})}) }
async function setPolling(value: string | number | boolean) { if(value && (!rangeValid.value || !selectedFolders.value.length))return; await action('poll',async()=>{status.value=await mailApi.poll({...selection(),enabled:!!value,seconds:seconds.value,resume_on_start:resumeOnStart.value})}) }
async function openMatchedMail(identity: string) {section.value='mail';await selectMessage({id:identity} as MailRow)}
async function loadRelated(limit = 5) {
  const identity = selected.value?.id
  if (!identity) return
  const request = ++relatedRequest
  relatedLoading.value = true; relatedError.value = ''
  try {
    const result = await mailApi.receipts(identity, limit,3,relatedPaymentId.value)
    if (!disposed && request === relatedRequest && selected.value?.id === identity) related.value = result
  } catch (error) {
    if (!disposed && request === relatedRequest && selected.value?.id === identity) relatedError.value = `相关流水暂时无法加载：${errorText(error)}`
  } finally { if (!disposed && request === relatedRequest) relatedLoading.value = false }
}
function selectPayment(id:string){if(id!==relatedPaymentId.value){relatedPaymentId.value=id;related.value=null;void loadRelated()}}
async function openRelatedReceipt(id: string, ref: string) {
  const mailId = selected.value?.id
  section.value = 'receipts'
  await nextTick()
  try { await receiptMatching.value?.openReceipt(id, ref, mailId,relatedPaymentId.value) }
  catch (error) { ElMessage.error(errorText(error)) }
}
async function selectMessage(row: MailRow | null) {
  if(!row) return
  const request=++detailRequest
  ++relatedRequest; related.value=null; relatedError.value=''; relatedLoading.value=false
  relatedPaymentId.value=''
  selected.value = rows.value.find(item => item.id === row.id) || null
  detailLoading.value=true
  try {
    const detail=await mailApi.message(row.id)
    if(!disposed && request===detailRequest) {relatedPaymentId.value=detail.payment_candidates?.[0]?.id || ''; selected.value=detail; void loadRelated() }
  } catch(error) { if(!disposed && request===detailRequest) ElMessage.error(errorText(error)) }
  finally { if(!disposed && request===detailRequest) detailLoading.value=false }
}
async function importSample(event: Event) {
  const input=event.target as HTMLInputElement, file=input.files?.[0];input.value=''
  if(!file) return
  if(file.size>25*1024*1024) {ElMessage.error('邮件不能超过 25 MB');return}
  await action('import',async()=>{const result=await mailApi.import(file);rows.value=await mailApi.messages();const row=rows.value.find(r=>r.id===result.id);if(row) await selectMessage(row);ElMessage.success(result.new ? '已导入，正在本地识别附件' : '邮件已存在，已保留识别结果')})
}
async function analyzeAttachments() {
  if(!selected.value) return
  await action('analyze',async()=>{await mailApi.analyze(selected.value!.id);status.value=await mailApi.status()})
}
async function clearLocal() {
  try {await ElMessageBox.confirm('仅清除本机保存的邮件正文和解析结果，不删除 Outlook 邮件。','清空本地记录',{type:'warning',confirmButtonText:'清空',cancelButtonText:'取消'})} catch{return}
  await action('clear',async()=>{await mailApi.clear();detailRequest++;relatedRequest++;related.value=null;relatedLoading.value=false;relatedError.value='';selected.value=null;rows.value=[];savedTotal.value=0;mailTotal.value=0;mailPage.value=1;messageRequest++})
}
function exportRows() {
  const cell=(value: string)=>'"'+(/^[\s]*[=+@-]/.test(value) ? "'"+value : value).replace(/"/g,'""')+'"'
  const lines=[['主题','发件人','时间','付款人（多值独立列示）','付款日期（多值独立列示）','付款金额（多值独立列示）','业务编号（可选）','识别状态','附件名称','来源'],...filteredRows.value.map(r=>[r.subject,r.sender,r.received,(r.payment_summary?.payers || []).join('; '),(r.payment_summary?.dates || []).join('; '),(r.payment_summary?.amounts || []).map(a=>`${a.currency} ${a.amount}`).join('; '),(r.payment_summary?.business_ids || r.candidates || []).join('; '),summaryStatus(r),r.attachments.join('; '),r.source])]
  downloadBlob(new Blob(['\uFEFF'+lines.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}),'邮件读取结果.csv')
}
onMounted(refresh)
onBeforeUnmount(()=>{disposed=true;detailRequest++;relatedRequest++;clearTimeout(timer);clearTimeout(searchTimer);messageRequest++})
</script>

<style scoped>
.mail-page{padding:24px 32px 40px;max-width:1920px;margin:auto}
.mail-heading,.mail-actions,.mail-folder-select,.mail-schedule,.mail-schedule-options,.mail-section-heading,.mail-detail-heading,.mail-list-footer{display:flex;align-items:center;gap:12px}
.mail-heading{justify-content:space-between;margin-bottom:16px}.mail-heading>div:first-child{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px 16px}.mail-heading h1{font-size:24px;margin:0}.mail-heading p{font-size:13px;color:var(--ink-muted);margin:0}.mail-file-input{display:none}
.mail-controls{display:grid;grid-template-columns:minmax(260px,1fr) 150px 150px auto;align-items:end;gap:20px;background:white;border-bottom:1px solid var(--border);padding:24px}
.mail-controls label{display:block;font-size:13px;margin-bottom:10px;color:var(--ink-muted)}.mail-folder-field,.mail-folder-select .el-select{min-width:0}.mail-folder-select .el-select{flex:1}.mail-folder-field{grid-column:1/-1}.mail-date-field{grid-column:span 2;min-width:0}.mail-date-field :deep(.el-date-editor){max-width:100%;width:100%}.mail-number-field .el-input-number{width:100%}
.mail-schedule{justify-content:space-between;padding:18px 24px;background:white;margin-bottom:20px;flex-wrap:wrap}.mail-schedule-options{flex-wrap:wrap;font-size:13px}.mail-schedule-options .el-select{width:120px}.mail-sync-status{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--ink-muted)}
.mail-alert{margin-bottom:16px}.mail-workspace{display:grid;grid-template-columns:300px minmax(0,1fr);background:white;min-height:640px}.mail-list,.mail-detail{padding:24px;min-width:0}.mail-list{border-right:1px solid var(--border)}.mail-section-heading{justify-content:space-between;margin-bottom:20px}.mail-section-heading h2{font-size:17px;white-space:nowrap}.mail-section-heading h2 span{font-size:13px;color:var(--ink-muted);margin-left:6px}.mail-section-heading{align-items:stretch;flex-direction:column}.mail-section-heading .el-input{width:100%;max-width:100%}.mail-subject{max-width:100%;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}.mail-list-footer{justify-content:space-between;flex-wrap:wrap;margin-top:16px;font-size:12px;color:var(--ink-muted)}
.mail-detail h2{font-size:20px;margin:0 0 20px;overflow-wrap:anywhere}.mail-detail dl{display:grid;grid-template-columns:60px minmax(0,1fr);gap:10px;font-size:13px;line-height:1.6;margin-bottom:24px}.mail-detail dt{color:var(--ink-muted)}.mail-detail dd{margin:0;overflow-wrap:anywhere}.mail-detail h3{font-size:14px;margin:22px 0 12px}.mail-detail-heading{justify-content:space-between}.mail-detail-heading h3{margin:0}.mail-candidates{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.mail-detail li{font-size:13px;overflow-wrap:anywhere;line-height:1.8}.mail-muted{color:var(--ink-muted);font-size:12px;line-height:1.7}.mail-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.9 system-ui,sans-serif;max-height:420px;overflow:auto;border-top:1px solid var(--border);padding-top:16px}.mail-actions .el-button+.el-button{margin:0}
@media(max-width:800px){.mail-controls{grid-template-columns:1fr 1fr}.mail-folder-field{grid-column:1/-1}.mail-workspace{grid-template-columns:minmax(0,1fr)}.mail-list{border-right:0;border-bottom:1px solid var(--border)}.mail-list :deep(.el-table){height:300px!important}}
@media(max-width:600px){.mail-page{padding:20px 14px}.mail-heading{align-items:start;flex-direction:column}.mail-actions{flex-wrap:wrap}.mail-controls,.mail-list,.mail-detail{padding:16px}.mail-controls{gap:16px}.mail-folder-select{align-items:stretch;flex-direction:column}.mail-schedule{padding:16px}.mail-section-heading{align-items:start;flex-direction:column}.mail-section-heading .el-input{max-width:100%;width:100%}.mail-candidates .el-tag{max-width:100%}}
.mail-settings{margin-bottom:20px;border:1px solid var(--border);border-radius:10px;background:white}.mail-settings>summary{padding:16px;cursor:pointer;font-size:14px}.mail-settings .mail-schedule{margin-bottom:0}.mail-list{padding:20px 16px}.mail-cards{max-height:460px;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:2px}.mail-card{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;background:white;border:1px solid var(--border);border-radius:10px;padding:12px;color:inherit;font-family:inherit;font-size:12px;line-height:1.6;cursor:pointer;overflow-wrap:anywhere}.mail-card.selected{border-color:var(--el-color-primary);background:var(--el-color-primary-light-9)}.mail-card:focus-visible,.mail-filters button:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:2px}.mail-card-amount{font-size:16px;font-variant-numeric:tabular-nums}.mail-card-subject{font-size:13px;font-weight:500;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.mail-card-meta,.mail-card-status{font-size:11px;color:var(--ink-muted)}.mail-filters{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px}.mail-filters button{font:inherit;font-size:11px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;background:white;color:inherit;cursor:pointer}.mail-filters button[aria-pressed=true]{background:var(--el-fill-color-light);border-color:var(--el-color-primary)}
.mail-settings>summary{display:flex;align-items:center;gap:12px;flex-wrap:wrap;list-style:none}.mail-settings>summary strong{font-size:15px}.auto-mail-state{font-size:12px;color:var(--ink-muted)}.auto-mail-config{margin-left:auto;font-size:13px;color:var(--el-color-primary)}.auto-mail-config::after{content:' ▾'}.mail-settings[open] .auto-mail-config::after{content:' ▴'}.auto-mail-note{font-size:13px;line-height:1.7;margin:0;padding:0 16px 16px;color:var(--ink-muted)}

.mail-settings>summary{padding:20px 24px 12px}.mail-settings>summary strong{font-size:18px}.mail-connection-line{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:0 24px 20px;border-bottom:1px solid var(--border)}.mail-connection-line p{margin:0;color:var(--ink-muted);font-size:13px;line-height:1.7}.mail-connection-line .el-button{flex-shrink:0}.mail-setup-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}.mail-source-pane,.mail-options-pane{padding:24px;min-width:0}.mail-source-pane{border-right:1px solid var(--border)}.mail-setup-grid h2{font-size:17px;margin:0 0 8px;font-weight:600}.setup-caption{font-size:13px;line-height:1.6;color:var(--ink-muted);margin:0 0 16px}.mail-directory-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}.mail-directory-footer>span{font-size:12px;color:var(--ink-muted)}.mail-range-tabs{display:flex;width:100%;margin-bottom:18px;--el-radio-button-checked-bg-color:var(--el-color-primary-light-9);--el-radio-button-checked-text-color:var(--el-color-primary);--el-radio-button-checked-border-color:var(--el-color-primary-light-5)}.mail-range-tabs :deep(.el-radio-button){flex:1}.mail-range-tabs :deep(.el-radio-button.is-active .el-radio-button__inner){background:var(--el-color-primary-light-9);color:var(--el-color-primary);border-color:var(--el-color-primary-light-5);box-shadow:none}.mail-range-tabs :deep(.el-radio-button__inner){width:100%;box-sizing:border-box}.mail-setting-row{display:flex;align-items:center;gap:12px;margin-bottom:14px;font-size:14px}.mail-setting-row label{width:76px;flex-shrink:0}.mail-setting-row .el-input-number{width:144px}.mail-setting-row>span{color:var(--ink-muted)}.mail-auto-options{border-top:1px solid var(--border);padding-top:20px;margin-top:22px}.mail-auto-options .setup-caption{margin-bottom:12px}.mail-auto-row{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:12px;font-size:13px}.mail-auto-row .el-select{width:116px}.mail-auto-row label{white-space:nowrap}.mail-auto-row .el-checkbox{margin-right:0}.mail-fixed-dates{display:flex;flex-direction:column;gap:8px;margin-bottom:14px;font-size:14px}.mail-fixed-dates :deep(.el-date-editor){width:100%;max-width:100%;box-sizing:border-box}.mail-fixed-dates .setup-caption{margin:0}.mail-setup-footer{display:flex;align-items:center;justify-content:space-between;gap:24px;border-top:1px solid var(--border);padding:16px 24px}.mail-setup-footer .mail-sync-status{gap:8px 16px;flex:1}.mail-status-lead{display:flex;align-items:center;gap:8px;color:var(--ink);font-size:13px}.mail-setup-actions{display:flex;gap:10px;flex-shrink:0}.mail-setup-actions .el-button+.el-button{margin:0}.mail-reading-note{border-top:1px solid var(--border);padding:12px 24px;font-size:12px;color:var(--ink-muted)}.mail-reading-note summary{cursor:pointer}.mail-reading-note p{line-height:1.7;margin:10px 0 0;max-width:900px}
.mail-performance{border-top:1px solid var(--border);padding:12px 24px;font-size:12px;color:var(--ink-muted)}.mail-performance summary{cursor:pointer;color:var(--ink);line-height:1.7;overflow-wrap:anywhere}.mail-performance-totals{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:12px}.mail-performance-footer{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:12px}.mail-performance-footer p{margin:0;flex:1;min-width:180px;line-height:1.7}.mail-queue-note{margin:0;padding:12px 24px;border-top:1px solid var(--border);color:var(--ink-muted);font-size:12px}
@media(max-width:1000px){.mail-setup-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.mail-auto-row .el-checkbox{width:100%}.mail-setup-footer{align-items:flex-start;flex-wrap:wrap}}@media(max-width:720px){.mail-setup-grid{grid-template-columns:minmax(0,1fr)}.mail-source-pane{border-right:0;border-bottom:1px solid var(--border)}.mail-source-pane,.mail-options-pane{padding:20px}.mail-settings>summary{padding:16px 20px 12px}.mail-connection-line{padding:0 20px 16px;align-items:flex-start}.mail-setup-footer{padding:16px 20px;gap:16px}.mail-setup-actions{width:100%;justify-content:flex-end}.mail-auto-row .el-checkbox{width:auto}.mail-reading-note{padding:12px 20px}}@media(max-width:380px){.mail-connection-line{flex-direction:column;gap:8px}.mail-directory-footer{align-items:flex-start;flex-direction:column}.mail-setting-row{gap:8px}.mail-setting-row label{width:64px}.mail-setting-row .el-input-number{width:130px}}
.mail-list-pagination{display:flex;flex-direction:column;gap:10px;align-items:flex-start;border-top:1px solid var(--border);padding-top:12px;margin-top:12px;font-size:12px;color:var(--ink-muted)}.mail-list-pagination :deep(.el-pagination){max-width:100%;flex-wrap:wrap}
</style>
