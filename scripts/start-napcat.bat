@echo off
REM 用已安装的 QQ 启动 NapCat（不依赖注册表）。面板 http://localhost:6099
REM 用法：scripts\start-napcat.bat [QQ.exe 路径] [小号QQ号，可选，登录过一次后可免扫码]
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "QQPath=%~1"
if "%QQPath%"=="" set "QQPath=D:\software\QQ\QQ.exe"
cd /d "%~dp0..\runtime\napcat-shell" || exit /b 1
set "NAPCAT_PATCH_PACKAGE=%cd%\qqnt.json"
set "NAPCAT_LOAD_PATH=%cd%\loadNapCat.js"
set "NAPCAT_INJECT_PATH=%cd%\NapCatWinBootHook.dll"
set "NAPCAT_LAUNCHER_PATH=%cd%\NapCatWinBootMain.exe"
set "NAPCAT_MAIN_PATH=%cd%\napcat.mjs"
if not exist "%QQPath%" (
    echo 找不到 QQ：%QQPath%
    exit /b 1
)
if "%~2"=="" (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%"
) else (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%" -q %~2
)
