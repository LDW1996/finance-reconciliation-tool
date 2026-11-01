<template>
      <section class="retention-module"><h2>解锁记录保存</h2>
      <el-form label-position="top" :disabled="retentionSaving" class="retention-fields">
        <el-form-item label="截图保留天数"><el-input-number v-model="retentionForm.screenshot_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="任务结果保留天数"><el-input-number v-model="retentionForm.record_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="自动清理过期数据"><el-switch v-model="retentionForm.auto_enabled" aria-label="自动清理过期数据"/></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="运行中、暂停中及待核实记录不会自动清理；防重复凭据始终单独保留。" description="自动清理在打开页面、查看历史或新建任务时检查，每 24 小时最多一次；有任务正在执行时延后。清理不会修改 iLog。"/>
      <div class="management-footer"><el-button type="primary" :loading="retentionSaving" @click="saveRetention">保存规则</el-button></div>
      <div class="retention-cleanup"><div><h2>过期数据清理</h2></div><el-button :disabled="retentionSaving" @click="cleanupExpired">预览并清理过期数据</el-button></div>
      </section>
</template>
<script setup lang="ts">
import { ref, onMounted } from "vue"
import axios from "axios"
import { ElMessage, ElMessageBox } from "element-plus"
const retentionSaving=ref(false)
const retentionForm=ref({screenshot_days:90,record_days:365,auto_enabled:true})
const savedRetention=ref("")
const accountHeaders={"X-Finance-Local":"1"}
async function openRetention() {
  try { retentionForm.value=(await axios.get('/api/ilog/retention')).data;savedRetention.value=JSON.stringify(retentionForm.value) }
  catch { ElMessage.error('无法读取保存规则') }
}
async function saveRetention() {
  retentionSaving.value=true
  try { retentionForm.value=(await axios.put('/api/ilog/retention',retentionForm.value,{headers:accountHeaders})).data;savedRetention.value=JSON.stringify(retentionForm.value);ElMessage.success('保存规则已更新') }
  catch { ElMessage.error('保存失败，请检查天数和本地服务') }
  finally { retentionSaving.value=false }
}
async function cleanupExpired() {
  if(JSON.stringify(retentionForm.value)!==savedRetention.value) { ElMessage.warning('请先保存规则，再预览清理');return }
  retentionSaving.value=true
  try {
    const preview=(await axios.get('/api/ilog/retention/preview')).data
    if(preview.busy) { ElMessage.warning('有任务正在执行，暂不清理');return }
    await ElMessageBox.confirm(`按已保存规则，预计删除 ${preview.tasks} 个历史任务、${preview.screenshots} 张截图、${preview.reports} 份报告。待核实记录和防重复凭据保留。删除不能撤销。`,'确认清理过期数据',{type:'warning',confirmButtonText:'确认清理',cancelButtonText:'取消'})
    const result=(await axios.post('/api/ilog/retention/cleanup',{confirmed:true},{headers:accountHeaders})).data
    if(result.busy) ElMessage.warning('任务已开始，清理已延后')
    else ElMessage.success(`已清理 ${result.tasks} 个任务、${result.screenshots} 张截图、${result.reports} 份报告${result.errors ? `；${result.errors} 个文件未能删除` : ''}`)
  } catch(error) { if(axios.isAxiosError(error)) ElMessage.error('清理失败，请检查本地服务') }
  finally { retentionSaving.value=false }
}
onMounted(openRetention)
</script>
