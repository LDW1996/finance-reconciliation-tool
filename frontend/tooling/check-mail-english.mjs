import { readFileSync, readdirSync } from 'node:fs'
import assert from 'node:assert/strict'
import { mailEnglish, translateMailSource } from './mailEnglish.ts'
const root = new URL('../', import.meta.url)
const catalog = JSON.parse(readFileSync(new URL('./mailEnglish.json', import.meta.url)))
const names = ['views/MailView.vue', ...['MailAttachments','MailFolderTree','MailPaymentDetail','MailStorageSettings','ReceiptMatching','ReceiptReview','CustomerMapping','CustomerMappingPreview'].map(n=>`components/${n}.vue`), ...readdirSync(new URL('src/services', root)).filter(n=>/^mail.*\.ts$/.test(n)).map(n=>`services/${n}`)]
let count = 0
for (const name of names) {
 const source = readFileSync(new URL(`src/${name}`, root), 'utf8')
 for (const [segment] of source.matchAll(/[\u4e00-\u9fff][\u4e00-\u9fff，。、：；（）！？/ ·\-]*/g)) {
  assert.ok(catalog[segment], `${name}: missing ${JSON.stringify(segment)}`)
  assert.ok(!/[\u4e00-\u9fff'"`\\$<>]/.test(catalog[segment]), `unsafe translation ${segment}`)
  count++
 }
}
const shell = readFileSync(new URL('src/MailEnglishApp.vue', root), 'utf8')
assert.ok(!/UnlockView|ReconciliationView|\/api\/ilog|ExcelApp/.test(shell))
assert.ok(shell.includes('MailView') && shell.includes('MailStorageSettings'))
console.log(`English catalog covers ${count} source occurrences across ${names.length} files; dedicated shell isolation passed.`)

process.env.VITE_EDITION = 'mail_en'
const plugin = mailEnglish()
plugin.buildStart()
// Build a source expression referencing API values. The source transformation
// translates only its UI literal; the returned customer data remains exact.
const source = "(data) => ({ label: '付款人', customer: data.customer, body: data.body, bank: data.bank })"
const transformed = plugin.transform(source, '/project/src/views/MailView.vue').code
const data = { customer: '上海公司', body: '客户名称：张三，付款金额：100', bank: '中文银行备注' }
const result = Function(`return (${transformed})`)()(data)
assert.equal(result.label, catalog['付款人'])
assert.equal(result.customer, data.customer)
assert.equal(result.body, data.body)
assert.equal(result.bank, data.bank)
assert.equal(plugin.transform(source, '/project/src/App.vue'), undefined)
assert.throws(() => translateMailSource("'新界面文字'", catalog, 'future.vue'), /Missing/)
assert.throws(() => translateMailSource("'付款人'", { '付款人': "Payer's" }, 'unsafe.vue'), /unsafe/)
console.log('Runtime Chinese customer, email and bank values preserved; non-mail source untouched; missing and unsafe catalog entries rejected.')
