import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { mailEnglish } from './tooling/mailEnglish'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [mailEnglish(), vue()],
  resolve: { alias: process.env.VITE_EDITION === 'mail_en' ? [{ find: /^\.\/App\.vue$/, replacement: fileURLToPath(new URL('./src/MailEnglishApp.vue', import.meta.url)) }] : process.env.VITE_EDITION === 'excel' ? [{ find: /^\.\/App\.vue$/, replacement: fileURLToPath(new URL('./src/ExcelApp.vue', import.meta.url)) }] : [] },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
