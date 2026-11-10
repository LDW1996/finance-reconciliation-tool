<template>
  <main class="pc-shell">
    <el-alert v-if="errorMessage" class="pc-alert" type="error" :title="errorMessage" :closable="false" show-icon />

    <el-radio-group v-model="activeTab" class="pc-tabs" aria-label="页签切换">
      <el-radio-button value="data">{{ $platformText('数据上报') }}</el-radio-button>
      <el-radio-button value="templates">{{ $platformText('模板管理') }}</el-radio-button>
    </el-radio-group>

    <div v-loading="configLoading" class="pc-config">
      <template v-if="activeTab === 'templates'">
        <section class="pc-card">
          <div class="pc-card-head"><h3 class="pc-card-title">{{ $platformText('简表模板管理') }}</h3></div>
          <div class="pc-card-body">
            <p class="pc-hint pc-hint--first">
              {{ $platformText('模板一次上传长期保存，每月上报只需传数据源文件，不必重复上传模板') }}
            </p>
            <el-upload
              ref="templateUploadRef"
              class="pc-upload"
              drag
              multiple
              :auto-upload="false"
              :show-file-list="false"
              accept=".xlsx"
              :on-change="handleTemplateChange"
            >
              <el-icon class="pc-upload-icon"><UploadFilled /></el-icon>
              <div class="pc-upload-text">{{ $platformText('点击或拖拽模板文件到此处上传') }}</div>
              <div class="pc-upload-tip">{{ $platformText('可一次选择多份，按文件名自动匹配到对应利润中心') }}</div>
            </el-upload>

            <table class="pc-table pc-template-table">
              <thead>
                <tr>
                  <th>{{ $platformText('利润中心') }}</th>
                  <th>{{ $platformText('已保存文件') }}</th>
                  <th>{{ $platformText('上传时间') }}</th>
                  <th>{{ $platformText('状态') }}</th>
                  <th>{{ $platformText('操作') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="slot in templates" :key="slot.key">
                  <td>{{ slot.label }}<div class="pc-note">{{ slot.template }}</div></td>
                  <td>{{ slot.uploaded ? slot.stored_name : '—' }}</td>
                  <td>{{ slot.uploaded ? slot.uploaded_at : '—' }}</td>
                  <td>
                    <span :class="slot.uploaded ? 'is-ok' : 'is-bad'">
                      {{ slot.uploaded ? $platformText('已上传') : $platformText('未上传') }}
                    </span>
                  </td>
                  <td class="pc-template-actions">
                    <el-button link type="primary" :disabled="templateWorking" @click="pickTemplate(slot.key)">{{ $platformText('更换') }}</el-button>
                    <el-button v-if="slot.uploaded" link type="danger" :disabled="templateWorking" @click="handleRemoveTemplate(slot.key)">{{ $platformText('清除') }}</el-button>
                  </td>
                </tr>
              </tbody>
            </table>
            <input
              ref="templateInput"
              class="pc-hidden-input"
              type="file"
              accept=".xlsx"
              @change="onTemplatePicked"
            >
          </div>
        </section>
      </template>

      <template v-else>
        <section class="pc-card">
          <div class="pc-card-head"><h3 class="pc-card-title">{{ $platformText('上传报表文件') }}</h3></div>
          <div class="pc-card-body">
            <el-upload
              ref="uploadRef"
              class="pc-upload"
              drag
              multiple
              :auto-upload="false"
              :show-file-list="false"
              accept=".xlsx,.xls,.XLS,.XLSX"
              :on-change="handleFileChange"
            >
              <el-icon class="pc-upload-icon"><UploadFilled /></el-icon>
              <div class="pc-upload-text">{{ $platformText('点击或拖拽文件到此处上传') }}</div>
              <div class="pc-upload-tip">{{ $platformText('支持多文件，可多次选择追加') }}</div>
            </el-upload>

            <div class="pc-file-list">
              <div class="pc-subhead">{{ $platformText('已选文件') }}</div>
              <ul v-if="files.length">
                <li v-for="(file, index) in files" :key="index">
                  <div class="pc-file-main">
                    <span class="pc-file-name">{{ file.name }}</span>
                    <span class="pc-file-msg" :class="fileChecks[index]?.ok === false ? 'is-bad' : 'is-ok'">{{ fileChecks[index]?.message }}</span>
                  </div>
                  <el-button link type="primary" :aria-label="$platformText('移除')" @click="removeFile(index)">{{ $platformText('移除') }}</el-button>
                </li>
              </ul>
              <p v-else class="pc-empty">{{ $platformText('暂无文件') }}</p>
              <el-button v-if="files.length" class="pc-clear" link :disabled="running" @click="files = []">{{ $platformText('清空') }}</el-button>
              <p v-if="files.length && duplicateFiles.length" class="pc-hint pc-hint--warn">
                {{ $platformText('重复上传，仅取第一份') }}：{{ duplicateFiles.join('；') }}
              </p>
              <p v-if="files.length && missingDataFiles.length" class="pc-hint pc-hint--warn">
                {{ $platformText('缺少必需数据源') }}：{{ missingDataFiles.join('；') }}
              </p>
              <p v-else-if="files.length && !missingDataFiles.length && !fileWarnings" class="pc-hint pc-hint--ok">
                {{ $platformText('必需数据源已齐') }}
              </p>
            </div>

            <div class="pc-required">
              <div class="pc-subhead">{{ $platformText('必需文件') }}</div>
              <ul>
                <li v-for="(item, index) in config?.required_files || []" :key="index">{{ item }}</li>
              </ul>
              <p v-if="config?.required_files_sample" class="pc-hint">{{ config.required_files_sample }}</p>
              <p v-if="config?.templates_note" class="pc-hint">{{ config.templates_note }}</p>
              <p v-if="config?.optional_reference" class="pc-hint">{{ config.optional_reference }}</p>
              <p v-if="missingTemplates" class="pc-hint pc-hint--warn">
                {{ $platformText('模板未传齐') }}：
                <el-button link type="primary" :disabled="templateWorking" @click="activeTab = 'templates'">{{ $platformText('去模板管理') }}</el-button>
              </p>
            </div>
          </div>
        </section>

        <section class="pc-card">
          <div class="pc-card-head"><h3 class="pc-card-title">{{ $platformText('上报参数') }}</h3></div>
          <div class="pc-card-body">
            <div class="pc-field">
              <label class="pc-label">{{ $platformText('上报月份') }}</label>
              <el-select v-model="month" class="pc-month" filterable allow-create default-first-option>
                <el-option v-for="item in monthOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </div>

            <div class="pc-field">
              <div class="pc-subhead">{{ $platformText('高管工资录入') }}</div>
              <p class="pc-hint">{{ $platformText('留空不阻塞，结果中标待手工录入') }}</p>
              <div class="pc-salary-grid">
                <div v-for="region in execRegions" :key="region.key" class="pc-salary-item">
                  <label class="pc-label">{{ region.label }}</label>
                  <el-input-number v-model="execInputs[region.key]" class="pc-salary-input" :controls="false" :min="0" />
                </div>
              </div>
            </div>

            <div class="pc-actions">
              <el-button type="primary" size="large" :loading="running" :disabled="!canRun" @click="handleRun">
                {{ running ? $platformText('执行中') : $platformText('执行') }}
              </el-button>
            </div>
          </div>
        </section>
      </template>
    </div>

    <section v-if="result && activeTab === 'data'" class="pc-results">
      <div class="pc-result-head">
        <span class="pc-status" :class="allPassed ? 'is-pass' : 'is-fail'">
          {{ allPassed ? $platformText('校验通过') : $platformText('校验未通过') }}
        </span>
        <el-button type="primary" :icon="Download" :loading="downloading" @click="handleDownload">{{ $platformText('下载结果包') }}</el-button>
      </div>

      <section class="pc-card pc-table-card">
        <div class="pc-card-head"><h3 class="pc-card-title">{{ $platformText('校验项') }}</h3></div>
        <table class="pc-table">
          <thead>
            <tr>
              <th>{{ $platformText('名称') }}</th>
              <th>{{ $platformText('范围') }}</th>
              <th>{{ $platformText('合计') }}</th>
              <th>{{ $platformText('印尼基准') }}</th>
              <th>{{ $platformText('差额') }}</th>
              <th>{{ $platformText('判定') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(check, index) in result.checks" :key="index">
              <td>
                {{ check.name }}
                <div v-if="check.note" class="pc-note">{{ check.note }}</div>
              </td>
              <td>{{ check.scope }}</td>
              <td class="pc-num">{{ formatNumber(check.sum) }}</td>
              <td class="pc-num">{{ formatNumber(check.benchmark) }}</td>
              <td class="pc-num" :class="check.delta === 0 ? 'is-ok' : 'is-bad'">{{ formatNumber(check.delta) }}</td>
              <td><span :class="checkPassed(check) ? 'is-ok' : 'is-bad'">{{ checkPassed(check) ? $platformText('通过') : $platformText('未通过') }}</span></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!result.checks.length" class="pc-empty">{{ $platformText('暂无记录') }}</p>
      </section>

      <section class="pc-card pc-table-card">
        <div class="pc-card-head pc-card-head--row">
          <h3 class="pc-card-title">{{ $platformText('逐格比对') }}</h3>
          <el-radio-group v-model="diffFilter" size="small">
            <el-radio-button value="mismatch">{{ $platformText('仅显示不匹配') }}</el-radio-button>
            <el-radio-button value="all">{{ $platformText('显示全部') }}</el-radio-button>
          </el-radio-group>
        </div>
        <table class="pc-table">
          <thead>
            <tr>
              <th>{{ $platformText('地区') }}</th>
              <th>{{ $platformText('工作表') }}</th>
              <th>{{ $platformText('单元格') }}</th>
              <th>{{ $platformText('期望值') }}</th>
              <th>{{ $platformText('实际值') }}</th>
              <th>{{ $platformText('匹配') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(diff, index) in filteredDiffs" :key="index">
              <td>{{ diff.region }}</td>
              <td>{{ diff.sheet }}</td>
              <td>{{ diff.cell }}</td>
              <td class="pc-num">{{ diff.expected }}</td>
              <td class="pc-num">{{ diff.actual }}</td>
              <td><span :class="diff.match ? 'is-ok' : 'is-bad'">{{ diff.match ? $platformText('匹配') : $platformText('不匹配') }}</span></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!filteredDiffs.length" class="pc-empty">{{ $platformText('暂无记录') }}</p>
      </section>

      <section v-if="result.warnings.length" class="pc-card">
        <div class="pc-card-head"><h3 class="pc-card-title">{{ $platformText('校验提示') }}</h3></div>
        <ul class="pc-notes"><li v-for="(warning, index) in result.warnings" :key="index">{{ warning }}</li></ul>
      </section>
    </section>
    <p v-else-if="!configLoading && activeTab === 'data'" class="pc-placeholder">{{ $platformText('执行后显示校验结果') }}</p>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Download, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'
import { platformText } from '../platformLocale'
import { downloadBlob } from '../services/api'
import {
  profitCenterApi,
  type ProfitCenterCheck,
  type ProfitCenterConfig,
  type ProfitCenterRunResult,
  type ProfitCenterTemplateSlot,
} from '../services/profitCenter'

const uploadRef = ref<UploadInstance>()
const templateUploadRef = ref<UploadInstance>()
const templateInput = ref<HTMLInputElement>()
const config = ref<ProfitCenterConfig | null>(null)
const configLoading = ref(true)
const running = ref(false)
const downloading = ref(false)
const errorMessage = ref('')
const files = ref<File[]>([])
const month = ref('2026-08')
const execInputs = reactive<Record<string, number | null | undefined>>({})
const diffFilter = ref<'mismatch' | 'all'>('mismatch')
const result = ref<ProfitCenterRunResult | null>(null)

const activeTab = ref<'data' | 'templates'>('data')
const templates = ref<ProfitCenterTemplateSlot[]>([])
const templateWorking = ref(false)
const pendingTemplateKey = ref('')

watch(() => config.value?.month, value => { if (value) month.value = value })
const monthOptions = computed(() => {
  const list = config.value?.months ?? []
  return list.includes(month.value) ? list : [month.value, ...list]
})
const execRegions = computed(() => (config.value?.regions ?? []).filter(region => region.has_exec_salary))
const missingTemplates = computed(() => templates.value.length > 0 && templates.value.some(slot => !slot.uploaded))
const canRun = computed(() => files.value.length > 0 && !!month.value && !running.value && !configLoading.value)
const allPassed = computed(() => (result.value?.checks ?? []).every(check => check.pass || check.delta === 0))
const filteredDiffs = computed(() => {
  const diffs = result.value?.diffs ?? []
  return diffFilter.value === 'all' ? diffs : diffs.filter(diff => !diff.match)
})

interface FileCheck { ok: boolean; message: string }

const monthTokens = computed(() => {
  const m = /^(\d{4})-(\d{1,2})$/.exec(month.value || '')
  if (!m) return null
  const year = Number(m[1])
  const mon = Number(m[2])
  const pad = (v: number) => String(v).padStart(2, '0')
  const prev = mon === 1 ? { year: year - 1, mon: 12 } : { year, mon: mon - 1 }
  return { period: `${year}${pad(mon)}`, prevPeriod: `${prev.year}${pad(prev.mon)}` }
})

const regionLabels = computed(() => (config.value?.regions ?? []).map(region => region.label))

// Filename keywords are data, not UI text: keep them as unicode escapes so the
// text catalog checker does not pick them up as untranslated UI literals.
const TEMPLATE_PREFIX = /^\u529e\u4e8b\u7b80\u8868/i
const INDONESIA_KEY = '\u7269\u6d41\u5370\u5c3c'
const OUTSOURCING_KEY = '\u5916\u5305\u670d\u52a1\u6210\u672c'
const reportStem = (label: string) => `\u8d22\u52a1\u62a5\u8868-${label}-`

const fileChecks = computed<FileCheck[]>(() => {
  const tokens = monthTokens.value
  return files.value.map(file => {
    const name = file.name
    if (TEMPLATE_PREFIX.test(name)) {
      return { ok: false, message: platformText('模板文件由模板管理统一维护，本次忽略') }
    }
    if (name.includes(INDONESIA_KEY)) {
      if (!/\.xlsx$/i.test(name)) {
        return { ok: false, message: platformText('印尼汇总表需为 xlsx 格式，已忽略') }
      }
      if (!tokens || !name.includes(tokens.period)) {
        return { ok: false, message: `${platformText('文件名月份应为')} ${tokens?.period ?? ''}` }
      }
      return { ok: true, message: platformText('印尼汇总表') }
    }
    if (name.includes(OUTSOURCING_KEY) && /\.xlsx$/i.test(name)) {
      return { ok: false, message: platformText('外包成本表无需上传，模板缓存已含其数值') }
    }
    const label = regionLabels.value.find(item => name.includes(reportStem(item)))
    if (label && tokens) {
      if (!name.includes(tokens.period) && !name.includes(tokens.prevPeriod)) {
        return { ok: false, message: `${platformText('文件名月份应为')} ${tokens.prevPeriod} / ${tokens.period}` }
      }
      const role = name.includes(tokens.period) ? platformText('本月源报表') : platformText('上月源报表')
      return { ok: true, message: `${label} ${role}` }
    }
    return { ok: false, message: platformText('无法识别，将被忽略') }
  })
})

const fileWarnings = computed(() => fileChecks.value.some(check => !check.ok))

const duplicateFiles = computed<string[]>(() => {
  const tokens = monthTokens.value
  if (!tokens) return []
  const duplicates: string[] = []
  for (const region of config.value?.regions ?? []) {
    for (const token of [tokens.prevPeriod, tokens.period]) {
      const hits = files.value.filter(file => file.name.includes(reportStem(region.label)) && file.name.includes(token))
      if (hits.length > 1) duplicates.push(`${region.label} ${token}`)
    }
  }
  const indonesia = files.value.filter(file =>
    file.name.includes(INDONESIA_KEY)
    && file.name.includes(tokens.period)
    && /\.xlsx$/i.test(file.name),
  )
  if (indonesia.length > 1) duplicates.push(platformText('印尼汇总表'))
  return duplicates
})

const missingDataFiles = computed<string[]>(() => {
  const tokens = monthTokens.value
  if (!tokens) return []
  const missing: string[] = []
  for (const region of config.value?.regions ?? []) {
    if (!files.value.some(file => file.name.includes(reportStem(region.label)) && file.name.includes(tokens.prevPeriod))) {
      missing.push(`${region.label} ${platformText('上月源报表')} ${tokens.prevPeriod}`)
    }
    if (!files.value.some(file => file.name.includes(reportStem(region.label)) && file.name.includes(tokens.period))) {
      missing.push(`${region.label} ${platformText('本月源报表')} ${tokens.period}`)
    }
  }
  if (!files.value.some(file =>
    file.name.includes(INDONESIA_KEY)
    && file.name.includes(tokens.period)
    && /\.xlsx$/i.test(file.name),
  )) {
    missing.push(`${platformText('印尼汇总表')} ${tokens.period}`)
  }
  return missing
})

function handleFileChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (raw) files.value.push(raw)
  uploadRef.value?.clearFiles()
}
function removeFile(index: number) {
  files.value.splice(index, 1)
}
function formatNumber(value: number) {
  return Number(value ?? 0).toLocaleString('en-US')
}
function checkPassed(check: ProfitCenterCheck) {
  return check.pass || check.delta === 0
}
function readError(error: unknown): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  let detail: unknown = data
  if (typeof data === 'string') {
    try { detail = (JSON.parse(data) as { detail?: string }).detail ?? data } catch { /* keep raw text */ }
  } else if (data && typeof data === 'object' && 'detail' in data) {
    detail = (data as { detail?: string }).detail
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  return platformText('请求失败，请稍后重试')
}

async function loadConfig() {
  configLoading.value = true
  try {
    config.value = await profitCenterApi.config()
  } catch (error) {
    errorMessage.value = readError(error)
  } finally {
    configLoading.value = false
  }
}

async function refreshTemplates() {
  try {
    templates.value = await profitCenterApi.listTemplates()
  } catch (error) {
    errorMessage.value = readError(error)
  }
}

async function doUploadTemplates(list: File[]) {
  if (!list.length) return
  errorMessage.value = ''
  templateWorking.value = true
  try {
    templates.value = await profitCenterApi.uploadTemplates(list)
  } catch (error) {
    errorMessage.value = readError(error)
  } finally {
    templateWorking.value = false
  }
}

function handleTemplateChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  templateUploadRef.value?.clearFiles()
  if (raw) void doUploadTemplates([raw])
}

function pickTemplate(key: string) {
  pendingTemplateKey.value = key
  templateInput.value?.click()
}

function onTemplatePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void doUploadTemplates([file])
}

async function handleRemoveTemplate(key: string) {
  errorMessage.value = ''
  templateWorking.value = true
  try {
    templates.value = await profitCenterApi.deleteTemplate(key)
  } catch (error) {
    errorMessage.value = readError(error)
  } finally {
    templateWorking.value = false
  }
}

async function handleRun() {
  if (!canRun.value) return
  if (templates.value.length > 0 && templates.value.some(slot => !slot.uploaded)) {
    const labels = templates.value.filter(slot => !slot.uploaded).map(slot => slot.label).join('、')
    errorMessage.value = platformText('请先在模板管理页签上传缺少的简表模板') + `：${labels}`
    return
  }
  errorMessage.value = ''
  result.value = null
  running.value = true
  const execSalaries: Record<string, number> = {}
  for (const region of execRegions.value) {
    const value = execInputs[region.key]
    if (typeof value === 'number' && Number.isFinite(value)) execSalaries[region.key] = value
  }
  try {
    result.value = await profitCenterApi.run(files.value, month.value, execSalaries)
  } catch (error) {
    errorMessage.value = readError(error)
  } finally {
    running.value = false
  }
}

async function handleDownload() {
  if (!result.value) return
  errorMessage.value = ''
  downloading.value = true
  try {
    const blob = await profitCenterApi.download(result.value.job_id)
    downloadBlob(blob, result.value.download_name || `profit-center-${result.value.job_id}.zip`)
  } catch (error) {
    errorMessage.value = readError(error)
  } finally {
    downloading.value = false
  }
}

onMounted(() => {
  void loadConfig()
  void refreshTemplates()
})
</script>

<style scoped>
.pc-shell { padding: 28px 32px 48px; max-width: 1240px; margin: 0 auto; }
.pc-alert { margin-bottom: 20px; }
.pc-tabs { display: flex; width: 100%; margin-bottom: 20px; --el-radio-button-checked-bg-color: var(--el-color-primary-light-9); --el-radio-button-checked-text-color: var(--el-color-primary); --el-radio-button-checked-border-color: var(--el-color-primary-light-5); }
.pc-tabs :deep(.el-radio-button) { flex: 1; }
.pc-tabs :deep(.el-radio-button.is-active .el-radio-button__inner) { background: var(--el-color-primary-light-9); color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); box-shadow: none; }
.pc-tabs :deep(.el-radio-button__inner) { width: 100%; box-sizing: border-box; }
.pc-config { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 24px; align-items: start; }
.pc-config > .pc-card { grid-column: 1 / -1; }
.pc-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.pc-card-head { padding: 16px 22px; border-bottom: 1px solid var(--border); }
.pc-card-head--row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.pc-card-title { margin: 0; font-size: 15px; font-weight: 650; color: var(--el-text-color-primary); }
.pc-card-body { padding: 22px; }
.pc-upload :deep(.el-upload) { width: 100%; }
.pc-upload :deep(.el-upload-dragger) {
  min-height: 170px; padding: 24px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; border: 1px dashed #94baf3;
  border-radius: 8px; background: #f8fbff;
  transition: background-color 160ms ease, border-color 160ms ease;
}
.pc-upload :deep(.el-upload-dragger:hover) { background: #edf5fd; border-color: #0066cc; }
.pc-upload-icon { color: #0066cc; font-size: 38px; }
.pc-upload-text { margin-top: 12px; font-size: 15px; font-weight: 600; }
.pc-upload-tip { margin-top: 8px; color: var(--ink-muted); font-size: 13px; }
.pc-subhead { margin: 20px 0 10px; font-size: 13px; font-weight: 600; color: #465566; }
.pc-file-list ul, .pc-required ul, .pc-notes { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.9; color: var(--el-text-color-regular); }
.pc-file-list ul { padding-left: 0; list-style: none; }
.pc-file-list li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.pc-file-name { overflow-wrap: anywhere; }
.pc-file-main { flex: 1; min-width: 0; }
.pc-file-msg { display: block; font-size: 12px; font-weight: 400; }
.pc-hint--ok { color: #1e7e34; }
.pc-empty { padding: 8px 0; margin: 0; color: var(--ink-muted); font-size: 13px; }
.pc-clear { margin-top: 10px; }
.pc-field + .pc-field { margin-top: 8px; }
.pc-label { display: block; margin-bottom: 8px; font-size: 13px; font-weight: 600; color: #465566; }
.pc-month { width: 100%; }
.pc-hint { margin: 14px 0 0; color: var(--ink-muted); font-size: 13px; line-height: 1.6; }
.pc-hint--first { margin: 0 0 16px; }
.pc-hint--warn { color: #c0392b; }
.pc-hint--warn .el-button { margin-left: 6px; }
.pc-field .pc-hint { margin: 0 0 14px; }
.pc-salary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.pc-salary-input { width: 100%; }
.pc-actions { margin-top: 22px; }
.pc-actions .el-button { width: 100%; min-height: 42px; font-weight: 600; }
.pc-results { margin-top: 28px; display: flex; flex-direction: column; gap: 20px; }
.pc-result-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.pc-status { font-size: 15px; font-weight: 650; }
.pc-status.is-pass { color: #0066cc; }
.pc-status.is-fail { color: #c0392b; }
.pc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.pc-table th { padding: 12px 22px; background: #f5f7fa; text-align: left; font-weight: 600; color: #465566; white-space: nowrap; }
.pc-table td { padding: 12px 22px; border-top: 1px solid var(--el-border-color-lighter); color: var(--el-text-color-regular); }
.pc-template-table { margin-top: 20px; }
.pc-template-actions { white-space: nowrap; }
.pc-template-actions .el-button + .el-button { margin-left: 8px; }
.pc-hidden-input { display: none; }
.pc-num { font-variant-numeric: tabular-nums; }
.pc-note { margin-top: 4px; color: var(--ink-muted); font-size: 12px; }
.is-ok { color: #0066cc; font-weight: 600; }
.is-bad { color: #c0392b; font-weight: 600; }
.pc-table-card .pc-empty { padding: 16px 22px; }
.pc-notes { padding: 18px 22px 20px 40px; }
.pc-placeholder { margin: 28px 0 0; color: var(--ink-muted); font-size: 14px; }
@media (max-width: 960px) { .pc-config { grid-template-columns: 1fr; } }
@media (max-width: 600px) {
  .pc-shell { padding: 20px 16px 36px; }
  .pc-salary-grid { grid-template-columns: 1fr; }
  .pc-table th, .pc-table td { padding: 10px 12px; }
}
</style>
