# 前端结构

- `src/App.vue`：统一应用导航和 URL hash 页面切换。切换页面时保留本次打开期间的对账文件和演示任务。
- `src/views/ReconciliationView.vue`：原有 Excel 对账页面，保留详细导出、原表标色和中英印尼语。
- `src/views/UnlockView.vue`：批量解锁工作台与账号管理界面。费用解锁对应小锁，订单解锁对应大锁。
- `src/components/FileDrop.vue`：复用的 Excel 上传组件。
- `src/services/api.ts`：现有真实对账 API。
- `src/style.css`：共享主题、应用导航和响应式布局。

## 当前阶段

默认打开 Excel 对账；顶部菜单可切换 iLog 批量解锁和账号管理，也支持 `#excel`、`#unlock`、`#accounts` 直达。

页面已整合浅蓝主题、宽屏对账布局、语言下拉选项和金额容差上下调节按钮修复。

解锁功能仅改造前端。解锁任务的进度和结果均为演示，不发起 iLog 请求。
账号管理只在 localStorage 中保存名称、用户名和示例标记，不保存密码或登录状态。演示任务仅存在于内存，刷新后清空。
同类单号自动去重，跨大小锁的同一单号在核对框提示，会分别处理。

## 后续接入

最后再接入真实登录与解锁事件：由 Python 后端管理任务和浏览器会话，前端显示后端真实进度与结果。密码应通过本机凭据库管理，不放在 localStorage。真实服务接入前需移除模拟执行逻辑；不能将计时器结果作为解锁成功依据。

## 验证

在仓库根目录执行 `npm run frontend:build`；启动使用 `npm run dev`。
