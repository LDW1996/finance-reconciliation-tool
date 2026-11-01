# 内置角色账号与统一财务平台改造计划

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Do not launch parallel agents without authorization. This is a scoped architecture and delivery plan; obtain concrete UI/credential decisions before coding those parts.

**Goal:** 一个财务综合业务平台安装包，内置固定角色账号，本地登录、本地数据、离线运行，无注册、用户管理或授权文件。

**Architecture:** 共用全量后台和业务组件，固定角色定义为唯一权限来源；服务端会话和接口授权负责拦截，前端根据会话渲染模块。不按登录账号新建业务数据库，不迁移原有邮件、密钥、读取断点及确认记录。

**Tech Stack:** 现有 FastAPI、Vue 3、TypeScript、Element Plus、SQLite 加密业务存储和 PyInstaller Windows 打包。

## 已确认范围
- 各人各电脑、数据不共享；同一电脑切换账号仍访问同一套本地业务数据。
- 四个固定角色：excel / mail / ilog / full。full 是全功能业务账号，不设置平台用户管理功能。
- 无注册、增删用户、权限编辑、授权文件、云登录、机器绑定、联网认证。
- 现有 iLog 登录凭据保留并改名为“iLog业务账号”，不与平台账号混淆。
- 权限用于内部功能分工，不承诺防止本机拥有者逆向程序或直接访问文件。

## 权限清单
| 能力 | excel | mail | ilog | full |
|---|---|---|---|---|
| Excel导入/校验/对账/导出 | 是 | 否 | 否 | 是 |
| Outlook目录/自动读取/附件识别/重试 | 否 | 是 | 否 | 是 |
| 流水、客户台账来源配置/读取 | 否 | 是 | 否 | 是 |
| 匹配/确认/拒绝/回写/导出 | 否 | 是 | 否 | 是 |
| 邮件容量、缓存、队列设置 | 否 | 是 | 否 | 是 |
| 邮件诊断、优化、数据库整理、清理备份恢复 | 否 | 是 | 否 | 是 |
| iLog业务账号和凭据配置 | 否 | 否 | 是 | 是 |
| 解锁执行、控制、历史、截图和导出 | 否 | 否 | 是 | 是 |
| iLog保留规则、过期清理 | 否 | 否 | 是 | 是 |

## 当前实现约束
- `backend/app/main.py` 的本机请求头/Origin 检查不是登录认证，必须保留并叠加身份和角色校验。
- 现有多入口 `main.py / excel_main.py / mail_main.py` 不可留下绕过统一鉴权的正式启动入口。
- `frontend/src/App.vue` 通过 hash 导航且使用 v-show 保留隐藏模块，需改为鉴权后有条件挂载，避免隐藏组件发请求。
- `UnlockView.vue` 的 retention 页面混合邮件和 iLog设置，必须拆分。
- 当前英文版通过构建时静态翻译和专用入口实现。运行时切换中英文不是直接修改 VITE_EDITION，需要独立语言层。

## 阶段1：固定角色与离线登录
Files: create `backend/app/platform_roles.py`, `platform_auth.py`, `platform_auth_routes.py`; modify `backend/app/main.py`.
- [ ] 固定角色到能力集合映射；默认拒绝未知角色与未知受保护接口。
- [ ] 固定账号使用密码摘要；不存明文密码，不在日志或接口回传摘要。账号名称和交付密码通过独立渠道确定；公开仓库不提交可用明文密码。
- [ ] 使用现有运行环境支持的标准密码派生函数与随机盐，验证使用恒定时间比较；不自创加密。
- [ ] 登录、会话查询、退出三个入口；随机服务端会话，HttpOnly/SameSite cookie，重启后重新登录。
- [ ] 保留本机绑定、Origin检查和变更请求防护；登录错误统一提示，失败有有限频率限制。
- [ ] 测试未登录、错误密码、有效登录、退出失效、重启失效及不泄露密码。

## 阶段2：后台权限与任务恢复
Files: modify `backend/app/main.py`, `excel_routes.py`, `mail_tasks.py`, `ilog_tasks.py`; add `backend/tests/test_platform_permissions.py`.
- [ ] 枚举所有API，包括列表、下载、截图、诊断、凭据、后台控制和数据维护接口；每条路由绑定能力。
- [ ] 未登录返回401、角色不允许返回403；健康检查与登录所需静态资源仅暴露最低信息。
- [ ] 对所有路由逐角色验证访问矩阵，包含GET/POST/PUT/DELETE和下载，防止仅做菜单隐藏。
- [ ] 登录前不自动恢复邮件扫描、OCR和iLog任务；授权角色登录后才按已有恢复配置处理。
- [ ] 后台任务不只依赖HTTP请求上下文；调度入口同样校验当前授权的能力，防止定时器绕过。
- [ ] 退出或换角色时停止接收新任务；在途操作走现有安全暂停边界，保留断点，不硬杀数据库写入、识别、清理或外部业务操作。必要时明确提示等待当前操作完成再退出。
- [ ] 队列、失败状态、识别结果、解锁防重复凭据保持原格式；退出不清空业务数据。

## 阶段3：登录与按角色导航
Files: create `frontend/src/components/PlatformLogin.vue`, `frontend/src/services/platformAuth.ts`; modify `frontend/src/App.vue`, `main.ts`, `UnlockView.vue`, `MailEnglishApp.vue` as transitional entry only.
- [ ] 启动先查会话，再显示登录页或授权模块，不先挂载隐藏业务组件。
- [ ] 登录后展示角色名称和退出入口；不展示用户管理、注册、账号创建或权限编辑入口。
- [ ] 默认页面分别为Excel对账/邮件收款/iLog解锁/全功能首页。
- [ ] 手动输入未授权hash时回到本角色默认页面；后端仍独立拒绝对应请求。
- [ ] 统一平台名称“财务综合业务平台”，原业务导航功能保留。

## 阶段4：保存设置按模块归属拆分
Files: modify `UnlockView.vue`, `MailStorageSettings.vue`; create `frontend/src/components/IlogRetentionSettings.vue`.
- [ ] 邮件账号保留邮件全部保存、诊断、预览优化、清理、备份恢复能力，继续使用现有保护流程。
- [ ] iLog账号保留业务账号、历史、保留天数和过期清理能力。
- [ ] 全功能账号可访问两组设置；拆组件避免未授权模块在加载时发请求。
- [ ] 保留两套独立保存按钮与接口，不合并为一个会意外写入另一模块的“全局保存”。
- [ ] 验证预览先于确认、候选默认不选、备份先于清理及恢复冲突保护均保持。

## 阶段5：统一语言与打包
Files: modify `frontend/src/main.ts`, `vite.config.ts`, `frontend/tooling/mailEnglish.ts`, `.github/workflows/build-windows-exe.yml`, `packaging/finance-reconcile.spec`; add runtime locale dictionaries and a shared language service.
- [ ] 统一登录与导航支持中英文；语言为本机偏好，不决定权限。
- [ ] 将现有邮件英文能力迁入运行时语言层，覆盖组件库、日期、提示、导出及回写生成说明；业务原文保持原样。
- [ ] 全平台英文需要补齐Excel、iLog和管理文案，作为明确工作项，不把已有邮件英文能力当成全平台已翻译完成。
- [ ] 对外默认只生成“财务综合业务平台.zip”和同名EXE；旧独立包先保留为内部回归入口，统一版验证后再停止发布。
- [ ] 正式包只有统一鉴权入口，旧独立入口不允许绕过角色限制。
- [ ] 打包测试用临时数据目录和测试账号，经正常登录验证每个角色、语言、OCR和重启；不新增生产鉴权后门。

## 阶段6：历史数据与Windows交付验收
- [ ] 对测试副本记录升级前邮件数、附件数、完成结果、确认关联、队列断点及密钥校验值。
- [ ] 升级登录后对照这些值；仅允许正常任务继续带来的可解释变化，不重新扫描或重跑已完成OCR。
- [ ] 验证退出、角色切换、重启、异常结束后的恢复，不丢断点、不重复提交iLog业务操作。
- [ ] Excel、邮件、iLog、全功能四角色覆盖全部路由矩阵；中英文桌面与窄窗口覆盖菜单、长标签、错误提示。
- [ ] 在Windows实际EXE中完成正常登录、持久业务数据读取、离线识别、退出及重启验证。
- [ ] 确认包内不含开发数据、密钥、Google授权或明文交付密码。
- [ ] 更新使用说明：同一Windows用户/原数据目录升级，关闭程序后完整备份；明确账号固定、不提供平台账号管理。

## 分批交付
1. 登录和接口权限先在隔离数据上形成可测试版本，暂不替代在用正式包。
2. 拆设置和按角色导航，完成四角色业务验收。
3. 完成运行时双语、统一包和Windows历史数据回归后替换对外版本。

本计划不声称修复此前尚未定位的内存故障；保持原诊断能力，避免权限改造掩盖它。
