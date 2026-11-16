# 财务综合业务平台

财务综合业务平台是运行在本机浏览器里的财务工具。Windows 交付包包含一个可执行文件和使用说明。登录后，顶部菜单按账号权限显示以下模块。

| 模块 | 用途 |
| --- | --- |
| Excel 对账 | 上传往来 Excel，校验数据，导出对账明细或原表标色结果 |
| 利润中心上报 | 维护上报模板，按月份上传源报表，生成并下载结果包 |
| 邮件收款 | 读取 Outlook 收款邮件，为流水匹配候选邮件，人工确认后回写 |
| iLog | 按提单或航次执行费用解锁（小锁）和订单解锁（大锁），查看和导出结果 |

## 统一登录与角色

对外交付一个 `财务综合业务平台.zip`，解压后运行 `财务综合业务平台.exe`。启动后先使用交付方单独提供的固定平台账号登录；平台不提供注册、用户管理或权限编辑。交付密码不得写入仓库或使用说明。

| 固定角色 | 可访问业务 |
| --- | --- |
| excel | Excel 对账、利润中心上报 |
| mail | 邮件收款、匹配回写、邮件保存设置与维护 |
| ilog | iLog业务账号、按提单/按航次解锁、历史及解锁记录保留设置 |
| full | 上述全部业务 |

在登录页选择中文 / English，登录后的各模块统一沿用所选语言，内部不再重复显示语言切换。语言作为本机偏好保存，不影响权限。平台账号与“iLog业务账号”是两套不同凭据，后者仍用于登录公司iLog业务系统。退出遇到任务忙提示时，请等待当前操作完成再重试；退出不清空业务数据。

同一Windows用户仍沿用 `%LOCALAPPDATA%\FinanceReconciliation`，切换平台账号不新建数据库。升级前关闭旧程序，完整备份数据目录及密钥，再解压运行新版本；不要同时运行新旧版本。原有邮件、附件、确认关联、读取断点、业务凭据和防重复记录继续保留。登录前不会自动恢复后台业务任务，进入对应授权业务后按现有恢复设置处理。不同电脑或Windows用户之间不自动同步。

## Mac 本地开发与预览

首次启动前，在项目根目录安装依赖。

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm install
npm install --prefix frontend
npm run dev
```

开发页面访问 `http://127.0.0.1:5173`，后端 API 监听 `127.0.0.1:8000`。

要在 8765 端口预览打包版使用的页面，从项目根目录执行以下命令。前端改动后重新构建即可。

```bash
npm run frontend:build
./启动服务.command
```

打开 `http://127.0.0.1:8765`。

## 验证

```bash
npm test
npm run frontend:build
```

## Windows 打包

Windows `.exe` 需要在 Windows 电脑上构建。Mac 不能可靠直接交叉打包 Windows 可执行文件。

### 方案一：GitHub Actions 云端打包

推送到 `main` 时自动运行一次 `Windows Tests`，日常推送只跑测试，不会生成 EXE。需要交付时手动打包：

1. 确认待打包提交的 `Windows Tests` 已通过。
2. 在 GitHub 仓库进入 `Actions → Build Financial Platform → Run workflow`，选择对应的分支或引用。
3. 保留默认勾选的「快速打包」。工作流会检查**同一提交**的 Windows 测试通过记录，复用该结果。缺少通过记录时，先等待或手动运行 `Windows Tests`。如果要在打包流程中重新跑全量测试，可以取消勾选。
4. 两种模式都会构建 EXE，并对成品做冒烟测试。通过后，从该次运行的 `Artifacts` 下载「财务综合业务平台」。

解压后得到：

```text
财务综合业务平台.exe
使用说明.txt
```

### 方案二：Windows 本机打包

在 Windows 打包机上准备：

1. 安装 Python 3.11
2. 安装 Node.js 20 或更高版本
3. 在项目根目录执行：

```bat
npm ci --prefix frontend
npm run build --prefix frontend
packaging\build_windows.bat
```

生成文件：

```text
dist\财务综合业务平台.exe
```

交付给财务人员时，把以下两个文件放在同一个文件夹发给对方：

```text
dist\财务综合业务平台.exe
packaging\财务人员使用说明.txt
```

财务人员双击 `财务综合业务平台.exe`，在打开的浏览器中登录，再选择需要使用的模块。浏览器未自动打开时，访问 `http://127.0.0.1:8765`。

## Excel 对账

1. 选择「对账结果」或「原表标色」，上传一份包含两家公司往来明细的 `.xlsx` 或 `.xls` 文件。
2. 系统检查 `公司代码or伙伴公司`、`分配`、`原币金额`，并识别公司与明细行数。检查通过后点击「开始分析」。默认按「分配」汇总两家公司的原币金额，可设置金额容差。
3. 下载对账 Excel 或标色 Excel。对账文件包含汇总、匹配记录、金额差异、未匹配项和原始数据。原表标色会生成一份新文件。

## 利润中心上报

1. 首次使用时，在「模板管理」上传模板。模板保存后可供后续月份复用。
2. 在「数据上报」先选上报月份（支持 2–12 月），再按清单上传上月、本月的源报表。清单标明每份报表是「已上传」还是「待上传」。
3. 按需录入高管工资。留空时结果会标为待手工录入。点击「生成结果」，查看校验结果并下载结果包。

## iLog 解锁

顶部“iLog”支持按提单、按航次两种范围。按提单逐单处理输入清单；按航次只需输入该航次任一提单，系统先只读查询船名与航次、归并同航次代表单，用户确认清单后再提交平台费用解锁(Vessel)或订单解锁(Vessel)。船名航次无法可靠确定时报错而不猜测，也不默认取查询结果第一条；平台不返回全航次影响数量，因此不会宣称整航次逐笔核验。

任务可选标准模式（提交后复查）或快速模式（不复查，仅记录平台返回成功），可配置截图方式（仅异常/每笔/不保存）与分阶段耗时。可暂停或取消剩余项，也可为首选账号查无订单的情况设置备用账号。业务账号页可维护平台地址并测试登录。实现口径、结果状态与首批真实验证见 [iLog 解锁与结果核实](backend/ILOG_UNLOCK.md)。

## Outlook 邮件读取

顶部“邮件收款”支持 Windows 经典版 Outlook 自动分批补扫及增量读取，图片和 PDF 附件在本地识别。可连接 Google Sheets 在线流水，按付款人、付款日期和金额生成 0–100 分候选，默认展示前三封来源邮件；人工确认后回写专用备注、分数和邮件检索信息，本地保留加密修改记录。手工导入流水及导出保留为备用。MSG/EML 手工导入用于验证。不会发送或删除邮件、改变核销状态或触发 iLog 解锁。

连接条件、扫描上限、隐私和财务验证步骤见 [Outlook 邮件读取说明](backend/OUTLOOK_MAIL.md)。Windows 安装依赖时会自动安装 pywin32，现有打包流程包含邮件模块。

### 打包优化与诊断

- 财务交付仍为单个 EXE 加使用说明，用户数据目录保持不变。
- `backend/requirements.txt` 是运行依赖；本地开发/测试使用 `backend/requirements-dev.txt`；打包机使用 `packaging/requirements-build.txt`，固定 PyInstaller 和标准 hooks 版本。
- Actions 缓存 pip 下载及前端 npm 下载；首次或依赖变更后缓存可能不命中，仍会正常安装。缓存不跳过测试，不缓存 EXE 或用户数据。
- 恢复 NumPy、pandas、Playwright、MSG、OCR、ONNX Runtime、PDFium 的完整依赖收集方式，不再裁剪子模块；显式保留 NumPy C 扩展动态加载的 `numpy._core._exceptions`，并固定 NumPy 版本。当前优先保证兼容性，不承诺缩小包体。
- 打包阶段检查关键模型、Playwright Node 驱动与 PDFium DLL。成品冒烟测试另启 EXE 内部离线验证，加载完整服务配置、MSG、Windows COM 组件和浏览器驱动桥接，不连接真实 Outlook/iLog；继续验证图片 OCR、PDF、Excel 和本地服务。
- `build-diagnostics` 是单独的开发诊断附件，提供 `bundle-size-report.json`，列出压缩前文件占用、依赖分组、最大文件及 EXE 大小；体积清单不含本机源文件路径；另包含合成测试的启动日志、组件检查结果及构建警告，用于定位成品异常，不包含财务邮件或账号数据。不需要发给财务人员。
- EXE 本身已压缩，上传交付附件时不再额外进行 ZIP 高强度压缩；文件名、解压及运行方式不变。
- 本地打包保留 `--clean`，避免历史分析缓存造成漏文件。实际 Windows 包体和耗时需比较优化前后同环境 Actions 结果，不以 Mac 安装目录大小估算。

## 平台账号交付配置

正式包不再从仓库读取固定的 `backend/app/platform_accounts.json`。GitHub 使用已有的
`PLATFORM_SMOKE_ACCOUNTS` Secret（`excel`、`mail`、`ilog`、`full` 对应口令的 JSON），
在打包前生成 `build/platform_accounts.json`，只将带随机盐的密码哈希放入 EXE。
同一密钥配置不变则登录口令不变，不影响本地邮件、业务账号及读取进度。
缺少配置会停止打包，不使用默认口令。原有本地开发账号文件保留但不再提交。

手工打包前，可设置上述环境变量后运行 `python packaging/prepare_platform_accounts.py`；
也可通过 `FINANCE_PLATFORM_ACCOUNTS` 指定外部哈希文件，文件名须为 `platform_accounts.json`。
不要上传口令或这些配置文件。历史提交中的旧哈希未被改写。
