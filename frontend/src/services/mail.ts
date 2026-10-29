import axios from 'axios'
import type { MatchingCandidate, LedgerMetadata, MatchingRow } from './mailMatching'

export type MailPayment = Partial<Omit<MatchingCandidate, 'score' | 'breakdown' | 'payer' | 'currency' | 'warnings' | 'business_ids'>> & {
  payer?: string | null; currency?: string | null; warnings?: string[] | null; business_ids?: string[] | null
}
export interface MailPaymentSummary {
  count: number; complete_count: number; payers: string[]; dates: string[]
  amounts: { amount: string; currency: string }[]; business_ids: string[]
  status: 'pending' | 'ready' | 'partial' | 'empty' | 'failed'
}
export interface MailRelatedReceipts {
  ledger: LedgerMetadata | null; rows: MatchingRow[]; total_rows: number; day_window: number
}

export interface MailRow {
  id: string; subject: string; sender: string; received: string; source: string; folder: string
  sources?: {folder:string;store:string}[]
  payment_summary?: MailPaymentSummary; payment_candidates?: MailPayment[]
  candidates: string[]; attachments: string[]; body?: string; truncated?: boolean
  latest_body?: string; quoted_body?: string; signature?: string
  attachment_details?: MailAttachment[]; attachment_warning?: string
}
export interface MailEvidence {
  value: string; page: number; line: number; quote: string; confidence: number | null
  box: number[][] | null; currency?: string; note?: string
}
export interface MailAttachment {
  id: string; name: string; size: number; status: 'pending'|'running'|'done'|'failed'|'unsupported'|'skipped'
  error?: string
  result?: {
    warnings: string[]; fields: Record<string, MailEvidence[]>
    pages: { number: number; method: string; preview: string; text: string; width: number; height: number
      lines: { text: string; confidence: number | null; box: number[][] | null }[] }[]
  }
}
export interface MailPerformance {
  active_analysis?: number
  elapsed_seconds: number
  counters: { read_batches:number; read_mail:number; unchanged_mail:number; completed_attachments:number; analysis_runs:number; cache_hits:number; analysis_failures:number; ocr_pages:number; pdf_text_pages:number }
  seconds: { read_seconds:number; attachment_read_seconds:number; analysis_seconds:number; save_seconds:number; cache_wait_seconds:number }
  recent: { completed_attachments:number; ocr_pages:number; pdf_text_pages:number }
  last_batch: { read_seconds:number; scanned:number; unchanged_mail:number; attachment_read_seconds:number } | null
}
export interface MailDiagnostic {
  kind:string;phase:string;error_type:string;error_code:number|null;attempt:number;limit:number
  reason:string;action:string;message:string;at:string;status?:'recovering'|'paused'|'recovered'
}
export interface MailStatus {
  analysis_halted?:boolean;analysis_recovering?:boolean;analysis_recovery?:string
  analysis_diagnostic?:MailDiagnostic;analysis_diagnostics?:MailDiagnostic[];status_diagnostic?:MailDiagnostic|null
  performance?: MailPerformance; analysis_workers?:number; analysis_active?:number; queue_paused?:boolean
  saved_total?:number; analysis_pending?:number
  busy: boolean; enabled: boolean; seconds: number; last_run: string | null; next_run: string | null
  error: string; errors: string[]; partial: boolean; new: number; read: number; outlook_supported: boolean
  store?: string; folder?: string; days?: number; limit?: number; folder_label?: string
  folders?: {store:string;folder:string;label?:string}[]; include_children?:boolean; start_date?:string; end_date?:string
  folders_total?:number; folders_done?:number
  analysis_busy?: boolean
  analysis_skipped?: number
  analysis_warning?: string
  analysis_error?: string
  resume_on_start?: boolean; sync_phase?: 'history' | 'incremental'; sync_complete?: boolean
  sync_scanned?: number; sync_start?: string; sync_end?: string
}
export interface MailFolder { id: string; store: string; label: string; name?:string; parent?:string|null; store_label?:string; selectable?:boolean }
const client = axios.create({ baseURL: '/api/mail', headers: { 'X-Finance-Local': '1' }, timeout: 30000 })
export const mailApi = {
  preview: async (mailId:string,attachmentId:string,page:number) => (await client.get<Blob>(`/messages/${encodeURIComponent(mailId)}/attachments/${encodeURIComponent(attachmentId)}/preview/${page}`,{responseType:'blob'})).data,
  status: async () => (await client.get<MailStatus>('/status')).data,
  diagnostics: async (days=3) => (await client.get('/diagnostics/export',{params:{days},responseType:'blob'})).data,
  messagePage: async (params:{page:number;page_size:number;keyword:string;payment_filter:string}) => (await client.get<{rows:MailRow[];total:number;total_saved:number;page:number;page_size:number}>('/messages/page',{params})).data,
  messages: async () => (await client.get<MailRow[]>('/messages')).data,
  message: async (id: string) => (await client.get<MailRow>(`/messages/${encodeURIComponent(id)}`)).data,
  receipts: async (id: string, limit = 5, dayWindow = 3, paymentId?:string) =>
    (await client.get<MailRelatedReceipts>(`/messages/${encodeURIComponent(id)}/receipts`, { params: { day_window: dayWindow, limit,payment_id:paymentId || undefined } })).data,
  analyze: async (id: string) => (await client.post(`/messages/${encodeURIComponent(id)}/analyze`)).data,
  folders: async () => (await client.post<{ folders: MailFolder[]; errors: string[]; note: string }>('/folders')).data,
  scan: async (selection: object) => (await client.post<MailStatus>('/scan', selection)).data,
  poll: async (selection: object) => (await client.put<MailStatus>('/poll', selection)).data,
  clear: async () => client.delete('/messages'),
  import: async (file: File) => {
    const data = new FormData(); data.append('mail', file)
    return (await client.post<{new: number; id: string}>('/import', data)).data
  },
}
