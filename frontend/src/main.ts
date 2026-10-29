import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import en from 'element-plus/es/locale/lang/en'
const mailEnglish = import.meta.env.VITE_EDITION === 'mail_en'
if (mailEnglish) { document.documentElement.lang = 'en'; document.title = 'Mail Reconciliation' }

createApp(App).use(ElementPlus, mailEnglish ? { locale: en } : {}).mount('#app')
