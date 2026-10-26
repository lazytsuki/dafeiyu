# 用 uv 安装 AstrBot（uv 会自带 Python），并在 runtime\astrbot 初始化数据目录、链接大肥鱼插件。
# 前置：winget install astral-sh.uv
# 用法：.\scripts\install-astrbot.ps1 [-Root C:\dafeiyu\runtime\astrbot]
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\runtime\astrbot')
)
$ErrorActionPreference = 'Stop'
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe' }
if (-not (Test-Path $uv)) { throw '找不到 uv，先运行：winget install astral-sh.uv' }

& $uv tool install astrbot --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'AstrBot 安装失败' }

New-Item -ItemType Directory -Force $Root | Out-Null
$Root = (Resolve-Path $Root).Path
$py = Join-Path (& $uv tool dir) 'astrbot\Scripts\python.exe'
Push-Location $Root
try { & $py -m astrbot.cli init -y } finally { Pop-Location }

$dst = Join-Path $Root 'data\plugins\astrbot_plugin_dafeiyu'
if (-not (Test-Path $dst)) { & (Join-Path $PSScriptRoot 'link-plugin.ps1') -AstrBotRoot $Root }
Write-Host "装好了。启动：.\scripts\start-astrbot.ps1"
