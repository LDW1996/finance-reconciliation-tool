<template>
  <div class="file-card">
    <div class="file-card-header">
      <h2>{{ title }}</h2>
      <el-tag v-if="checking" type="warning" effect="plain">{{ checkingText }}</el-tag>
      <el-tag v-else-if="file" type="success" effect="plain">{{ uploadedText }}</el-tag>
      <el-tag v-else effect="plain">{{ pendingText }}</el-tag>
    </div>

    <el-upload
      ref="uploadRef"
      drag
      :auto-upload="false"
      :show-file-list="false"
      accept=".xlsx,.xls"
      :on-change="handleChange"
    >
      <el-icon class="upload-icon"><UploadFilled /></el-icon>
      <div class="upload-text">{{ uploadPrompt }}</div>
      <template #tip>
        <div class="upload-tip">{{ supportedText }}</div>
      </template>
    </el-upload>

    <dl class="file-status">
      <div class="status-item">
        <dt>{{ filenameLabel }}</dt>
        <dd>{{ file?.name ?? notSelectedText }}</dd>
      </div>
      <div class="status-item">
        <dt>{{ companyCodeLabel }}</dt>
        <dd>{{ companyCode ?? identifyAfterText }}</dd>
      </div>
    </dl>
  </div>
</template>

<script setup lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
import { ref } from 'vue'
import type { UploadFile, UploadInstance } from 'element-plus'

const uploadRef = ref<UploadInstance>()

defineProps<{
  title: string
  file: File | null
  companyCode?: string
  checking?: boolean
  checkingText: string
  uploadedText: string
  pendingText: string
  uploadPrompt: string
  supportedText: string
  filenameLabel: string
  notSelectedText: string
  companyCodeLabel: string
  identifyAfterText: string
}>()

const emit = defineEmits<{
  selected: [file: File]
}>()

function handleChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  uploadRef.value?.clearFiles()
  emit('selected', raw)
}
</script>
