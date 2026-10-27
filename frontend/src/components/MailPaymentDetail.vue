<template>
  <article class="payment-detail">
    <header class="source-header"><h2>{{ mail.subject || '（无主题）' }}</h2><p>{{ mail.sender || '发件人未提供' }} · {{ mail.received || '时间未提供' }}</p></header>
    <details v-if="mail.source === 'outlook'"><summary>邮件存档来源<template v-if="mail.sources && mail.sources.length > 1"> · {{ mail.sources.length }} 处副本已合并</template></summary><p v-for="(source,index) in (mail.sources?.length ? mail.sources : [{folder:mail.folder,store:''}])" :key="index" class="muted">{{ source.folder || '目录未提供' }}</p></details>
    <section aria-label="付款信息">
      <div class="section-title"><h3>付款信息</h3><span class="status">{{ statusLabel }}</span></div>
      <label v-if="payments.length > 1" class="payment-picker">选择付款记录
        <el-select v-model="paymentIndex" aria-label="选择付款记录">
          <el-option v-for="(item, index) in payments" :key="item.payment_id || item.id || index" :value="index" :label="`${index + 1}. ${item.payer || '付款人待补充'} · ${amountText(item.amount, item.currency)} · ${item.date || '日期待补充'} · ${sourceName(item)}${item.page ? ` 第 ${item.page} 页` : ''}`" />
        </el-select>
      </label>
      <div class="facts">
        <div><span>付款人</span><strong>{{ payment?.payer || '待补充付款人' }}</strong></div>
        <div><span>付款日期</span><strong>{{ payment?.date || '待补充付款日期' }}</strong></div>
        <div><span>付款金额</span><strong>{{ amountText(payment?.amount, payment?.currency) }}</strong></div>
      </div>
      <p v-if="payment" class="muted">来源：{{ sourceName(payment) }}<template v-if="payment.page"> · 第 {{ payment.page }} 页</template> · 请结合付款原件核对</p>
      <p v-else class="muted">{{ emptyPaymentText }}</p>
      <p v-for="warning in paymentWarnings" :key="warning" class="warning">{{ warning }}</p>
    </section>
    <section class="related" aria-label="相关银行流水" :aria-busy="relatedLoading">
      <div class="section-title"><h3>相关银行流水</h3><span class="muted">{{ related ? `付款日期前后 ${related.day_window} 天` : '按付款信息查找' }}</span></div>
      <p v-if="relatedLoading" role="status">正在查找相关流水…</p>
      <p v-if="relatedError" class="warning" role="alert">{{ relatedError }}</p>
      <template v-if="related && !relatedLoading">
        <p v-if="!related.ledger" class="muted">尚未读取银行流水，请前往「收款匹配」读取流水。</p>
        <template v-else>
          <p v-if="!related.rows.length" class="muted">当前范围内暂无相关流水，可在「收款匹配」扩大范围查找。</p>
          <p v-if="related.rows.length" class="muted">找到 {{ related.total_rows }} 笔候选，显示前 {{ related.rows.length }} 笔</p>
          <div class="related-list">
          <article v-for="row in related.rows" :key="row.receipt.id" class="receipt">
            <div class="section-title"><strong>{{ amountText(row.receipt.amount, row.receipt.currency) }}</strong><span>{{ reviewLabel(row.review.state) }}</span></div>
            <p>{{ row.receipt.payer || '付款人未提供' }} · {{ row.receipt.date || '日期未提供' }}</p>
            <p class="muted">流水号：{{ row.receipt.ref || '未提供' }} · 匹配分 {{ row.candidates[0]?.score ?? '—' }}</p>
            <el-button @click="$emit('open-receipt', row.receipt.id, row.receipt.ref)">核对这笔流水</el-button>
          </article>
          </div>
          <p v-if="related.rows.length" class="muted">匹配分仅供查找参考，请打开流水核对付款来源。</p>
          <el-button v-if="related.total_rows > related.rows.length" link @click="$emit('more-receipts')">查看更多流水（最多 20 笔）</el-button>
        </template>
      </template>
    </section>
    <section class="optional"><h3>业务编号 <small>可选补充</small></h3><p>{{ businessIds.length ? businessIds.join('、') : '未提及业务编号，可用邮件信息备注' }}</p><p v-if="businessIds.length" class="muted">邮件中的号码仅作关联线索，需核对与本笔付款的关系。</p><span class="muted">建议备注</span><p class="remark">{{ suggestedRemark }}</p></section>
    <details><summary>付款原件与识别依据</summary><MailAttachments :key="mail.id" :assets="readableAssets" :legacy-names="mail.attachments || []" :busy="!!analysisBusy" :warning="readableWarning(mail.attachment_warning || '')" @analyze="$emit('analyze')" /></details>
    <details><summary>邮件正文与历史引用</summary>
      <p v-if="mail.truncated" class="warning">正文过长，仅保留前 20 万字符</p>
      <el-tabs :key="mail.id" model-value="latest">
        <el-tab-pane label="最新正文" name="latest"><pre>{{ mail.latest_body ?? mail.body ?? '（正文为空）' }}</pre></el-tab-pane>
        <el-tab-pane label="历史引用" name="history"><pre>{{ mail.quoted_body || '未识别到历史引用' }}</pre></el-tab-pane>
        <el-tab-pane label="签名" name="signature"><pre>{{ mail.signature || '未识别到签名' }}</pre></el-tab-pane>
        <el-tab-pane label="完整正文" name="original"><pre>{{ mail.body || '（正文为空）' }}</pre></el-tab-pane>
      </el-tabs>
    </details>
  </article>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import MailAttachments from './MailAttachments.vue'
import type { MailRow, MailPayment, MailRelatedReceipts } from '../services/mail'
const props = defineProps<{ mail: MailRow; related: MailRelatedReceipts | null; relatedLoading: boolean; relatedError: string; analysisBusy?: boolean }>()
const emit=defineEmits<{ analyze: []; 'open-receipt': [receiptId: string, ref: string]; 'more-receipts': []; 'select-payment':[paymentId:string] }>()
const paymentIndex = ref(0)
const payments = computed(() => props.mail.payment_candidates || [])
const payment = computed(() => payments.value[paymentIndex.value])
watch(()=>payment.value?.id || '',id=>emit('select-payment',id),{immediate:true})
watch(() => props.mail.id, () => { paymentIndex.value = 0 })
watch(() => payments.value.length, count => { if (paymentIndex.value >= count) paymentIndex.value = 0 })
const labels = { pending: '正在识别', ready: '信息齐全 · 待核对', partial: '信息待补充', empty: '暂无适合的付款信息', failed: '识别失败，可重试' }
const statusLabel = computed(() => labels[props.mail.payment_summary?.status || 'pending'])
const emptyPaymentText = computed(() => props.mail.payment_summary?.status === 'empty' ? '未识别到适合的付款信息，可查看邮件原文和附件。' : props.mail.payment_summary?.status === 'failed' ? '付款信息识别失败，可在付款原件中重试。' : '付款信息尚未就绪，可先查看邮件原文和附件。')
const amountText = (amount: string | number | null | undefined, currency?: string | null) => amount === null || amount === undefined || amount === '' ? '待补充付款金额' : `${currency || '币种待补充'} ${Number.isFinite(Number(amount))?Number(amount).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):amount}`
const sourceName = (item: MailPayment) => item.attachment_name || props.mail.attachment_details?.find(asset => asset.id === item.attachment_id)?.name || '邮件付款信息'
const businessIds = computed(() => payment.value ? payment.value.business_ids || [] : props.mail.payment_summary?.business_ids || [])
const suggestedRemark = computed(() => {
  const p = payment.value
  const facts = p ? [p.payer, p.date, p.amount != null && p.amount !== '' ? `${p.currency || ''} ${p.amount}`.trim() : ''].filter(Boolean).join(' · ') : ''
  return p?.remark || [facts,props.mail.subject || '（无主题）', props.mail.sender || '发件人未提供', props.mail.received || '时间未提供'].filter(Boolean).join(' · ')
})
function readableWarning(value: string): string {
  if (/[\u3400-\u9fff]/.test(value)) return value
  const field = /payer/i.test(value) ? '付款人' : /date/i.test(value) ? '付款日期' : /amount/i.test(value) ? '付款金额' : ''
  if (/missing/i.test(value) && field) return `未识别到${field}，请查看原件补充。`
  if (/ambiguous/i.test(value) && field) return `同页出现多个${field}，请核对原件中的对应关系。`
  if (/invalid/i.test(value) && field) return `${field}格式无法识别，请核对原件。`
  if (/unknown currency/i.test(value)) return '币种不明确，请核对付款原件。'
  if (/business IDs/i.test(value)) return '业务编号仅供备注参考，付款归属仍需核对。'
  return value
}
const paymentWarnings = computed(() => [...new Set((payment.value?.warnings || []).map(readableWarning).filter(value=>value&&!/^邮件中的业务号码为关联线索|^业务编号仅供备注参考/.test(value)))])
const readableAssets = computed(() => (props.mail.attachment_details || []).map(asset => ({ ...asset, error: readableWarning(asset.error || ''), result: asset.result ? { ...asset.result, warnings: asset.result.warnings.map(readableWarning).filter(Boolean) } : undefined })))
const reviewLabel = (state: string) => ({ pending: '待核对', confirmed: '已确认关联', rejected: '暂不关联' }[state] || '待核对')
</script>

<style scoped>
.payment-detail{min-width:0;color:var(--ink)}.source-header h2{font-size:20px;line-height:1.5;margin:0 0 8px;overflow-wrap:anywhere}.source-header p,.muted{color:var(--ink-muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}.source-header{margin-bottom:24px}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}h3{font-size:15px;margin:0 0 12px}.status{font-size:12px;color:var(--ink-muted)}.facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}.facts>div{background:var(--el-fill-color-light,#f5f5f7);border:1px solid var(--border);border-radius:10px;padding:16px;min-width:0}.facts span{display:block;font-size:12px;color:var(--ink-muted);margin-bottom:8px}.facts strong{font-size:18px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.payment-picker{display:flex;flex-direction:column;gap:8px;font-size:13px}.payment-picker .el-select{width:100%}.related,.optional{border-top:1px solid var(--border);padding-top:20px;margin-top:24px}.receipt{border:1px solid var(--border);border-radius:8px;padding:16px;margin-top:12px}.receipt p{font-size:13px;overflow-wrap:anywhere}.receipt .section-title span{font-size:12px;color:var(--ink-muted)}.warning{color:var(--el-color-warning-dark-2);font-size:13px;overflow-wrap:anywhere}.optional{font-size:13px}.optional small{font-weight:400;color:var(--ink-muted);margin-left:8px}.remark{white-space:pre-wrap;overflow-wrap:anywhere}details{border-top:1px solid var(--border);padding:16px 0;margin-top:16px}summary{cursor:pointer;font-size:14px;font-weight:500;line-height:1.7}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.9 system-ui,sans-serif;max-height:420px;overflow:auto}summary:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:4px}@media(max-width:700px){.facts{grid-template-columns:1fr}.facts>div{padding:12px}.facts strong{font-size:17px}}
.related-list{max-height:320px;overflow-y:auto;padding:0 6px 4px 0;overscroll-behavior:contain}
</style>
