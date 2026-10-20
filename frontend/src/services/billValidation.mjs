// Syntax screening only; existence and permissions must be checked in iLog.
const format = /^[A-Z0-9_/-]{1,80}$/
export function duplicateBills(rows) {
  const counts = new Map()
  for (const row of rows) {
    if (row.status !== 'invalid') counts.set(row.bill, (counts.get(row.bill) || 0) + 1)
  }
  return [...counts].filter(([, count]) => count > 1).map(([bill, count]) => ({ bill, count }))
}
export function inspectBills(value) {
  const seen = new Set()
  return value.split(/[\s,，;；]+/).filter(Boolean).map(raw => {
    const bill = raw.toUpperCase()
    if (!format.test(bill)) {
      const candidate = bill.replace(/^[^A-Z0-9]+|[^A-Z0-9]+$/g, '')
      return { raw, bill, status: 'invalid', suggestion: format.test(candidate) ? candidate : '' }
    }
    const status = seen.has(bill) ? 'duplicate' : 'valid'
    seen.add(bill)
    return { raw, bill, status, suggestion: '' }
  })
}
