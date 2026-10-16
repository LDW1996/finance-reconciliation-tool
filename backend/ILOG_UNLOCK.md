# iLog 单笔解锁与结果核实

单笔脚本已由页面后端串行任务调用；页面确认后执行真实解锁。本脚本只操作单个委托单，不操作 Vessel 菜单或费用明细逐行审核。

## 默认只读

安装 `backend/requirements-automation.txt` 后，在项目目录运行：

```sh
PYTHONPATH=backend .venv/bin/python -m app.ilog_unlock --account ilog-west-indonesia --bill TRBTSTZH2606S --kind fee
```

默认只登录、查询、记录当前状态并保存截图。加 `--show-browser` 显示浏览器。
`--channel msedge` 可选择 Edge；Windows 运行和打包尚未验证。

## 显式执行

必须获得业务人员对账号、单号、锁类型的授权后再执行。普通查询命令不会提交解锁。
真正执行需要同时提供 `--execute` 和与 `--bill` 完全相同的 `--confirm-bill`。
`--kind fee` 对应费用小锁（操作 3），`--kind order` 对应订单大锁（操作 4）。

脚本验证：查询结果只有一条、提单号精确匹配、选中记录 ID 一致、确认提示一致，且提交请求的 ID 与操作类型完全一致。
缺一项即停止。支持标准 JSON 和旧式 JSON5 数据响应，不使用 eval 执行返回文本。

## 逐笔结果

| 状态 | 含义 |
| --- | --- |
| inspected | 只读检查完成，未解锁 |
| verified_unlocked | 平台明确成功且重新查询为 U（列表无锁图标） |
| verified_fee_unlocked | 小锁平台明确成功且复查 A → B，仍可能保留另一层锁 |
| platform_confirmed | 平台明确成功且锁状态变化，但仍有锁，不能等同全部解除 |
| already_unlocked | 原状态 U，未再次提交 |
| already_fee_unlocked | 小锁原状态 B，未再次提交 |
| rejected | 平台明确拒绝，附返回说明 |
| needs_verification | 响应异常、超时、状态未变化或复查失败，不认定成功 |
| not_found / ambiguous | 没有记录或结果不唯一，未提交 |
| blocked_duplicate | 本机存在此账号、单号、锁类型的历史提交，阻止重复执行 |

图标映射：U 无锁，A 为 lock 图标，其余为 block 图标。当前平台实测小锁成功 A → B、大锁成功 B → U；只有平台明确成功且状态吻合才认定相应操作已核实。

结果和截图保存在本机应用数据目录 `unlock-results`，每次独立 JSON 报告；`submitted` 子目录记录不可盲目重试的操作。
确认提交前先持久化日志，进程崩溃、断网、解析失败后不会自动重试。
日志中的 `submitted: true` 表示已进入提交阶段，极端情况下可能尚未到达服务器，必须人工核实。
不要为重跑直接删除日志。更换电脑不会自动同步这些本机保护记录。

2026-10-16：将此前已授权执行过的 TRBTSTZH2606S 费用解锁导入本机保护记录；新脚本仅只读复查，结果 B，没有重复提交。
随后实测：TRBTSTZH2606S 大锁返回明确成功且 B → U；TRBTSTHP2607S 小锁返回明确成功且 A → B，均只提交一次。

## 页面任务

`POST /api/ilog/tasks` 接受已确认的账号、fee/order 单号清单及唯一 request_id；后台再次校验格式、去重并串行执行。所有写接口需要本机页面请求头和本机 Origin 校验。
`GET /api/ilog/tasks/latest` 恢复最近任务；`GET /api/ilog/tasks/{id}` 读取逐笔进度。
`POST /api/ilog/tasks/{id}/control` 支持 pause/resume/cancel。异常暂停后继续只执行等待项，已提交项不自动重放。
任务保存在应用数据目录 `ilog-tasks/tasks.sqlite3`；跨线程及进程通过 SQLite 事务和工作锁串行执行。
页面接线通过请求拦截测试与后端替身执行器测试，没有额外对真实订单执行批量解锁。

## 账号兜底

任务可指定一个 `fallback_account_id`，需与主账号不同且已保存密码，并由用户在同一确认窗口授权。
只有主账号明确查无订单且没有提交过操作 (`not_found`, `submitted=false`) 才查询兜底账号。
登录失败、查询异常、多条结果、解锁拒绝和结果待核实均不会兜底。
当前单的处理包含主账号及兜底查询，暂停仍只停止后续单号，不中断当前单。
每次尝试和实际账号在切换前写入任务数据库；发生崩溃后标记待核实，不自动重放当前单。
两边都未找到则标记待核实，并暂停后续单。继续只跳过异常项、执行等待项。
2026-10-16 已使用真实平台做只读链路验证：雅加达未找到 TRBTSTZH2606S，巴淡找到；两次均未提交解锁。

## 数据保留

默认截图 90 天、正常结束的任务结果 365 天，可通过页面“保存设置”修改。
打开页面、查看历史或新建任务时进行每日最多一次的到期检查；服务不运行时不会定时唤醒，有执行任务则延后。
运行、暂停、待核实任务及关联证据不清理。独立脚本的待核实或无法识别报告也保留。
只遍历本机 `unlock-results`、`query-results` 顶层的报告和截图，不递归进入 `submitted`。任务数据清理后保留确认请求墓碑，防止旧请求重放。
提供 GET `/api/ilog/retention`、PUT 同路径、GET `/api/ilog/retention/preview` 和需本机请求头及确认的 POST `/api/ilog/retention/cleanup`。
历史记录分页通过 GET `/api/ilog/tasks?limit=20&offset=0` 读取。清理不修改任何远程业务状态。
