# 财务综合业务平台 · 产品化 UI 设计方案

日期：2026-11-06（v3 更新：完整版已落地到代码）
负责人：UI Designer（像素君）
状态：**已落地 v4**（1:1 效果图已落地，业务逻辑零改动；v3 / v4 记录见 §9 / §10）
配套原型：`output/ui-redesign/index.html`（v2.1，视觉规范基准）

## 1. 设计上下文（Design Context）

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| 目标用户 | 货代/物流公司财务人员，非技术用户；Windows 本地 EXE 启动后浏览器访问 | README「统一登录与角色」「财务人员使用方式」 |
| 使用场景 | ① 上传往来 Excel → 自动对账 → 下载结果；② Outlook 邮件收款识别 → 人工确认核销；③ iLog 按提单/按航次批量解锁；④ 存储/保留规则设置 | README 当前范围、邮件读取、iLog 章节 |
| 品牌气质 | 克制、精确、值得托付资金的专业工具感；延续现有「应用蓝 + 灰 + 白」令牌，flat white 表面、细分割线、克制圆角 | `design-qa.md` Fidelity 条目 |
| 硬约束 | 本地离线运行 → 仅系统字体，不引外部资源；Vue3 + Element Plus；桌面优先（1440）+ 390px 可用；中英双语，登录页选定后全站沿用；WCAG AA | README 打包优化、design-qa 功能检查 |
| 角色差异 | excel / mail / ilog / full 四种固定角色，导航只显示被授权模块 | README 角色表、design-qa 权限导航证据 |

## 2. 设计原则（四条，所有界面决策以此裁决）

1. **数据优先（Numbers first）**：金额、单号、状态是页面主角。所有数字使用 `tabular-nums` 等宽数字；关键指标 26–30px；表格行高 40px，与现有数字控件几何保持一致。
2. **流程可见（Visible progress）**：上传 → 校验 → 分析 → 导出这类多步流程，始终显示当前步骤与已完成产物；后台任务（邮件扫描、iLog 解锁）必须显示阶段、进度与"已有结果保留"的安抚文案。
3. **保护性操作（Protective actions）**：清理、重置、删除等破坏性操作一律"预览 → 二次确认 → 可恢复提示"三段式；确认按钮不放在默认焦点位。
4. **双语一致（One interface, two languages）**：布局按英文（更长的标签）设计，中文作为收缩态；禁止为中文单独调布局；业务原文（邮件正文、客户名）永远不翻译。

## 3. 设计令牌（Design Tokens）

与 Element Plus 通过 CSS 变量映射落地（见 §7）。**主色以现有 `frontend/src/style.css` 的 `:root` 为唯一来源**，本次不换色，避免产品级视觉回归（依据 `design-qa.md`「现有应用蓝、灰、白保留」）。

### 3.1 色彩

主色色阶（已定稿，直接沿用现有值）：

| Token | 值 | 对应现有变量 | 用途 |
| --- | --- | --- | --- |
| `--color-primary-500` | `#0066cc` | `--el-color-primary` / `--accent` | 主按钮、当前导航、链接、焦点环 |
| `--color-primary-600` | `#0052a3` | `--el-color-primary-dark-2` / `--accent-dark` | 主按钮 hover |
| `--color-primary-100` | `#d6e7f8` | `--el-color-primary-light-8` | 头像/选中态次级底 |
| `--color-primary-50` | `#edf5fd` | `--el-color-primary-light-9` | 选中行、信息条背景 |

现有浅色阶中间档（`--el-color-primary-light-3` `#4d94db` / `-5` `#80b3e6` / `-7` `#b3d1f0`）保持不变，按需取用。白字在 `#0066cc` 上的对比度为 **5.57 : 1**，通过 WCAG AA；焦点环光晕用 `rgb(0 102 204 / .12)`。

中性色与语义色（v2 原型采用值属**提案**；v3 落地采用「零视觉回归」策略，右侧现有值全部保留）：

| Token | 原型值 | 现有 `style.css` 值 | 用途 |
| --- | --- | --- | --- |
| `--color-bg` | `#f5f7fa` | `#f5f5f7` | 页面背景 |
| `--color-surface` | `#ffffff` | `#ffffff` | 卡片、表格、表单表面 |
| `--color-border` | `#e4e9f0` | `#e5e5ea` | 分割线、输入框描边 |
| `--color-text` | `#0f172a` | `#1d1d1f` | 主文本 |
| `--color-text-secondary` | `#45566b` | `#48484a` | 次要文本 |
| `--color-text-muted` | `#8a97a8` | `#6e6e73` | 占位、说明 |
| `--color-success` | `#059669` | `--el-color-success` `#326b4b` | 匹配成功、核销完成 |
| `--color-warning` | `#b45309` | `--el-color-warning` `#946000` | 金额差异、待确认 |
| `--color-error` | `#dc2626` | 无对应变量 | 校验失败、未匹配、破坏性操作 |

规则：差异金额、未匹配一律用 `--color-error` 红；成功用绿；不用渐变、不用毛玻璃、不引入新色相。深色模式暂不提供（本地财务工具，保持单一主题降低维护面）。**v3 已采用「零视觉回归」策略**：上表右侧现有值全部保留，新增 `--ds-*` 令牌映射到这些现有值，17 个组件内的 110 处硬编码色值未做批量替换；原型仅用于确认布局与密度。

### 3.2 字体

| Token | 值 |
| --- | --- |
| `--font-family` | `-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif` |
| 字号阶梯 | 12（辅助）/ 14（正文，默认）/ 16（小节标题）/ 18–22（区块标题）/ 26–30（关键指标） |
| 字重 | 400 正文 / 500 按钮与标签 / 600 标题 |
| 数字 | `font-variant-numeric: tabular-nums`，表格与指标必开 |

离线约束：不加载任何 Web Font；通过加粗与字距（`letter-spacing: -0.01em` 标题）建立层级。

### 3.3 间距 / 圆角 / 阴影

- 间距按 8px 系统：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 72（分节）；面板内统一 20px，区块间 40px。
- 圆角：控件 6px（现有 `--el-border-radius-base` 已是 6px），卡片 8px，弹窗 10px；不使用 >12px 圆角。
- 阴影：方案提出三档，v3 实际落地为**两档**（`--ds-shadow-1` 表面 / `--ds-shadow-2` 浮层），取值沿用现有中性色而非引入新色相。禁止"卡片套卡片 + 双阴影"。

## 4. 组件规范

### 4.1 布局骨架
- 顶部统一 Header（已定稿）：品牌左 + 模块导航中 + 账号/退出右；移动端导航换行自滚动。
- 内容区最大宽度 1200px，左右 padding 24px（≥1024px 时 32px）。
- 页面结构统一为：`页头（标题 + 主操作）→ 概览带 → 主内容 → 抽屉/弹窗`。

### 4.2 核心组件
| 组件 | 规范要点 |
| --- | --- |
| 按钮 | Primary 唯一（每屏 ≤1 个）；次级用描边按钮；破坏性操作用红描边按钮，不用红实心 |
| 状态徽章 | 匹配成功 / 金额差异 / 未匹配 / 处理中 四态；圆点 + 12px 文字，背景取语义色 10% 透明度 |
| 表格 | 行高 40px，表头 12px 大写灰字；金额右对齐 + tabular-nums；差异行整行浅红底 `#fef2f2` |
| 表单 | 标签内联左置（桌面双栏），说明文字折叠为"详细说明"披露；数字输入统一 40px 高右向步进器 |
| 抽屉（收款核对） | 右侧 480px；候选邮件按分数展示前三封，每封显示付款人/日期/金额/证据缩略；确认按钮需先勾选候选 |
| 空态 | 一句话说明 + 主行动按钮；邮件扫描空态须说明"已到所选范围末尾" |
| 任务条 | 邮件扫描/iLog 解锁共用心智：阶段名 + 进度条 + 暂停/恢复/取消 + "中断后已有结果保留" |

### 4.3 反馈与文案
- 成功提示讲结果（"12 笔差异已写入结果 Excel"），失败提示讲下一步（"缺少『原币金额』列，请检查第 3 行"）。
- 不确定的事不猜测：船名航次无法确定时报错并让用户选择，界面不显示"自动取第一条"。
- 所有百分比显示与底层字节计算一致，不在前端写死。

## 5. 关键界面方案（对应原型页）

1. **登录**：延续已定稿的左半幅深蓝影像 + 右侧白色表单构图；标题/字段/按钮左对齐同轴；中英切换在页脚；密码可见切换、通用错误文案。
2. **Excel 对账（核心工作流）**：三段式 —— ① 上传区（拖拽 + 字段校验即时反馈）；② 校验摘要（两家公司识别结果 + 阻止同公司比对的明确报错）；③ 结果页签（处理汇总 / 匹配成功 / 金额差异明细 / 未匹配 / 金额差异汇总 / 原始数据），页签上带数量徽章，主按钮"下载结果 Excel"固定在页头右侧。
3. **邮件收款**：左列表（邮件 + 状态徽章）右详情；确认关联走抽屉；列表按分数排序展示前三候选；人工确认后回写分数与检索信息，写入过程禁用按钮并显示进行态。
4. **iLog 解锁**：范围选择（按提单 / 按航次）+ 模式选择（标准 / 快速）+ 截图选项；任务列表显示每单状态与分阶段耗时；航次归并需用户确认清单后提交。
5. **设置**：沿用已验收层级 `概览 → 分组次要指标 → 2×2 规则区（旁挂保存）→ 优化 → 保守清理 → 诊断`；full 角色分别展示邮件与 iLog 两块规则。

## 6. 可访问性与响应式

- WCAG AA：正文对比 ≥ 4.5:1；焦点环 2px `--color-primary-500` offset 2px；全部功能可键盘到达；弹窗 Esc 关闭后焦点返回触发元素（现有行为保留）。
- 触控目标 ≥ 44px（移动端）；`prefers-reduced-motion` 下关闭过渡动画。
- 断点：≥1440 满配 / 1024 收窄侧栏 / 390 单列、表格转卡片、无横向溢出（与 design-qa 既有验收口径一致）。

## 7. 落地路线（Element Plus 映射）

1. **阶段一（令牌层）**：`frontend/src/style.css` 已有 `:root` 变量层（341 行，含 `--el-color-primary` 全套色阶），`main.ts` 中 Element Plus CSS 在前、`style.css` 在后，覆盖机制已生效。**无需新建 `tokens.css`，也无需改动 `main.ts`**：主色保持 `#0066cc` 原样不动，仅按需补几个语义变量（状态徽章、指标卡、任务条用）。业务代码零改动。
2. **阶段二（组件层，已落地）**：新建 `StatusBadge / MetricCard / TaskBar / ConfirmDialog` 四个通用组件；前三个已接入三条业务线，`ConfirmDialog` 因约束保留为后续统一入口（见 §9.3）。
3. **阶段三（页面层，已落地）**：三个主视图完成版式重排与组件接入；构建、双语隔离检查已通过，桌面/390px 截图与 Windows 实测仍需在运行环境补做。
4. **验收基线**：沿用 `design-qa.md` 口径——不删除既有保护与工作流、不写死数据、业务原文不翻译、Windows 实测另行安排。

## 8. 改造边界（不推翻重做）

本方案是**设计层的增量改造**，不是技术重构。以下部分原样保留：

- `views/`（MailView / ReconciliationView / UnlockView）与 `components/` 16 个组件的全部业务逻辑；
- `services/`、`platformHttp` 的后端 API 调用；
- `i18n.ts`、`platformLocale.ts` 的中英双语与角色/版本隔离；
- `App.vue` / `ExcelApp.vue` / `MailEnglishApp.vue` 应用外壳与权限导航；
- 后端、Windows PyInstaller 打包链路、数据目录与密钥；
- `design-qa.md` 中已验收通过的登录页、统一 Header、存储保留设置（final result: passed）。

按 `design-qa.md` 既有口径，页面改版时 `load / save / preview / cleanup / restore / poll / unmount` 等函数应保持与 HEAD 一致，仅动 CSS 与模板布局。

---
实施：v3 完整版已落地，业务代码零改动；验收与遗留项见 §9。

## 9. v3 实际落地记录（2026-11-06）

### 9.1 改动文件清单

| 类型 | 文件 | 改动 |
| --- | --- | --- |
| 修改（纯追加） | `frontend/src/style.css` | +134 行：`:root` 新增 `--ds-*` 令牌（全部映射到现有色值）、`.ds-*` 组件样式、`.ds-scope` 作用域内的 Element Plus 对齐、390px 响应式。上方 341 行**一行未改**，主色 `#0066cc` 色阶原样保留。 |
| 修改（模板/样式 + import） | `frontend/src/views/ReconciliationView.vue` | 接入 `StatusBadge` x4、`MetricCard` x9；根容器加 `.ds-scope`；结果指标改 3 列卡片网格 |
| 修改（模板/样式 + import） | `frontend/src/views/MailView.vue` | 接入 `StatusBadge`（邮件卡片）、`TaskBar`（读取任务阶段/进度）；列表与详情卡片节奏重排 |
| 修改（模板/样式 + import） | `frontend/src/views/UnlockView.vue` | 接入 `StatusBadge` x3；执行/结果面板节奏与等宽数字重排 |
| 新增 | `components/StatusBadge.vue` / `MetricCard.vue` / `TaskBar.vue` / `ConfirmDialog.vue` | 纯展示组件，零 API 调用、零业务状态；文案全部由 props/slots 传入，组件源码不含中文，避免污染语言目录 |

**零改动文件**：`services/` 全部、`i18n.ts`、`platformLocale.ts`、`main.ts`、`App.vue`、`ExcelApp.vue`、`MailEnglishApp.vue`、`PlatformLogin.vue`、`MailStorageSettings.vue`、`IlogRetentionSettings.vue`、`PlatformStorageSettings.vue`、`tooling/`、`vite.config.ts`、`package.json`、后端与打包链路。

**script 验证口径**：三个视图的 `<script setup>` 内容经哈希比对，**仅新增 1-2 行组件 import**，其余字节完全一致；其余 30 个源码文件 script 哈希与改动前完全相同。主色与既有色阶未改；新样式全部限定 `.ds-scope` / `.ds-*`。

### 9.2 验收结果

- `npm run build`（`vue-tsc -b` + `vite build`）：通过，1733 modules，产物 CSS 471KB / JS 1.42MB（chunk 体积警告为既有状况）。
- `npm run check`：通过 —— runtime locale 隔离、matching transport、mail English 目录 953 处、platform English 目录 1381 段 / 7 个运行时样本无中文残留。
- 构建产物实测：`npm run preview` 返回 200，CSS 产物含 `--ds-primary`、`--ds-sp-6`、`.ds-badge--*`、`.ds-metric__value`、`.ds-task__fill`、`.ds-scope .el-table`。
- 未执行：桌面/390px 截图对比、四种角色权限回归、Windows EXE 实测（需在运行环境执行）。

### 9.3 未接入项与原因

- **`ConfirmDialog.vue` 本次不替换现有确认**：项目内 10 处破坏性操作（`clearLocal`、`cancelRemaining`、`newTask`、`removeAccount`、`cleanupExpired` 等）的二次确认均写在业务函数内部（`ElMessageBox.confirm`）。在模板层再套一层会造成双重确认；要统一到新组件必须把这 10 处确认逻辑上移出业务函数，属于业务代码改动，与本次「零业务改动」约束冲突。组件已实现并留作后续统一入口。
- **结果六页签未新增**：后端 `AnalyzeSummary` 只返回汇总计数，不返回明细数据集；按 `design-qa.md`「不写死数据」口径，不造前端假数据。
- **登录 / Header / 保存设置页未动**：`design-qa.md` 已 `final result: passed`，且新样式全部限定 `.ds-scope`，这三处不受影响。
- **中性色/语义色未批量替换**：采用零视觉回归策略，110 处组件内硬编码色值保持原样。


## 10. v4 落地记录（1:1 效果图 → 真实代码，2026-11-06）

### 10.1 用户反馈与决策

- v3 被判定为「规范对齐，不是视觉改版」，第一屏零改动、改动集中在结果态。用户选择「先出 1:1 效果图」，确认后要求按效果图落地。
- 效果图评审中用户指出 **「执行模式 / 截图方式」两组分段选择器还是旧的更好**：效果图曾把它们拆成独立小方标签，已回退为胶囊单选组（32px 高 / 18px 圆角 / 实心 `#0066cc` 白字选中 / 选项间 1px 竖线 / 完整文案「仅异常保存」）。落地时这两组控件**一行样式都没加**，保持现状。
- 关键边界澄清：效果图的邮件页对应的是 **`收款匹配` 页签（`components/ReceiptMatching.vue`）**，即默认打开的那一页；不是 `MailView` 的「邮箱与邮件」页签。只改 MailView 会导致默认页零变化，故一并纳入。

### 10.2 改动文件清单（5 个，全部为样式层）

| 文件 | 改动 |
| --- | --- |
| `frontend/src/style.css` | 纯追加 v4 层（+199 行累计含 v3）：`--ds-hero-*` / `--ds-row-h-lg` / `--ds-title-icon` 令牌、`.ds-page-title`（CSS 绘制的蓝色柱状图图标块）、表格 44px 行高与浅灰表头、主按钮字重/阴影、`.quiet-state` 浅蓝空状态、720px 响应式。上方既有行**一行未改** |
| `frontend/src/views/UnlockView.vue` | `<h2>` 加 `ds-page-title`；scoped 追加浅蓝 hero 进度区（0/0 → 48px、进度条 8px）、`01 / 02` 分区序号（CSS counter，零文案）、解锁范围选中卡主色左边框、结果空状态圆形图标底、结果页脚分隔 |
| `frontend/src/components/ReceiptMatching.vue` | 根容器加 `ds-scope`；scoped 追加把**已有的四个 `reviewTabs` 按钮**重排成 4 列大数字指标卡（28px 等宽数字、选中卡浅蓝底 + 主色左边框），文案与数据全部沿用 `reviewTabs` computed，零新增中文；900/480px 降级为 2 列 / 1 列 |
| `frontend/src/views/ReconciliationView.vue` | scoped 追加：四个区块标题统一蓝色竖条 + 16px 层级、匹配规则卡头分隔线、状态区节奏 |
| `frontend/src/views/MailView.vue` | scoped 追加：邮件列表标题数字放大到 24px 主色、金额加粗、筛选 chip 选中态主色、页脚分隔 |

**`<script>` 验证口径**：37 个源码文件的 `<script>` 区块经 SHA-256 比对，**变化文件数 = 0**。本轮所有 `.vue` 改动仅涉及 `class` 属性与 `<style>` 块，未新增任何 import、中文字符串、数据绑定或业务函数。

### 10.3 零改动 / 明确不落地

- 零改动：`services/`、`i18n.ts`、`platformLocale.ts`、`main.ts`、`App.vue`、`ExcelApp.vue`、`MailEnglishApp.vue`、`PlatformLogin.vue`、三个设置组件、`tooling/`、`vite.config.ts`、`package.json`、后端与打包链路。
- **顶栏 64px 品牌块 / 导航指示条不落地**：属于 `App.vue` 统一 Header，`design-qa.md` 已 `final result: passed`，效果图 §6 也声明不动。
- **分段控件不落地**：按用户反馈保留现状。
- **结果六页签 / `ConfirmDialog` 接入 / 中性色批量替换**：沿用 §9.3 的原因，本轮未做。

### 10.4 验收结果

- `npm run build`（`vue-tsc -b` + `vite build`）：通过，1733 modules，CSS 479KB / JS 1.42MB（chunk 体积警告为既有状况）。
- `npm run check`：通过 —— runtime locale 隔离、matching transport、mail English 953 处 / 13 文件、platform English 1381 段 / 35 文件、7 个运行时样本无中文残留。
- 8765 产物：`index.html` 与 `frontend/dist/index.html` MD5 一致；CSS 含 `--ds-hero-top`、`ds-page-title`、`counter(ds-section`、`review-tabs button`、`decimal-leading-zero`。
- 8765 真实渲染（1440×1300，无头浏览器仅伪造 session，未碰账号/后端数据）：
  - `#unlock`：hero 浅蓝渐变生效、进度数字 48px、两处分区序号 counter 生效、选中卡主色框、分段控件仍为原胶囊、结果空状态圆形图标。
  - `#mail`：四个大数字指标卡（4 个 button / 28px 数字 / 1 个选中态）正常，搜索行与表头无错位。
  - `#excel`：四处标题蓝色竖条、规则卡分隔线、浅蓝空状态提示区正常。
  - 390px：三页 `scrollWidth == clientWidth == 390`，无横向溢出；指标卡降为单列 328px。
- 截图留档：`output/ui-redesign/landed-{unlock,mail,excel}.png`。截图里邮件页那条黄色「本地服务版本较旧」提示，是无头浏览器把非 session 的 `/api/*` 统一伪造为 `{}` 造成的，真实环境不出现。

### 10.5 查看方式

`npm run build` 后 8765 会实时读取 `frontend/dist`（`web_common.py::bundled_frontend_dir`），无需重启后端。assets 文件名已变化，需在**已登录**的浏览器里对 `http://127.0.0.1:8765/` 按 Cmd+Shift+R 强刷一次。


## 11. 评审修订：移除解锁页独立大标题（2026-11-06 16:45）

- **反馈**：用户截图圈出导航下方的「蓝色方块图标 + 大号『批量解锁』」，判定「太突兀了 不要了」。
- **判断**：顶部产品导航已有同名高亮标签，该标题属于重复信息；v4 的 27px 字重 + CSS 绘制的蓝色柱状图图标块是突兀感来源。按「整块移除」处理，而非缩小或改成图标less 小标题。
- **改动（2 个文件，纯 template / style，零 `<script>` 变化）**：
  | 文件 | 改动 |
  | --- | --- |
  | `frontend/src/views/UnlockView.vue` | 删除 `<h2 class="ds-page-title">` 整行；`<header class="unlock-heading">` 加 `ds-no-title` 标记，四个页签的按钮与状态标签全部保留 |
  | `frontend/src/style.css` | v4 层删除 `--ds-title-icon` 令牌、`.ds-page-title` / `.ds-page-title::before`、720px 内两条对应规则；新增 `.unlock-heading.ds-no-title { justify-content: flex-end; }` 保证右侧操作按钮不因标题消失而跑到左边 |
- **其他页面不动**：`ReconciliationView` / `MailView` 的区块标题竖条属于小节层级，和本次圈出的页面级标题不是同一元素。
- **验收**：`npm run build`、`npm run check` 均通过；8765 首页与 `frontend/dist/index.html` MD5 一致；构建 CSS 中 `ds-page-title` 残留计数 0、含 `ds-no-title`；真实 8765 渲染诊断 `oldTitlePresent=false`、`justify=flex-end`、操作按钮右边界 1408/1440、`overflow=false`，内容卡片位置无错位。`<script>` 区块与修订前逐字节一致（与 HEAD 的差异仅为 v3 既有组件 import）。
- **截图留档**：`output/ui-redesign/landed-unlock-no-title.png`（截图中「执行环境未就绪」为无头浏览器伪造 session 所致，真实环境不出现）。


## 12. 评审修订：表格表头铺满卡片（2026-11-06 16:51）

- **反馈**：用户截图「解锁历史」表格，指出「表头两边都没铺满」。
- **定位**：像素量测显示白色卡片左右各留出约 16 CSS px 白边，浅灰表头（`#f5f5f7`）只覆盖表格自身宽度。根因是既有 `.management-section{padding:0 16px}`（style.css 329 行），720px 下还有 `padding:0 8px`（339 行）；表头与分隔线都停在 padding 内侧。
- **改动（2 个文件，纯 class / CSS，零 `<script>` 变化）**：
  | 文件 | 改动 |
  | --- | --- |
  | `frontend/src/views/UnlockView.vue` | 「业务账号」「解锁历史」两个 `management-section` 加 `ds-flush-table` 标记；「保存设置」不加（它是表单，需要保留内边距） |
  | `frontend/src/style.css` | v4 层追加 `.management-section.ds-flush-table{padding:0}`，并把内缩还给首列/末列 `.cell`（28px = 原容器 16 + 单元格 12，文字位置不变）与 `.management-footer`（16px）；0,2,0 特异性同时覆盖 720px 的 8px 规则 |
- **不采用**负 margin / header-wrapper padding 方案：会挤压 Element Plus 表头内部 table 宽度，造成表头与表体列错位。
- **验收**：build / check 通过；8765 与 dist MD5 一致，CSS 含 `ds-flush-table`；真实 8765 渲染（伪造一条历史数据）诊断：卡片 `l=32,r=1408`、表头 `l=33,r=1407`，**左右 gap 由 16px 降为 1px（仅 1px 边框）**，首列 padding-left 28px、footer padding-left 16px、无横向溢出；业务账号页同样 gap=1px；390px `scrollWidth == clientWidth == 390`。
- **截图留档**：`output/ui-redesign/landed-history-flush.png`。
