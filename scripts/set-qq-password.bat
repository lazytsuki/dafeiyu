@echo off
REM Save the bot QQ password as an MD5 hash to runtime\secrets.bat, so NapCat can fall back to
REM password login when quick login fails (e.g. after a reboot). Run this yourself; input is hidden.
REM The plain password is never written to disk. secrets.bat is not in git and not in the backup.
REM To turn the fallback off, delete runtime\secrets.bat.
setlocal
set "OUT=%~dp0..\runtime\secrets.bat"
powershell -NoProfile -Command "$s = Read-Host -AsSecureString 'Bot QQ password (input hidden)'; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { $p = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }; if (-not $p) { Write-Host 'Empty input, nothing saved.'; exit 1 }; $md5 = -join ([Security.Cryptography.MD5]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($p)) | ForEach-Object { $_.ToString('x2') }); $p = $null; Set-Content -Path $env:OUT -Encoding ASCII -Value @('@echo off', 'REM Bot QQ password MD5 for NapCat fallback login. Private: not in git, not backed up.', ('set NAPCAT_QUICK_PASSWORD_MD5=' + $md5)); Write-Host 'Saved to runtime\secrets.bat. It takes effect the next time NapCat starts.'"
pause
