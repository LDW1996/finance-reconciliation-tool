# 前端结构

- `src/App.vue`：统一应用导航和 URL hash 页面切换。切换页面时保留本次打开期间的对账文件和演示任务。
- `src/views/ReconciliationView.vue`：原有 Excel 对账页面，保留详细导出、原表标色和中英印尼语。
- `src/views/UnlockView.vue`：批量解锁工作台与账号管理界面。费用解锁对应小锁，订单解锁对应大锁。
- `src/components/FileDrop.vue`：复用的 Excel 上传组件。
- `src/services/api.ts`：现有真实对账 API。
- `src/style.css`：共享主题、应用导航和响应式布局。

## 当前阶段

默认打开 Excel 对账；顶部菜单可切换 iLog 批量解锁和账号管理，也支持 `#excel`、`#unlock`、`#accounts` 直达。

页面已整合苹果风浅色主题、宽屏对账布局、语言下拉选项和金额容差上下调节按钮修复。

解锁功能仅改造前端。解锁任务的进度和结果均为演示，不发起 iLog 请求。
账号管理由后端 SQLite 保存，支持 Windows 和 Mac，不依赖系统钥匙串。密码使用 Fernet 加密，不返回原密码到浏览器。演示任务仅存在于内存，刷新后清空。
同类单号自动去重，跨大小锁的同一单号在核对框提示，会分别处理。
输入框下展示重复单号及次数；核对弹窗以大小锁两个 Tab 展示原文、识别结果和异常建议。异常未修正或未勾选核对确认时不能开始演示。格式校验不等于单号存在性校验。

独立的只读登录与提单查询脚本见 `backend/ILOG_QUERY.md`，尚未接入页面的解锁操作。

账号可新增、编辑和删除。编辑时新密码留空保留原密码；取消或关闭窗口会清空新密码输入。旧版浏览器账号元数据会迁移到后端，不再依赖浏览器存储。

账号数据目录：Windows 为 `%LOCALAPPDATA%/FinanceReconciliation`，Mac 为 `~/Library/Application Support/FinanceReconciliation`。目录内的 `accounts.sqlite3` 与 `accounts.key` 需要一起保留；替换程序不会覆盖它们。可用 `FINANCE_RECONCILE_DATA_DIR` 指定目录。实际账号不会打进代码或安装包。

## 后续接入

最后再接入真实登录与解锁事件：由 Python 后端管理任务和浏览器会话，前端显示后端真实进度与结果。后端通过账号存储读取密码，前端不接收原密码。真实服务接入前需移除模拟执行逻辑；不能将计时器结果作为解锁成功依据。

## 验证

在仓库根目录执行 `npm run frontend:build`；启动使用 `npm run dev`。
