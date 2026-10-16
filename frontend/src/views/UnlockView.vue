<template>
  <main class="unlock-shell">
    <header class="unlock-heading">
      <div><p class="unlock-eyebrow">ILOG WORKSPACE</p><h1>{{ view === 'accounts' ? '账号管理' : '批量解锁' }}</h1><p>{{ view === 'accounts' ? '为不同业务区域配置独立账号，在开始任务时选择。' : '费用解锁（小锁）与订单解锁（大锁），分开输入，一次处理。' }}</p></div>
      <el-tag effect="plain" type="warning">交互演示 · 未连接 iLog</el-tag>
    </header>

    <template v-if="view === 'unlock'">
      <div class="unlock-account-strip">
        <el-icon><User /></el-icon><span>业务账号</span>
        <el-select v-model="accountId" aria-label="业务账号" :disabled="busy" placeholder="选择账号" style="width:220px">
          <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <span class="account-caption">{{ selectedAccount?.username || '请先添加业务账号' }}</span>
        <el-tag type="info" size="small">{{ selectedAccount?.example ? '示例账号' : '未验证登录' }}</el-tag>
        <el-button text :disabled="busy" @click="emit('navigate', 'accounts')">管理账号</el-button>
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
          <el-button class="unlock-start" size="large" type="primary" :disabled="busy || !reviewRows.length || !selectedAccount" :icon="VideoPlay" @click="openReview">校验单号并核对清单</el-button>
          <p class="demo-note">演示仅模拟执行进度，不登录或修改 iLog 数据。</p>
        </aside>

        <section class="unlock-execution">
          <section class="unlock-progress-panel">
            <div class="unlock-section-heading"><h2>执行进度</h2><el-button v-if="busy" :icon="paused ? VideoPlay : VideoPause" @click="paused = !paused">{{ paused ? '继续演示' : '暂停任务' }}</el-button><el-tag v-else effect="plain" :type="rows.length ? 'success' : 'info'">{{ rows.length ? '演示已完成' : '等待开始' }}</el-tag></div>
            <div class="progress-number">{{ done }}<span>/ {{ rows.length || total }}</span><small>{{ paused ? '已暂停' : busy ? '演示处理中' : rows.length ? '条已处理' : '条待处理' }}</small></div>
            <el-progress :percentage="percentage" :show-text="false" :stroke-width="12" color="var(--accent)" />
            <div class="progress-description"><span>{{ currentRow ? `正在演示处理 ${currentRow.bill}` : paused ? '任务已暂停，点击继续演示恢复。' : rows.length ? '演示结束，结果仅用于体验界面。' : '粘贴单号并选择账号后开始。' }}</span><span class="progress-tip"><el-icon><InfoFilled /></el-icon>真实接入后，登录失效时暂停</span></div>
          </section>
          <section class="unlock-results-panel">
            <div class="unlock-section-heading"><h2>执行结果</h2><el-select v-model="filter" aria-label="筛选结果" style="width:150px" size="small"><el-option label="全部结果" value="all"/><el-option label="演示成功" value="success"/><el-option label="待人工处理" value="attention"/><el-option label="等待处理" value="waiting"/></el-select></div>
            <div v-if="!rows.length" class="unlock-empty"><el-icon><List /></el-icon><h3>每一条结果，都在这里</h3><p>执行后可查看提单号、解锁类型和处理说明。</p><el-button plain @click="loadSample">填入示例单号</el-button></div>
            <el-table v-else :data="filteredRows" height="420" empty-text="没有符合条件的结果" :row-class-name="rowClassName">
              <el-table-column type="index" label="#" width="44"/>
              <el-table-column prop="bill" label="提单号" min-width="185"/>
              <el-table-column label="类型" min-width="100"><template #default="{ row }">{{ row.kind === 'fee' ? '费用解锁' : '订单解锁' }}</template></el-table-column>
              <el-table-column label="结果" min-width="125"><template #default="{ row }"><el-tag :type="statusStyle(row.status)" size="small" round>{{ statusText(row.status) }}</el-tag></template></el-table-column>
              <el-table-column prop="message" label="说明" min-width="180"/>
              <el-table-column prop="time" label="处理时间" width="100"/>
            </el-table>
            <footer class="unlock-results-footer"><span>{{ rows.length ? `演示成功 ${successCount} 条 · 待处理 ${attentionCount} 条` : '结果可导出为 CSV' }}</span><div><el-button :icon="Download" :disabled="!rows.length || busy" @click="exportResults">导出结果</el-button><el-button :icon="RefreshRight" :disabled="!attentionCount || busy" @click="retry">仅重试失败项</el-button></div></footer>
          </section>
        </section>
      </div>
    </template>

    <section v-else class="account-page">
      <el-alert title="账号和密码保存在本机应用内，可随时编辑修改。iLog 自动登录尚未接入。" type="info" :closable="false" show-icon/>
      <div class="account-toolbar"><h2>业务账号 <span>{{ accounts.length }}</span></h2><el-button type="primary" :icon="Plus" :disabled="busy" @click="openAccount()">添加账号</el-button></div>
      <el-table :data="accounts" empty-text="添加第一个业务账号">
        <el-table-column prop="name" label="账号名称" min-width="180"/>
        <el-table-column prop="username" label="用户名" min-width="180"/>
        <el-table-column label="密码状态" min-width="140"><template #default="{ row }"><el-tag :type="row.hasPassword ? 'success' : 'info'">{{ row.hasPassword ? '已保存密码' : '未登记密码' }}</el-tag></template></el-table-column>
        <el-table-column label="登录状态" min-width="140"><template #default="{ row }"><el-tag type="info">{{ row.example ? '示例账号' : '未接入登录' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button link type="primary" :disabled="busy" @click="openAccount(row)">编辑</el-button><el-button link type="danger" :disabled="busy" @click="removeAccount(row)">删除</el-button></template></el-table-column>
      </el-table>
      <el-button class="back-unlock" :icon="ArrowLeft" @click="emit('navigate', 'unlock')">返回批量解锁</el-button>
    </section>

    <el-dialog v-model="reviewOpen" class="bill-review-dialog" title="单号校验与二次确认" width="min(900px, 94vw)" top="4vh">
      <el-descriptions :column="1" border><el-descriptions-item label="业务账号">{{ selectedAccount?.name }}</el-descriptions-item><el-descriptions-item label="费用解锁（小锁）">{{ parsed.fee.length }} 条</el-descriptions-item><el-descriptions-item label="订单解锁（大锁）">{{ parsed.order.length }} 条</el-descriptions-item></el-descriptions>
      <el-alert v-if="overlap.length" class="review-alert" :title="`${overlap.length} 个提单号同时出现在大小锁列表，会分别演示两种操作。`" type="warning" :closable="false"/>
      <el-alert class="review-alert" :type="invalidCount ? 'error' : 'info'" :closable="false" :title="`识别 ${reviewRows.length} 项 · 有效 ${total} 项 · 重复 ${reviewRows.filter(r => r.status === 'duplicate').length} 项 · 异常 ${invalidCount} 项`" description="仅检查格式，不代表 iLog 中存在该单号。格式要求：8–40 位字母与数字组合。异常项必须返回修改；重复项不会执行。"/>
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
      <el-checkbox v-model="reviewAcknowledged" style="margin-top:16px;white-space:normal">我已核对账号、单号清单及大小锁类型</el-checkbox>
      <el-text v-if="total > 2000" type="danger">每批最多 2000 项，请返回拆分。</el-text>
      <p class="demo-note">本次使用模拟结果，不会连接 iLog，也不会执行真实解锁。</p>
      <template #footer><el-button @click="reviewOpen = false">返回修改</el-button><el-button type="primary" :disabled="!reviewAcknowledged || !!invalidCount || !total || total > 2000" @click="start">确认 {{ total }} 项并开始演示</el-button></template>
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
import { computed, ref, onBeforeUnmount, onMounted } from 'vue'
import axios from 'axios'
import { User, Unlock, VideoPlay, VideoPause, Download, RefreshRight, InfoFilled, List, Plus, ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { downloadBlob } from '../services/api'
import { inspectBills, duplicateBills } from '../services/billValidation.mjs'
type Kind = 'fee' | 'order'
type Status = 'waiting' | 'processing' | 'success' | 'attention'
type Row = { bill: string; kind: Kind; status: Status; message: string; time: string }
type Account = { id: string; name: string; username: string; example?: boolean; hasPassword?: boolean }
defineProps<{ view: 'unlock' | 'accounts' }>()
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
})
const selectedAccount = computed(() => accounts.value.find(a => a.id === accountId.value))
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
const busy = ref(false)
const paused = ref(false)
const filter = ref('all')
const reviewOpen = ref(false)
const done = computed(() => rows.value.filter(r => r.status === 'success' || r.status === 'attention').length)
const successCount = computed(() => rows.value.filter(r => r.status === 'success').length)
const attentionCount = computed(() => rows.value.filter(r => r.status === 'attention').length)
const percentage = computed(() => rows.value.length ? Math.round(done.value / rows.value.length * 100) : 0)
const currentRow = computed(() => rows.value.find(r => r.status === 'processing'))
const filteredRows = computed(() => filter.value === 'all' ? rows.value : rows.value.filter(r => r.status === filter.value))
let timer: ReturnType<typeof setInterval> | undefined
function run() {
  busy.value = true; paused.value = false
  timer = setInterval(() => {
    if (paused.value) return
    const active = rows.value.find(r => r.status === 'processing')
    if (active) {
      const index = rows.value.indexOf(active)
      active.status = index % 5 === 2 ? 'attention' : 'success'
      active.message = active.status === 'attention' ? '模拟异常：查到多条记录，已跳过' : '模拟结果，未执行真实解锁'
      active.time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }
    const next = rows.value.find(r => r.status === 'waiting')
    if (next) { next.status = 'processing'; next.message = '模拟查询中…' }
    else { clearInterval(timer); timer = undefined; busy.value = false; ElMessage.success('演示已完成，未操作 iLog。') }
  }, 650)
}
function start() {
  if (busy.value || !total.value || !selectedAccount.value || invalidCount.value || !reviewAcknowledged.value) return
  if (total.value > 2000) { ElMessage.warning('每批最多 2000 条，请拆分后演示。'); return }
  rows.value = kinds.flatMap(k => parsed.value[k.key].map(bill => ({ bill, kind: k.key, status: 'waiting' as Status, message: '等待演示', time: '—' })))
  taskAccountName.value = selectedAccount.value.name
  reviewOpen.value = false; filter.value = 'all'; run()
}
function retry() {
  rows.value.filter(r => r.status === 'attention').forEach(r => { r.status = 'waiting'; r.message = '等待重新演示'; r.time = '—' })
  filter.value = 'all'; run()
}
function loadSample() {
  if (input.value.fee.trim() || input.value.order.trim()) { ElMessage.info('请先清空输入，再填入示例，避免覆盖你的单号。'); return }
  input.value.fee = Array.from({ length: 8 }, (_, i) => `SITGSHBAZA${String(451 + i).padStart(5, '0')}`).join('\n')
  input.value.order = Array.from({ length: 4 }, (_, i) => `SITGSHBAZA${String(461 + i).padStart(5, '0')}`).join('\n')
}
const rowClassName = ({ row }: { row: Row }) => row.status === 'processing' ? 'active-unlock-row' : ''
const statusText = (s: Status) => ({ waiting: '等待处理', processing: '演示处理中', success: '演示成功', attention: '待人工处理' }[s])
const statusStyle = (s: Status) => ({ waiting: 'info', processing: 'primary', success: 'success', attention: 'warning' } as const)[s]
function exportResults() {
  const cell = (v: string) => `"${(/^[=+@\-\t\r]/.test(v) ? "'" + v : v).replace(/"/g, '""')}"`
  const content = [['账号', '提单号', '类型', '结果', '说明', '时间'], ...rows.value.map(r => [taskAccountName.value, r.bill, r.kind === 'fee' ? '费用解锁' : '订单解锁', statusText(r.status), r.message, r.time])].map(r => r.map(cell).join(',')).join('\r\n')
  downloadBlob(new Blob(['\uFEFF', content], { type: 'text/csv;charset=utf-8' }), 'iLog解锁-演示结果.csv')
}
const taskAccountName = ref('')
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
onBeforeUnmount(() => clearInterval(timer))
</script>
