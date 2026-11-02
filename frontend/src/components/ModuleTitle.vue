<template><div class="module-title"><el-icon class="module-title-icon" aria-hidden="true"><component :is="icon"/></el-icon><h1 :id="titleId"><slot/></h1></div></template>
<script setup lang="ts">
import type { Component } from 'vue'
defineProps<{icon:Component;titleId?:string}>()
</script>
<style scoped>
.module-title{display:flex;align-items:center;gap:14px;min-width:0}
.module-title .module-title-icon{flex:0 0 32px;font-size:32px;color:var(--accent,#0066cc)}
.module-title h1{margin:0;font-size:30px;line-height:1.4;font-weight:700;letter-spacing:-.025em}
@media(max-width:720px){.module-title{gap:10px}.module-title .module-title-icon{flex-basis:28px;font-size:28px}.module-title h1{font-size:26px}}
</style>
