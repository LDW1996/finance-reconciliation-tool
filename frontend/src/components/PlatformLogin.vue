<template><main class="platform-login"><h1>财务综合业务平台</h1><label>语言 / Language <select v-model="language" @change="changeLanguage"><option value="zh-CN">中文</option><option value="en">English</option></select></label><el-form label-position="top" @submit.prevent="login"><el-form-item label="账号"><el-input v-model="username" autocomplete="username" /></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" show-password /></el-form-item><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-button native-type="submit" type="primary" :loading="busy">登录</el-button></el-form></main></template>
<script setup lang="ts">
import { ref } from 'vue'
import { loginPlatform, type PlatformSession } from '../services/platformAuth'
const emit=defineEmits<{ authenticated:[session:PlatformSession] }>()
const username=ref(''),password=ref(''),busy=ref(false),error=ref('')
const language=ref(localStorage.getItem('finance-platform-language') || 'zh-CN')
function changeLanguage(){localStorage.setItem('finance-platform-language',language.value);window.dispatchEvent(new Event('platform-language-change'))}
async function login(){busy.value=true;error.value='';try{emit('authenticated',await loginPlatform(username.value,password.value));password.value=''}catch(failure:any){const status=failure.response?.status;error.value=status===409?'当前任务正在处理，请等待完成后再登录':status===503?'平台账号暂不可用，请联系交付方检查本机账号配置':status===429?'登录尝试过于频繁，请稍后重试':'登录失败，请检查账号密码或稍后重试'}finally{busy.value=false}}
</script>
<style scoped>.platform-login{max-width:400px;margin:12vh auto;padding:32px}.el-form{margin-top:24px}.el-button{margin-top:16px}</style>
