# iLog 查询脚本

只执行：读取本机已保存账号 → 选择登录语言 → 登录 → 委托单列表 → 按提单号回车查询。
不执行订单解锁、费用修改、密码修改或任何业务提交。不自动重试登录。

## 本地使用

在项目目录安装可选依赖（不影响现有 EXE 的依赖）：

```sh
.venv/bin/pip install -r backend/requirements-automation.txt
PYTHONPATH=backend .venv/bin/python -m app.ilog_query --account ilog-west-indonesia --bill TRBTSTZH2606S
```

默认使用已安装的 Chrome 无头执行；加 `--show-browser` 可显示浏览器。
Windows 可选择 `--channel msedge`，但此独立脚本尚未做 Windows 打包验证。
东印尼账号 ID 为 `ilog-east-indonesia`，西印尼为 `ilog-west-indonesia`。

账号密码只从本机账号库读取，不写入脚本、参数或输出；不持久化浏览器登录会话。
请勿同时用同一账号启动多个任务，以免平台踢出会话。

## 输出

- `found`：返回匹配行，最多 10 条，以及查询截图路径。
- `not_found`：查询完成，列表为空；仅代表当前账号的可见范围。
- `needs_manual`：页面超时或结果异常，停止并说明所在阶段，不能当作查无记录。
- `query_failed` / `failed`：查询或运行失败，退出状态非零。

截图可能包含业务数据，保存在本机应用数据目录的 `query-results` 文件夹；按需删除，勿上传公共仓库。

2026-10-16 实测：西印尼账号查询 TRBTSTZH2606S 返回 1 条，订舱号 SLINX260300253。
脚本当前独立运行，尚未接入页面按钮，不能据此认定页面的批量解锁功能已经接通。
