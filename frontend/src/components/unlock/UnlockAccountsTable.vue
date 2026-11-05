<template>
      <el-table :data="accounts" empty-text="添加第一个业务账号">
        <el-table-column prop="name" label="账号名称" min-width="180"/>
        <el-table-column prop="username" label="用户名" min-width="180"/>
        <el-table-column label="密码状态" min-width="140"><template #default="{ row }"><el-tag :type="row.hasPassword ? 'success' : 'info'">{{ row.hasPassword ? '已保存密码' : '未登记密码' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button link type="primary" :disabled="busy" @click="emit('edit', row)">编辑</el-button><el-button link type="danger" :disabled="busy" @click="emit('remove', row)">删除</el-button></template></el-table-column>
      </el-table>
</template>
<script setup lang="ts">
type Account = { id: string; name: string; username: string; example?: boolean; hasPassword?: boolean }
defineProps<{ accounts: Account[]; busy: boolean }>()
const emit = defineEmits<{ edit: [account: Account]; remove: [account: Account] }>()
</script>
