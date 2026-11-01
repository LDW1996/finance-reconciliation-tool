import { ref, watch } from 'vue'
import mailCatalog from '../tooling/mailEnglish.json'
import excelCatalog from '../tooling/excelEnglish.json'
import platformCatalog from '../tooling/platformEnglish.json'
const catalog = { ...mailCatalog, ...excelCatalog, ...platformCatalog }
export type PlatformLanguage = 'zh-CN' | 'en'
const readLanguage = (): PlatformLanguage => localStorage.getItem('finance-platform-language') === 'en' ? 'en' : 'zh-CN'
export const platformLanguage = ref<PlatformLanguage>(readLanguage())
window.addEventListener('platform-language-change', () => { platformLanguage.value = readLanguage() })
window.addEventListener('storage', event => { if (event.key === 'finance-platform-language') platformLanguage.value = readLanguage() })
watch(platformLanguage, language => {
  const english = import.meta.env.VITE_EDITION === 'mail_en' || language === 'en'
  document.documentElement.lang = english ? 'en' : 'zh-CN'
  if (!import.meta.env.VITE_EDITION || import.meta.env.VITE_EDITION === 'full') document.title = english ? 'Finance Operations Platform' : '财务综合业务平台'
}, { immediate: true })
export function platformText(text: string): string {
  if (platformLanguage.value !== 'en') return text
  const values = catalog as Record<string, string>
  if (values[text]) return values[text]
  const trimmed = text.trim()
  if (values[trimmed]) return text.replace(trimmed, values[trimmed])
  // Only authored source text reaches this function. Longest catalog segments win.
  return text.replace(/[\u4e00-\u9fff][\u4e00-\u9fff，。、：；（）！？/ ·\-]*/g, segment => values[segment] || segment)
}
declare module 'vue' { interface ComponentCustomProperties { $platformText: typeof platformText } }
