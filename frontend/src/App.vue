<template>
  <el-config-provider :locale="elementLocale">
  <main class="app-shell">
    <section class="workspace" aria-labelledby="page-title">
      <header class="page-header">
        <div class="header-title">
          <el-icon class="brand-icon"><DataAnalysis /></el-icon>
          <h1 id="page-title">{{ t('title') }}</h1>
        </div>
        <div class="header-actions">
          <el-select
            v-model="language"
            class="language-select"
            :aria-label="t('languageLabel')"
            :disabled="isAnalyzing"
          >
            <el-option v-for="option in localeOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </div>
      </header>

      <nav class="function-nav" :aria-label="t('menuLabel')">
        <el-menu :default-active="outputMode" mode="horizontal" @select="handleMenuSelect">
          <el-menu-item index="detailed" :disabled="isAnalyzing"><el-icon><Document /></el-icon>{{ t('detailedMode') }}</el-menu-item>
          <el-menu-item index="marked" :disabled="isAnalyzing"><el-icon><Brush /></el-icon>{{ t('markedMode') }}</el-menu-item>
        </el-menu>
      </nav>

      <div class="layout-grid">
        <div class="work-column">
        <section class="panel upload-panel single-upload" :aria-label="t('uploadLabel')">
          <FileDrop
            :title="t('workbookTitle')"
            :file="workbook"
            :company-code="companyCodeText"
            :checking="isInspecting"
            :checking-text="t('dropStatusChecking')"
            :uploaded-text="t('uploaded')"
            :pending-text="t('pendingUpload')"
            :upload-prompt="t('uploadPrompt')"
            :supported-text="t('supportedFiles')"
            :filename-label="t('filename')"
            :not-selected-text="t('notSelected')"
            :company-code-label="t('companyCode')"
            :identify-after-text="t('identifyAfter')"
            @selected="handleFileSelected"
          />
        </section>
      <section class="panel inspection-panel" :aria-label="t('inspection')">
        <div class="inspection-header">
          <h2>{{ t('inspection') }}</h2>
          <el-tag v-if="!inspectionReport && !isInspecting" effect="plain">{{ t('waitingUpload') }}</el-tag>
          <el-tag v-else-if="isInspecting" type="warning" effect="plain">{{ t('checking') }}</el-tag>
          <el-tag v-else-if="inspectionReport?.valid" type="success" effect="plain">{{ t('passed') }}</el-tag>
          <el-tag v-else type="danger" effect="plain">{{ t('invalid') }}</el-tag>
        </div>

        <el-skeleton v-if="isInspecting" :rows="3" animated />
        <div v-else-if="!inspectionReport" class="quiet-state" role="status"><el-icon><Checked /></el-icon><p>{{ t('emptyInspection') }}</p></div>
        <div v-else class="inspection-content">
          <dl class="inspection-summary">
            <div><dt>{{ t('recognizeCompanies') }}</dt><dd>{{ inspectionReport.companyCodes.join(' / ') || '-' }}</dd></div>
            <div><dt>{{ t('detailRows') }}</dt><dd>{{ inspectionReport.rowCount }} {{ t('rows') }}</dd></div>
            <div><dt>{{ t('allocationCount') }}</dt><dd>{{ inspectionReport.allocationCount }} {{ t('items') }}</dd></div>
          </dl>

          <el-alert
            v-if="inspectionReport.valid"
            type="success"
            :title="t('inspectionPassed')"
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
        </div>
        <aside class="panel settings-panel" :aria-label="t('matchingRules')">
          <div class="section-title-row">
            <h2>{{ t('matchingRules') }}</h2>
            <el-popover placement="left" trigger="click" width="300">
              <template #reference>
                <el-button
                  class="help-button"
                  circle
                  text
                  :icon="QuestionFilled"
                  :aria-label="t('ruleHelp')"
                />
              </template>
              <div class="help-content">
                <h3>{{ t('helpTitle') }}</h3>
                <p>{{ t('helpMatch') }}</p>
                <p>{{ t('helpAmount') }}</p>
                <p>{{ t('helpTolerance') }}</p>
              </div>
            </el-popover>
          </div>
          <el-form label-position="top">
            <el-form-item :label="t('matchField')">
              <el-input v-model="matchField" name="match-field" :aria-label="t('matchField')" :disabled="isAnalyzing" clearable />
            </el-form-item>
            <el-form-item :label="t('amountField')">
              <el-input v-model="amountField" name="amount-field" :aria-label="t('amountField')" :disabled="isAnalyzing" clearable />
            </el-form-item>
            <el-form-item :label="t('tolerance')">
              <el-input-number v-model="tolerance" name="tolerance" :aria-label="t('tolerance')" :disabled="isAnalyzing" :min="0" :precision="2" :step="100" controls-position="right" />
            </el-form-item>
          </el-form>

          <div class="standard-box">
            <h3>{{ t('formatRequirements') }}</h3>
            <ul>
              <li>{{ t('requiredFields') }}</li>
              <li>{{ t('twoCompanies') }}</li>
              <li>{{ t('numericAmount') }}</li>
              <li>{{ t('blankAllocation') }}</li>
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
            {{ t('startAnalysis') }}
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

      <section class="panel status-panel" :aria-label="`${t('process')} / ${t('analysisResult')}`">
        <div class="status-column">
          <h2>{{ t('process') }}</h2>
          <el-steps :active="activeStep" finish-status="success" align-center>
            <el-step v-for="step in displaySteps" :key="step" :title="step" />
          </el-steps>
        </div>

        <div class="result-column">
          <h2>{{ outputMode === 'marked' ? t('markedResult') : t('analysisResult') }}</h2>
          <div v-if="!result" class="quiet-state result-empty" role="status"><el-icon><Document /></el-icon><p>{{ resultEmptyText }}</p></div>
          <div v-else class="result-box">
            <div class="result-title">{{ t('analysisDone') }}</div>
            <dl class="metrics">
              <div><dt>{{ t('processedDetails') }}</dt><dd>{{ result.total }} {{ t('rows') }}</dd></div>
              <div><dt>{{ t('matchSuccess') }}</dt><dd>{{ result.matchedAllocations }} {{ t('allocationRows') }} {{ result.matched }} {{ t('rows') }}</dd></div>
              <div><dt>{{ t('exactMatch') }}</dt><dd>{{ result.exactAllocations }} {{ t('items') }}</dd></div>
              <div><dt>{{ t('toleranceMatch') }}</dt><dd>{{ result.toleranceAllocations }} {{ t('items') }}</dd></div>
              <div><dt>{{ t('amountDifference') }}</dt><dd>{{ result.differenceAllocations }} {{ t('allocationRows') }} {{ result.differences }} {{ t('rows') }}</dd></div>
              <div><dt>{{ t('unmatched') }}</dt><dd>{{ result.unmatchedAllocations }} {{ t('allocationRows') }} {{ result.unmatched }} {{ t('rows') }}</dd></div>
            </dl>
            <el-button type="primary" plain :icon="Download" @click="downloadResult">
              {{ outputMode === 'marked' ? t('downloadMarked') : t('downloadDetailed') }}
            </el-button>
          </div>
        </div>
      </section>
    </section>
  </main>
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Brush, Checked, Cpu, DataAnalysis, Document, Download, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import en from 'element-plus/es/locale/lang/en'
import id from 'element-plus/es/locale/lang/id'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import FileDrop from './components/FileDrop.vue'
import {
  analyzeCombinedExcel,
  downloadBlob,
  inspectCombinedExcel,
  type AnalyzeSummary,
  type InspectReport,
} from './services/api'
import { localeOptions, translate, type Locale } from './i18n'

const supportedLocales: Locale[] = ['zh-CN', 'en', 'id']
const storedLocale = localStorage.getItem('finance-reconcile-locale') as Locale | null
const language = ref<Locale>(storedLocale && supportedLocales.includes(storedLocale) ? storedLocale : 'zh-CN')
const elementLocale = computed(() => ({ 'zh-CN': zhCn, en, id }[language.value]))
const t = (key: string) => translate(language.value, key)

watch(language, (locale) => {
  localStorage.setItem('finance-reconcile-locale', locale)
  document.documentElement.lang = locale === 'zh-CN' ? 'zh-CN' : locale
}, { immediate: true })

const workbook = ref<File | null>(null)
const matchField = ref('分配')
const amountField = ref('原币金额')
const tolerance = ref(100)
const outputMode = ref<'detailed' | 'marked'>('detailed')
const isAnalyzing = ref(false)
const isInspecting = ref(false)
const errorMessage = ref('')
const activeStep = ref(0)
const result = ref<AnalyzeSummary | null>(null)
const inspectionReport = ref<InspectReport | null>(null)

watch(language, () => {
  if (workbook.value && !isAnalyzing.value) void inspectWorkbook()
})

const pendingSteps = computed(() => ['pendingExcel', 'pendingCheck', 'pendingAnalysis', 'pendingGenerate', 'downloadable'].map(t))
const readySteps = computed(() => ['excelUploaded', 'passed', 'pendingAnalysis', 'pendingGenerate', 'downloadable'].map(t))
const analyzingSteps = computed(() => ['readingExcel', 'validatingFields', 'matchingData', 'generating', 'completed'].map(t))
const invalidSteps = computed(() => ['excelUploaded', 'checkFailed', 'fixAccording', 'reupload', 'downloadable'].map(t))
const displaySteps = computed(() => {
  if (isAnalyzing.value || result.value) return analyzingSteps.value
  if (!workbook.value) return pendingSteps.value
  if (inspectionReport.value?.valid) return readySteps.value
  if (inspectionReport.value && !inspectionReport.value.valid) return invalidSteps.value
  return [t('excelUploaded'), t('checkingTable'), t('pendingAnalysis'), t('pendingGenerate'), t('downloadable')]
})
const resultEmptyText = computed(() => {
  if (!workbook.value) return t('noWorkbook')
  if (isInspecting.value) return t('checkingContent')
  if (inspectionReport.value && !inspectionReport.value.valid) return t('invalidAdvice')
  if (inspectionReport.value?.valid) return t('readyAdvice')
  return t('uploadAutoCheck')
})
const canAnalyze = computed(() =>
  workbook.value
  && inspectionReport.value?.valid
  && matchField.value
  && amountField.value
  && Number.isFinite(tolerance.value)
  && tolerance.value >= 0
  && !isAnalyzing.value
  && !isInspecting.value,
)
const companyCodeText = computed(() => {
  if (inspectionReport.value?.companyCodes.length) return inspectionReport.value.companyCodes.join(' / ')
  if (result.value) return `${result.value.companyA} / ${result.value.companyB}`
  return undefined
})

function handleMenuSelect(index: string) {
  if (index !== 'detailed' && index !== 'marked') return
  outputMode.value = index
  result.value = null
  errorMessage.value = ''
  activeStep.value = 0
}

async function handleFileSelected(file: File) {
  workbook.value = file
  inspectionReport.value = null
  result.value = null
  errorMessage.value = ''
  activeStep.value = 0
  await inspectWorkbook()
}

async function inspectWorkbook() {
  if (!workbook.value) return
  const currentFile = workbook.value
  const currentLocale = language.value
  inspectionReport.value = null
  isInspecting.value = true
  try {
    const report = await inspectCombinedExcel(currentFile, matchField.value, amountField.value, currentLocale)
    if (workbook.value === currentFile && language.value === currentLocale) inspectionReport.value = report
  } catch {
    if (workbook.value !== currentFile || language.value !== currentLocale) return
    inspectionReport.value = {
      valid: false,
      errors: [t('inspectionFailed')],
      warnings: [],
      companyCodes: [],
      rowCount: 0,
      allocationCount: 0,
      requiredColumns: ['公司代码or伙伴公司', '分配', '原币金额'],
    }
  } finally {
    if (workbook.value === currentFile && language.value === currentLocale) isInspecting.value = false
  }
}

async function handleAnalyze() {
  if (!workbook.value) return
  const currentFile = workbook.value
  const currentMode = outputMode.value
  errorMessage.value = ''
  result.value = null
  isAnalyzing.value = true
  activeStep.value = 1

  const timer = window.setInterval(() => {
    if (activeStep.value < 4) activeStep.value += 1
  }, 450)

  try {
    const analyzed = await analyzeCombinedExcel(currentFile, matchField.value, amountField.value, tolerance.value, currentMode, language.value)
    if (workbook.value !== currentFile || outputMode.value !== currentMode) return
    result.value = analyzed
    activeStep.value = 5
    ElMessage.success(t('analysisDone'))
  } catch (error: any) {
    if (workbook.value !== currentFile || outputMode.value !== currentMode) return
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
  if (!detail) return t('analysisFailed')
  try {
    const parsed = JSON.parse(detail)
    return parsed.detail ?? t('analysisFailed')
  } catch {
    return t('analysisFailed')
  }
}

function downloadResult() {
  if (!result.value) return
  downloadBlob(result.value.blob, result.value.filename)
}
</script>
