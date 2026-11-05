# 附件诊断、英文回归与账号交付配置

- Outlook/MSG 附件内容读取失败保留异常类型、错误代码、处理阶段及脱敏诊断，不将异常原文或邮件内容写到提示里；单个附件失败不阻断其他附件或邮件。
- GitHub 完整/快速打包均执行 npm run check，覆盖词条、运行时翻译和源数据隔离。字符串样例调用真实 platformText，不冒称浏览器端到端验证。
- 正式包从 build/platform_accounts.json 或 FINANCE_PLATFORM_ACCOUNTS 指定的同名文件读取哈希，不再使用仓库内的固定哈希文件；本地开发配置保留原路径并加入忽略规则。
- GitHub 沿用已有 PLATFORM_SMOKE_ACCOUNTS Secret（excel/mail/ilog/full 到口令的 JSON），准备步骤生成随机盐 PBKDF2 哈希，然后用同一密钥做 EXE 冒烟登录。缺失或非法配置时停止打包，不回退固定口令。
- 同一 Secret 不变则升级后登录口令不变；无需移动用户数据。更换 Secret 意味着新交付包使用新口令，应由交付方同步通知。
- Windows 手工打包前设置 PLATFORM_SMOKE_ACCOUNTS 环境变量并运行 python packaging/prepare_platform_accounts.py；或设置 FINANCE_PLATFORM_ACCOUNTS 指向外部平台哈希 JSON（文件名须为 platform_accounts.json）。不要上传这些配置或原始口令。
- 历史 Git 提交中的旧哈希没有改写。此次变更阻止后续包继续自动使用旧哈希，不代表已从历史中删除凭据。
- 保留并行会话的路由顺序改动，不改 .workbuddy/；不执行真实解锁或远程打包。
