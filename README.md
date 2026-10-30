# 财务Excel自动核对工具

第一阶段实现为本地 Web 应用：FastAPI 负责 Excel 读取、校验、核对和导出，Vue3 + Element Plus 负责上传、状态展示和下载。

当前页面按真实往来表流程设计：上传一份包含 `公司代码or伙伴公司`、`分配`、`原币金额` 的往来 Excel，系统自动拆分两家公司并生成对账结果。

## Mac 本地启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm install
npm install --prefix frontend
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

## 验证

```bash
npm test
npm run frontend:build
```

## Windows 打包

Windows `.exe` 需要在 Windows 电脑上构建。Mac 不能可靠直接交叉打包 Windows 可执行文件。

### 方案一：GitHub Actions 云端打包

没有 Windows 电脑时，推荐用这个方案。

1. 把项目上传到 GitHub 仓库。
2. 打开仓库页面。
3. 进入 `Actions`。
4. 选择 `Build Windows EXE`。
5. 点击 `Run workflow`，选择版本。默认不勾选“快速打包”，会执行完整测试。
   - `Windows Tests` 会在推送主分支时独立执行全量 Windows 测试，也支持手动启动。
   - 同一提交的 `Windows Tests` 已通过时，可勾选“快速打包”，跳过重复测试。
   - 没有通过记录、记录不属于当前提交或无法查询时，快速打包会停止并提示；可取消勾选使用完整流程。
   - 两种模式均保留 EXE 启动和功能检查，通过后才上传成品。快速模式主要节省重复测试的时间，依赖安装和生成 EXE 仍需执行。
6. 等构建完成后，进入本次运行记录。
7. 在 `Artifacts` 下载 `财务综合业务平台`（压缩包为 `财务综合业务平台.zip`）。
8. 解压后会得到：

```text
财务综合业务平台.exe
财务人员使用说明.txt
```

### 独立英文邮件核销版

`Build Windows EXE` 的 `edition` 可选 `mail_en`。产物为 `邮件核销工具（英文版）.zip`，包含 `邮件核销工具（英文版）.exe` 和 `User Guide.txt`。独立版仅提供邮件核销与存储管理，保留Outlook、OCR、在线银行流水、客户台账、确认回写和导出，不提供Excel对账、iLog或账号管理接口。

界面和程序生成的说明使用英文；客户名称、邮件正文和原始银行表格内容保留原文。同一用户使用原有邮件数据目录和密钥，与原版共用8765端口，请关闭原版后再打开。构建文件不包含本机邮件、账号或Google授权数据。英文专用变更推送后会自动触发 `Build English Mail Reconciliation`，也可手动运行；只有Windows打包和EXE启动检查通过才会上传成品。

本地检查英文界面可使用 `VITE_EDITION=mail_en npm run build --prefix frontend -- --outDir ../build/mail-en-frontend`；普通构建仍为原中文版本。

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

财务人员使用方式：

1. 双击 `财务综合业务平台.exe`
2. 浏览器会自动打开
3. 上传往来 Excel
4. 检查通过后点击 `开始分析`
5. 点击 `下载结果Excel`

如果浏览器没有自动打开，手动访问：

```text
http://127.0.0.1:8765
```

## 当前范围

- 支持 `.xlsx` / `.xls`
- 必要字段校验：`公司代码or伙伴公司`、`分配`、`原币金额`
- 自动识别两家公司代码
- 阻止同公司文件比对
- 按 `分配` 汇总两家公司金额
- 输出：处理汇总、匹配成功、金额差异明细、未匹配、金额差异汇总、原始数据

Windows 一键启动程序已预留 PyInstaller 打包层，入口为 `backend/launcher.py`。

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
