<template>
  <section class="ilog-retention" aria-label="解锁记录保存设置">
    <div class="retention-grid">
      <section class="policy-editor" v-loading="loading">
        <header><h2>保存设置</h2><p>管理解锁记录与截图的本地保留时间。</p></header>
        <el-form label-position="top" :disabled="retentionSaving || loading">
          <div class="policy-fields">
            <el-form-item label="截图保留"><div class="day-field"><el-input-number v-model="retentionForm.screenshot_days" aria-label="截图保留天数" :min="1" :max="36500" controls-position="right"/><span>天</span></div></el-form-item>
            <el-form-item label="任务结果保留"><div class="day-field"><el-input-number v-model="retentionForm.record_days" aria-label="任务结果保留天数" :min="1" :max="36500" controls-position="right"/><span>天</span></div></el-form-item>
          </div>
          <div class="auto-policy"><div class="auto-heading"><h3>自动清理过期数据</h3><el-switch v-model="retentionForm.auto_enabled" aria-label="自动清理过期数据"/></div><p>在打开页面、查看历史或新建任务时检查，每 24 小时最多一次；有任务正在执行时延后。清理不会修改 iLog。</p></div>
          <footer><el-button type="primary" :loading="retentionSaving" @click="saveRetention">保存设置</el-button></footer>
        </el-form>
      </section>
      <aside class="protected-records"><h2>保护中的记录 <el-icon aria-hidden="true"><InfoFilled/></el-icon></h2>
        <div v-for="item in protectedItems" :key="item.title" class="protected-item"><span class="protection-icon"><el-icon aria-hidden="true"><component :is="item.icon"/></el-icon></span><div><h3>{{item.title}}</h3><p>{{item.text}}</p></div></div>
      </aside>
    </div>
    <section class="manual-cleanup"><div><h2>过期数据清理</h2><p>手动预览将要清理的过期数据，确认后执行清理。</p></div><el-button :disabled="retentionSaving || loading" @click="cleanupExpired">预览并清理过期数据</el-button></section>
  </section>
</template>
<script setup lang="ts">
import { ref, onMounted } from "vue"
import axios from "axios"
import { ElMessage, ElMessageBox } from "element-plus"
import { VideoPlay, VideoPause, Tickets, Lock, InfoFilled } from '@element-plus/icons-vue'
const protectedItems=[{title:'运行中',text:'正在执行的任务记录与截图不会清理。',icon:VideoPlay},{title:'暂停中',text:'已暂停的任务记录与截图不会清理。',icon:VideoPause},{title:'待核实',text:'待核实的任务记录与截图不会清理。',icon:Tickets},{title:'防重复凭据',text:'防重复凭据相关数据始终单独保留。',icon:Lock}]
const loading=ref(true)
const retentionSaving=ref(false)
const retentionForm=ref({screenshot_days:90,record_days:365,auto_enabled:true})
const savedRetention=ref("")
const accountHeaders={"X-Finance-Local":"1"}
async function openRetention() {
  try { retentionForm.value=(await axios.get('/api/ilog/retention')).data;savedRetention.value=JSON.stringify(retentionForm.value) }
  catch { ElMessage.error('无法读取保存规则') }
  finally { loading.value=false }
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

<style scoped>
.ilog-retention{padding-top:6px;font-size:14px;color:#1d2633}
.retention-grid{display:grid;grid-template-columns:minmax(0,2.15fr) minmax(280px,1fr);gap:20px;align-items:stretch}
.policy-editor{background:#fff;border:1px solid #edf0f4;border-radius:10px;padding:28px 30px}
h2,h3,p{margin:0}h2{font-size:20px;line-height:1.5;font-weight:600}h3{font-size:15px;line-height:1.5;font-weight:600}p{color:#697383;font-size:14px;line-height:1.8}
.policy-editor header p{margin-top:6px}.policy-fields{display:grid;grid-template-columns:1fr 1fr;margin:30px 0 26px;gap:28px}
.policy-fields :deep(.el-form-item){margin:0}.policy-fields :deep(.el-form-item__label){font-size:14px;color:#414c5d;padding-bottom:10px}
.policy-fields :deep(.el-form-item)+:deep(.el-form-item){border-left:1px solid #e8ebf0;padding-left:28px}
.day-field{display:flex;align-items:center;gap:12px}.day-field :deep(.el-input-number){width:160px}
.auto-policy{padding-top:24px;border-top:1px solid #e8ebf0}.auto-heading{display:flex;align-items:center;gap:24px}.auto-policy p{margin-top:12px;max-width:680px}
.policy-editor footer{display:flex;justify-content:flex-end;margin-top:28px}.policy-editor footer .el-button{height:38px;padding:0 22px}
.protected-records{background:#edf2f8;border-radius:10px;padding:28px}.protected-records>h2{display:flex;align-items:center;gap:10px;margin-bottom:24px;font-size:18px}.protected-records>h2 .el-icon{font-size:16px;color:#537899}
.protected-item{display:flex;gap:14px;margin-top:22px;align-items:flex-start}.protected-item p{margin-top:4px;font-size:13px}.protection-icon{display:flex;align-items:center;justify-content:center;flex:0 0 36px;height:36px;border-radius:50%;background:#e1edfb;color:#0066cc;font-size:20px}.protected-item:last-child{padding-top:22px;border-top:1px solid #d9e1eb}
.manual-cleanup{display:flex;align-items:center;justify-content:space-between;gap:24px;background:#fff;border:1px solid #edf0f4;border-radius:10px;padding:24px 30px;margin-top:20px}.manual-cleanup h2{font-size:18px}.manual-cleanup p{margin-top:6px}.manual-cleanup .el-button{flex-shrink:0}
@media(max-width:1000px){.retention-grid{grid-template-columns:1fr}.protected-records{display:grid;grid-template-columns:1fr 1fr;gap:20px}.protected-records>h2{grid-column:1/-1;margin-bottom:0}.protected-item{margin:0}.protected-item:last-child{padding:0;border:0}}
@media(max-width:600px){.policy-editor,.protected-records,.manual-cleanup{padding:20px}.policy-fields{grid-template-columns:1fr;gap:22px}.policy-fields :deep(.el-form-item)+:deep(.el-form-item){padding-left:0;border:0}.protected-records{grid-template-columns:1fr}.manual-cleanup{align-items:flex-start;flex-direction:column}.manual-cleanup .el-button{width:100%}}
</style>
