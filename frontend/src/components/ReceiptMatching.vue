<template>
  <section ref="root" class="receipt-matching" aria-label="收款匹配" :aria-busy="busy">
    <section class="online-panel" aria-label="在线流水来源">
      <div class="online-heading"><h3>Google Sheets 在线流水</h3><el-tag :type="onlineConfig?.configured ? 'success' : 'info'">{{ onlineConfig?.configured ? '已配置' : '待配置' }}</el-tag><el-tag v-if="result?.ledger?.online" type="success">当前使用在线流水</el-tag></div>
      <p class="source">{{ result?.ledger?.online ? `${result.ledger.sheet} · ${result.ledger.entries_count.toLocaleString()} 条收款` : '连接在线流水后，核对邮件并回写关联信息' }}<span v-if="onlineConfig?.last_read_at"> · 更新于 {{ readableTime(onlineConfig.last_read_at) }}</span></p>
      <div class="actions">
        <el-button :disabled="busy" @click="openConfig">配置在线来源</el-button>
        <el-button type="primary" :loading="operation === 'online-read'" :disabled="busy || !onlineConfig?.configured" @click="readOnline">读取在线流水</el-button>
        <el-button :loading="operation === 'online-write'" :disabled="busy || stale || !onlineConfig?.configured || !result?.ledger?.online" @click="writeOnline">回写已确认关联</el-button>
        <el-button :disabled="busy" @click="openHistory">同步记录</el-button>
      </div>
      <el-alert v-if="onlineError" :title="onlineError" type="error" :closable="false" show-icon />
      <template v-if="writebackResult">
        <p class="summary" role="status">已写入 {{ writebackResult.written }} · 无变化 {{ writebackResult.unchanged }} · 失败 {{ writebackResult.failed }} · 待核实 {{ writebackResult.uncertain }}</p>
        <el-alert v-if="writebackResult.uncertain" title="部分写入结果待核实，请先读取在线流水并查看同步记录，再决定是否重试。" type="warning" :closable="false" />
        <el-table v-if="writebackIssues.length" :data="writebackIssues" max-height="240" empty-text="无失败记录"><el-table-column prop="ref" label="收款编号" min-width="150" /><el-table-column prop="status" label="状态" width="120" /><el-table-column prop="message" label="处理说明" min-width="240" /></el-table>
      </template>
    </section>
    <el-dialog v-model="configOpen" title="配置在线流水" width="min(600px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="operation !== 'online-config'" :show-close="operation !== 'online-config'" @closed="clearCredential">
      <el-form label-position="top" @submit.prevent="saveConfig">
        <el-form-item label="Google Sheets 工作表链接（含 gid）"><el-input v-model="configUrl" aria-label="Google Sheets 工作表链接" :disabled="operation === 'online-config'" /></el-form-item>
        <el-form-item label="流水币种"><el-select v-model="configCurrency" aria-label="在线流水币种" :disabled="operation === 'online-config'"><el-option v-for="item in currencies" :key="item" :label="item" :value="item" /></el-select></el-form-item>
        <el-form-item label="服务账号 JSON（已配置时可留空）"><input ref="credentialInput" type="file" accept=".json,application/json" aria-label="服务账号 JSON 文件" :disabled="operation === 'online-config'" @change="chooseCredential" /></el-form-item>
      </el-form>
      <p class="source">授权文件加密保存在本机，后续读取无需重复选择；页面不展示密钥内容。</p>
      <el-alert :title="onlineConfig?.service_email ? `请将工作表共享给 ${onlineConfig.service_email}，授予编辑权限。` : '首次保存后将显示服务账号邮箱，请将工作表共享给该邮箱并授予编辑权限。'" type="info" :closable="false" />
      <el-alert v-if="onlineError" :title="onlineError" type="error" :closable="false" />
      <template #footer><el-button :disabled="operation === 'online-config'" @click="configOpen = false">关闭</el-button><el-button type="primary" :loading="operation === 'online-config'" :disabled="busy" @click="saveConfig">保存配置</el-button></template>
    </el-dialog>
    <el-dialog v-model="historyOpen" title="同步记录" width="min(1000px, 94vw)">
      <el-alert v-if="onlineError" :title="onlineError" type="error" :closable="false" />
      <el-table :data="history" max-height="480" :empty-text="operation === 'online-history' ? '正在加载…' : '暂无同步记录'">
        <el-table-column type="expand"><template #default="{ row }"><div class="history-details"><p>工作表 {{ row.spreadsheet_id }} · gid {{ row.sheet_id }} · 第 {{ row.row }} 行</p><h4>写入前</h4><pre>{{ display(row.before) }}</pre><h4>写入后</h4><pre>{{ display(row.after) }}</pre></div></template></el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="180" /><el-table-column prop="ref" label="收款编号" min-width="150" /><el-table-column prop="status" label="状态" min-width="110" /><el-table-column prop="message" label="说明" min-width="240" />
      </el-table>
    </el-dialog>
    <details class="file-backup">
      <summary>本地文件验证 / 导出<span>日常使用在线流水，此处保留备用</span></summary>
    <div class="toolbar">
      <label>台账币种<el-select v-model="currency" aria-label="台账币种" :disabled="busy"><el-option v-for="item in currencies" :key="item" :label="item" :value="item" /></el-select></label>
      <label>工作表<el-input v-model="sheet" aria-label="工作表" placeholder="自动选择 Bank Transactions" :disabled="busy" list="receipt-ledger-sheets" /></label>
      <datalist id="receipt-ledger-sheets"><option v-for="item in result?.ledger?.available_sheets || []" :key="item" :value="item" /></datalist>
      <input ref="fileInput" class="file-input" type="file" accept=".xlsx,.xls,.csv" aria-label="选择收款台账" @change="importFile" />
      <el-button :icon="Upload" :loading="operation === 'import'" :disabled="busy" @click="fileInput?.click()">导入台账</el-button>
      <el-button :icon="Download" :loading="operation === 'export'" :disabled="busy || !result?.ledger" @click="exportFile">导出匹配建议</el-button>
    </div>
    <p class="source" v-if="result?.ledger && !result.ledger.online">{{ result.ledger.name }} · {{ result.ledger.sheet || 'CSV' }} · {{ result.ledger.entries_count }} 条收款</p>
    <p class="source" v-if="!result?.ledger">导入 xlsx、xls 或 csv 台账，最大 25MB</p>
    </details>
    <CustomerMapping :disabled="busy" @busy-change="mappingBusy=$event" @changed="mappingChanged" />
    <section class="review-workspace" aria-label="收款查询与列表">
      <header class="workspace-heading"><div><h2>收款核对</h2></div><span v-if="result?.ledger?.skipped" class="skipped-note">{{ result.ledger.skipped }} 行无有效日期或金额，已跳过</span></header>
      <div v-if="result?.ledger" class="overview" role="status">
        <div><span>当前收款范围</span><strong>{{ result.summary.eligible.toLocaleString() }}</strong></div>
        <div class="has-clues"><span>有邮件线索</span><strong>{{ result.summary.matched.toLocaleString() }}</strong></div>
        <div><span>暂无邮件线索</span><strong>{{ result.summary.unmatched.toLocaleString() }}</strong></div>
        <div><span>已确认关联</span><strong>{{ result.summary.confirmed.toLocaleString() }}</strong></div>
      </div>
    <form class="query-bar" @submit.prevent="applySearch">
      <label class="query-keyword">查找收款<el-input v-model="keywordDraft" clearable :prefix-icon="Search" maxlength="200" placeholder="流水号、付款人或银行摘要" aria-label="搜索流水号付款人或摘要" :disabled="mutating" @clear="applySearch" /></label>
      <label>收款范围<el-select v-model="scope" aria-label="收款状态" :disabled="mutating"><el-option label="未识别收款" value="unidentified" /><el-option label="未结清收款" value="unclosed" /><el-option label="全部收款" value="all" /></el-select></label>
      <label>匹配分数<el-select v-model="minimumScore" aria-label="最低匹配分数" :disabled="mutating"><el-option label="全部分数" :value="0" /><el-option label="70 分及以上" :value="70" /><el-option label="90 分及以上" :value="90" /><el-option v-if="![0,70,90].includes(minimumScore)" :label="`${minimumScore} 分及以上`" :value="minimumScore" /></el-select></label>
      <el-button native-type="submit" type="primary" :icon="Search" :loading="operation === 'load'" :disabled="mutating">查询</el-button>
      <el-button text :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">{{ advancedOpen ? '收起条件' : '更多条件' }}<el-icon><ArrowDown /></el-icon></el-button>
    </form>
    <div v-if="advancedOpen" class="advanced-filters">
      <label>付款日期允许相差<el-select v-model="dayWindow" aria-label="日期容差天数" :disabled="mutating"><el-option v-for="days in 7" :key="days" :label="`前后 ${days} 天`" :value="days" /></el-select></label>
      <label>排列顺序<el-select v-model="sortBy" aria-label="排列顺序" :disabled="mutating"><el-option label="匹配分从高到低" value="score" /><el-option label="流水原始顺序" value="source" /></el-select></label>
      <el-button text :disabled="mutating" @click="resetFilters">重置条件</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="results-caption"><strong>{{ result?.total_rows.toLocaleString() || 0 }} 笔筛选结果</strong><span>{{ sortBy === 'score' ? '按匹配分从高到低排列' : '按流水原始顺序排列' }} · 评分仅供核对参考</span><el-button text :icon="Refresh" :loading="operation === 'load'" :disabled="busy" @click="refresh()">刷新结果</el-button></div>
    <div class="table-scroll">
      <el-table :data="result?.rows || []" row-key="receipt.id" :max-height="550" highlight-current-row :current-row-key="selectedId" :empty-text="busy ? '正在加载收款…' : result?.ledger ? '没有符合条件的收款，可降低分数或调整范围' : '请先读取在线流水或导入台账'" @row-click="selectRow">
        <el-table-column label="银行流水" min-width="185"><template #default="{row}"><strong class="row-reference">{{ row.receipt.ref || '未提供参考号' }}</strong><small class="row-secondary">{{ row.receipt.date }}</small></template></el-table-column>
        <el-table-column label="收款金额" min-width="165"><template #default="{ row }"><strong class="row-amount">{{ money(row.receipt.amount, row.receipt.currency) }}</strong></template></el-table-column>
        <el-table-column label="付款人" min-width="180"><template #default="{row}">{{ row.receipt.payer || '未提供付款人' }}</template></el-table-column>
        <el-table-column prop="receipt.status" label="收款状态" min-width="120" />
        <el-table-column label="匹配分" width="100"><template #default="{ row }"><span v-if="row.candidates.length" class="score-pill" :class="scoreClass(row.candidates[0].score)">{{ row.candidates[0].score }}<small>分</small></span><span v-else class="row-secondary">—</span><small v-if="hasTie(row)" class="warning row-secondary">存在同分候选</small></template></el-table-column>
        <el-table-column label="候选凭证" width="100"><template #default="{row}">{{ row.candidate_count }} 条</template></el-table-column>
        <el-table-column label="操作" width="125" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="selectRow(row)">{{ row.review.state === 'confirmed' ? '查看已确认' : '核对邮件' }}</el-button><small class="row-secondary">{{ reviewLabels[row.review.state as ReviewState] }}</small></template></el-table-column>
      </el-table>
    </div>
    <div class="pagination"><el-pagination :current-page="page" :page-size="50" :total="result?.total_rows || 0" layout="prev, pager, next" :pager-count="5" :disabled="mutating" @current-change="changePage" /></div>
    </section>
    <el-drawer v-model="reviewOpen" :with-header="false" size="min(1180px, 96vw)" class="receipt-review-drawer" :close-on-click-modal="!mutating" :close-on-press-escape="!mutating" destroy-on-close>
      <ReceiptReview v-if="selected" :row="selected" :busy="mutating" :stale="stale" :expanded="expanded" :preferred-mail-id="preferredMailId" :preferred-payment-id="preferredPaymentId" @close="reviewOpen=false" @expand="expandCandidates" @review="review" @open-mail="openMail" />
      <el-empty v-else description="该收款不在当前结果中，请重新选择"><el-button @click="reviewOpen=false">返回列表</el-button></el-empty>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { Download, Refresh, Upload, Search, ArrowDown } from '@element-plus/icons-vue'
import ReceiptReview from './ReceiptReview.vue'
import CustomerMapping from './CustomerMapping.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { mailMatchingApi, matchingError, validateSpreadsheetUrl, type OnlineConfig, type OnlineHistoryItem, type OnlineWriteback, type MatchingCandidate, type MatchingResult, type MatchingRow, type MatchingScope, type ReviewState } from '../services/mailMatching'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })
const emit = defineEmits<{ 'open-mail': [mailId: string] }>()
const root = ref<HTMLElement>(), fileInput = ref<HTMLInputElement>()
const currency = ref('IDR'), sheet = ref(''), scope = ref<MatchingScope>('unidentified')
const currencies = ['IDR', 'USD', 'CNY', 'SGD', 'EUR', 'HKD', 'MYR', 'THB', 'JPY', 'AUD', 'GBP']
const dayWindow = ref(3), minimumScore = ref(0), page = ref(1), selectedId = ref(''), expanded = ref(false)
const keywordDraft=ref(''), keyword=ref(''), advancedOpen=ref(false), reviewOpen=ref(false), sortBy=ref<'source'|'score'>('score')
const pendingReceiptId=ref(''),preferredMailId=ref(''),preferredPaymentId=ref('')
const result = ref<MatchingResult>(), error = ref(''), operation = ref(''), stale = ref(true)
const defaultSheetUrl = ''
const onlineConfig = ref<OnlineConfig>(), onlineError = ref('')
const configOpen = ref(false), configUrl = ref(defaultSheetUrl), configCurrency = ref('IDR')
const credentialInput = ref<HTMLInputElement>(), credential = ref<File>()
const historyOpen = ref(false), history = ref<OnlineHistoryItem[]>([])
const writebackResult = ref<OnlineWriteback>()
const writebackIssues = computed(() => writebackResult.value?.items.filter(item => !['written', 'unchanged'].includes(item.status)) || [])
const mappingBusy=ref(false)
const busy = computed(() => !!operation.value || mappingBusy.value)
const mutating = computed(() => mappingBusy.value || (!!operation.value && operation.value !== 'load'))
const selected = computed(() => result.value?.rows.find(row => row.receipt.id === selectedId.value))
const reviewLabels: Record<ReviewState, string> = { pending: '待核对', confirmed: '已确认', rejected: '暂不关联' }
let sequence = 0, alive = true, activated = true, visible = true, pendingRefresh = false
let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
let observer: IntersectionObserver | undefined
const isActive = () => alive && activated && visible && props.active && !document.hidden
const query = () => ({ scope: scope.value, page: page.value, page_size: 50, minimum_score: minimumScore.value ?? 0, day_window: dayWindow.value ?? 3, candidate_limit: expanded.value ? 20 : 3, keyword:keyword.value,sort_by:sortBy.value })
function schedule() {
  clearTimeout(timer)
  if (isActive()) timer = setTimeout(() => { void refresh() }, 5000)
}
// A single request lane serializes polling, imports, exports and review writes.
async function run(kind: string, work: (signal: AbortSignal, token: number) => Promise<void>) {
  if (busy.value || !alive) return
  clearTimeout(timer)
  operation.value = kind
  controller = new AbortController()
  const token = ++sequence
  try { await work(controller.signal, token) }
  catch (cause) { if (alive && token === sequence && !controller.signal.aborted) error.value = matchingError(cause) }
  finally {
    if (alive) {
      operation.value = ''
      if (pendingRefresh && isActive()) { pendingRefresh = false; void refresh() }
      else schedule()
    }
  }
}
function mappingChanged(){stale.value=true;void refresh()}
async function refresh() {
  if (!isActive()) return
  if (busy.value) { pendingRefresh = true; return }
  const params = query()
  await run('load', async (signal, token) => {
    if (!onlineConfig.value) {
      try { onlineConfig.value = await mailMatchingApi.onlineConfig(signal) }
      catch (cause) { if (!signal.aborted) onlineError.value = matchingError(cause) }
    }
    const data = await mailMatchingApi.matching(params, signal)
    if (!alive || token !== sequence) return
    const sameLedger = !result.value || result.value.ledger?.id === data.ledger?.id
    result.value = data
    if (!sameLedger) { selectedId.value = ''; expanded.value = false }
    if (selectedId.value && !data.rows.some(row => row.receipt.id === selectedId.value)) selectedId.value = ''
    stale.value = false
    error.value = ''
    if(pendingReceiptId.value){
      const target=data.rows.find(row=>row.receipt.id===pendingReceiptId.value)
      if(target){selectedId.value=target.receipt.id;reviewOpen.value=true}
      else error.value='这笔流水已变化或不在当前台账中，请重新读取并查询。'
      pendingReceiptId.value=''
    }
    const lastPage = Math.max(1, Math.ceil(data.total_rows / 50))
    if (page.value > lastPage) { page.value = lastPage; stale.value = true; pendingRefresh = true }
  })
}
function invalidate() {
  stale.value = true
  ++sequence
  if (operation.value === 'load') controller?.abort()
  pendingRefresh = true
  if (!busy.value) { pendingRefresh = false; void refresh() }
}
watch([scope, dayWindow, minimumScore,keyword,sortBy], () => { page.value = 1; expanded.value = false; invalidate() })
function applySearch(){if(mutating.value)return;if(keyword.value===keywordDraft.value.trim())void refresh();else keyword.value=keywordDraft.value.trim()}
function resetFilters(){keywordDraft.value='';keyword.value='';scope.value='unidentified';minimumScore.value=0;dayWindow.value=3;sortBy.value='score'}
function changePage(value: number) { page.value = value; expanded.value = false; invalidate() }
function selectRow(row: MatchingRow) {
  if (mutating.value) return
  reviewOpen.value=true
  preferredMailId.value=''
  preferredPaymentId.value=''
  if(selectedId.value===row.receipt.id)return
  selectedId.value = row.receipt.id
  if (expanded.value) { expanded.value = false; invalidate() }
}
function expandCandidates() { expanded.value = true; invalidate() }
async function openReceipt(id:string,reference:string,mailId='',paymentId=''){
  if(mutating.value)throw new Error('请等待当前操作完成后再打开流水。')
  pendingReceiptId.value=id;preferredMailId.value=mailId;preferredPaymentId.value=paymentId;selectedId.value='';reviewOpen.value=false
  keywordDraft.value=reference;keyword.value=reference;scope.value='all';minimumScore.value=0;page.value=1
  await nextTick()
  expanded.value=!!mailId
  invalidate()
}
function clearCredential() { credential.value = undefined; if (credentialInput.value) credentialInput.value.value = '' }
function chooseCredential(event: Event) {
  credential.value = (event.target as HTMLInputElement).files?.[0]
  if (credential.value && !/\.json$/i.test(credential.value.name)) { clearCredential(); onlineError.value = '请选择服务账号 JSON 文件' }
}
async function onlineWork(kind: string, work: (signal: AbortSignal, token: number) => Promise<void>) {
  await run(kind, async (signal, token) => {
    onlineError.value = ''
    try { await work(signal, token) }
    catch (cause) {
      // Config errors must not echo credential content returned by a server.
      if (alive && token === sequence && !signal.aborted) onlineError.value = kind === 'online-config' ? '保存失败，请检查工作表链接、JSON 凭据及服务配置后重试。' : matchingError(cause)
    }
  })
}
async function openConfig() {
  if (busy.value) return
  clearCredential()
  configUrl.value = onlineConfig.value?.spreadsheet_url || defaultSheetUrl
  configCurrency.value = onlineConfig.value?.currency || 'IDR'
  onlineError.value = ''; configOpen.value = true
}
async function saveConfig() {
  if (busy.value) return
  try { validateSpreadsheetUrl(configUrl.value) } catch (cause) { onlineError.value = matchingError(cause); return }
  await onlineWork('online-config', async (signal, token) => {
    try {
      const config = await mailMatchingApi.saveOnlineConfig(configUrl.value, configCurrency.value, credential.value, signal)
      if (!alive || token !== sequence) return
      onlineConfig.value = config
      ElMessage.success('配置已保存，请确认服务账号拥有工作表编辑权限')
    } finally { clearCredential() }
  })
}
async function readOnline() {
  if (busy.value || !onlineConfig.value?.configured) return
  await onlineWork('online-read', async (signal, token) => {
    stale.value = true
    try {
      const ledger = await mailMatchingApi.refreshOnline(signal)
      if (!alive || token !== sequence) return
      selectedId.value = ''; expanded.value = false; page.value = 1; writebackResult.value = undefined
      result.value = { ledger, rows: [], summary: { total: 0, eligible: 0, matched: 0, unmatched: 0, confirmed: 0 }, revision: '', page: 1, total_rows: 0 }
      error.value = ''
      onlineConfig.value = await mailMatchingApi.onlineConfig(signal)
      ElMessage.success('在线流水已读取，正在重新加载匹配')
    } finally { stale.value = true; pendingRefresh = true }
  })
}
async function writeOnline() {
  if (busy.value || stale.value || !onlineConfig.value?.configured || !result.value?.ledger?.online) return
  await onlineWork('online-write', async (signal, token) => {
    const snapshot = result.value!, ledgerId = snapshot.ledger!.id, window = dayWindow.value ?? 3
    const fresh = await mailMatchingApi.matching({ ...query(), scope: 'all', page: 1, minimum_score: 0 }, signal)
    if (!alive || token !== sequence) return
    if (fresh.ledger?.id !== ledgerId || fresh.revision !== snapshot.revision || !fresh.ledger.online) {
      stale.value = true; pendingRefresh = true
      throw new Error('匹配结果已更新，请刷新核对后再回写')
    }
    if (!fresh.summary.confirmed || !fresh.confirmation_token) throw new Error('当前没有已确认关联可回写')
    try {
      await ElMessageBox.confirm(`将回写当前在线流水的 ${fresh.summary.confirmed} 条已确认关联（包含其他页面及筛选范围），保留原备注和核销状态。冲突会跳过并记录原因。回写期间请勿排序或增删在线表行。是否继续？`, '确认回写在线工作表', { type: 'warning', confirmButtonText: '确认回写', cancelButtonText: '取消' })
    } catch { return }
    if (!alive || token !== sequence || signal.aborted) return
    try {
      writebackResult.value = undefined
      writebackResult.value = await mailMatchingApi.writebackOnline({ ledger_id: ledgerId, day_window: window, expected_count: fresh.summary.confirmed, confirmation_token: fresh.confirmation_token }, signal)
      error.value = ''
      ElMessage.info('回写已完成，请查看逐行结果与同步记录')
    } catch (cause) {
      throw new Error(`回写结果未确认，请先读取在线流水并查看同步记录，避免直接重试。${matchingError(cause)}`)
    } finally { stale.value = true; pendingRefresh = true }
  })
}
async function openHistory() {
  if (busy.value) return
  historyOpen.value = true
  await onlineWork('online-history', async signal => { history.value = (await mailMatchingApi.onlineHistory(signal)).items })
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  input.value = ''
  if (!file || busy.value) return
  await run('import', async (signal, token) => {
    const ledger = await mailMatchingApi.importLedger(file, currency.value, sheet.value, signal)
    if (!alive || token !== sequence) return
    selectedId.value = ''; expanded.value = false; page.value = 1; stale.value = true
    // Never allow a candidate from the previous ledger to remain actionable.
    result.value = { ledger, rows: [], summary: { total: 0, eligible: 0, matched: 0, unmatched: 0, confirmed: 0 }, revision: '', page: 1, total_rows: 0 }
    error.value = ''; pendingRefresh = true
    ElMessage.success('台账已导入')
  })
}
async function review(state: ReviewState, candidate?: MatchingCandidate) {
  const row = selected.value, snapshot = result.value
  if (busy.value || stale.value || !row || !snapshot?.ledger) return
  if (state === 'confirmed' && (!candidate?.payment_id || !row.candidates.includes(candidate))) return
  const receiptId = row.receipt.id, ledgerId = snapshot.ledger.id
  await run('review', async (signal, token) => {
    // Revalidate the selected page immediately before writing, including its revision.
    const fresh = await mailMatchingApi.matching(query(), signal)
    if (!alive || token !== sequence) return
    const freshRow = fresh.rows.find(item => item.receipt.id === receiptId)
    if (fresh.ledger?.id !== ledgerId || !freshRow ||
      (candidate && !freshRow.candidates.some(item => item.payment_id === candidate.payment_id && item.evidence_signature === candidate.evidence_signature))) {
      stale.value = true; pendingRefresh = true
      throw new Error('匹配结果已更新，请刷新后重新核对')
    }
    try {
      await mailMatchingApi.review({ ledger_id: ledgerId, receipt_id: receiptId, payment_id: state === 'confirmed' ? candidate!.payment_id : null, state, day_window: dayWindow.value ?? 3, expected_signature: candidate?.evidence_signature }, signal)
    } finally {
      // A timed-out write may have reached the server; always re-read before another write.
      stale.value = true; pendingRefresh = true
    }
    if (!alive || token !== sequence) return
    stale.value = true; pendingRefresh = true; error.value = ''
    ElMessage.success(state === 'confirmed' ? '已确认关联' : state === 'rejected' ? '已标记暂不关联' : '已重置核对')
  })
}
async function exportFile() {
  await run('export', async (signal, token) => {
    const blob = await mailMatchingApi.export(query(), signal)
    if (!alive || token !== sequence) return
    const url = URL.createObjectURL(blob), link = document.createElement('a')
    link.href = url; link.download = '收款匹配建议.xlsx'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    error.value = ''
  })
}
function openMail(mailId: string) { if (mailId) {reviewOpen.value=false;emit('open-mail', mailId)} }
function scoreClass(score:number){return score>=90?'score-high':score>=70?'score-medium':'score-low'}
function readableTime(value:string){const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}
function hasTie(row: MatchingRow) { return row.candidates.length > 1 && row.candidates.filter(item => item.score === row.candidates[0]?.score).length > 1 }
function display(value: unknown): string { return value == null || value === '' ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function money(value: number | string | null, unit: string) {
  if (value == null || value === '') return '金额未提供'
  const number = Number(value)
  return `${unit || '币种未提供'} ${Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) : value}`
}
function visibilityChanged() { if (isActive()) void refresh(); else clearTimeout(timer) }
watch(() => props.active, visibilityChanged)
onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  if (root.value && typeof IntersectionObserver !== 'undefined') {
    observer = new IntersectionObserver(entries => { visible = !!entries[0]?.isIntersecting; visibilityChanged() })
    observer.observe(root.value)
  }
  void refresh()
})
onActivated(() => { activated = true; visibilityChanged() })
onDeactivated(() => { activated = false; clearTimeout(timer); if (operation.value === 'load') { ++sequence; controller?.abort() } })
onBeforeUnmount(() => { clearCredential(); alive = false; ++sequence; controller?.abort(); clearTimeout(timer); observer?.disconnect(); document.removeEventListener('visibilitychange', visibilityChanged) })
defineExpose({ refresh, openMail, openReceipt })
</script>

<style scoped>
.receipt-matching{min-width:0;max-width:100%;color:var(--el-text-color-primary)}
.online-panel{padding:18px 20px;margin-bottom:12px;border:1px solid var(--border);border-radius:10px;background:#fff;display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:20px;align-items:center}
.online-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.online-heading h3{margin:0;font-size:15px;font-weight:600}.online-panel>.source{grid-column:1;grid-row:2;margin:7px 0 0}.online-panel>.actions{grid-column:2;grid-row:1/3}.online-panel>.el-alert,.online-panel>.summary,.online-panel>.el-table{grid-column:1/-1}
.history-details{padding:16px;overflow-wrap:anywhere}.history-details pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.file-backup{margin:4px 0 12px;padding:6px 4px;color:var(--ink-muted);font-size:13px}.file-backup summary{cursor:pointer;min-height:24px;line-height:24px}.file-backup summary span{margin-left:14px;font-size:12px;color:#85858b}.file-backup[open] .toolbar{padding-top:16px}
.toolbar{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;margin-bottom:12px}.toolbar label{display:flex;flex-direction:column;gap:8px;min-width:0;font-size:13px;color:var(--ink-muted)}
.toolbar .el-select{width:160px;max-width:100%}.toolbar .el-input{width:260px;max-width:100%}.toolbar .el-button,.actions .el-button{margin:0}.file-input{display:none}
.source,.summary{font-size:13px;color:var(--ink-muted);line-height:1.7;overflow-wrap:anywhere}.actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.review-workspace{background:white;border:1px solid var(--border);border-radius:12px;padding:24px;min-width:0}.workspace-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.workspace-heading h2{font-size:20px;margin:0;font-weight:650;letter-spacing:-.4px}.workspace-heading p{margin:6px 0 0;font-size:13px;color:var(--ink-muted)}.skipped-note{font-size:12px;color:var(--el-color-warning);text-align:right}
.overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:18px 0;padding:0 0 16px;border-bottom:1px solid var(--border)}.overview>div{display:flex;flex-direction:column;gap:6px;padding-left:24px;border-left:1px solid var(--border)}.overview>div:first-child{padding-left:0;border:0}.overview span{font-size:12px;color:var(--ink-muted)}.overview strong{font-size:24px;line-height:1.1;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.5px}.overview .has-clues strong{color:var(--el-color-primary)}
.query-bar{display:grid;grid-template-columns:minmax(220px,1fr) 170px 160px auto auto;gap:12px;align-items:end}.query-bar label,.advanced-filters label{display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--ink-muted);min-width:0}.query-bar :deep(.el-input__wrapper),.query-bar :deep(.el-select__wrapper){min-height:40px}.query-bar>.el-button{height:40px;margin:0}.query-bar .el-select{width:100%}.query-bar .el-icon{margin-left:5px}.advanced-filters{display:flex;flex-wrap:wrap;align-items:end;gap:16px;padding:16px;background:#f7f7f9;border-radius:8px;margin-top:14px}.advanced-filters .el-select{width:170px}
.results-caption{display:flex;align-items:center;gap:12px;margin:18px 0 12px;font-size:12px;color:var(--ink-muted)}.results-caption>strong{color:var(--el-text-color-primary);font-size:13px;font-weight:600}.results-caption>.el-button{margin-left:auto}.table-scroll{min-width:0;max-width:100%;overflow-x:auto}.table-scroll :deep(.el-table){width:100%;--el-table-header-bg-color:#f7f7f9;font-size:13px}.table-scroll :deep(th.el-table__cell){font-size:12px;font-weight:500;height:42px}.table-scroll :deep(td.el-table__cell){padding:12px 0}.table-scroll :deep(.el-table__row){cursor:pointer}.table-scroll :deep(.cell){overflow-wrap:anywhere;line-height:1.5}.row-reference{font-weight:500;letter-spacing:.1px;font-variant-numeric:tabular-nums}.row-secondary{display:block;margin-top:4px;color:var(--ink-muted);font-size:11px;font-weight:400}.row-amount{font-weight:600;font-variant-numeric:tabular-nums}.score-pill{display:inline-flex;gap:3px;align-items:baseline;padding:4px 9px;border-radius:6px;font-weight:650;font-size:16px;font-variant-numeric:tabular-nums}.score-pill small{font-size:10px;font-weight:400}.score-high{background:#edf6f0;color:#326b4b}.score-medium{background:#fff7e8;color:#946000}.score-low{background:#f1f1f4;color:#636368}.warning{color:var(--el-color-warning)}.pagination{display:flex;justify-content:flex-end;overflow-x:auto;padding:18px 0 0}.receipt-matching :deep(.el-alert){margin:12px 0;overflow-wrap:anywhere}
:global(.receipt-review-drawer .el-drawer__body){padding:0;overflow:hidden}
@media(max-width:1100px){.online-panel{grid-template-columns:minmax(0,1fr)}.online-panel>.actions{grid-column:1;grid-row:3;margin-top:12px}.query-bar{grid-template-columns:minmax(180px,1fr) 150px 140px auto}.query-bar>.el-button:last-child{grid-column:4;grid-row:2}}
@media(max-width:700px){.review-workspace{padding:16px}.online-panel{padding:16px}.workspace-heading{align-items:flex-start;flex-direction:column;gap:8px}.skipped-note{text-align:left}.overview{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:20px 0}.overview>div:nth-child(3){border:0;padding-left:0}.overview strong{font-size:24px}.query-bar{grid-template-columns:repeat(2,minmax(0,1fr))}.query-keyword{grid-column:1/-1}.query-bar>.el-button:last-child{grid-column:2;grid-row:auto}.results-caption{flex-wrap:wrap;gap:6px}.results-caption>span{width:100%;order:3}.toolbar label{width:100%}.toolbar .el-select,.toolbar .el-input{width:100%}.file-backup summary span{display:block;margin-left:14px}.advanced-filters label{width:100%}.advanced-filters .el-select{width:100%}}
</style>
