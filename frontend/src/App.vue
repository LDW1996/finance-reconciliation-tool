<template>
  <main class="app-shell">
    <section class="workspace" aria-labelledby="page-title">
      <header class="page-header">
        <div>
          <p class="eyebrow">本地处理 · localhost</p>
          <h1 id="page-title">财务Excel自动核对工具</h1>
        </div>
        <el-tag effect="plain" type="success">数据不离开本机</el-tag>
      </header>

      <div class="layout-grid">
        <section class="panel upload-panel single-upload" aria-label="Excel文件上传">
          <FileDrop
            title="往来Excel文件"
            :file="workbook"
            :company-code="companyCodeText"
            :checking="isInspecting"
            @selected="handleFileSelected"
          />
        </section>

        <aside class="panel settings-panel" aria-label="匹配规则">
          <div class="section-title-row">
            <h2>匹配规则</h2>
            <el-popover placement="left" trigger="click" width="300">
              <template #reference>
                <el-button
                  class="help-button"
                  circle
                  text
                  :icon="QuestionFilled"
                  aria-label="查看匹配规则说明"
                />
              </template>
              <div class="help-content">
                <h3>匹配规则说明</h3>
                <p>系统会按“匹配字段”把往来明细分组，当前默认使用“分配”。</p>
                <p>每个分组内分别汇总两家公司的“金额字段”，当前默认使用“原币金额”。</p>
                <p>两家公司金额相加等于 0 时为匹配成功；不等于 0 时为金额差异；只在单方出现时为未匹配。</p>
              </div>
            </el-popover>
          </div>
          <el-form label-position="top">
            <el-form-item label="匹配字段">
              <el-input v-model="matchField" clearable />
            </el-form-item>
            <el-form-item label="金额字段">
              <el-input v-model="amountField" clearable />
            </el-form-item>
          </el-form>

          <div class="standard-box">
            <h3>表格格式要求</h3>
            <ul>
              <li>必须包含：公司代码or伙伴公司、分配、原币金额</li>
              <li>公司代码or伙伴公司必须且只能有两家公司</li>
              <li>原币金额必须是可识别数字</li>
              <li>空白分配会统一按“空白”分组处理</li>
            </ul>
          </div>

          <el-button
            class="analyze-button"
            type="primary"
            size="large"
            :icon="Cpu"
            :loading="isAnalyzing"
            :disabled="!canAnalyze"
            @click="handleAnalyze"
          >
            开始分析
          </el-button>

          <el-alert
            v-if="errorMessage"
            class="message"
            type="error"
            :title="errorMessage"
            :closable="false"
            show-icon
          />
        </aside>
      </div>

      <section class="panel inspection-panel" aria-label="表格内容检查">
        <div class="inspection-header">
          <h2>表格内容检查</h2>
          <el-tag v-if="!inspectionReport && !isInspecting" effect="plain">等待上传</el-tag>
          <el-tag v-else-if="isInspecting" type="warning" effect="plain">检查中</el-tag>
          <el-tag v-else-if="inspectionReport?.valid" type="success" effect="plain">检查通过</el-tag>
          <el-tag v-else type="danger" effect="plain">不符合要求</el-tag>
        </div>

        <el-skeleton v-if="isInspecting" :rows="3" animated />
        <el-empty v-else-if="!inspectionReport" description="上传往来Excel后自动检查" />
        <div v-else class="inspection-content">
          <dl class="inspection-summary">
            <div><dt>识别公司</dt><dd>{{ inspectionReport.companyCodes.join(' / ') || '-' }}</dd></div>
            <div><dt>明细行数</dt><dd>{{ inspectionReport.rowCount }} 行</dd></div>
            <div><dt>分配数量</dt><dd>{{ inspectionReport.allocationCount }} 个</dd></div>
          </dl>

          <el-alert
            v-if="inspectionReport.valid"
            type="success"
            title="表格符合比对要求，可以开始分析"
            :closable="false"
            show-icon
          />

          <el-alert
            v-for="item in inspectionReport.errors"
            :key="item"
            class="message"
            type="error"
            :title="item"
            :closable="false"
            show-icon
          />

          <el-alert
            v-for="item in inspectionReport.warnings"
            :key="item"
            class="message"
            type="warning"
            :title="item"
            :closable="false"
            show-icon
          />
        </div>
      </section>

      <section class="panel status-panel" aria-label="分析过程和结果">
        <div class="status-column">
          <h2>分析过程</h2>
          <el-steps direction="vertical" :active="activeStep" finish-status="success">
            <el-step v-for="step in displaySteps" :key="step" :title="step" />
          </el-steps>
        </div>

        <div class="result-column">
          <h2>分析结果</h2>
          <el-empty v-if="!result" :description="resultEmptyText" />
          <div v-else class="result-box">
            <div class="result-title">分析完成</div>
            <dl class="metrics">
              <div><dt>处理明细</dt><dd>{{ result.total }} 行</dd></div>
              <div><dt>匹配成功</dt><dd>{{ result.matchedAllocations }} 个分配 / {{ result.matched }} 行</dd></div>
              <div><dt>金额差异</dt><dd>{{ result.differenceAllocations }} 个分配 / {{ result.differences }} 行</dd></div>
              <div><dt>未匹配</dt><dd>{{ result.unmatchedAllocations }} 个分配 / {{ result.unmatched }} 行</dd></div>
            </dl>
            <el-button type="success" :icon="Download" @click="downloadResult">
              下载结果Excel
            </el-button>
          </div>
        </div>
      </section>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Cpu, Download, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import FileDrop from './components/FileDrop.vue'
import {
  analyzeCombinedExcel,
  downloadBlob,
  inspectCombinedExcel,
  type AnalyzeSummary,
  type InspectReport,
} from './services/api'

const workbook = ref<File | null>(null)
const matchField = ref('分配')
const amountField = ref('原币金额')
const isAnalyzing = ref(false)
const isInspecting = ref(false)
const errorMessage = ref('')
const activeStep = ref(0)
const result = ref<AnalyzeSummary | null>(null)
const inspectionReport = ref<InspectReport | null>(null)

const pendingSteps = ['等待上传Excel', '等待表格检查', '等待开始分析', '等待生成结果', '完成后可下载']
const readySteps = ['Excel已上传', '表格检查通过', '等待开始分析', '等待生成结果', '完成后可下载']
const analyzingSteps = ['正在读取Excel...', '正在校验字段...', '正在匹配数据...', '正在生成结果...', '完成']
const invalidSteps = ['Excel已上传', '表格检查未通过', '请按提示修改表格', '等待重新上传', '完成后可下载']
const displaySteps = computed(() => {
  if (isAnalyzing.value || result.value) return analyzingSteps
  if (!workbook.value) return pendingSteps
  if (inspectionReport.value?.valid) return readySteps
  if (inspectionReport.value && !inspectionReport.value.valid) return invalidSteps
  return ['Excel已上传', '正在检查表格...', '等待开始分析', '等待生成结果', '完成后可下载']
})
const resultEmptyText = computed(() => {
  if (!workbook.value) return '请先上传往来Excel'
  if (isInspecting.value) return '正在检查表格内容'
  if (inspectionReport.value && !inspectionReport.value.valid) return '表格不符合要求，请按上方提示修改后重新上传'
  if (inspectionReport.value?.valid) return '表格检查通过，点击开始分析'
  return '上传往来Excel后自动检查'
})
const canAnalyze = computed(() =>
  workbook.value
  && inspectionReport.value?.valid
  && matchField.value
  && amountField.value
  && !isAnalyzing.value
  && !isInspecting.value,
)
const companyCodeText = computed(() => {
  if (inspectionReport.value?.companyCodes.length) return inspectionReport.value.companyCodes.join(' / ')
  if (result.value) return `${result.value.companyA} / ${result.value.companyB}`
  return undefined
})

async function handleFileSelected(file: File) {
  workbook.value = file
  result.value = null
  errorMessage.value = ''
  activeStep.value = 0
  await inspectWorkbook()
}

async function inspectWorkbook() {
  if (!workbook.value) return
  inspectionReport.value = null
  isInspecting.value = true
  try {
    inspectionReport.value = await inspectCombinedExcel(workbook.value, matchField.value, amountField.value)
  } catch {
    inspectionReport.value = {
      valid: false,
      errors: ['表格检查失败，请重新上传Excel文件'],
      warnings: [],
      companyCodes: [],
      rowCount: 0,
      allocationCount: 0,
      requiredColumns: ['公司代码or伙伴公司', '分配', '原币金额'],
    }
  } finally {
    isInspecting.value = false
  }
}

async function handleAnalyze() {
  if (!workbook.value) return
  errorMessage.value = ''
  result.value = null
  isAnalyzing.value = true
  activeStep.value = 1

  const timer = window.setInterval(() => {
    if (activeStep.value < 4) activeStep.value += 1
  }, 450)

  try {
    result.value = await analyzeCombinedExcel(workbook.value, matchField.value, amountField.value)
    activeStep.value = 5
    ElMessage.success('分析完成')
  } catch (error: any) {
    activeStep.value = 0
    const detail = error?.response?.data instanceof Blob
      ? await error.response.data.text()
      : error?.response?.data?.detail
    errorMessage.value = parseError(detail)
  } finally {
    window.clearInterval(timer)
    isAnalyzing.value = false
  }
}

function parseError(detail: string | undefined) {
  if (!detail) return '分析失败，请检查Excel文件后重试'
  try {
    const parsed = JSON.parse(detail)
    return parsed.detail ?? '分析失败，请检查Excel文件后重试'
  } catch {
    return detail
  }
}

function downloadResult() {
  if (!result.value) return
  downloadBlob(result.value.blob, result.value.filename)
}
</script>
