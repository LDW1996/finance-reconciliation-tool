import axios from 'axios'

export type MatchingScope = 'unidentified' | 'unclosed' | 'all'
export type ReviewState = 'pending' | 'confirmed' | 'rejected'
export interface LedgerMetadata {
  id: string; name: string; sheet: string; entries_count: number
  available_sheets: string[]; imported_at: string
  skipped?: number; warnings?: string[]
  online?: { spreadsheet_id: string; sheet_id: string | number; url: string }
}
export interface Receipt {
  id: string; row: number; date: string | null; amount: number | string | null
  currency: string; payer: string; ref: string; description: string; status: string
}
export interface MatchingCandidate {
  payment_id: string; id: string; mail_id: string; attachment_id: string; page: number
  payer: string; date: string | null; amount: number | string | null; currency: string
  score: number; breakdown: { amount: number; payer: number; date: number }
  warnings: string[]; subject: string; sender: string; received: string; keyword: string
  business_ids: string[]; remark: string; search_info: unknown; evidence: unknown
  related_mail_ids?: string[]
  evidence_signature?: string; attachment_name?: string
}
export interface MatchingRow {
  receipt: Receipt; candidates: MatchingCandidate[]; candidate_count: number; warnings: string[]
  review: { state: ReviewState; payment_id?: string }
}
export interface MatchingResult {
  ledger: LedgerMetadata | null
  summary: { total: number; eligible: number; matched: number; unmatched: number; confirmed: number }
  rows: MatchingRow[]; revision: string; page: number; total_rows: number; confirmation_token?: string
}
export interface MatchingQuery {
  scope: MatchingScope; page: number; page_size: number; minimum_score: number
  day_window: number; candidate_limit: number
  keyword?: string; sort_by?: 'source' | 'score'
}
export interface OnlineConfig {
  configured: boolean; spreadsheet_url: string; sheet_id: string | number
  currency: string; service_email: string; last_read_at: string | null
}
export interface OnlineWriteback {
  written: number; unchanged: number; failed: number; uncertain: number
  items: { ref: string; status: string; message: string }[]
}
export interface OnlineHistoryItem {
  id: string; created_at: string; ref: string; status: string; message: string
  before?: unknown; after?: unknown; spreadsheet_id: string; sheet_id: string | number; row: number
}
export function validateSpreadsheetUrl(value: string): string {
  try {
    const url = new URL(value.trim())
    const gid = new URLSearchParams(url.hash.slice(1)).get('gid') ?? url.searchParams.get('gid')
    if (url.protocol === 'https:' && url.hostname === 'docs.google.com' &&
      /^\/spreadsheets\/d\/[\w-]+(?:\/|$)/.test(url.pathname) && gid !== null && /^\d+$/.test(gid)) return url.href
  } catch { /* Invalid URL. */ }
  throw new Error('请输入含 gid 的 Google Sheets 工作表链接')
}
const client = axios.create({ baseURL: '/api/mail', headers: { 'X-Finance-Local': '1' }, timeout: 30000 })
export const mailMatchingApi = {
  onlineConfig: async (signal?: AbortSignal) =>
    (await client.get<OnlineConfig>('/online/config', { signal })).data,
  saveOnlineConfig: async (spreadsheetUrl: string, currency: string, credential?: File, signal?: AbortSignal) => {
    const data = new FormData()
    data.append('spreadsheet_url', validateSpreadsheetUrl(spreadsheetUrl))
    data.append('currency', currency)
    if (credential) {
      if (!/\.json$/i.test(credential.name)) throw new Error('请选择服务账号 JSON 文件')
      data.append('credential', credential)
    }
    return (await client.post<OnlineConfig>('/online/config', data, { signal })).data
  },
  refreshOnline: async (signal?: AbortSignal) =>
    (await client.post<LedgerMetadata>('/online/refresh', undefined, { signal, timeout: 120000 })).data,
  writebackOnline: async (data: { ledger_id: string; day_window: number; expected_count: number; confirmation_token: string }, signal?: AbortSignal) =>
    (await client.post<OnlineWriteback>('/online/writeback', data, { signal, timeout: 120000 })).data,
  onlineHistory: async (signal?: AbortSignal) =>
    (await client.get<{ items: OnlineHistoryItem[] }>('/online/history', { signal })).data,
  ledger: async (signal?: AbortSignal) => (await client.get<LedgerMetadata | null>('/ledger', { signal })).data,
  importLedger: async (file: File, currency = 'IDR', sheet?: string, signal?: AbortSignal) => {
    if (!/\.(xlsx|xls|csv)$/i.test(file.name)) throw new Error('请选择 xlsx、xls 或 csv 文件')
    if (file.size > 25 * 1024 * 1024) throw new Error('台账文件不能超过 25MB')
    const data = new FormData()
    data.append('workbook', file)
    data.append('currency', currency)
    if (sheet?.trim()) data.append('sheet_name', sheet.trim())
    return (await client.post<LedgerMetadata>('/ledger/import', data, { signal })).data
  },
  matching: async (params: MatchingQuery, signal?: AbortSignal) =>
    (await client.get<MatchingResult>('/matching', { params, signal })).data,
  review: async (data: { ledger_id: string; receipt_id: string; payment_id: string | null; state: ReviewState; day_window: number; expected_signature?: string }, signal?: AbortSignal) =>
    (await client.post<{ ok: true }>('/matching/review', data, { signal })).data,
  export: async (params: Pick<MatchingQuery, 'scope' | 'day_window' | 'minimum_score'>, signal?: AbortSignal) =>
    (await client.get<Blob>('/matching/export', { params, responseType: 'blob', signal })).data,
}

export function matchingError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (error.code === 'ECONNABORTED') return '请求超时，请重试'
    return error.message || '请求失败，请重试'
  }
  return error instanceof Error ? error.message : '操作失败，请重试'
}
