<#
  生成可发布的预览版压缩包：
    dist/TritonAIOS-Preview-0.1.0.zip
  自动排除虚拟环境、私密配置、缓存、日志与本地备份。
#>
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "TritonAIOS-Preview-0.1.0"
$zip = Join-Path $dist "TritonAIOS-Preview-0.1.0.zip"

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$excludeDirs = @(".venv", "__pycache__", "dist", ".ui-backups", ".git")
$excludeFiles = @("config.local.json", "uvicorn.log", "uvicorn.err.log", "*.pyc")

function Copy-Entry($src, $dst) {
  $item = Get-Item -LiteralPath $src -Force
  if ($item.PSIsContainer) {
    if ($excludeDirs -contains $item.Name) { return }
    $destDir = Join-Path $dst $item.Name
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    Get-ChildItem -LiteralPath $item.FullName -Force | ForEach-Object { Copy-Entry $_.FullName $destDir }
  } else {
    foreach ($pattern in $excludeFiles) { if ($item.Name -like $pattern) { return } }
    Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $dst $item.Name) -Force
  }
}

Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -ne "dist" } | ForEach-Object {
  Copy-Entry $_.FullName $stage
}

# 首次启动从空对话开始（示例对话由前端内置）
$convDir = Join-Path $stage "data"
New-Item -ItemType Directory -Force -Path $convDir | Out-Null
Set-Content -LiteralPath (Join-Path $convDir "conversations.json") -Value "[]" -Encoding UTF8 -NoNewline

# 安全检查：打包内容里不能有 API Key / 私密地址
$leaks = Get-ChildItem -LiteralPath $stage -Recurse -File |
  Select-String -Pattern 'sk-[A-Za-z0-9]{8,}', '192\.168\.3\.188', '211\.101\.244\.216' -ErrorAction SilentlyContinue
if ($leaks) {
  $leaks | ForEach-Object { Write-Host ("LEAK " + $_.Path + ":" + $_.LineNumber) -ForegroundColor Red }
  throw "打包中止：发现疑似敏感内容"
}

Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 2)
Write-Host ("已生成 " + $zip + " (" + $size + " MB)") -ForegroundColor Green
