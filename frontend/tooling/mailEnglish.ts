import type { Plugin } from 'vite'
import { readFileSync } from 'node:fs'

// This allowlist is deliberately limited to source code. No email content,
// customer names, bank descriptions or other API values are translated.
const mailSource = /\/src\/(?:views\/MailView\.vue|components\/(?:MailAttachments|MailFolderTree|MailPaymentDetail|MailStorageSettings|ReceiptMatching|ReceiptReview|CustomerMapping|CustomerMappingPreview)\.vue|services\/mail[^/]*\.ts)$/
export const chineseSegment = /[\u4e00-\u9fff][\u4e00-\u9fff，。、：；（）！？/ ·\-]*/g
export function translateMailSource(source: string, catalog: Record<string, string>, filename: string): string {
  return source.replace(chineseSegment, segment => {
    const value = catalog[segment]
    if (!value || /[\u4e00-\u9fff'"`\\$<>]/.test(value)) {
      throw new Error(`Missing or unsafe English catalog entry in ${filename}: ${JSON.stringify(segment)}`)
    }
    return value
  }).replaceAll("'zh-CN'", "'en-US'")
    .replace(/[，。；：（）【】]/g, value => ({ '，': ', ', '。': '.', '；': '; ', '：': ': ', '（': ' (', '）': ')', '【': '[', '】': ']' })[value]!)
}
export function mailEnglish(): Plugin {
  let catalog: Record<string, string> = {}
  const enabled = process.env.VITE_EDITION === 'mail_en'
  return {
    name: 'mail-english-static-source',
    enforce: 'pre',
    buildStart() {
      if (enabled) catalog = JSON.parse(readFileSync(new URL('./mailEnglish.json', import.meta.url), 'utf8'))
    },
    transform(source, id) {
      if (!enabled || !mailSource.test(id.split('?')[0]!)) return
      // Use the structured queue flag; translated prose is not a state identifier.
      const prepared = source.replace("status.queue_paused && status.error.includes('队列')", 'status.queue_paused')
      return { code: translateMailSource(prepared, catalog, id), map: null }
    },
    transformIndexHtml(html) {
      return enabled ? html.replace(/lang="[^"]*"/, 'lang="en"').replace(/<title>[^<]*<\/title>/, '<title>Mail Reconciliation</title>') : html
    },
  }
}
