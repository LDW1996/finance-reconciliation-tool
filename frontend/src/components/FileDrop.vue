<template>
  <div class="file-card">
    <div class="file-card-header">
      <h2>{{ title }}</h2>
      <el-tag v-if="checking" type="warning" effect="plain">检查中</el-tag>
      <el-tag v-else-if="file" type="success" effect="plain">已上传</el-tag>
      <el-tag v-else effect="plain">待上传</el-tag>
    </div>

    <el-upload
      drag
      :auto-upload="false"
      :limit="1"
      :show-file-list="false"
      accept=".xlsx,.xls"
      :on-change="handleChange"
    >
      <el-icon class="upload-icon"><UploadFilled /></el-icon>
      <div class="upload-text">点击选择或拖拽Excel到这里</div>
      <template #tip>
        <div class="upload-tip">支持 .xlsx / .xls</div>
      </template>
    </el-upload>

    <dl class="file-status">
      <div class="status-item">
        <dt>文件名</dt>
        <dd>{{ file?.name ?? '未选择' }}</dd>
      </div>
      <div class="status-item">
        <dt>公司代码</dt>
        <dd>{{ companyCode ?? '分析后识别' }}</dd>
      </div>
    </dl>
  </div>
</template>

<script setup lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'

defineProps<{
  title: string
  file: File | null
  companyCode?: string
  checking?: boolean
}>()

const emit = defineEmits<{
  selected: [file: File]
}>()

function handleChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  emit('selected', raw)
}
</script>
