@echo off
REM One-click start: AstrBot first, then NapCat, both minimized. Skips whatever is already running.
REM Machine-specific settings live in runtime\local.bat (not in git):
REM   set "QQ_PATH=D:\path\to\QQ.exe"
REM   set "BOT_QQ=your bot QQ number"
setlocal
chcp 65001 >nul
set "ROOT=%~dp0.."
if exist "%ROOT%\runtime\local.bat" call "%ROOT%\runtime\local.bat"
if "%QQ_PATH%"=="" (
    echo Please set QQ_PATH and BOT_QQ in runtime\local.bat
    exit /b 1
)
set "PY=%APPDATA%\uv\tools\astrbot\Scripts\python.exe"
if not exist "%PY%" (
    echo AstrBot not found: %PY%
    exit /b 1
)

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'astrbot\.cli run' }) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    start "dafeiyu-astrbot" /min /d "%ROOT%\runtime\astrbot" "%PY%" -m astrbot.cli run
    timeout /t 15 /nobreak >nul
)

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'QQ.exe' -and $_.CommandLine -match '-q %BOT_QQ%' }) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    start "dafeiyu-napcat" /min cmd /c ""%~dp0start-napcat.bat" "%QQ_PATH%" %BOT_QQ%"
)
