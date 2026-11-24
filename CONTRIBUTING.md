# Contributing to CoSolve Lens

感谢你改进 CoSolve Lens。提交代码前请先阅读 README，并遵守以下数据边界。

## 开发流程

1. Fork 仓库并从 `main` 创建短期分支。
2. 使用 `.env.example` 创建本地 `.env`，不要提交任何真实 Key。
3. 运行 `scripts/setup.ps1` 安装依赖。
4. 修改后运行后端测试、前端构建和相关浏览器测试。
5. 提交说明应写清问题、行为变化、验证方法和数据影响。

## 数据与隐私

- Pull Request 只能使用合成数据或确认可公开再分发的数据。
- 不要提交学生姓名、账号、原始对话、成绩、数据库、浏览器配置、日志、API Key 或来源快照。
- 新 fixture 应使用虚构身份，并在文件中说明其为合成数据。
- 发现疑似真实数据时，请停止提交并按 `SECURITY.md` 私下报告。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Set-Location frontend
npm run build
npm run test:e2e
```

界面变化应同时说明教师、学生和管理员角色是否受影响。评价逻辑变化还应提供量规版本、证据范围与兼容性说明。
