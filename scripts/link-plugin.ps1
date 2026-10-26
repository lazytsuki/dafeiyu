# 把本仓库的大肥鱼插件链接到 AstrBot 的插件目录，改代码后在 AstrBot 面板里「重载插件」即可生效。
# 用法：.\scripts\link-plugin.ps1 -AstrBotRoot D:\AstrBot
param(
    [Parameter(Mandatory = $true)][string]$AstrBotRoot
)
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot '..\plugins\astrbot_plugin_dafeiyu' | Resolve-Path
$pluginsDir = Join-Path $AstrBotRoot 'data\plugins'
if (-not (Test-Path $pluginsDir)) { throw "找不到 $pluginsDir，确认 AstrBot 已经启动过一次" }
$dst = Join-Path $pluginsDir 'astrbot_plugin_dafeiyu'
if (Test-Path $dst) { throw "$dst 已存在，先手动移走再运行" }
# 目录联接不需要管理员权限
New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
Write-Host "已链接：$dst -> $src"
