@echo off
REM Stop AstrBot and the QQ that NapCat launched for the bot account. Your own QQ is not touched.
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\runtime\local.bat" call "%ROOT%\runtime\local.bat"
if "%BOT_QQ%"=="" (
    echo Please set BOT_QQ in runtime\local.bat
    exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'astrbot\.cli run') -or ($_.Name -eq 'QQ.exe' -and $_.CommandLine -match '-q %BOT_QQ%') -or $_.Name -eq 'NapCatWinBootMain.exe' } | ForEach-Object { taskkill /t /f /pid $_.ProcessId 2>$null | Out-Null }"
echo stopped
