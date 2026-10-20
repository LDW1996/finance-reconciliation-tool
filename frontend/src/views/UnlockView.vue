<template>
  <main class="unlock-shell">
    <header class="unlock-heading">
      <h1>{{ {accounts:'账号管理',history:'解锁历史',retention:'保存设置',unlock:'批量解锁'}[view] }}</h1>
      <el-button v-if="view === 'accounts'" type="primary" :icon="Plus" :disabled="busy" @click="openAccount()">添加账号</el-button>
      <el-button v-if="view === 'history'" :icon="RefreshRight" :loading="historyLoading" @click="loadHistory">刷新</el-button>
      <el-tag v-if="view === 'unlock' && !runtimeAvailable" type="warning">执行环境未就绪</el-tag>
      <el-button v-if="view === 'unlock'" :icon="Plus" :disabled="busy || controlling || !taskSyncReady" :loading="resetting" @click="newTask">新建任务</el-button>
    </header>

    <template v-if="view === 'unlock'">
      <div class="unlock-account-strip">
        <div class="unlock-account-field">
        <div class="unlock-account-label"><span><el-icon><User /></el-icon>首选账号</span></div>
        <el-select v-model="accountId" aria-label="首选账号" :disabled="busy" placeholder="选择账号">
          <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <span class="account-caption">{{ selectedAccount?.username || '请先添加业务账号' }}</span>
        </div>
        <div class="unlock-account-field">
        <div class="unlock-account-label"><span>备用账号</span><el-switch v-model="fallbackEnabled" :disabled="busy" aria-label="启用备用账号"/></div>
        <el-select v-model="selectedFallbackId" :disabled="busy || !fallbackEnabled || !fallbackCandidates.length" aria-label="备用账号" placeholder="没有可用备用账号">
          <el-option v-for="a in fallbackCandidates" :key="a.id" :label="a.name" :value="a.id"/>
        </el-select>
        <span class="account-caption" :class="{ 'account-warning': fallbackEnabled && !fallbackCandidates.length }">{{ !fallbackEnabled ? '未启用，仅使用首选账号' : fallbackCandidates.length ? '仅首选账号查无订单时使用' : '没有可用备用账号，仅使用首选账号' }}</span>
        </div>
      </div>
      <div class="unlock-layout">
        <aside class="unlock-inputs">
          <section v-for="kind in kinds" :key="kind.key" class="unlock-input-panel">
            <div class="unlock-section-heading"><h2><el-icon><Unlock /></el-icon>{{ kind.title }}</h2><span class="count-pill">{{ parsed[kind.key].length }} 条</span></div>
            <label :for="`bills-${kind.key}`">每行一个提单号，也支持逗号、空格分隔</label>
            <textarea :id="`bills-${kind.key}`" v-model="input[kind.key]" :disabled="busy" spellcheck="false" :placeholder="kind.placeholder" />
            <div class="input-caption"><span>{{ duplicateCount(kind.key) ? `已合并 ${duplicateCount(kind.key)} 条重复单号` : '自动去空行、合并同类重复单号' }}</span><button :disabled="busy || !input[kind.key]" @click="input[kind.key] = ''">清空</button></div>
            <div v-if="duplicates[kind.key].length" class="duplicate-bills" role="status">
              <strong>重复单号（每个仅保留一次）</strong>
              <ul><li v-for="item in duplicates[kind.key]" :key="item.bill"><code>{{ item.bill }}</code><span>出现 {{ item.count }} 次 · 重复 {{ item.count - 1 }} 次</span></li></ul>
            </div>
            <el-text v-if="inspections[kind.key].some(r => r.status === 'invalid')" type="danger">有格式异常的单号，请在核对清单中查看并返回修改。</el-text>
          </section>
          <div class="unlock-input-actions"><el-button class="unlock-start" size="large" type="primary" :disabled="busy || resetting || !taskSyncReady || !reviewRows.length || !selectedAccount" :icon="VideoPlay" @click="openReview">校验单号并核对清单</el-button></div>
          <p v-if="!runtimeAvailable" class="demo-note">{{ runtimeMessage }}</p>
        </aside>

        <section class="unlock-execution">
          <section class="unlock-progress-panel">
            <div class="unlock-section-heading"><h2>执行进度</h2><div v-if="taskState === 'running' || taskState === 'paused'"><el-button :disabled="controlling" :icon="paused ? VideoPlay : VideoPause" @click="controlTask(paused ? 'resume' : 'pause')">{{ paused ? '继续剩余项' : '暂停后续项' }}</el-button><el-button :disabled="controlling" @click="cancelRemaining">取消剩余项</el-button></div><el-tag v-else effect="plain" :type="attentionCount ? 'warning' : rows.length ? 'success' : 'info'">{{ rows.length ? '任务已结束' : '等待开始' }}</el-tag></div>
            <div class="progress-number">{{ done }}<span>/ {{ rows.length || total }}</span><small>{{ paused ? '已暂停后续项' : taskState === 'running' ? '真实处理中' : rows.length ? '条已处理' : '条待处理' }}</small></div>
            <el-progress :percentage="percentage" :show-text="false" :stroke-width="12" color="var(--accent)" />
            <div class="progress-description"><span>{{ currentRow ? `正在处理 ${currentRow.bill}` : paused ? '已暂停，继续将跳过待核实项。' : rows.length ? `任务账号：${taskAccountName}` : '等待提交任务' }}</span><span v-if="pollError" class="progress-tip" role="alert"><el-icon><InfoFilled /></el-icon>{{ pollError }}</span></div>
          </section>
          <section class="unlock-results-panel">
            <div class="unlock-section-heading"><h2>执行结果</h2><el-select v-model="filter" aria-label="筛选结果" style="width:150px" size="small"><el-option label="全部结果" value="all"/><el-option label="核实通过" value="success"/><el-option label="待人工处理" value="attention"/><el-option label="等待处理" value="waiting"/><el-option label="已取消" value="cancelled"/></el-select></div>
            <el-alert v-if="taskState === 'completed' && attentionCount" class="unlock-attention-summary" type="warning" :closable="false" :title="`本批已结束，${attentionCount} 项需人工核实，未自动重提解锁。`"><template #default><el-button link type="primary" @click="filter = 'attention'">只看待处理项</el-button></template></el-alert>
            <div v-if="!rows.length" class="unlock-empty"><el-icon><List /></el-icon><h3>逐笔结果将在这里显示</h3><p>平台响应、复查状态与异常原因都会保留。</p></div>
            <el-table v-else :data="filteredRows" height="420" empty-text="没有符合条件的结果" :row-class-name="rowClassName">
              <el-table-column type="index" label="#" width="44"/>
              <el-table-column prop="bill" label="提单号" min-width="185"/>
              <el-table-column label="类型" min-width="100"><template #default="{ row }">{{ row.kind === 'fee' ? '费用解锁' : '订单解锁' }}</template></el-table-column>
              <el-table-column label="实际使用账号" min-width="180"><template #default="{ row }">{{ row.actualAccountName || '—' }}<el-tag v-if="(row.attempts?.length || 0) > 1" size="small" type="info">兜底</el-tag><div v-for="(attempt,i) in row.attempts || []" :key="i" style="font-size:12px;color:var(--ink-muted)">{{ attempt.accountName }}：{{ attemptText(attempt.status) }}</div></template></el-table-column>
              <el-table-column label="结果" min-width="125"><template #default="{ row }"><el-tag :type="statusStyle(row.status)" size="small" round>{{ statusText(row.status,row.detail) }}</el-tag></template></el-table-column>
              <el-table-column label="平台响应" min-width="120"><template #default="{ row }">{{ row.detail?.platform_success === true ? '明确成功' : row.detail?.platform_success === false ? '明确拒绝' : '—' }}</template></el-table-column>
              <el-table-column label="锁状态复查" min-width="120"><template #default="{ row }">{{ row.detail?.before?.lock || '—' }} → {{ row.detail?.after?.lock || '—' }}</template></el-table-column>
              <el-table-column prop="message" label="说明" min-width="180"/>
              <el-table-column prop="time" label="处理时间" width="100"/>
              <el-table-column label="凭证" width="110"><template #default="{ row }"><el-link v-if="row.detail?.screenshot" :href="`/api/ilog/tasks/${encodeURIComponent(taskId)}/evidence/${row.evidenceIndex}`" target="_blank" rel="noopener" type="primary">查看截图</el-link><span v-else>{{ row.detail?.screenshotExpired ? '已过期清理' : '—' }}</span></template></el-table-column>
            </el-table>
            <footer class="unlock-results-footer"><span>{{ rows.length ? `核实通过 ${successCount} 条 · 待核实 ${attentionCount} 条` : '结果可导出为 CSV' }}</span><div><el-button :icon="Download" :disabled="!rows.length" @click="exportResults()">导出结果</el-button><el-button :icon="RefreshRight" :disabled="!taskId" @click="refreshTask">刷新结果</el-button></div></footer>
          </section>
        </section>
      </div>
    </template>

    <section v-else-if="view === 'accounts'" class="management-section">
      <el-table :data="accounts" empty-text="添加第一个业务账号">
        <el-table-column prop="name" label="账号名称" min-width="180"/>
        <el-table-column prop="username" label="用户名" min-width="180"/>
        <el-table-column label="密码状态" min-width="140"><template #default="{ row }"><el-tag :type="row.hasPassword ? 'success' : 'info'">{{ row.hasPassword ? '已保存密码' : '未登记密码' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button link type="primary" :disabled="busy" @click="openAccount(row)">编辑</el-button><el-button link type="danger" :disabled="busy" @click="removeAccount(row)">删除</el-button></template></el-table-column>
      </el-table>
    </section>

    <section v-if="view === 'history'" class="management-section">
      <el-table v-loading="historyLoading" :data="historyItems" empty-text="暂无历史任务">
        <el-table-column label="创建时间" min-width="170"><template #default="{ row }">{{ formatDate(row.createdAt) }}</template></el-table-column>
        <el-table-column prop="accountName" label="主账号" min-width="180"/>
        <el-table-column prop="fallbackAccountName" label="兜底账号" min-width="180"/>
        <el-table-column label="状态" width="100"><template #default="{ row }">{{ {running:'执行中',paused:'已暂停',completed:'已结束'}[row.state as Task['state']] }}</template></el-table-column>
        <el-table-column prop="total" label="总项数" width="80"/>
        <el-table-column prop="attention" label="待核实" width="80"/>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="viewHistory(row.id)">查看</el-button></template></el-table-column>
      </el-table>
      <div class="management-footer"><el-pagination v-model:current-page="historyPage" :page-size="20" :total="historyTotal" layout="prev,pager,next,total" @current-change="loadHistory"/></div>
    </section>
    <el-dialog v-model="historyDetailOpen" append-to-body class="bill-review-dialog" title="历史任务详情" width="min(1100px,94vw)" top="4vh">
      <p>{{ historyTask?.accountName }} · {{ formatDate(historyTask?.createdAt) }}</p>
      <el-table :data="historyTask?.rows || []" max-height="480" empty-text="暂无记录">
        <el-table-column prop="bill" label="单号" min-width="180"/>
        <el-table-column label="类型" width="100"><template #default="{ row }">{{ row.kind==='fee' ? '小锁' : '大锁' }}</template></el-table-column>
        <el-table-column prop="actualAccountName" label="实际账号" min-width="170"/>
        <el-table-column label="结果" min-width="130"><template #default="{ row }">{{ statusText(row.status,row.detail) }}</template></el-table-column>
        <el-table-column label="说明" min-width="240"><template #default="{ row }">{{ cleanMessage(row.message) }}</template></el-table-column>
        <el-table-column label="截图" width="110"><template #default="{ row,$index }"><el-link v-if="row.detail?.screenshot" :href="`/api/ilog/tasks/${historyTask?.id}/evidence/${$index}`" target="_blank" rel="noopener" type="primary">查看截图</el-link><span v-else>{{ row.detail?.screenshotExpired ? '已过期清理' : '无截图' }}</span></template></el-table-column>
      </el-table>
      <template #footer><el-button :icon="Download" :disabled="!historyTask" @click="exportResults(historyTask || undefined)">导出本任务</el-button><el-button @click="historyDetailOpen=false">关闭</el-button></template>
    </el-dialog>
    <section v-if="view === 'retention'" class="management-section retention-settings">
      <h2>记录保存</h2>
      <el-form label-position="top" :disabled="retentionSaving" class="retention-fields">
        <el-form-item label="截图保留天数"><el-input-number v-model="retentionForm.screenshot_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="任务结果保留天数"><el-input-number v-model="retentionForm.record_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="自动清理过期数据"><el-switch v-model="retentionForm.auto_enabled" aria-label="自动清理过期数据"/></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="运行中、暂停中及待核实记录不会自动清理；防重复凭据始终单独保留。" description="自动清理在打开页面、查看历史或新建任务时检查，每 24 小时最多一次；有任务正在执行时延后。清理不会修改 iLog。"/>
      <div class="management-footer"><el-button type="primary" :loading="retentionSaving" @click="saveRetention">保存规则</el-button></div>
      <div class="retention-cleanup"><h2>过期数据清理</h2><el-button :disabled="retentionSaving" @click="cleanupExpired">预览并清理过期数据</el-button></div>
    </section>
    <el-dialog v-model="reviewOpen" class="bill-review-dialog" title="单号校验与二次确认" width="min(900px, 94vw)" top="4vh">
      <el-descriptions :column="1" border><el-descriptions-item label="业务账号">{{ selectedAccount?.name }}</el-descriptions-item><el-descriptions-item label="费用解锁（小锁）">{{ parsed.fee.length }} 条</el-descriptions-item><el-descriptions-item label="订单解锁（大锁）">{{ parsed.order.length }} 条</el-descriptions-item></el-descriptions>
      <el-alert class="review-alert" type="info" :closable="false" :title="fallbackAccount && fallbackEnabled ? `主账号：${selectedAccount?.name}；兜底账号：${fallbackAccount.name}` : '仅使用主账号'" description="主账号查无订单才使用兜底账号；单笔异常记录后继续下一项，多条匹配不解锁。"/>
      <el-alert v-if="overlap.length" class="review-alert" :title="`${overlap.length} 个提单号同时出现在大小锁列表，将分别执行两种真实操作。`" type="warning" :closable="false"/>
      <el-alert class="review-alert" :type="invalidCount ? 'error' : 'info'" :closable="false" :title="`识别 ${reviewRows.length} 项 · 有效 ${total} 项 · 重复 ${reviewRows.filter(r => r.status === 'duplicate').length} 项 · 异常 ${invalidCount} 项`" description="格式校验不代表订单存在。重复项已排除，异常项需返回修改。"/>
      <el-tabs v-model="reviewKind" style="margin-top:16px">
        <el-tab-pane v-for="kind in kinds" :key="kind.key" :name="kind.key" :label="`${kind.title} · ${parsed[kind.key].length} 条`"/>
      </el-tabs>
      <p style="margin:0 0 12px">本页：有效 {{ parsed[reviewKind].length }} 项 · 重复 {{ duplicateCount(reviewKind) }} 项 · 异常 {{ inspections[reviewKind].filter(r => r.status === 'invalid').length }} 项</p>
      <el-table :key="reviewKind" :data="inspections[reviewKind]" max-height="360" border empty-text="此类型暂无单号">
        <el-table-column type="index" label="#" width="50"/>
        <el-table-column prop="raw" label="粘贴原文" min-width="170"/>
        <el-table-column label="识别单号" min-width="170"><template #default="{ row }">{{ row.status === 'invalid' ? '—' : row.bill }}</template></el-table-column>
        <el-table-column label="校验结果" min-width="170"><template #default="{ row }"><el-tag :type="row.status === 'invalid' ? 'danger' : row.status === 'duplicate' ? 'warning' : 'success'">{{ row.status === 'invalid' ? '格式异常' : row.status === 'duplicate' ? '重复，已排除' : '格式通过' }}</el-tag><div v-if="row.suggestion" style="margin-top:6px;overflow-wrap:anywhere">建议修改为：{{ row.suggestion }}</div></template></el-table-column>
      </el-table>
      <el-checkbox v-model="reviewAcknowledged" style="margin-top:16px;white-space:normal">我已核对主账号、兜底账号、单号及大小锁类型，并授权本次真实解锁</el-checkbox>
      <el-text v-if="total > 2000" type="danger">每批最多 2000 项，请返回拆分。</el-text>
      <p class="demo-note">将实际修改 iLog 锁状态。单笔异常不阻断后续项，解锁提交不自动重试；只读复查最多重试三次。暂停仅停止后续项。</p>
      <template #footer><el-button :disabled="submitting" @click="reviewOpen = false">返回修改</el-button><el-button type="primary" :loading="submitting" :disabled="busy || !runtimeAvailable || !reviewAcknowledged || !!invalidCount || !total || total > 2000" @click="start">确认解锁 {{ total }} 项</el-button></template>
    </el-dialog>
    <el-dialog v-model="accountOpen" :title="editingId ? '编辑业务账号' : '添加业务账号'" width="460px" :close-on-click-modal="!savingAccount" :close-on-press-escape="!savingAccount" :show-close="!savingAccount" @closed="accountPassword = ''">
      <el-form label-position="top" :disabled="savingAccount" @submit.prevent="saveAccount">
        <el-form-item label="账号名称"><el-input v-model="accountForm.name" name="account-name" maxlength="40" placeholder="例如：印尼业务账号"/></el-form-item>
        <el-form-item label="用户名"><el-input v-model="accountForm.username" name="username" maxlength="100" placeholder="输入 iLog 用户名" autocomplete="username"/></el-form-item>
        <el-form-item :label="editingId ? '新密码' : '密码'"><el-input v-model="accountPassword" name="account-password" :aria-label="editingId ? '新密码' : '密码'" type="password" autocomplete="new-password" show-password :placeholder="editingId ? '留空则保留原密码' : '输入账号密码'"/></el-form-item>
      </el-form>
      <p v-if="editingId" class="demo-note">密码留空则保留原密码。</p>
      <template #footer><el-button :disabled="savingAccount" @click="accountOpen = false">取消</el-button><el-button type="primary" :loading="savingAccount" :disabled="!accountForm.name.trim() || !accountForm.username.trim()" @click="saveAccount">保存账号信息</el-button></template>
    </el-dialog>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, onBeforeUnmount, onMounted, watch } from 'vue'
import axios from 'axios'
import { User, Unlock, VideoPlay, VideoPause, Download, RefreshRight, InfoFilled, List, Plus, ArrowLeft, Clock, Setting } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { downloadBlob } from '../services/api'
import { inspectBills, duplicateBills } from '../services/billValidation.mjs'
type Kind = 'fee' | 'order'
type Status = 'waiting' | 'processing' | 'success' | 'attention' | 'cancelled'
type Detail = { status?: string; platform_success?: boolean; before?: {lock?: string}; after?: {lock?: string}; screenshot?: string; screenshotExpired?: boolean }
type Attempt = { account: string; accountName: string; status: string }
type Row = { bill: string; kind: Kind; status: Status; message: string; time: string; detail?: Detail; evidenceIndex?: number; actualAccountName?: string; attempts?: Attempt[] }
type Task = { id: string; account: string; accountName: string; fallbackAccount?: string; fallbackAccountName?: string; state: 'running' | 'paused' | 'completed'; rows: Row[]; createdAt?: string; completedAt?: string }
type HistoryItem = Omit<Task,'rows'> & {total:number;attention:number}
const historyOpen=ref(false), historyDetailOpen=ref(false), historyLoading=ref(false)
const historyItems=ref<HistoryItem[]>([]), historyTask=ref<Task|null>(null)
const historyPage=ref(1), historyTotal=ref(0)
const retentionOpen=ref(false), retentionSaving=ref(false)
const retentionForm=ref({screenshot_days:90,record_days:365,auto_enabled:true})
const savedRetention=ref('')
const formatDate=(value?:string) => value ? new Date(value).toLocaleString('zh-CN') : '—'
const cleanMessage=(value:string) => value.replace(/<br\s*\/?\s*>/gi,' ').replace(/<[^>]*>/g,'')
async function openHistory() { historyOpen.value=true;historyPage.value=1;await loadHistory() }
async function loadHistory() {
  historyLoading.value=true
  try { const response=await axios.get('/api/ilog/tasks',{params:{limit:20,offset:(historyPage.value-1)*20}});historyItems.value=response.data.items;historyTotal.value=response.data.total }
  catch { ElMessage.error('无法读取历史记录') }
  finally { historyLoading.value=false }
}
async function viewHistory(id:string) {
  try { historyTask.value=(await axios.get<Task>(`/api/ilog/tasks/${encodeURIComponent(id)}`)).data;historyDetailOpen.value=true }
  catch { ElMessage.error('记录不存在或已过期清理') }
}
async function openRetention() {
  try { retentionForm.value=(await axios.get('/api/ilog/retention')).data;savedRetention.value=JSON.stringify(retentionForm.value);retentionOpen.value=true }
  catch { ElMessage.error('无法读取保存规则') }
}
async function saveRetention() {
  retentionSaving.value=true
  try { retentionForm.value=(await axios.put('/api/ilog/retention',retentionForm.value,{headers:accountHeaders})).data;savedRetention.value=JSON.stringify(retentionForm.value);ElMessage.success('保存规则已更新') }
  catch { ElMessage.error('保存失败，请检查天数和本地服务') }
  finally { retentionSaving.value=false }
}
async function cleanupExpired() {
  if(JSON.stringify(retentionForm.value)!==savedRetention.value) { ElMessage.warning('请先保存规则，再预览清理');return }
  retentionSaving.value=true
  try {
    const preview=(await axios.get('/api/ilog/retention/preview')).data
    if(preview.busy) { ElMessage.warning('有任务正在执行，暂不清理');return }
    await ElMessageBox.confirm(`按已保存规则，预计删除 ${preview.tasks} 个历史任务、${preview.screenshots} 张截图、${preview.reports} 份报告。待核实记录和防重复凭据保留。删除不能撤销。`,'确认清理过期数据',{type:'warning',confirmButtonText:'确认清理',cancelButtonText:'取消'})
    const result=(await axios.post('/api/ilog/retention/cleanup',{confirmed:true},{headers:accountHeaders})).data
    if(result.busy) ElMessage.warning('任务已开始，清理已延后')
    else ElMessage.success(`已清理 ${result.tasks} 个任务、${result.screenshots} 张截图、${result.reports} 份报告${result.errors ? `；${result.errors} 个文件未能删除` : ''}`)
    if(historyOpen.value) await loadHistory()
  } catch(error) { if(axios.isAxiosError(error)) ElMessage.error('清理失败，请检查本地服务') }
  finally { retentionSaving.value=false }
}
type Account = { id: string; name: string; username: string; example?: boolean; hasPassword?: boolean }
const props = defineProps<{ view: 'unlock' | 'accounts' | 'history' | 'retention' }>()
watch(() => props.view, (view) => {
  if (view === 'history') void openHistory()
  if (view === 'retention') void openRetention()
}, { immediate: true })
const emit = defineEmits<{ navigate: [value: 'unlock' | 'accounts'] }>()
const storageKey = 'finance-ilog-account-metadata-v1'
const accounts = ref<Account[]>([])
const accountId = ref('')
const accountHeaders = { 'X-Finance-Local': '1' }
onMounted(async () => {
  try {
    const response = await axios.get<Account[]>('/api/ilog/accounts')
    accounts.value = response.data
    const legacy: Account[] = JSON.parse(localStorage.getItem(storageKey) || '[]')
    if (Array.isArray(legacy)) {
      for (const account of legacy) {
        if (account.example || !account.name || !account.username || accounts.value.some(a => a.username === account.username)) continue
        const saved = await axios.post<Account>('/api/ilog/accounts', { name: account.name, username: account.username }, { headers: accountHeaders })
        accounts.value.push(saved.data)
      }
    }
    localStorage.removeItem(storageKey)
  } catch { ElMessage.warning('未能读取本机已登记账号，请检查本地服务。') }
  finally { accountId.value = accounts.value[0]?.id || '' }
  try {
    const runtime = await axios.get('/api/ilog/runtime')
    runtimeAvailable.value = runtime.data.available
    runtimeMessage.value = runtime.data.message
    const latest = await axios.get<Task | null>('/api/ilog/tasks/latest')
    if (latest.data && latest.data.state !== 'completed') applyTask(latest.data)
    taskSyncReady.value = true
  } catch { pollError.value = '无法连接任务服务，请检查本地服务状态' }
})
const selectedAccount = computed(() => accounts.value.find(a => a.id === accountId.value))
const fallbackEnabled = ref(true)
const fallbackId = ref('')
const fallbackCandidates = computed(() => accounts.value.filter(a => a.id !== accountId.value && a.hasPassword))
const fallbackAccount = computed(() => fallbackCandidates.value.find(a => a.id === fallbackId.value) || fallbackCandidates.value[0])
const selectedFallbackId = computed({ get: () => fallbackAccount.value?.id || '', set: (value: string) => { fallbackId.value = value } })
const input = ref<Record<Kind, string>>({ fee: '', order: '' })
const kinds: { key: Kind; title: string; placeholder: string }[] = [
  { key: 'fee', title: '费用解锁（小锁）', placeholder: '粘贴需要费用解锁的提单号\n例如：SITGSHBAZA00451' },
  { key: 'order', title: '订单解锁（大锁）', placeholder: '粘贴需要订单解锁的提单号\n例如：SITGSHBAZA00461' },
]
const inspections = computed(() => ({ fee: inspectBills(input.value.fee), order: inspectBills(input.value.order) }))
const duplicates = computed(() => ({ fee: duplicateBills(inspections.value.fee), order: duplicateBills(inspections.value.order) }))
const parsed = computed(() => ({ fee: inspections.value.fee.filter(r => r.status === 'valid').map(r => r.bill), order: inspections.value.order.filter(r => r.status === 'valid').map(r => r.bill) }))
const reviewRows = computed(() => kinds.flatMap(k => inspections.value[k.key].map(r => ({ ...r, kind: k.key }))))
const invalidCount = computed(() => reviewRows.value.filter(r => r.status === 'invalid').length)
const duplicateCount = (kind: Kind) => inspections.value[kind].filter(r => r.status === 'duplicate').length
const reviewAcknowledged = ref(false)
const reviewKind = ref<Kind>('fee')
function openReview() {
  reviewAcknowledged.value = false
  reviewKind.value = inspections.value.fee.length ? 'fee' : 'order'
  reviewOpen.value = true
}
const total = computed(() => parsed.value.fee.length + parsed.value.order.length)
const overlap = computed(() => parsed.value.fee.filter(bill => parsed.value.order.includes(bill)))
const rows = ref<Row[]>([])
const taskId = ref('')
const taskState = ref<Task['state'] | ''>('')
const runtimeAvailable = ref(false)
const runtimeMessage = ref('正在检查真实执行环境')
const submitting = ref(false)
const controlling = ref(false)
const resetting = ref(false)
const taskSyncReady = ref(false)
const pollError = ref('')
const busy = computed(() => submitting.value || resetting.value || taskState.value === 'running' || taskState.value === 'paused')
const paused = computed(() => taskState.value === 'paused')
const filter = ref('all')
const reviewOpen = ref(false)
const done = computed(() => rows.value.filter(r => !['waiting','processing'].includes(r.status)).length)
const successCount = computed(() => rows.value.filter(r => r.status === 'success').length)
const attentionCount = computed(() => rows.value.filter(r => r.status === 'attention').length)
const percentage = computed(() => rows.value.length ? Math.round(done.value / rows.value.length * 100) : 0)
const currentRow = computed(() => rows.value.find(r => r.status === 'processing'))
const filteredRows = computed(() => filter.value === 'all' ? rows.value : rows.value.filter(r => r.status === filter.value))
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
function applyTask(task: Task) {
  taskId.value = task.id; taskState.value = task.state; taskAccountName.value = task.accountName
  accountId.value = task.account
  fallbackEnabled.value = !!task.fallbackAccount; fallbackId.value = task.fallbackAccount || ''
  taskFallbackName.value = task.fallbackAccountName || ''
  rows.value = task.rows.map((r,index) => ({ ...r, evidenceIndex:index, message: r.message.replace(/<br\s*\/?\s*>/gi,' ').replace(/<[^>]*>/g,''), time: r.time ? new Date(r.time).toLocaleTimeString('zh-CN',{hour12:false}) : '' }))
  clearTimeout(timer)
  if (!disposed && task.state !== 'completed') timer = setTimeout(refreshTask,1500)
}
async function refreshTask() {
  if (!taskId.value || disposed) return
  const requestedId = taskId.value
  try {
    const response = await axios.get<Task>(`/api/ilog/tasks/${encodeURIComponent(requestedId)}`)
    if (taskId.value !== requestedId || disposed) return
    pollError.value = ''; applyTask(response.data)
  } catch {
    if (taskId.value !== requestedId || disposed) return
    pollError.value = '连接中断，结果尚未同步；请勿重新提交'
    clearTimeout(timer); if (!disposed) timer = setTimeout(refreshTask,4000)
  }
}
async function newTask() {
  if (busy.value || controlling.value || resetting.value || !taskSyncReady.value) return
  resetting.value = true
  try {
    if (localStorage.getItem('finance-ilog-pending-task')) {
      ElMessage.warning('上次提交结果尚未确认，请先核对历史记录，当前凭据不会清除')
      return
    }
    if (input.value.fee.trim() || input.value.order.trim()) {
      await ElMessageBox.confirm('将清空当前单号、进度和结果；历史记录、截图和防重复凭据仍保留。', '新建任务', { confirmButtonText: '新建任务', cancelButtonText: '取消' })
    }
    const latest = (await axios.get<Task | null>('/api/ilog/tasks/latest')).data
    if (latest && latest.state !== 'completed') {
      applyTask(latest)
      ElMessage.warning('有尚未结束的任务，已恢复；请先完成或取消剩余项')
      return
    }
    clearTimeout(timer)
    taskId.value = ''; taskState.value = ''; rows.value = []
    taskAccountName.value = ''; taskFallbackName.value = ''
    input.value = { fee: '', order: '' }; filter.value = 'all'
    pollError.value = ''; reviewOpen.value = false; reviewAcknowledged.value = false
    ElMessage.success('可以开始新任务，上次结果可在管理 → 解锁历史中查看')
  } catch (error) {
    if (axios.isAxiosError(error)) ElMessage.error('无法确认任务状态，未清空当前工作区')
  } finally { resetting.value = false }
}
async function start() {
  if (busy.value || resetting.value || !taskSyncReady.value || !total.value || !selectedAccount.value || invalidCount.value || !reviewAcknowledged.value) return
  if (!runtimeAvailable.value || total.value > 2000) return
  const payload = { account_id: accountId.value, fallback_account_id: fallbackEnabled.value ? fallbackAccount.value?.id || null : null, fee: parsed.value.fee, order: parsed.value.order, confirmed: true }
  const signature = JSON.stringify(payload)
  submitting.value = true
  try {
    const saved = JSON.parse(localStorage.getItem('finance-ilog-pending-task') || 'null')
    const request_id = saved?.signature === signature ? saved.id : crypto.randomUUID()
    localStorage.setItem('finance-ilog-pending-task',JSON.stringify({signature,id:request_id}))
    const response = await axios.post<Task>('/api/ilog/tasks',{ ...payload,request_id },{headers:accountHeaders})
    localStorage.removeItem('finance-ilog-pending-task')
    applyTask(response.data); reviewOpen.value = false; filter.value = 'all'
    ElMessage.success('任务已提交')
  } catch (error) {
    ElMessage.error(axios.isAxiosError(error) ? error.response?.data?.detail || '提交结果未确认，请刷新任务状态，不要重复新建' : '无法保存提交凭据，未建立任务')
  } finally { submitting.value = false }
}
async function controlTask(action: 'pause' | 'resume' | 'cancel') {
  if (!taskId.value || controlling.value) return
  controlling.value = true
  try {
    const response = await axios.post<Task>(`/api/ilog/tasks/${encodeURIComponent(taskId.value)}/control`,{action},{headers:accountHeaders})
    applyTask(response.data)
  } catch { ElMessage.error('任务控制结果未确认，请刷新查看') }
  finally { controlling.value = false }
}
async function cancelRemaining() {
  try { await ElMessageBox.confirm('只取消尚未开始的单号；当前单会完成并保留结果。','取消剩余项',{type:'warning'}); await controlTask('cancel') } catch { /* User cancelled. */ }
}
const rowClassName = ({ row }: { row: Row }) => row.status === 'processing' ? 'active-unlock-row' : ''
const statusText = (s: Status, detail?: Detail) => s === 'success' ? ({ verified_fee_unlocked:'小锁已解', verified_unlocked:'复查无锁', already_unlocked:'原本已无锁', already_fee_unlocked:'小锁原本已解' }[detail?.status || ''] || '核实通过') : ({ waiting: '等待处理', processing: '真实处理中', attention: '待人工核实', cancelled: '已取消' }[s])
const statusStyle = (s: Status) => ({ waiting: 'info', processing: 'primary', success: 'success', attention: 'warning', cancelled:'info' } as const)[s]
const attemptText = (status: string) => ({ not_found:'未找到', ambiguous:'多条匹配', verified_unlocked:'成功并复查无锁', verified_fee_unlocked:'小锁已核实解开', already_unlocked:'原本已无锁', already_fee_unlocked:'小锁原本已解', rejected:'平台拒绝', blocked_duplicate:'已阻止重复提交', platform_confirmed:'平台成功，仍需核实', needs_verification:'待核实' }[status] || '待核实')
function exportResults(task?:Task) {
  const cell = (v: string) => `"${(/^[=+@\-\t\r]/.test(v) ? "'" + v : v).replace(/"/g, '""')}"`
  const content = [['主账号', '兜底账号', '实际使用账号', '查询路径', '提单号', '类型', '结果', '平台响应', '原锁状态', '复查锁状态', '说明', '时间'], ...(task?.rows || rows.value).map(r => [task?.accountName || taskAccountName.value, task ? task.fallbackAccountName || '' : taskFallbackName.value, r.actualAccountName || '', (r.attempts || []).map(a => `${a.accountName}: ${attemptText(a.status)}`).join(' → '), r.bill, r.kind === 'fee' ? '费用解锁' : '订单解锁', statusText(r.status,r.detail), r.detail?.platform_success === true ? '成功' : r.detail?.platform_success === false ? '拒绝' : '未确认', r.detail?.before?.lock || '', r.detail?.after?.lock || '', cleanMessage(r.message), r.time])].map(r => r.map(cell).join(',')).join('\r\n')
  downloadBlob(new Blob(['\uFEFF', content], { type: 'text/csv;charset=utf-8' }), 'iLog解锁-真实结果.csv')
}
const taskAccountName = ref('')
const taskFallbackName = ref('')
const accountOpen = ref(false)
const editingId = ref('')
const accountPassword = ref('')
const savingAccount = ref(false)
const accountForm = ref({ name: '', username: '' })
function openAccount(account?: Account) { editingId.value = account?.id || ''; accountPassword.value = ''; accountForm.value = { name: account?.name || '', username: account?.username || '' }; accountOpen.value = true }
async function saveAccount() {
  const data = { name: accountForm.value.name.trim(), username: accountForm.value.username.trim() }
  if (!data.name || !data.username || busy.value || savingAccount.value) return
  savingAccount.value = true
  try {
    const payload = { ...data, password: accountPassword.value || null }
    if (editingId.value) {
      const response = await axios.put<Account>(`/api/ilog/accounts/${encodeURIComponent(editingId.value)}`, payload, { headers: accountHeaders })
      accounts.value = accounts.value.map(a => a.id === editingId.value ? response.data : a)
    } else {
      const response = await axios.post<Account>('/api/ilog/accounts', payload, { headers: accountHeaders })
      accounts.value.push(response.data)
      accountId.value = response.data.id
    }
    accountPassword.value = ''
    accountOpen.value = false
    ElMessage.success('账号信息已保存')
  } catch { ElMessage.error('保存失败，请检查本地服务和应用数据目录后重试。') }
  finally { savingAccount.value = false }
}
async function removeAccount(account: Account) {
  try { await ElMessageBox.confirm(`删除账号“${account.name}”的本机记录？`, '删除账号', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) } catch { return }
  if (busy.value) return
  try {
    await axios.delete(`/api/ilog/accounts/${encodeURIComponent(account.id)}`, { headers: accountHeaders })
    accounts.value = accounts.value.filter(a => a.id !== account.id)
    if (accountId.value === account.id) accountId.value = accounts.value[0]?.id || ''
    ElMessage.success('账号已删除')
  } catch { ElMessage.error('删除失败，请检查本地服务后重试。') }
}
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>
