param([string]$Python = 'python', [switch]$BuildFrontend)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& (Join-Path $PSScriptRoot 'restore-collaboration-state.ps1')
New-Item -ItemType Directory -Force -Path 'var/tmp' | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = (Resolve-Path -LiteralPath 'var/tmp').Path
    $env:TMP = $env:TEMP
    if (-not (Test-Path -LiteralPath '.venv/Scripts/pip.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & '.\.venv\Scripts\python.exe' -m pip install -r backend/requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($BuildFrontend -or -not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
        Push-Location -LiteralPath 'frontend'
        try {
            npm.cmd ci --no-audit --no-fund --cache ..\var\npm-cache
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally { Pop-Location }
    }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
