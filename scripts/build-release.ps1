param(
    [string]$Version = '0.1.0',
    [string]$OutputDirectory = 'var/release'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$output = [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$bundleName = "CoSolve-Lens-v$Version-windows-x64"
$bundle = Join-Path $output $bundleName
$archive = Join-Path $output "$bundleName.zip"

if ((Test-Path -LiteralPath $bundle) -or (Test-Path -LiteralPath $archive)) {
    throw "Release output already exists: $bundleName"
}

foreach ($required in @('教师登录.exe','学生登录.exe','管理员登录.exe','frontend/dist/index.html')) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing release input: $required. Run scripts/build-desktop.ps1 first."
    }
}

New-Item -ItemType Directory -Path $bundle -Force | Out-Null

foreach ($name in @('README.md','LICENSE','.env.example','教师登录.exe','学生登录.exe','管理员登录.exe')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $bundle $name)
}

Copy-Item -LiteralPath 'desktop/Install-CoSolve-Lens.cmd' -Destination $bundle
Copy-Item -LiteralPath 'desktop/README-WINDOWS.txt' -Destination $bundle

New-Item -ItemType Directory -Path (Join-Path $bundle 'backend') | Out-Null
foreach ($name in @('app','migrations','task_packages','requirements.txt')) {
    Copy-Item -LiteralPath (Join-Path $root "backend/$name") -Destination (Join-Path $bundle 'backend') -Recurse
}

New-Item -ItemType Directory -Path (Join-Path $bundle 'frontend') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'frontend/dist') -Destination (Join-Path $bundle 'frontend') -Recurse

New-Item -ItemType Directory -Path (Join-Path $bundle 'fixtures') | Out-Null
Get-ChildItem -LiteralPath (Join-Path $root 'fixtures') | Copy-Item -Destination (Join-Path $bundle 'fixtures') -Recurse

$data = New-Item -ItemType Directory -Path (Join-Path $bundle 'data')
[IO.File]::WriteAllText((Join-Path $data 'studychat-showcase-v1.json'), "[]`n", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $data 'studychat-pilot-guidance.json'), "{}`n", [Text.UTF8Encoding]::new($false))

$bundlePrefix = $bundle.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($cache in Get-ChildItem -LiteralPath $bundle -Recurse -Directory -Filter '__pycache__') {
    $resolved = $cache.FullName
    if (-not $resolved.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a cache outside the release bundle: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Compress-Archive -LiteralPath $bundle -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$archive.sha256", "$hash  $bundleName.zip`n", [Text.UTF8Encoding]::new($false))

Write-Host $archive
Write-Host "SHA256 $hash"
