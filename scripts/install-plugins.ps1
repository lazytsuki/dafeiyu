# 按 plugins.lock.json 安装社区插件。需要已安装 git。
# 用法：.\scripts\install-plugins.ps1 -AstrBotRoot D:\AstrBot
param(
    [Parameter(Mandatory = $true)][string]$AstrBotRoot
)
$ErrorActionPreference = 'Stop'
$lock = Get-Content (Join-Path $PSScriptRoot '..\plugins.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$pluginsDir = Join-Path $AstrBotRoot 'data\plugins'
foreach ($p in $lock.plugins) {
    $dst = Join-Path $pluginsDir $p.name
    if (Test-Path $dst) {
        Write-Host "更新 $($p.name)"
        git -C $dst fetch --depth 1 origin $p.ref
        git -C $dst checkout FETCH_HEAD
    } else {
        Write-Host "安装 $($p.name)"
        git clone --depth 1 --branch $p.ref $p.repo $dst
    }
}
Write-Host '完成。回到 AstrBot 面板重启或重载插件，插件依赖会自动安装。'
