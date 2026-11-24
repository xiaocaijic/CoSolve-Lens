$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

Push-Location -LiteralPath (Join-Path $root 'frontend')
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '无法找到 Windows .NET Framework C# 编译器。' }

$source = Join-Path $root 'desktop\Launcher.cs'
$launchers = [ordered]@{ TEACHER = '教师登录.exe'; STUDENT = '学生登录.exe'; ADMIN = '管理员登录.exe' }
foreach ($role in $launchers.Keys) {
    $target = Join-Path $root $launchers[$role]
    & $compiler '/nologo' '/codepage:65001' '/target:winexe' '/optimize+' '/reference:System.Windows.Forms.dll' "/define:$role" "/out:$target" $source
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host $target
}
