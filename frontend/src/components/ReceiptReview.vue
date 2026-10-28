<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Close } from '@element-plus/icons-vue'
import type { MatchingRow, MatchingCandidate, ReviewState } from '../services/mailMatching'

const props = defineProps<{ row: MatchingRow; busy: boolean; stale: boolean; expanded: boolean; preferredMailId?: string; preferredPaymentId?:string }>()
const emit = defineEmits<{
  close: []; expand: []; review: [state: ReviewState, candidate?: MatchingCandidate]; 'open-mail': [mailId: string]
}>()
const chosenId = ref('')
watch(() => [props.row.receipt.id, props.row.candidates.map(item => item.payment_id)] as const,
  ([id, ids], previous) => {
    if (id !== previous?.[0] || !ids.includes(chosenId.value)) {
      const savedId = props.row.review.state === 'confirmed' ? props.row.review.payment_id : undefined
      const preferred=props.row.candidates.find(candidate=>candidate.payment_id===props.preferredPaymentId) || (props.preferredMailId && props.row.candidates.find(candidate=>candidate.mail_id===props.preferredMailId || candidate.related_mail_ids?.includes(props.preferredMailId!)))
      chosenId.value = preferred ? preferred.payment_id : savedId && ids.includes(savedId) ? savedId : ids[0] ?? ''
    }
  }, { immediate: true })
const chosen = computed(() => props.row.candidates.find(item => item.payment_id === chosenId.value))
const labels: Record<ReviewState, string> = { pending: '待核对', confirmed: '已确认关联', rejected: '暂不关联' }
const confirmed = computed(() => props.row.review.state === 'confirmed' && props.row.review.payment_id === chosen.value?.payment_id)
const fields = [
  { key: 'amount', label: '付款金额', max: 50 },
  { key: 'date', label: '付款日期', max: 20 },
  { key: 'payer', label: '付款人', max: 30 },
] as const
function missing(value: unknown) { return value == null || String(value).trim() === '' }
function money(value: number | string | null, currency: string) {
  if (missing(value)) return '未提供'
  const number = Number(value)
  return `${currency || '币种未知'} ${Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) : value}`
}
function valueFor(source: MatchingRow['receipt'] | MatchingCandidate, key: typeof fields[number]['key']) {
  return key === 'amount' ? money(source.amount, source.currency) : source[key] || '未提供'
}
function explanation(field: typeof fields[number]) {
  const candidate = chosen.value
  if (!candidate) return ''
  const points = candidate.breakdown[field.key]
  const absent = [missing(props.row.receipt[field.key]) ? '银行' : '', missing(candidate[field.key]) ? '邮件' : ''].filter(Boolean)
  if (absent.length) return `${absent.join('、')}未提供`
  if (field.key === 'amount') {
    if (!knownCurrency(props.row.receipt.currency) || !knownCurrency(candidate.currency)) return '币种未知，需核实'
    if (props.row.receipt.currency.trim().toUpperCase() !== candidate.currency.trim().toUpperCase()) return '币种不同，金额项 0 分，需核实'
    return points >= field.max ? '金额一致' : '金额不一致'
  }
  if (field.key === 'date') {
    if (points >= field.max) return '同一天'
    const bankDay = isoDay(props.row.receipt.date)
    const mailDay = isoDay(candidate.date)
    return bankDay !== null && mailDay !== null ? `相差${Math.abs(bankDay - mailDay)}天` : '日期格式待核实'
  }
  if (points >= field.max) return '名称一致（忽略格式）'
  if (points === 24) return '名称前缀一致，可能被截断'
  return points > 0 ? '名称相近，需核对' : '名称不一致'
}
function isoDay(value: string | null): number | null {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|T|\s)/)
  if (!match) return null
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  const timestamp = Date.UTC(year, month - 1, day)
  const date = new Date(timestamp)
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? timestamp / 86400000 : null
}
function needsAttention(field: typeof fields[number]) {
  const candidate = chosen.value
  return !!candidate && (candidate.breakdown[field.key] < field.max || missing(props.row.receipt[field.key]) || missing(candidate[field.key]) ||
    (field.key === 'amount' && (!knownCurrency(props.row.receipt.currency) || !knownCurrency(candidate.currency) || props.row.receipt.currency.trim().toUpperCase() !== candidate.currency.trim().toUpperCase())))
}
function knownCurrency(value: string) { return !!value?.trim() && !/^(unknown|unk|n\/a|未知)$/i.test(value.trim()) }
const reason = computed(() => chosen.value ? fields.map(explanation).join(' · ') : '')
const warnings = computed(() => [...new Set([
  ...[...props.row.warnings,...(chosen.value?.warnings ?? [])].filter(warning=>!/^分数表示规则相似度/.test(warning)&&!/^邮件中的业务号码为关联线索/.test(warning)),
  ...(!knownCurrency(props.row.receipt.currency) || (chosen.value && !knownCurrency(chosen.value.currency)) ? ['币种未知，请先核实币种，金额分数不能替代币种核对。'] : []),
])])
const quotes = computed(() => {
  const evidence = chosen.value?.evidence
  if (typeof evidence === 'string') return [{ label: '原文', text: evidence }]
  if (!evidence || typeof evidence !== 'object') return []
  const names: Record<string, string> = { amount: '金额', date: '日期', payer: '付款人', currency: '币种' }
  return Object.entries(names).flatMap(([key, label]) => {
    const items = (evidence as Record<string, unknown>)[key]
    return Array.isArray(items) ? items.flatMap(item => {
      if (!item || typeof item !== 'object' || !('quote' in item) || typeof item.quote !== 'string' || !item.quote.trim()) return []
      return [{ label, text: item.quote }]
    }) : []
  })
})
const relatedCount = computed(() => new Set(chosen.value?.related_mail_ids?.filter(id => id && id !== chosen.value?.mail_id) ?? []).size)
function review(state: ReviewState) {
  if (props.busy || props.stale) return
  if (state === 'confirmed') {
    if (chosen.value?.payment_id && !confirmed.value) emit('review', state, chosen.value)
  } else emit('review', state)
}
</script>

<template>
  <section class="receipt-review" aria-label="收款核对详情" :aria-busy="busy">
    <header class="review-header">
      <div class="header-title"><span class="eyebrow">收款核对</span><h2>{{ row.receipt.ref || `第 ${row.receipt.row} 行` }}</h2></div>
      <el-tag :type="row.review.state === 'confirmed' ? 'success' : 'info'">{{ labels[row.review.state] }}</el-tag>
      <el-button class="close-button" :icon="Close" circle aria-label="关闭收款核对" @click="emit('close')" />
    </header>

    <section class="bank-summary" aria-label="银行流水摘要">
      <div class="bank-amount-group"><p class="eyebrow">银行流水金额</p><p class="bank-amount">{{ money(row.receipt.amount, row.receipt.currency) }}</p></div>
      <dl class="bank-facts"><div><dt>付款人</dt><dd>{{ row.receipt.payer || '未提供' }}</dd></div><div><dt>付款日期</dt><dd>{{ row.receipt.date || '未提供' }}</dd></div></dl>
      <details :key="row.receipt.id"><summary>流水描述与详情</summary><p class="raw-text">{{ row.receipt.description || '无流水描述' }}</p><p class="muted">流水状态：{{ row.receipt.status || '未提供' }} · 第 {{ row.receipt.row }} 行</p></details>
    </section>
    <p v-if="stale" class="notice" role="status">匹配结果待刷新，刷新后可保存核对。</p>
    <div class="review-columns">
      <aside class="candidate-sidebar" aria-label="邮件付款候选">
        <div class="sidebar-heading"><h3>付款候选 <span class="muted">{{ row.candidate_count }}</span></h3><el-button :disabled="busy || expanded" @click="emit('expand')">{{ expanded ? '已展开 · 最多 20 条' : '展开至 20 条' }}</el-button></div>
        <p class="muted list-caption">显示 {{ row.candidates.length }} 条 · 按匹配分排序</p>
        <ol class="candidate-list">
          <li v-for="(candidate, index) in row.candidates" :key="candidate.payment_id">
            <button type="button" class="candidate-choice" :class="{ active: chosenId === candidate.payment_id }" :aria-pressed="chosenId === candidate.payment_id" @click="chosenId = candidate.payment_id">
              <span class="rank">{{ index + 1 }}</span><span class="candidate-copy"><span class="candidate-subject">{{ candidate.subject || '无主题邮件' }}</span><span class="candidate-meta">{{ candidate.payer || '付款人未识别' }} · {{ candidate.date || '日期未识别' }}</span><span v-if="row.review.state === 'confirmed' && row.review.payment_id === candidate.payment_id" class="confirmed-label">已确认关联</span></span>
              <span class="candidate-score"><strong>{{ candidate.score }}</strong><span>匹配分</span></span>
            </button>
          </li>
        </ol>
        <p v-if="!row.candidates.length" class="empty" role="status">暂无符合条件的付款候选，可展开候选或调整筛选条件。</p>
      </aside>

      <article v-if="chosen" class="candidate-detail" aria-label="所选付款凭证">
        <header class="selected-heading"><h3>所选凭证 <span class="selected-score">{{ chosen.score }}</span><span class="muted"> 匹配分 / 100</span></h3><p>{{ reason }}</p><p class="muted score-footnote">规则相似度，非正确率</p></header>
        <table class="comparison"><caption class="sr-only">银行流水与邮件凭证逐项核对</caption><colgroup><col class="field-column"><col><col><col></colgroup><thead><tr><th scope="col">核对项</th><th scope="col">银行流水</th><th scope="col">邮件凭证</th><th scope="col">核对说明</th></tr></thead><tbody><tr v-for="field in fields" :key="field.key" :class="{ warning: needsAttention(field) }"><th scope="row">{{ field.label }}</th><td>{{ valueFor(row.receipt, field.key) }}</td><td>{{ valueFor(chosen, field.key) }}</td><td class="muted">{{ explanation(field) }}</td></tr></tbody></table>
        <ul v-if="warnings.length" class="warnings"><li v-for="warning in warnings" :key="warning">{{ warning }}</li></ul>
        <section class="detail-section" aria-label="业务信息"><h3>业务信息与建议备注</h3><div class="business-ids"><el-tag v-for="id in chosen.business_ids" :key="id" type="info" effect="plain">{{ id }}</el-tag><span v-if="!chosen.business_ids?.length" class="muted">未识别业务编号</span></div><p class="remark raw-text">{{ chosen.remark || '暂无建议备注' }}</p></section>
        <section class="detail-section" aria-label="凭证来源"><h3>凭证来源</h3><dl class="provenance"><div><dt>邮件主题</dt><dd>{{ chosen.subject || '无主题邮件' }}</dd></div><div><dt>发件人</dt><dd>{{ chosen.sender || '未提供' }}</dd></div><div><dt>收件时间</dt><dd>{{ chosen.received || '未提供' }}</dd></div><div><dt>附件 / 页码</dt><dd>{{ chosen.attachment_name || (chosen.attachment_id === 'latest_body' ? '邮件正文' : '附件名称未提供') }}<span v-if="chosen.page > 0"> · 第 {{ chosen.page }} 页</span></dd></div></dl><p v-if="relatedCount" class="muted">另有 {{ relatedCount }} 封相关邮件</p></section>
        <details :key="`${row.receipt.id}:${chosen.payment_id}`" class="evidence"><summary>查看原文证据 <span class="muted">{{ quotes.length }} 条</span></summary><figure v-for="(quote, index) in quotes" :key="index"><figcaption>{{ quote.label }}</figcaption><blockquote class="raw-text">{{ quote.text }}</blockquote></figure><p v-if="!quotes.length" class="muted">暂无可展示的原文引文，请查看原邮件及付款附件。</p></details>
      </article>
      <div v-else class="empty detail-empty">选择付款候选后，可在此逐项核对凭证。</div>
    </div>

    <footer class="review-footer">
      <div class="footer-actions"><el-button :disabled="!chosen?.mail_id" @click="chosen?.mail_id && emit('open-mail', chosen.mail_id)">查看原邮件</el-button><el-button type="primary" :disabled="busy || stale || !chosen?.payment_id || confirmed" :loading="busy" @click="review('confirmed')">确认关联</el-button><el-tag v-if="confirmed" type="success">已确认关联</el-tag><el-button :disabled="busy || stale" @click="review('rejected')">暂不关联</el-button><el-button :disabled="busy || stale" @click="review('pending')">重置核对</el-button></div>
      <p class="muted">确认只保存本地关联，回写需单独提交</p>
    </footer>
  </section>
</template>

<style scoped>
.receipt-review{height:100%;display:flex;flex-direction:column;padding:24px;min-height:0;overflow:hidden;min-width:0;max-width:100%;color:var(--el-text-color-primary);font-size:14px;line-height:1.6;overflow-wrap:anywhere;background:var(--surface,var(--el-bg-color))}.receipt-review *{box-sizing:border-box}.receipt-review h2,.receipt-review h3,.receipt-review p{margin:0}.receipt-review h2{font-size:18px}.receipt-review h3{font-size:14px}.review-header{flex-shrink:0;min-height:60px;display:flex;align-items:center;gap:12px;padding-bottom:12px;border-bottom:1px solid var(--border)}.header-title{flex:1;min-width:0}.eyebrow,.muted{color:var(--ink-muted);font-size:13px}.close-button{flex-shrink:0}.bank-summary{flex-shrink:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:center;gap:4px 24px;padding:12px 0}.bank-summary>details{grid-column:1/-1;max-height:120px;overflow-y:auto}.bank-amount-group{min-width:0}.bank-amount{font-size:clamp(26px,4vw,36px);font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.bank-facts{display:flex;flex-wrap:wrap;gap:8px 24px;margin:0}.bank-facts>div{min-width:0}.receipt-review dt{color:var(--ink-muted);font-size:12px}.receipt-review dd{margin:4px 0 0;font-size:14px;font-weight:400}.receipt-review summary{cursor:pointer;min-height:36px;padding:8px 0;font-weight:500}.receipt-review details p{margin:8px 0}.raw-text{white-space:pre-wrap;overflow-wrap:anywhere}.notice,.warnings{color:var(--el-color-warning);background:var(--el-color-warning-light-9);border-radius:6px;padding:12px 16px;margin-bottom:16px!important}.warnings{padding-left:32px}.review-columns{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;display:grid;grid-template-columns:minmax(220px, .8fr) minmax(0,2fr);border-top:1px solid var(--border);align-items:start;align-content:start}.column-notices{grid-column:1/-1;padding-top:12px}.comparison tr.warning{background:var(--el-color-warning-light-9)}.comparison tr.warning td:last-child{color:var(--el-color-warning)}.candidate-sidebar{min-width:0;padding:20px 20px 24px 0}.sidebar-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px;justify-content:space-between}.list-caption{margin:8px 0 12px!important}.candidate-list{padding:0;margin:0;list-style:none;display:grid;gap:8px}.candidate-choice{width:100%;display:flex;align-items:flex-start;gap:10px;min-height:80px;padding:12px;text-align:left;background:var(--surface,var(--el-bg-color));color:inherit;border:1px solid var(--border);border-radius:6px;cursor:pointer;font:inherit}.candidate-choice:hover{background:var(--el-fill-color-light)}.candidate-choice.active{border-color:var(--el-color-primary);background:var(--el-color-primary-light-9)}.candidate-choice:focus-visible,.receipt-review summary:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:3px}.rank{color:var(--ink-muted);font-size:12px;flex-shrink:0}.candidate-copy{min-width:0;flex:1}.candidate-subject{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-weight:500;line-height:1.5}.candidate-meta,.confirmed-label{display:block;font-size:12px;margin-top:6px;color:var(--ink-muted)}.confirmed-label{color:var(--el-color-success)}.candidate-score{display:flex;flex-direction:column;align-items:center;flex-shrink:0;font-size:11px;color:var(--ink-muted);font-variant-numeric:tabular-nums}.candidate-score strong{font-size:20px;line-height:1.3;color:var(--el-text-color-primary);font-weight:600}.candidate-detail{min-width:0;border-left:1px solid var(--border);padding:20px 0 28px 24px}.selected-heading{margin-bottom:20px}.selected-heading p{margin-top:8px}.selected-score{font-size:24px;font-variant-numeric:tabular-nums;margin-left:8px}.comparison{width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px}.comparison th,.comparison td{padding:12px 8px;vertical-align:top;text-align:left;border-bottom:1px solid var(--border);overflow-wrap:anywhere;white-space:normal}.comparison thead th{background:var(--el-fill-color-light);font-weight:500}.comparison tbody th{font-weight:500}.field-column{width:18%}.detail-section{margin-top:24px}.business-ids{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.business-ids :deep(.el-tag){max-width:100%;height:auto;min-height:24px;white-space:normal}.business-ids :deep(.el-tag__content){min-width:0;overflow-wrap:anywhere;white-space:normal}.remark{padding:12px;background:var(--el-fill-color-light);border-radius:6px}.provenance{display:grid;gap:12px;margin:12px 0}.provenance>div{display:grid;grid-template-columns:80px minmax(0,1fr);gap:12px}.provenance dd{margin:0}.evidence{margin-top:20px;border-top:1px solid var(--border)}.evidence figure{margin:12px 0}.evidence figcaption{font-size:12px;color:var(--ink-muted)}.evidence blockquote{margin:8px 0;padding-left:12px;border-left:2px solid var(--border)}.empty{padding:24px 8px;color:var(--ink-muted);font-size:13px}.detail-empty{border-left:1px solid var(--border);min-height:160px;padding:24px}.review-footer{flex-shrink:0;padding:16px 0 max(12px,env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface,var(--el-bg-color))}.footer-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.receipt-review :deep(.el-button){min-height:36px;margin:0;white-space:normal;height:auto}.review-footer>p{margin-top:8px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:799px){.bank-summary{gap:4px 12px}.bank-amount{font-size:26px}.review-columns{grid-template-columns:minmax(0,1fr)}.candidate-sidebar{padding-right:0}.candidate-detail,.detail-empty{border-left:0;border-top:1px solid var(--border);padding-left:0}.comparison th,.comparison td{padding:10px 4px;font-size:12px}.provenance>div{grid-template-columns:64px minmax(0,1fr);gap:8px}.review-header{gap:8px}.review-footer{padding-top:12px}.footer-actions{gap:8px}.bank-facts{gap:12px 24px}}
</style>
