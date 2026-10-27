<template>
  <div class="folder-browser">
    <el-input v-model="query" :prefix-icon="Search" clearable placeholder="搜索目录名称" aria-label="搜索邮件目录" :disabled="disabled || !nodes.length" />
    <div class="directory-list" aria-label="邮箱与存档目录">
      <el-tree v-if="nodes.length" ref="tree" :data="nodes" node-key="value" :default-expanded-keys="nodes.map(node => node.value)" :expand-on-click-node="false" :filter-node-method="matches" :indent="22">
        <template #default="{ data }">
          <el-checkbox :model-value="modelValue.includes(data.value)" :disabled="disabled || data.disabled" :aria-label="data.path || data.label" @change="toggle(data.value, Boolean($event))">
            <el-icon><Folder /></el-icon><span class="directory-name" :title="data.path || data.label">{{ data.label }}</span>
          </el-checkbox>
        </template>
      </el-tree>
      <div v-else class="directory-empty"><el-icon :size="28"><FolderOpened /></el-icon><strong>{{ disabled ? '暂未连接 Outlook' : '选择需要读取的目录' }}</strong><span>连接后显示邮箱和已打开的 PST 存档</span></div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { Folder, FolderOpened, Search } from '@element-plus/icons-vue'
export interface MailDirectoryNode { value:string; label:string; path?:string; disabled:boolean; children:MailDirectoryNode[] }
const props=defineProps<{ nodes:MailDirectoryNode[]; modelValue:string[]; disabled:boolean }>()
const emit=defineEmits<{ 'update:modelValue':[value:string[]] }>()
const query=ref(''), tree=ref<{filter:(value:string)=>void}>()
watch(query,value=>tree.value?.filter(value))
watch(()=>props.nodes,()=>tree.value?.filter(query.value),{flush:'post'})
const matches=(value:string,node:MailDirectoryNode)=>(node.path || node.label).toLowerCase().includes(value.toLowerCase())
function toggle(value:string,checked:boolean){emit('update:modelValue',checked?[...new Set([...props.modelValue,value])]:props.modelValue.filter(key=>key!==value))}
</script>
<style scoped>
.folder-browser{display:flex;flex-direction:column;gap:12px;min-width:0}.directory-list{height:248px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:10px 6px;background:var(--el-bg-color)}.directory-list :deep(.el-tree-node__content){height:40px;border-radius:5px}.directory-list :deep(.el-checkbox){min-width:0;width:100%;margin-right:10px}.directory-list :deep(.el-checkbox__label){display:flex;align-items:center;gap:10px;min-width:0;max-width:100%;font-size:14px;font-weight:400;color:var(--ink)}.directory-list :deep(.el-icon){flex-shrink:0;color:var(--ink-muted)}.directory-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.directory-empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center;color:var(--ink-muted);font-size:12px;padding:16px}.directory-empty strong{font-size:14px;font-weight:500;color:var(--ink)}
</style>
