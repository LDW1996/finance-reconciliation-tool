<template>
      <el-table :data="rows" max-height="360" border empty-text="此类型暂无单号">
        <el-table-column type="index" label="#" width="50"/>
        <el-table-column prop="raw" label="粘贴原文" min-width="170"/>
        <el-table-column label="识别单号" min-width="170"><template #default="{ row }">{{ row.status === 'invalid' ? '—' : row.bill }}</template></el-table-column>
        <el-table-column label="校验结果" min-width="170"><template #default="{ row }"><el-tag :type="row.status === 'invalid' ? 'danger' : row.status === 'duplicate' ? 'warning' : 'success'">{{ row.status === 'invalid' ? '格式异常' : row.status === 'duplicate' ? '重复，已排除' : '格式通过' }}</el-tag><div v-if="row.suggestion" style="margin-top:6px;overflow-wrap:anywhere">建议修改为：{{ row.suggestion }}</div></template></el-table-column>
      </el-table>
</template>
<script setup lang="ts">
defineProps<{ rows: { raw: string; bill: string; status: string; suggestion: string }[] }>()
</script>
