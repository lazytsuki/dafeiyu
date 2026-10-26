# 启动 AstrBot，面板在 http://localhost:6185
# 不用 astrbot.exe：uv 的启动壳在部分环境下会报 "failed to canonicalize script path"，直接用它的 Python 跑。
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\runtime\astrbot')
)
$ErrorActionPreference = 'Stop'
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe' }
$py = Join-Path (& $uv tool dir) 'astrbot\Scripts\python.exe'
Set-Location (Resolve-Path $Root)
& $py -m astrbot.cli run
