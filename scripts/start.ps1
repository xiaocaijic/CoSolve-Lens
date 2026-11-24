param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [string]$Database
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = if (Test-Path -LiteralPath '.venv/Scripts/python.exe') {
    (Resolve-Path -LiteralPath '.venv/Scripts/python.exe').Path
} else { 'python' }
if ($PSBoundParameters.ContainsKey('Database')) {
    $env:AI_COLLAB_DB = $Database
}
$env:AI_COLLAB_API_PROXY = "http://127.0.0.1:$ApiPort"
$env:AI_COLLAB_WEB_ORIGIN = "http://127.0.0.1:$WebPort"
& $python -m backend.app migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$api = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',$ApiPort) -PassThru -WindowStyle Hidden
$worker = $null
try {
    $worker = Start-Process -FilePath $python -ArgumentList @('-m','backend.app','worker') -PassThru -WindowStyle Hidden
    Write-Host "应用 http://127.0.0.1:$WebPort"
    Push-Location -LiteralPath (Join-Path $root 'frontend')
    try { npm.cmd run dev -- --port $WebPort } finally { Pop-Location }
} finally {
    foreach ($process in @($api, $worker)) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id }
    }
}
