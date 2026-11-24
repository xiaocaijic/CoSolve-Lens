# CoSolve Lens

> Evidence-Based Assessment for Human–AI Problem Solving<br>
> 大学生 AI 协同问题解决评价系统

这是一个面向课堂教学、评价方法验证和小规模研究的本地 Web 系统。它把学生使用 AI 解决计算机科学任务时的对话、代码、运行、测试和提交记录统一为可追溯证据，按五个过程维度生成评价报告，并支持教师复核、学生任务、数据导入和研究导出。

[在线查看去标识化案例演示](https://xiaocaijic.github.io/CoSolve-Lens/) · [下载 Windows Release](https://github.com/xiaocaijic/CoSolve-Lens/releases)

系统使用 React + TypeScript 前端、FastAPI 后端和 SQLite 本地数据库。默认为 Windows 本机演示模式，不需要云服务即可运行规则评价、演示任务和合成数据流程。

> [!IMPORTANT]
> 当前是本地教学／研究原型，没有生产身份认证，不得直接暴露到公网。代码按根目录 MIT License 开源；真实学生数据、StudyChat 会话及第三方课程材料不因代码许可证而自动获得公开授权。

### 公开干净克隆与本机研究实例的差别

| 能力 | 公开干净克隆 | 已配置的本机研究实例 |
|---|---|---|
| 10 个学生任务与本地材料 | 有 | 有 |
| 合成 Demo 与 Canonical JSON 导入 | 有 | 有 |
| 规则评价、复核和导出 | 有 | 有 |
| 12 个 StudyChat 精选案例 | 默认为空，需授权快照 | 可有 |
| 8 份 StudyChat 正式报告 | 不包含本地数据库，默认不显示 | 可有 |
| DeepSeek 真实评价 | 默认关闭，需 Key 和授权 | 可选配置 |

新用户在干净克隆中的正常首启结果是：学生任务库有 10 个任务，StudyChat 精选库可能为空，但通用 JSON 导入和合成 Demo 可完整运行。

文档对应应用版本 `0.1.0`，最后验收日期为 2026-11-24；验收环境为 Windows、Python 3.12.4、Node.js 24.22.3 和 Microsoft Edge。

## 适合用来做什么

- 展示学生如何提问、质疑 AI、调整方案和检查结果。
- 对 Canonical JSON / JSONL、StudyChat 快照或内置演示数据进行现场分析。
- 生成带原始证据定位的五维过程报告。
- 由教师核对真实提交材料，单独填写本地成果分。
- 让学生在本地工作台完成任务、记录 AI 对话并提交结果。
- 为课堂讲评、量规校准和研究数据核查提供审计记录。

## 当前功能

### 教师端

- **StudyChat 精选回放**：12 个精选案例，当前包含 8 份正式五分制报告和 4 份课堂证据讲评。
- **数据集评价**：选择数据源、任务和会话，先检查范围与质量，再创建新评价 Job。
- **证据定位**：从维度、批注或建议直接跳转到真实学生消息。
- **教师复核**：确认、修改后确认或退回重评，每次操作保留版本和理由。
- **本地成果评价**：来源成果分与教师按本地材料填写的成果分分开展示。
- **研究分析与导出**：查看报告分布、方法分歧，导出人工复核材料。

### 学生端

任务库当前有 10 个任务：

1. 校园商店销售数据清洗与分析
2. StudyChat A3 · Data Cleaning and Preprocessing
3. StudyChat A2 · SearchComplete
4. StudyChat A5 · Judging Flowers
5. 校园出行高峰诊断
6. 图书借阅异常检查
7. 社团活动排程与冲突解释
8. 食堂评论主题与情感基线
9. 宿舍能耗预测与泄漏检查
10. 课程先修关系路径检查

学生可以下载本地材料、编辑代码、保存快照、使用离线演示 AI、填写实际输出与检查记录，最后冻结当前版本并提交评价。

### 管理员端

- 配置评价模型、超时、用途和保留策略。
- 使用 Windows DPAPI 保存 DeepSeek Key，或从服务端环境变量读取。
- 运行配置自检，管理 StudyChat 回放与现场分析入口。
- 查看版本化量规、提示词和诊断阈值。
- 创建、修改和发布课堂任务。

## 五维评价

当前量规为 `bars-3.0-five-point`，每维 1–5 分：

1. 问题理解
2. 信息甄别
3. 分析推理
4. 结果评价
5. 策略调整

至少三维有足够证据时才计算过程均分。证据不足或任务中没有评价机会的维度保留空值，不用零分替代。

新版学生建议必须是直接、可执行的陈述式动作，不允许把建议写成问题。每份正式报告末尾还会提供 1–2 个迁移练习和完成标准。

## 运行环境

推荐在 Windows 10/11 上运行。

- Python 3.11 或更高（验收环境为 Python 3.12.4）
- Node.js 20 或更高与 npm（验收环境为 Node.js 24.22.3）
- Microsoft Edge 或 Google Chrome
- **桌面 EXE 模式必需**：Windows .NET Framework C# 编译器 `csc.exe`；纯开发模式不需要
- PowerShell 5.1 或 PowerShell 7

在安装前先验证命令可用：

```powershell
python --version
node --version
npm --version
Get-Command git -ErrorAction SilentlyContinue
Test-Path "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
Test-Path "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
```

Python 和 Node.js 安装时要确认已加入 `PATH`。如果两个 `csc.exe` 路径都不存在，可先使用下文的开发模式；需要 EXE 时再安装包含 .NET Framework 编译器的 Windows 开发工具。

桌面构建脚本当前只查找上面两个系统目录中的 `csc.exe`。在干净的 Windows 机器上，可从微软官网下载并安装 [.NET Framework 4.8 Developer Pack](https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48)，重启 PowerShell 后重新执行两个 `Test-Path`。微软的[开发者安装指南](https://learn.microsoft.com/en-us/dotnet/framework/install/guide-for-developers)说明了 Developer Pack 与运行时的区别：只安装运行时不等于具备构建工具。如果安装后这两个路径仍为 `False`，当前脚本不会自动发现 Visual Studio 自带的 Roslyn 编译器；请先使用开发模式，或修改 `scripts/build-desktop.ps1` 的编译器探测路径后再构建。

## 获取源码

如果项目已发布到 Git 托管平台：

```powershell
git clone https://github.com/xiaocaijic/CoSolve-Lens.git
Set-Location -LiteralPath '.\CoSolve-Lens'
```

如果获得的是 ZIP，先完整解压，再在包含 `backend`、`frontend`、`scripts` 和 `.env.example` 的根目录打开 PowerShell。

如 PowerShell 阻止本地 `.ps1` 脚本，先查看：

```powershell
Get-ExecutionPolicy -List
```

不要全局关闭脚本安全。可按组织策略由管理员处理，或只对当前 PowerShell 进程使用组织允许的执行策略。

## 快速开始：Windows 桌面模式

### 1. 安装依赖

在 PowerShell 中进入项目根目录：

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens'
Copy-Item .env.example .env
.\scripts\setup.ps1
```

`setup.ps1` 会创建 `.venv`，安装 Python 依赖，并在 `frontend/` 执行 `npm ci`。

### 2. 构建前端和三个登录器

```powershell
.\scripts\build-desktop.ps1
```

构建成功后根目录会出现：

- `教师登录.exe`
- `学生登录.exe`
- `管理员登录.exe`

### 3. 启动

双击任意一个登录 EXE。启动器会：

1. 运行数据库迁移。
2. 启动 FastAPI 和评价 worker。
3. 在 `http://127.0.0.1:5188` 提供构建后的前端。
4. 用独立浏览器配置打开对应角色的登录页。

三个角色共用同一个 SQLite 数据库，但使用不同浏览器配置。

### 4. 停止桌面服务

```powershell
.\教师登录.exe --stop
```

如需仅检查或启动服务而不打开浏览器：

```powershell
.\教师登录.exe --check
```

`--check` 不是纯只读探测：如果服务未启动，它会启动 5188 服务并等待就绪；如果服务已启动，它只验证实例与 worker 状态。成功返回码为 0，失败返回非 0，错误记录到 `var/desktop/launcher.log`。

## 快速开始：开发模式

开发模式默认使用 API 端口 `8000` 和 Vite 端口 `5173`。

`scripts/start.ps1` 会根据 `-WebPort` 自动设置 `AI_COLLAB_WEB_ORIGIN`，桌面启动器会自动将它设为 `http://127.0.0.1:5188`。一般不需要为本地启动手动修改这个变量。

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens'
Copy-Item .env.example .env
.\scripts\setup.ps1
.\scripts\start.ps1
```

打开：

- 前端：<http://127.0.0.1:5173>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>
- 就绪检查：<http://127.0.0.1:8000/api/readiness>

使用独立数据库和端口：

```powershell
.\scripts\start.ps1 -ApiPort 8016 -WebPort 5180 -Database var\local-test.sqlite3
```

## 第一次进入系统

默认 `.env.example` 使用 `AI_COLLAB_MODE=demo`，登录页会提供三个本地演示角色：

- **教师**：查看精选回放、导入数据、创建评价、复核报告和导出研究材料。
- **学生**：从 10 个任务中选择一个，下载材料并在工作台作答。
- **管理员**：配置模型、量规、StudyChat 入口和任务版本。

演示登录只用于本机展示与验收，不是生产身份认证。

演示模式不需要输入用户名或密码，直接点击「以教师身份登录」、「以学生身份登录」或「以管理员身份登录」。

### 教师首次体验

1. 进入「数据集评价」。
2. 干净克隆没有 StudyChat 快照时，切换到「现场分析」并选择「通用 JSON / JSONL」。
3. 使用 `fixtures/new_import_input.json` 完成下一节的导入流程。
4. 在「本次新生成的报告」中点击「查看本次报告」。
5. 确认页面显示五维、证据按钮、过程分和数据说明。

### 学生首次体验

1. 进入「真人任务」，确认任务库有 10 张任务卡。
2. 选择「校园商店销售数据清洗与分析」并点击「开始任务」。
3. 下载材料，在代码编辑区保存一个快照。
4. 可选发送一条离线演示 AI 消息，并运行静态演示。该演示不会执行用户代码。
5. 填写结果、检查记录和局限，勾选确认后提交。提交会冻结当前版本；需要继续尝试时应新建一次作答，不要覆盖已提交版本。

### 管理员首次体验

1. 进入「系统设置」，查看「StudyChat 入口设置」和「量规与诊断阈值」。
2. 未配置外部模型时，点击「运行配置自检」，成功标志是本地数据库和 Demo 提供器可用。
3. 进入「任务管理」，创建一个草稿，保存后再发布。
4. 发布后以学生身份重新进入「真人任务」，确认任务可见。

## 导入一份新数据

1. 以教师身份登录。
2. 打开「数据集评价」并切换到「现场分析」。
3. 选择「通用 JSON / JSONL」。
4. 点击「下一步 · 选择范围」和「导入并检查」。
5. 上传 Canonical v1.0 文件，填写稳定来源标识、用途、许可说明和保留期限。
6. 预览通过后，勾选「我确认有权按所填用途导入这些记录」。
7. 确认导入，选择会话，运行范围检查。
8. 点击「开始分析」，等待 worker 生成当次报告。

> [!WARNING]
> 导入时填写的保留期限目前只是元数据，系统不会在到期日自动删除数据。部署者需要在本地运维流程中安排备份、到期复核和可恢复删除。

成功时，进度区会显示「已完成」和「本次新生成的报告」，每条成功记录有「查看本次报告」链接。失败时会保留 Job 和中文错误码，应先修正数据或范围，再创建新的分析请求，不要把失败记录当作报告。

### Canonical v1.0 最小结构

文件使用 UTF-8。JSON 可以在根对象中包含多个 `sessions`；JSONL 则每行放一个 `sessions` 数组里的会话对象，不要再包 `schema_version`、`source` 或 `sessions`，也不允许一个对象跨行。适配器会自动把这些行包装成 Canonical v1.0 根对象。HTTP 上传默认上限为 100 MB。

```json
{
  "schema_version": "1.0",
  "source": {
    "name": "合成教学数据",
    "license_note": "自制合成数据"
  },
  "sessions": [
    {
      "external_session_id": "session-001",
      "participant_key": "student-001",
      "task": {
        "external_task_id": "task-001",
        "title": "示例任务",
        "instructions": "完成任务并记录核验过程。"
      },
      "events": [
        {
          "external_event_id": "event-001",
          "sequence_no": 1,
          "occurred_at": "2026-11-24T09:00:00+08:00",
          "type": "chat_user_message",
          "payload": {"text": "我先写预期，再运行对照。"}
        },
        {
          "external_event_id": "event-002",
          "sequence_no": 2,
          "occurred_at": null,
          "type": "run_finished",
          "payload": {
            "artifact_id": "artifact-code",
            "artifact_version": 1,
            "artifact_checksum": "03e693d9f2f687e0f40e36a8df7fcb4d1c22974012b7c2a55c000eb30f305824",
            "execution_mode": "sandbox",
            "synthetic": false,
            "verified": true,
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": ""
          }
        },
        {
          "external_event_id": "event-003",
          "sequence_no": 3,
          "occurred_at": null,
          "type": "test_finished",
          "payload": {
            "artifact_id": "artifact-code",
            "artifact_version": 1,
            "artifact_checksum": "03e693d9f2f687e0f40e36a8df7fcb4d1c22974012b7c2a55c000eb30f305824",
            "execution_mode": "sandbox",
            "synthetic": false,
            "verified": true,
            "passed": 1,
            "failed": 0,
            "cases": [{"id": "prints-greeting", "passed": true}]
          }
        },
        {
          "external_event_id": "event-004",
          "sequence_no": 4,
          "occurred_at": null,
          "type": "submission_created",
          "payload": {
            "artifact_refs": ["artifact-code"],
            "statement": "记录了预期和实际结果。"
          }
        }
      ],
      "artifacts": [
        {
          "external_artifact_id": "artifact-code",
          "kind": "code",
          "version": 1,
          "content": "print('hello')\n",
          "mime_type": "text/x-python"
        }
      ]
    }
  ]
}
```

必须满足：

- `external_session_id`、`external_event_id` 和 `external_artifact_id` 在对应来源内稳定且唯一。
- `sequence_no` 从小到大表示事件顺序；`occurred_at` 可为 `null`，有时间时使用带时区的 ISO 8601。
- 常用事件类型包括 `chat_user_message`、`chat_ai_message`、`code_snapshot`、`run_finished`、`test_finished`、`artifact_saved`、`submission_created`。
- `artifact_refs` 必须引用同会话 `artifacts` 中存在的 ID。
- 如果声称一次运行或测试可用于正式成果评价，其 `artifact_id`、`artifact_version`、`artifact_checksum` 必须与最终提交版本对齐，并且 `verified: true`、`synthetic: false`。
- `run_finished` 还必须提供整数 `exit_code`、字符串 `stdout` 和 `stderr`；`test_finished` 必须提供非负的 `passed`、`failed` 和 `cases`。已核验的每个 case 都要有唯一字符串 `id` 和布尔值 `passed`，并与汇总数量一致。
- `execution_mode` 只能是 `sandbox`、`fixture_simulation` 或 `external_unknown`。`fixture_simulation` 必须同时标记 `synthetic: true`；`external_unknown` 不能标记为已核验。
- 预览中的错误会按记录号显示。不要为了通过导入而删除失败记录；可在确认页选择「仅提交有效记录」，并保留错误清单。

`artifact_checksum` 是成果 `content` 的原始 UTF-8 字节的 SHA-256 小写十六进制值。换行符也参与计算，不做 Unicode 或换行归一化。上例的内容以一个 LF 结尾，可用兼容 Windows PowerShell 5.1 的命令复算：

```powershell
$content = "print('hello')`n"
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($content)
    $hash = $sha.ComputeHash($bytes)
    ($hash | ForEach-Object { $_.ToString('x2') }) -join ''
}
finally {
    $sha.Dispose()
}
```

输出应为 `03e693d9f2f687e0f40e36a8df7fcb4d1c22974012b7c2a55c000eb30f305824`。导入器会再次按成果正文计算；若版本、ID 或 checksum 不对齐，该运行/测试会被降为未核验，而不能作为正式成果证据。

JSONL 的一行等价于上面 `sessions[0]` 的对象。以下 PowerShell 可从一个合法 JSON 包装文件生成合法 JSONL，避免手工删除外层结构：

```powershell
$box = Get-Content -LiteralPath '.\fixtures\new_import_input.json' -Raw | ConvertFrom-Json
$box.sessions | ForEach-Object { $_ | ConvertTo-Json -Depth 100 -Compress } |
    Set-Content -LiteralPath '.\var\new-import.jsonl' -Encoding utf8
```

完整的运行、测试、checksum 和多材料示例见下列 fixture。

可用的最小参考文件：

- [`fixtures/new_import_input.json`](fixtures/new_import_input.json)
- [`fixtures/F1_input.json`](fixtures/F1_input.json)

导入文件中应使用稳定的外部会话号和事件号。代码、运行与测试事件若要支持正式结果评价，必须对应同一个成果版本，并标明 `verified: true` 和 `synthetic: false`。

## StudyChat 数据

StudyChat 不是运行系统的必需条件。没有 StudyChat 快照时，仍然可以使用学生任务、演示集、通用 JSON / JSONL 导入和规则评价。

为防止误发布研究数据，以下文件默认被 `.gitignore` 排除：

- `data/StudyChat/`
- `data/studychat-v1.1.snapshot.jsonl.gz`
- `var/` 中的数据库、假名密钥、审计与浏览器配置

如果你对 StudyChat v1.1 有独立的合法访问权，可以设置：

```powershell
$env:AI_COLLAB_STUDYCHAT_ROOT='D:\data\StudyChat'
```

再使用系统的 StudyChat 本地快照导入入口。数据的获取、使用、保留和共享必须遵守来源授权与 CC BY 4.0 归属要求。

## 配置真实模型（可选）

规则评价、演示流程和学生离线 AI 不需要外部 Key。

如要对已授权的 StudyChat 会话使用 DeepSeek：

1. 以管理员身份进入「系统设置」。
2. 选择 `deepseek-flash`。
3. 保存 Key，填写用途和保留说明。
4. 确认供应商数据用途和保留策略。
5. 运行连接测试和配置自检。
6. 只对已经完成逐会话授权和本地质量检查的会话开始分析。

DeepSeek 使用固定的 `https://api.deepseek.com/chat/completions`。系统会自动允许 `api.deepseek.com`，不需要手工把它追加到 `AI_COLLAB_PROVIDER_HOSTS`。连接测试成功时，管理员页面会显示返回模型名和成功消息；配置自检还会检查公网 DNS、HTTPS 地址和部署 Key 状态。

也可以从服务端环境变量提供 Key：

```powershell
$env:DEEPSEEK_API_KEY='your-key-here'
```

不要把 Key 写入代码、导入文件、截图、报告或 Git 历史。外发前本地会阻止疑似密钥，并对常见联系方式和标识符脱敏。

## 常用环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `AI_COLLAB_MODE` | `demo` 开启本地演示登录 | `demo` |
| `AI_COLLAB_DB` | SQLite 数据库路径 | `var/app.sqlite3` |
| `AI_COLLAB_PSEUDONYM_KEY` | 稳定假名 HMAC 密钥；非 Demo 模式至少 32 字符 | Demo 自动生成本地密钥 |
| `DEEPSEEK_API_KEY` | DeepSeek 服务端 Key | 空 |
| `AI_COLLAB_LLM_KEY` | 其他 OpenAI 兼容提供器 Key | 空 |
| `AI_COLLAB_PROVIDER_HOSTS` | 外部模型域名白名单 | `api.openai.com` |
| `AI_COLLAB_STUDYCHAT_ROOT` | StudyChat 本地发布目录 | 自动查找 |
| `AI_COLLAB_WEB_ORIGIN` | 允许的本地前端来源 | `http://127.0.0.1:5173` |
| `AI_COLLAB_ALLOWED_HOSTS` | HTTP Host 白名单 | `127.0.0.1,localhost,testserver` |

`.env` 会在应用启动时自动读取，该文件已被 Git 忽略。

## 运行测试

### 后端

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens'
.\.venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider --basetemp var\pytest-readme
```

当前基线为 32 项后端测试通过。

### 前端构建

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens\frontend'
npm.cmd run build
```

### 关键浏览器用例

浏览器用例直接启动电脑上已安装的 Microsoft Edge；`npm ci` 会安装 Playwright 库，但不会为你安装 Windows Edge。

对已启动的桌面服务 `http://127.0.0.1:5188`：

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens\frontend'
npm.cmd run test:showcase
npm.cmd run test:studychat
npm.cmd run test:analysis
npm.cmd run test:desktop
```

- `test:desktop` 需要三个 EXE 已构建且 5188 服务就绪。
- `test:showcase`、`test:studychat` 和 `test:analysis` 验证本机 StudyChat 正式数据，干净公开克隆默认不具备它们的数据前置。
- 干净克隆的必跑浏览器基线是下面使用合成数据的 `test:e2e`。

通用 E2E 会创建和复核演示数据，应使用全新数据库。先在一个 PowerShell 窗口中启动独立实例：

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens'
.\scripts\start.ps1 -ApiPort 8016 -WebPort 5180 -Database var\e2e-readme.sqlite3
```

再在另一个窗口中：

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens\frontend'
$env:AI_COLLAB_TEST_URL='http://127.0.0.1:5180'
npm.cmd run test:e2e
```

当前通用 Edge 基线为 6 个场景通过，包含一条新 Canonical JSON 数据从导入到报告的完整流程。

测试结束后，回到启动 5180 的窗口按 `Ctrl+C` 停止 API、worker 和 Vite。请不要复用已跑过通用 E2E 的数据库作为下一次基线。桌面服务失败时查看 `var/desktop/service.log`，开发服务失败时查看当前 PowerShell 窗口的 API、worker 和 Vite 输出。

## 项目结构

```text
backend/
  app/                  FastAPI 路由、评价、worker、导入和安全策略
  migrations/           SQLite 版本化迁移
  task_packages/        学生任务包和本地材料
data/                   精选目录、可选 StudyChat 索引与快照
desktop/                三个 Windows 启动器的 C# 源码
fixtures/               合成验收输入和 Canonical JSON 示例
frontend/
  src/                  React 页面与 API 类型
  tests/                Playwright / Edge 浏览器验收
scripts/                安装、启动、构建、验收与 StudyChat 工具
tests/                  后端与评价引擎测试
var/                    运行数据、SQLite、密钥、日志和浏览器配置（不入 Git）
```

## 数据与安全边界

- SQLite 数据库、导入正文、报告证据、本地 Key 和浏览器配置都在 `var/`，不应提交到 Git。
- `.env`、StudyChat 原始数据和打包快照默认不入 Git。
- 结构化来源标识在导入时用 HMAC 生成假名。数据库和对应假名密钥必须一起备份。
- 外部模型只发送已授权范围内的白名单字段。StudyChat 还要求逐会话授权、质量门控和管理员当次确认。
- 本机外发前会阻止疑似 API Key、Token、密码和私钥。
- 归档代码只作为文本保存。静态演示运行不是安全执行环境，不能用于正式运行分。

## 已知限制

- 当前主要是本地演示与研究原型，没有学校 SSO 或生产身份接入。
- 没有通用的隔离代码执行服务。
- 学生 AI 对话默认使用离线演示提供器。
- 来源保留期目前作为元数据记录，未包含自动到期清理调度器。
- 非 Demo 模式没有附带完整的机构身份系统，不应直接暴露在公网。
- 真实研究使用前仍需要学校伦理、数据授权、保留周期和人工金标准方案。

## 常见问题

### 双击 EXE 后无法启动

1. 先运行 `.\教师登录.exe --check`。
2. 检查 `var/desktop/service.log` 和 `var/desktop/launcher.log`。
3. 确认 `.venv/Scripts/python.exe` 和 `frontend/dist/index.html` 存在。
4. 确认 5188 端口没有被其他项目占用。

### 新修改的前端没有出现

重新运行：

```powershell
.\scripts\build-desktop.ps1
.\教师登录.exe --stop
.\教师登录.exe --check
```

### StudyChat 精选库为空

新克隆的开源代码库默认不包含 StudyChat 快照和本地报告数据。请使用通用 JSON 示例验证系统，或在获得授权后配置 `AI_COLLAB_STUDYCHAT_ROOT`并导入快照。

### DeepSeek 不可用

确认管理员界面中的模型、Key、用途与保留策略均已保存，再运行「连接测试」和「配置自检」。StudyChat 还需要现场入口开放且会话已获得逐条授权。

### 如何备份或重置

先停止服务。备份时必须同时保留 `AI_COLLAB_DB` 指向的 SQLite 数据库和同名 `.pseudonym-key` 文件，否则恢复后的假名不能保持稳定。管理员界面的「重置 Demo 数据源」只删除明确标记为 Demo 的来源、会话、报告和导出，不应用来清理真实研究数据。在删除整个项目目录前，请先确认备份和保留要求。

## 准备建立公开仓库

可以将本项目整理为公开仓库，但在发布前必须完成以下检查。

### 必做项

- [x] 根目录已添加 MIT `LICENSE`。
- [ ] 确认所有代码贡献者同意所选许可证。
- [ ] 保留三个 StudyChat 任务包中的原作者、课程和 CC BY 4.0 归属。
- [ ] 不提交 `.env`、`var/`、SQLite 文件、DPAPI 凭据、浏览器配置、StudyChat 原始数据、快照或来源索引。
- [ ] 在新的空目录中重新 clone，只使用合成 fixtures 完成安装、构建和 6 个 E2E 场景。
- [ ] 如发布预编译 EXE，放在 Release 附件中，不要把本机构建物当作源码提交。
- [x] 已补充 `SECURITY.md`、`CONTRIBUTING.md` 和公开版研究数据边界。

预检只检查 Git 已跟踪或已暂存的文件，因此顺序不能颠倒。先在一个不含真实数据的新目录中复制允许公开的源码，添加许可证并暂存，再运行预检和人工复核，最后才提交：

```powershell
Set-Location -LiteralPath 'D:\path\to\CoSolve-Lens'
git init
git add README.md LICENSE .gitignore .env.example pytest.ini CONTRIBUTING.md SECURITY.md backend data desktop docs fixtures frontend scripts tests .github
.\scripts\preflight-open-source.ps1
git status --short
git diff --cached --name-only
# 人工确认清单没有真实学生数据、密钥、数据库、EXE 或浏览器配置后再提交
git commit -m "Initial open-source release"
```

脚本会检查根 `LICENSE`、Git 已跟踪文件中的 `.env`、`var/`、SQLite、EXE、StudyChat 快照，并只输出疑似秘密所在的文件名、不输出匹配正文。它不能代替对已有 Git 历史的专业秘密和大文件扫描。

如果现有工作目录曾经存在真实学生数据或密钥，建议新建一个空 Git 项目，按允许清单复制源码和合成 fixtures，不要直接携带旧 Git 历史。

### 建议的首次发布内容

- 应用源码、数据库迁移和任务包。
- 合成 fixtures 和 Canonical JSON 示例。
- `.env.example`、本 README 和完整测试。
- 不含任何真实学生数据的截图或演示视频。
- 一个独立的合成数据 Release，便于试用者直接验证导入。

## 许可证与第三方内容

CoSolve Lens 的软件代码采用 [MIT License](LICENSE)。MIT License 允许使用、复制、修改和分发代码，但必须保留版权与许可证声明，软件按“原样”提供且不附带担保。

StudyChat 及三个离线课堂任务包保留各自 README 中的 CC BY 4.0 归属。这些数据与课程材料的许可与未来代码许可证分开管理。

## 更多文档

- [案例经验](docs/CASE_STUDIES.md)：八个去标识化案例的五维结果和可迁移经验。
- [DECISIONS.md](DECISIONS.md)：量规与实现决策。
- [DEVELOPMENT.md](DEVELOPMENT.md)：开发交付清单。
- [CONTRIBUTING.md](CONTRIBUTING.md)：贡献流程和合成数据要求。
- [SECURITY.md](SECURITY.md)：安全边界与私下报告方式。
