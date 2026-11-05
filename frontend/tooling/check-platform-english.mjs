import { readFileSync, readdirSync, statSync } from 'node:fs'
import assert from 'node:assert/strict'
import { transform } from 'esbuild'

// The platform (and excel) editions translate at runtime: every authored UI
// literal is wrapped in $platformText() and resolved against the merged
// catalog. A segment with no catalog entry falls back to Chinese, so a missing
// entry is a silent language leak rather than a build error. This check makes
// that leak loud.
const root = new URL('../', import.meta.url)
const catalog = {}
for (const name of ['mailEnglish', 'excelEnglish', 'platformEnglish']) {
  Object.assign(catalog, JSON.parse(readFileSync(new URL(`./${name}.json`, import.meta.url), 'utf8')))
}

// Mirrors the segment split in src/platformLocale.ts.
const SEGMENT = /[\u4e00-\u9fff][\u4e00-\u9fff，。、：；（）！？/ ·\-]*/g
const CJK = /[\u4e00-\u9fff]/
// src/i18n.ts is the Excel edition's own locale dictionary: Chinese there is
// source data selected by locale, not a UI literal to translate.
const SKIP = new Set(['src/i18n.ts'])

const files = []
const walk = dir => {
  for (const name of readdirSync(dir)) {
    const path = `${dir}/${name}`
    if (statSync(new URL(path, root)).isDirectory()) walk(path)
    else if (/\.(vue|ts)$/.test(name)) files.push(path)
  }
}
walk('src')

// A trailing space or punctuation-only difference is resolved by the trim path
// in platformText; the leading segment is what actually gets replaced.
const resolve = text => {
  if (catalog[text] !== undefined) return catalog[text]
  const trimmed = text.trim()
  if (catalog[trimmed] !== undefined) return catalog[trimmed]
  return undefined
}

const failures = []
for (const file of files) {
  if (SKIP.has(file)) continue
  const source = readFileSync(new URL(file, root), 'utf8')
  for (const match of source.matchAll(SEGMENT)) {
    const segment = match[0]
    const translation = resolve(segment)
    const line = source.slice(0, match.index).split('\n').length
    if (translation === undefined) failures.push(`${file}:${line} 未收录 ${JSON.stringify(segment)}`)
    // A translation may keep a short quoted label (如 “分配”) but must never
    // reintroduce UI Chinese, quotes or template syntax into the catalog.
    else if (CJK.test(translation) && !/“[^”]*”/.test(translation)) failures.push(`${file}:${line} 译文含中文 ${JSON.stringify(segment)} -> ${JSON.stringify(translation)}`)
    else if (/['"`\\$<>]/.test(translation)) failures.push(`${file}:${line} 译文含不安全字符 ${JSON.stringify(segment)} -> ${JSON.stringify(translation)}`)
  }
}

assert.equal(failures.length, 0, `英文目录缺口 ${failures.length} 处：\n${failures.join('\n')}`)

// Exercise the actual runtime function, without booting Vue or reading user data.
const localeSource = readFileSync(new URL('src/platformLocale.ts', root), 'utf8')
const functionSource = localeSource.slice(localeSource.indexOf('export function platformText'), localeSource.indexOf('declare module'))
const compiled = await transform(functionSource.replace('export function', 'function'), { loader: 'ts' })
const language = { value: 'en' }
const platformText = Function('platformLanguage', 'catalog', `${compiled.code}; return platformText`)(language, catalog)
// String-level regression samples, not a browser end-to-end test.
const samples = [
  '查询航次并核对',
  '选择航次分组',
  '输入代表提单',
  '本次操作影响整个航次，不仅是输入提单。',
  '航次平台成功（未复查）',
  '正在登录…',
  '当前任务正在处理，请等待完成后再登录',
]
for (const sample of samples) {
  const translated = platformText(sample)
  assert.ok(!CJK.test(translated), `未翻译：${sample} -> ${translated}`)
}

const segments = files.filter(file => !SKIP.has(file)).reduce((total, file) => total + (readFileSync(new URL(file, root), 'utf8').match(SEGMENT) || []).length, 0)
console.log(`Platform English catalog covers ${segments} source segments across ${files.length - SKIP.size} files; ${samples.length} runtime translation samples have no Chinese left.`)

language.value = 'zh-CN'
assert.equal(platformText('查询航次并核对'), '查询航次并核对')
