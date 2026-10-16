export type BillInspection = { raw: string; bill: string; status: 'valid' | 'duplicate' | 'invalid'; suggestion: string }
export function inspectBills(value: string): BillInspection[]
export function duplicateBills(rows: BillInspection[]): { bill: string; count: number }[]
