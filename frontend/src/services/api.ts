import axios from 'axios'

export interface AnalyzeSummary {
  companyA: string
  companyB: string
  filename: string
  matched: number
  differences: number
  unmatched: number
  total: number
  matchedAllocations: number
  differenceAllocations: number
  unmatchedAllocations: number
  blob: Blob
}

export interface InspectReport {
  valid: boolean
  errors: string[]
  warnings: string[]
  companyCodes: string[]
  rowCount: number
  allocationCount: number
  requiredColumns: string[]
}

export async function inspectCombinedExcel(
  workbook: File,
  matchField: string,
  amountField: string,
): Promise<InspectReport> {
  const form = new FormData()
  form.append('workbook', workbook)
  form.append('match_field', matchField)
  form.append('amount_field', amountField)

  const response = await axios.post<InspectReport>('/api/inspect-combined', form)
  return response.data
}

export async function analyzeCombinedExcel(
  workbook: File,
  matchField: string,
  amountField: string,
): Promise<AnalyzeSummary> {
  const form = new FormData()
  form.append('workbook', workbook)
  form.append('match_field', matchField)
  form.append('amount_field', amountField)

  const response = await axios.post('/api/analyze-combined', form, { responseType: 'blob' })
  const headers = response.headers
  return {
    companyA: headers['x-company-a-code'] ?? '-',
    companyB: headers['x-company-b-code'] ?? '-',
    filename: decodeURIComponent(headers['x-output-filename'] ?? '对账结果.xlsx'),
    matched: Number(headers['x-summary-matched'] ?? 0),
    differences: Number(headers['x-summary-differences'] ?? 0),
    unmatched: Number(headers['x-summary-unmatched'] ?? 0),
    total: Number(headers['x-summary-total'] ?? 0),
    matchedAllocations: Number(headers['x-allocation-matched'] ?? 0),
    differenceAllocations: Number(headers['x-allocation-differences'] ?? 0),
    unmatchedAllocations: Number(headers['x-allocation-unmatched'] ?? 0),
    blob: response.data,
  }
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
