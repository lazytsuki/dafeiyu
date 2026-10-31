@echo off
REM Save the SMTP auth code used to mail you the NapCat login QR code (scripts\qr-push.ps1) to
REM runtime\secrets.bat. Run this yourself; input is hidden. secrets.bat is not in git and not in the backup.
REM QQ Mail: mail.qq.com > Settings > Account > enable POP3/SMTP (or IMAP/SMTP) > generate an auth code.
REM Also put set "QR_PUSH_TO=you@example.com" in runtime\local.bat. To stop mailing, remove that line.
setlocal
set "OUT=%~dp0..\runtime\secrets.bat"
powershell -NoProfile -Command "$s = Read-Host -AsSecureString 'SMTP auth code (input hidden)'; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { $p = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }; $p = ([string]$p).Trim(); if (-not $p) { Write-Host 'Empty input, nothing saved.'; exit 1 }; if ($p -match '[\x22\r\n]') { Write-Host 'Unexpected characters, nothing saved.'; exit 1 }; $tag = 'REM SMTP auth code for the login QR push (set-push-secret.bat)'; $lines = @(); if (Test-Path $env:OUT) { $lines = @(Get-Content $env:OUT | Where-Object { $_ -ne $tag -and $_ -notmatch '^\s*set\s+\x22?QR_PUSH_SMTP_PASSWORD=' }) }; if (-not $lines) { $lines = @('@echo off', 'REM Private: not in git, not backed up.') }; $q = [char]34; $lines += $tag; $lines += ('set ' + $q + 'QR_PUSH_SMTP_PASSWORD=' + $p + $q); $p = $null; Set-Content -Path $env:OUT -Encoding ASCII -Value $lines; Write-Host 'Saved to runtime\secrets.bat. It takes effect the next time NapCat starts.'"
findstr /r /c:"QR_PUSH_TO=." "%~dp0..\runtime\local.bat" >nul 2>&1 || echo Reminder: add  set "QR_PUSH_TO=you@example.com"  to runtime\local.bat
pause
