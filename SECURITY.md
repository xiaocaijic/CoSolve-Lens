# Security Policy

CoSolve Lens 当前是本地教学与研究原型，不具备生产级身份认证，不应直接暴露到公网。

## 支持范围

安全修复优先覆盖 `main` 分支的最新版本。历史课程快照、第三方数据集和本地部署环境不属于公开仓库的支持范围。

## 报告问题

请使用 GitHub 仓库的 **Security → Report a vulnerability** 私下报告漏洞。不要在公开 Issue 中粘贴 API Key、学生数据、数据库、日志或可识别个人的信息。

报告最好包含：

- 受影响版本与运行环境；
- 可复现的最小步骤；
- 预期影响；
- 不含真实敏感数据的验证材料。

## 密钥处理

DeepSeek Key 只能通过本地环境变量或 Windows DPAPI 存储。`.env`、`var/`、SQLite、`.deepseek-key.dpapi`、浏览器配置和服务日志均不得提交到 Git。
