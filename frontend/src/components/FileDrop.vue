<template>
  <div class="file-card">
    <div class="file-card-header">
      <h2>{{ title }}</h2>
      <el-tag v-if="checking" type="warning" effect="plain">{{ checkingText }}</el-tag>
      <el-tag v-else-if="file" type="success" effect="plain">{{ uploadedText }}</el-tag>
      <el-tag v-else effect="plain">{{ pendingText }}</el-tag>
    </div>

    <div class="file-picker" :class="{ 'has-file': file }">
      <strong v-if="file" class="selected-filename">{{ file.name }}</strong>
      <el-upload
        ref="uploadRef"
        :drag="!file"
        :auto-upload="false"
        :show-file-list="false"
        accept=".xlsx,.xls"
        :on-change="handleChange"
      >
        <el-button v-if="file">{{ replaceText }}</el-button>
        <template v-else>
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="upload-text">{{ uploadPrompt }}</div>
        </template>
        <template #tip>
          <div v-if="!file" class="upload-tip">{{ supportedText }}</div>
        </template>
      </el-upload>
    </div>

    <dl v-if="!file" class="file-status">
      <div class="status-item">
        <dt>{{ filenameLabel }}</dt>
        <dd>{{ notSelectedText }}</dd>
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
  replaceText: string
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

<style scoped>
.file-picker.has-file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.selected-filename {
  min-width: 0;
  overflow-wrap: anywhere;
}

.has-file :deep(.el-upload) {
  width: auto;
}
</style>
