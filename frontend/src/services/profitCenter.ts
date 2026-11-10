import axios from 'axios'

export interface ProfitCenterConfig {
  month: string
  months: string[]
  regions: ProfitCenterRegion[]
  required_files: string[]
  required_files_sample?: string
  templates_note?: string
  optional_reference?: string
}

export interface ProfitCenterTemplateSlot {
  key: string
  label: string
  template: string
  uploaded: boolean
  stored_name: string | null
  size: number
  uploaded_at: string | null
}

export interface ProfitCenterRegion {
  key: string
  label: string
  has_exec_salary: boolean
  template?: string
  report?: string
}

export interface ProfitCenterCheck {
  name: string
  scope: string
  sum: number
  benchmark: number
  delta: number
  pass: boolean
  note: string
}

export interface ProfitCenterDiff {
  region: string
  sheet: string
  cell: string
  expected: string
  actual: string
  match: boolean
}

export interface ProfitCenterRunResult {
  job_id: string
  download_name: string
  checks: ProfitCenterCheck[]
  diffs: ProfitCenterDiff[]
  warnings: string[]
}

const client = axios.create({
  baseURL: '/api/profit-center',
  headers: { 'X-Finance-Local': '1', 'X-Requested-With': 'XMLHttpRequest' },
  timeout: 120000,
})

export const profitCenterApi = {
  config: async () => (await client.get<ProfitCenterConfig>('/config')).data,
  listTemplates: async () =>
    (await client.get<{ templates: ProfitCenterTemplateSlot[] }>('/templates')).data.templates,
  uploadTemplates: async (files: File[]) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return (await client.post<{ templates: ProfitCenterTemplateSlot[] }>('/templates', form)).data.templates
  },
  deleteTemplate: async (key: string) =>
    (await client.delete<{ templates: ProfitCenterTemplateSlot[] }>(`/templates/${encodeURIComponent(key)}`)).data.templates,
  run: async (files: File[], month: string, execSalaries: Record<string, number>) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    form.append('month', month)
    form.append('exec_salaries', JSON.stringify(execSalaries))
    return (await client.post<ProfitCenterRunResult>('/run', form)).data
  },
  download: async (jobId: string) =>
    (await client.get<Blob>(`/download/${encodeURIComponent(jobId)}`, { responseType: 'blob' })).data,
}
