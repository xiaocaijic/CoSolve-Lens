$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $root 'data\collaboration-app.sqlite3.gz'
$hashFile = "$archive.sha256"
$sourceKey = Join-Path $root 'data\collaboration-app.pseudonym-key'
$var = Join-Path $root 'var'
$database = Join-Path $var 'app.sqlite3'
$targetKey = Join-Path $var 'app.pseudonym-key'

if (-not (Test-Path -LiteralPath $archive)) { return }

New-Item -ItemType Directory -Path $var -Force | Out-Null
if (-not (Test-Path -LiteralPath $database)) {
    if (Test-Path -LiteralPath $hashFile) {
        $expected = (Get-Content -LiteralPath $hashFile -Raw).Trim().Split()[0].ToLowerInvariant()
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw '协作数据库压缩包校验失败。' }
    }
    $pending = "$database.restore.tmp"
    if (Test-Path -LiteralPath $pending) { throw "存在未完成的恢复文件：$pending" }
    $input = [IO.File]::OpenRead($archive)
    $gzip = [IO.Compression.GZipStream]::new($input, [IO.Compression.CompressionMode]::Decompress)
    $output = [IO.File]::Create($pending)
    try { $gzip.CopyTo($output) }
    finally { $output.Dispose(); $gzip.Dispose(); $input.Dispose() }
    Move-Item -LiteralPath $pending -Destination $database
    Write-Host "已恢复协作数据库：$database"
}

if (-not (Test-Path -LiteralPath $targetKey) -and (Test-Path -LiteralPath $sourceKey)) {
    Copy-Item -LiteralPath $sourceKey -Destination $targetKey
    Write-Host "已恢复假名映射文件：$targetKey"
}
