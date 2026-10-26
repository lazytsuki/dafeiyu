@echo off
REM Back up the pet's private data to the private git repo set in runtime\local.bat (BACKUP_REMOTE).
setlocal
chcp 65001 >nul
set "PY=%APPDATA%\uv\tools\astrbot\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
"%PY%" "%~dp0backup.py" >> "%~dp0..\runtime\backup.log" 2>&1
