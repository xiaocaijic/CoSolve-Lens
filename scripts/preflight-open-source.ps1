$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

if (-not (Test-Path -LiteralPath 'LICENSE')) {
    $failures.Add('缺少根目录 LICENSE；当前不能声明为已完成开源授权。')
}

$insideGit = (& git rev-parse --is-inside-work-tree 2>$null) -eq 'true'
if (-not $insideGit) {
    $warnings.Add('当前目录不是 Git 工作树；无法检查 tracked files 和 Git 历史。请在新仓库中再次运行。')
} else {
    $tracked = @(& git ls-files)
    $forbidden = @(
        '(^|/)\.env$', '(^|/)var/', '\.(?:sqlite|sqlite3|db)(?:-|$|\.)', '\.exe$',
        '^data/StudyChat/', '^data/studychat-v1\.1\.snapshot', '^data/studychat-v1\.1\.provenance\.json$',
        'browser-(?:teacher|student|admin)', '(?:^|/)service\.log$'
    )
    foreach ($path in $tracked) {
        if ($path -eq '.env.example') { continue }
        if ($forbidden | Where-Object { $path -match $_ }) {
            $failures.Add("禁止公开的文件已被 Git 跟踪：$path")
        }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $size = (Get-Item -LiteralPath $path).Length
            if ($size -gt 50MB) { $warnings.Add("超过 50 MB 的 tracked file：$path") }
        }
    }

    $secretPattern = 'sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_ -]?key|token|secret|password)[ :=]+[A-Za-z0-9_./+\-]{12,}'
    $secretFiles = @(& git grep -I -l -E $secretPattern -- . 2>$null)
    foreach ($path in $secretFiles) {
        if ($path -in @('README.md', '.env.example', 'backend/app/providers.py')) {
            $warnings.Add("示例或检测规则命中秘密扫描，请人工核对：$path")
        } else {
            $failures.Add("tracked file 命中疑似秘密扫描，请人工核对且不要输出正文：$path")
        }
    }
}

foreach ($item in $warnings) { Write-Warning $item }
if ($failures.Count) {
    foreach ($item in $failures) { Write-Error $item -ErrorAction Continue }
    Write-Host "Open-source preflight FAILED ($($failures.Count) blocking issue(s))."
    exit 1
}

Write-Host 'Open-source preflight passed for the current tracked file set.'
exit 0
