<template>
  <main class="platform-login">
    <section class="login-brand" aria-labelledby="platform-name">
      <img class="login-brand-image" :src="workspaceImage" alt="" />
      <div class="login-brand-sheen" aria-hidden="true"></div>
      <div class="login-brand-copy">
        <h1 id="platform-name">财务综合业务平台</h1>
        <p class="login-brand-description">让日常财务工作更有序</p>
        <hr class="login-brand-rule" />
        <p class="login-brand-caption" lang="en">FINANCE OPERATIONS PLATFORM</p>
      </div>
    </section>

    <section class="login-panel" aria-labelledby="login-heading">
      <nav class="login-language" aria-label="语言 / Language">
        <button type="button" :class="{ selected: language === 'zh-CN' }" :aria-pressed="language === 'zh-CN'" @click="changeLanguage('zh-CN')">中文</button>
        <span aria-hidden="true">/</span>
        <button type="button" :class="{ selected: language === 'en' }" :aria-pressed="language === 'en'" @click="changeLanguage('en')">EN</button>
      </nav>

      <div class="login-form-region">
        <header class="login-form-heading">
          <h2 id="login-heading">欢迎回来</h2>
          <p>登录以进入你的工作空间</p>
        </header>
        <form class="login-form" @submit.prevent="login">
          <div class="login-field">
            <label for="platform-username">账号</label>
            <input id="platform-username" v-model="username" name="username" type="text" autocomplete="username" autocapitalize="none" :spellcheck="false" placeholder="请输入账号" required :readonly="busy" />
          </div>
          <div class="login-field">
            <label for="platform-password">密码</label>
            <div class="login-password">
              <input id="platform-password" v-model="password" name="password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" required :readonly="busy" />
              <button type="button" class="login-password-toggle" :aria-label="showPassword ? $platformText('隐藏密码') : $platformText('显示密码')" :aria-pressed="showPassword" @click="showPassword = !showPassword"><el-icon :size="20"><Hide v-if="showPassword" /><View v-else /></el-icon></button>
            </div>
          </div>
          <el-alert v-if="error" :title="$platformText(error)" type="error" :closable="false" show-icon role="alert" />
          <el-button class="login-submit" native-type="submit" type="primary" :loading="busy" :disabled="busy" :aria-busy="busy">{{ busy ? $platformText('正在登录…') : $platformText('登录') }}</el-button>
        </form>
      </div>
      <footer class="login-footer"><span>本地工作空间</span></footer>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Hide, View } from '@element-plus/icons-vue'
import workspaceImage from '../assets/login-workspace.png'
import { platformLanguage, platformText, type PlatformLanguage } from '../platformLocale'
import { loginPlatform, type PlatformSession } from '../services/platformAuth'
const emit = defineEmits<{ authenticated: [session: PlatformSession] }>()
const username = ref(''), password = ref(''), busy = ref(false), error = ref(''), showPassword = ref(false)
const language = platformLanguage
function changeLanguage(value: PlatformLanguage) {
  localStorage.setItem('finance-platform-language', value)
  window.dispatchEvent(new Event('platform-language-change'))
}
async function login() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    emit('authenticated', await loginPlatform(username.value, password.value))
    password.value = ''
  } catch (failure: any) {
    const status = failure.response?.status
    error.value = platformText(status === 409 ? '当前任务正在处理，请等待完成后再登录' : status === 503 ? '平台账号暂不可用，请联系交付方检查本机账号配置' : status === 429 ? '登录尝试过于频繁，请稍后重试' : '登录失败，请检查账号密码或稍后重试')
  } finally { busy.value = false }
}
</script>

<style scoped>
.platform-login { min-height: 100vh; min-height: 100svh; display: grid; grid-template-columns: 1fr 1fr; background: #fff; }
.login-brand { position: relative; overflow: hidden; min-height: 100vh; min-height: 100svh; background: #15344b; color: #fff; }
.login-brand-image { position: absolute; inset: 0; height: 100%; width: 100%; object-fit: cover; object-position: center; }
.login-brand-copy { position: relative; padding: clamp(150px, 21.5vh, 260px) 7.3vw 64px; }
.login-brand-copy h1 { margin: 0; color: #fff; font-size: clamp(26px, 2.5vw, 38px); font-weight: 600; line-height: 1.3; letter-spacing: .015em; overflow-wrap: normal; }
.login-brand-description { margin: 16px 0 0; color: #d4e6fa; font-size: clamp(16px, 1.4vw, 20px); line-height: 1.6; letter-spacing: .08em; }
.login-brand-rule { width: 42px; margin: 30px 0 26px; border: 0; border-top: 3px solid #67a5f4; }
.login-brand-caption { margin: 0; color: #abcdf5; font-size: 13px; line-height: 1.8; letter-spacing: .25em; white-space: nowrap; }
.login-brand-sheen { position: absolute; inset: 0; z-index: 2; overflow: hidden; pointer-events: none; }
.login-brand-sheen::before { content: ''; position: absolute; inset: 0; background: linear-gradient(115deg, transparent 30%, rgb(150 205 255 / .10) 41%, rgb(190 226 255 / .38) 49%, rgb(255 255 255 / .24) 53%, rgb(150 205 255 / .10) 62%, transparent 71%); background-size: 260% 100%; animation: login-sheen 9s linear infinite; }
.login-brand-sheen::after { content: ''; position: absolute; width: 92%; height: 92%; left: -20%; top: -26%; background: radial-gradient(circle, rgb(56 142 232 / .34) 0%, rgb(56 142 232 / .13) 42%, transparent 70%); animation: login-aurora 10s ease-in-out infinite alternate; }
.login-brand-image { animation: login-drift 16s ease-in-out infinite alternate; }
.login-brand-copy { z-index: 3; text-shadow: 0 2px 16px rgb(6 24 42 / .5); }
.login-brand-copy h1, .login-brand-description, .login-brand-rule, .login-brand-caption, .login-form-heading, .login-form { animation: login-rise .85s cubic-bezier(.22,.8,.3,1) both; }
.login-brand-copy h1 { animation-delay: .05s; }
.login-brand-description { animation-delay: .2s; }
.login-brand-rule { animation-delay: .35s; }
.login-brand-caption { animation-delay: .5s; }
.login-form-heading { animation-delay: .18s; }
.login-form { animation-delay: .3s; }
@keyframes login-sheen { from { background-position: 140% 0; } to { background-position: -140% 0; } }
@keyframes login-aurora { from { transform: translate3d(-6%, -4%, 0) scale(.9); opacity: .55; } to { transform: translate3d(14%, 12%, 0) scale(1.18); opacity: 1; } }
@keyframes login-drift { from { transform: scale(1.09) translate3d(-1.6%, -1.1%, 0); } to { transform: scale(1.13) translate3d(1.6%, 1.2%, 0); } }
@keyframes login-rise { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: translateY(0); } }
.login-panel { position: relative; min-width: 0; min-height: 100vh; min-height: 100svh; display: flex; align-items: center; justify-content: center; padding: 120px 48px 140px; }
.login-language { position: absolute; top: 42px; right: 48px; display: flex; align-items: center; gap: 13px; color: #8c93a1; font-size: 16px; }
.login-language button { padding: 6px 3px; border: 0; background: transparent; color: #737c8d; font-size: 16px; cursor: pointer; }
.login-language button.selected { color: #0066cc; font-weight: 600; }
.login-language button:focus-visible, .login-password-toggle:focus-visible { outline: 2px solid #0066cc; outline-offset: 4px; border-radius: 3px; }
.login-form-region { width: 100%; max-width: 360px; transform: translate(0, -12px); }
.login-form-heading h2 { margin: 0; font-size: 30px; line-height: 1.35; font-weight: 600; letter-spacing: -.02em; color: #1d1d1f; }
.login-form-heading p { margin: 8px 0 0; color: #73777f; font-size: 14px; line-height: 1.7; }
.login-form { margin-top: 30px; display: grid; gap: 22px; }
.login-field { display: grid; gap: 10px; }
.login-field label { color: #30333a; font-size: 14px; line-height: 1.5; }
.login-field input { width: 100%; height: 44px; padding: 0 14px; border: 1px solid #cfd3dc; border-radius: 7px; background: #fff; color: #222936; font: inherit; font-size: 14px; outline: none; transition: border-color .15s, box-shadow .15s; }
.login-field input::placeholder { color: #9bA2af; }
.login-field input:hover { border-color: #8f9bab; }
.login-field input:focus { border-color: #0066cc; box-shadow: 0 0 0 3px #edf5fd; }
.login-field input:read-only { background: #fafbfc; }
.login-password { position: relative; }
.login-password input { padding-right: 60px; }
.login-password-toggle { position: absolute; inset: 8px 8px 8px auto; width: 44px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 4px; background: transparent; color: #818998; cursor: pointer; }
.login-password-toggle:hover { color: #0066cc; background: #f5f8fc; }
.login-submit.el-button { width: 100%; height: 44px; margin: 6px 0 0; border-radius: 7px; font-size: 15px; font-weight: 600; --el-color-primary: #0074ed; --el-color-primary-dark-2: #0066cc; }
.login-footer { position: absolute; bottom: 56px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; justify-content: center; gap: 22px; width: min(300px, calc(100% - 64px)); color: #8c94a4; font-size: 13px; white-space: nowrap; }
.login-footer::before, .login-footer::after { content: ''; flex: 1; height: 1px; background: #d9deE7; }
:lang(en) .login-brand-copy h1 { font-size: clamp(28px, 3.1vw, 44px); letter-spacing: -.02em; }
:lang(en) .login-brand-description { font-size: 22px; letter-spacing: 0; }
@media (max-width: 1100px) { .login-brand-copy { padding-left: 6vw; padding-right: 4vw; } .login-brand-caption { font-size: 10px; letter-spacing: .2em; } .login-panel { padding-left: 36px; padding-right: 36px; } .login-form-region { transform: translate(0, -16px); } }
@media (max-width: 760px) { .platform-login { grid-template-columns: 1fr; } .login-brand { min-height: 220px; } .login-brand-image { object-position: center 30%; } .login-brand-copy { padding: 48px 28px 32px; } .login-brand-copy h1 { font-size: 28px; } .login-brand-description { margin-top: 10px; font-size: 16px; } .login-brand-rule { margin: 18px 0 14px; } .login-brand-caption { font-size: 9px; } .login-panel { min-height: calc(100svh - 220px); padding: 104px 28px 104px; } .login-language { top: 24px; right: 28px; } .login-form-region { transform: none; } .login-form-heading h2 { font-size: 30px; } .login-form-heading p { font-size: 16px; } .login-form { margin-top: 32px; gap: 24px; } .login-field input { height: 44px; } .login-submit.el-button { height: 44px; margin-top: 4px; } .login-footer { bottom: 30px; } :lang(en) .login-brand-copy h1 { font-size: 28px; } :lang(en) .login-brand-description { font-size: 16px; } }
@media (min-width: 761px) and (max-height: 720px) { .login-panel { padding-top: 100px; padding-bottom: 100px; } .login-form-heading h2 { font-size: 32px; } .login-form { margin-top: 28px; gap: 20px; } .login-field input { height: 44px; } .login-submit.el-button { height: 44px; margin-top: 2px; } .login-footer { bottom: 28px; } .login-brand-copy { padding-top: 24vh; } }
@media (prefers-reduced-motion: reduce) { .login-field input { transition: none; } .login-brand-sheen { display: none; } .login-brand-sheen::before, .login-brand-sheen::after { animation: none; } .login-brand-image, .login-brand-copy h1, .login-brand-description, .login-brand-rule, .login-brand-caption, .login-form-heading, .login-form { animation: none; } }
</style>
