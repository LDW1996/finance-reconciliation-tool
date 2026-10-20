import { test } from 'node:test'
import assert from 'node:assert/strict'
import { inspectBills, duplicateBills } from './billValidation.mjs'

test('lists repeated normalized bills with total occurrences', () => {
  assert.deepEqual(duplicateBills(inspectBills('TRBTSTZH2606S,trbtstzh2606s TRBTSTZH2606S FSBTSTIE2606S')), [{ bill: 'TRBTSTZH2606S', count: 3 }])
  assert.deepEqual(duplicateBills(inspectBills('&^TRBTSTOD2609N TRBTSTOD2609N')), [])
})

test('splits separators and deduplicates case-insensitively', () => {
  const rows = inspectBills('TRBTSTZH2606S，trbtstzh2606s; FSBTSTIE2606S\n')
  assert.deepEqual(rows.map(r => r.status), ['valid', 'duplicate', 'valid'])
  assert.equal(rows[1].bill, 'TRBTSTZH2606S')
})
test('marks suspicious symbols without silently accepting them', () => {
  const [row] = inspectBills('&^TRBTSTOD2609N')
  assert.equal(row.status, 'invalid')
  assert.equal(row.suggestion, 'TRBTSTOD2609N')
})
test('does not invent corrections for internal symbols or arbitrary text', () => {
  for (const value of ['TRBT^STOD2609N', '<script>', 'A'.repeat(81)]) {
    assert.equal(inspectBills(value)[0].status, 'invalid')
  }
  assert.equal(inspectBills('TRBT^STOD2609N')[0].suggestion, '')
})
test('accepts partial and numeric platform queries without dropping leading zeroes', () => {
  const rows = inspectBills('082600028858 TRBT 2609 A-1/2_3')
  assert.deepEqual(rows.map(r => r.status), ['valid', 'valid', 'valid', 'valid'])
  assert.equal(rows[0].bill, '082600028858')
})
test('accepts existing example format and ignores empty input', () => {
  assert.equal(inspectBills('SITGSHBAZA00451')[0].status, 'valid')
  assert.deepEqual(inspectBills(' \n,；'), [])
})
