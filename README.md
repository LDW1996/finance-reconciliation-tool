# 财务Excel自动核对工具

第一阶段实现为本地 Web 应用：FastAPI 负责 Excel 读取、校验、核对和导出，Vue3 + Element Plus 负责上传、状态展示和下载。

当前页面按真实往来表流程设计：上传一份包含 `公司代码or伙伴公司`、`分配`、`原币金额` 的往来 Excel，系统自动拆分两家公司并生成对账结果。

## Mac 本地启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
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
5. 点击 `Run workflow`。
6. 等构建完成后，进入本次运行记录。
7. 在 `Artifacts` 下载 `财务对账工具-Windows`。
8. 解压后会得到：

```text
财务对账工具.exe
财务人员使用说明.txt
```

### 方案二：Windows 本机打包

在 Windows 打包机上准备：

1. 安装 Python 3.11
2. 安装 Node.js 20 或更高版本
3. 在项目根目录执行：

```bat
npm install
npm install --prefix frontend
npm run frontend:build
packaging\build_windows.bat
```

生成文件：

```text
dist\财务对账工具.exe
```

交付给财务人员时，把以下两个文件放在同一个文件夹发给对方：

```text
dist\财务对账工具.exe
packaging\财务人员使用说明.txt
```

财务人员使用方式：

1. 双击 `财务对账工具.exe`
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
