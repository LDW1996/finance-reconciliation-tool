import './services/platformHttp'
import { createApp, h } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { platformLanguage, platformText } from './platformLocale'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ElConfigProvider } from 'element-plus'
import en from 'element-plus/es/locale/lang/en'
const mailEnglish = import.meta.env.VITE_EDITION === 'mail_en'
if (mailEnglish) { document.documentElement.lang = 'en'; document.title = 'Mail Reconciliation' }

const app = createApp({ render: () => h(ElConfigProvider, { locale: mailEnglish || platformLanguage.value === 'en' ? en : zhCn }, { default: () => h(App) }) })
app.config.globalProperties.$platformText = platformText
app.use(ElementPlus).mount('#app')
