# NapCat 掉线要扫码时，把登录二维码推到手机（邮件附图，可选 webhook）。
# start-napcat.bat 会在后台顺手启动它；没配置就立刻退出，什么都不做。
# 配置（都不进 git）：
#   runtime\local.bat   set "QR_PUSH_TO=收件邮箱"            必填，填了才会推邮件
#                       set "QR_PUSH_FROM=发件邮箱"          可选，默认同收件
#                       set "QR_PUSH_SMTP_HOST=smtp.qq.com"  可选，默认按发件邮箱猜（qq/foxmail/163/126/gmail/outlook）
#                       set "QR_PUSH_SMTP_PORT=465"          可选，默认 465（SSL，借 AstrBot 的 Python 发）；587 走 STARTTLS
#                       set "QR_PUSH_WEBHOOK=https://..."    可选，Server酱/PushPlus 等，POST JSON（标题+Markdown 正文，内嵌二维码）
#   runtime\secrets.bat set "QR_PUSH_SMTP_PASSWORD=授权码"   用 scripts\set-push-secret.bat 写，不进备份
# 行为：二维码文件出现/变化就推（同一张只推一次；过期刷新后再推，间隔 2、4、8…最多 30 分钟拉长，免得刷屏），
#       登录成功或超时（默认 120 分钟）后退出。日志：runtime\qr-push.log
# 自测：-WhatIf 只打印要发什么，不真的发。
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Root,
    [string]$LocalBat,
    [string]$SecretsBat,
    [string]$QrPath,
    [string]$LogDir,
    [string]$LogFile,
    [double]$TimeoutMinutes = 120,
    [int]$PollSeconds = 3,
    [double]$RetryMinutes = 2,   # 第一次重推前至少等这么久，之后每次翻倍，最多 30 分钟
    [int]$WsPort = 6199
)
$ErrorActionPreference = 'Stop'
# -WhatIf = 干跑：照常监视、记日志，只是不真的发；内部的复制/写日志不受它影响
$DryRun = [bool]$WhatIfPreference
$WhatIfPreference = $false
if (-not $Root) { $Root = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..' }
$Root = [IO.Path]::GetFullPath($Root)
if (-not $LocalBat) { $LocalBat = Join-Path $Root 'runtime\local.bat' }
if (-not $SecretsBat) { $SecretsBat = Join-Path $Root 'runtime\secrets.bat' }
if (-not $QrPath) { $QrPath = Join-Path $Root 'runtime\napcat-shell\cache\qrcode.png' }
if (-not $LogDir) { $LogDir = Join-Path $Root 'runtime\napcat-shell\logs' }
if (-not $LogFile) { $LogFile = Join-Path $Root 'runtime\qr-push.log' }

function Write-Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    if ($DryRun) { Write-Host $line } else { Write-Verbose $line }
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Read-BatVars([string]$path) {
    # 认 set "NAME=value" 和 set NAME=value 两种写法
    $vars = @{}
    if (Test-Path -LiteralPath $path) {
        foreach ($l in Get-Content -LiteralPath $path -Encoding UTF8) {
            if ($l -match '^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$') { $vars[$Matches[1]] = $Matches[2] }
        }
    }
    $vars
}

$cfg = Read-BatVars $LocalBat
foreach ($kv in (Read-BatVars $SecretsBat).GetEnumerator()) { $cfg[$kv.Key] = $kv.Value }
function Get-Cfg([string]$name, [string]$default = '') {
    $v = $cfg[$name]
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($name) }
    if ($v) { $v.Trim() } else { $default }
}

$to = Get-Cfg 'QR_PUSH_TO'
$webhook = Get-Cfg 'QR_PUSH_WEBHOOK'
$pass = Get-Cfg 'QR_PUSH_SMTP_PASSWORD'
$mailOn = [bool]($to -and $pass)
if (-not $mailOn -and -not $webhook) { exit 0 }   # 没配置：安静退出，不打扰启动
if ($to -and -not $pass) { Write-Log '配了 QR_PUSH_TO 但没有授权码（运行 scripts\set-push-secret.bat），本次不发邮件' }

$from = Get-Cfg 'QR_PUSH_FROM' $to
$user = Get-Cfg 'QR_PUSH_SMTP_USER' $from
$domain = if ($from -match '@(.+)$') { $Matches[1].ToLower() } else { '' }
$guessHost = switch -Regex ($domain) {
    '^(qq|foxmail|vip\.qq)\.com$' { 'smtp.qq.com'; break }
    '^gmail\.com$' { 'smtp.gmail.com'; break }
    '^(outlook|hotmail|live)\.com$' { 'smtp.office365.com'; break }
    default { if ($domain) { "smtp.$domain" } else { '' } }
}
$smtpHost = Get-Cfg 'QR_PUSH_SMTP_HOST' $guessHost
$smtpPort = [int](Get-Cfg 'QR_PUSH_SMTP_PORT' $(if ($smtpHost -eq 'smtp.office365.com') { '587' } else { '465' }))
$py = Join-Path $env:APPDATA 'uv\tools\astrbot\Scripts\python.exe'

# 同一时间只跑一个，免得重复推
$mutex = New-Object Threading.Mutex($false, 'Local\dafeiyu-qr-push')
try { if (-not $mutex.WaitOne(0)) { exit 0 } } catch [Threading.AbandonedMutexException] {}

function Send-Mail([string]$png, [string]$stamp) {
    $subject = '大肥鱼掉线了，需要扫码登录'
    $body = "NapCat 需要扫码登录（$stamp 生成）。`r`n`r`n" +
        "1. 长按/保存附件里的二维码图片`r`n" +
        "2. 手机 QQ（机器人的小号）→ 扫一扫 → 右上角「相册」→ 选这张图`r`n" +
        "3. 在手机上确认登录`r`n`r`n" +
        "二维码几分钟就会过期；过期后会自动再推新的，间隔逐渐拉长。登录成功后不再推送。"
    $name = "qrcode-$($stamp -replace ':', '').png"
    if ($DryRun) { Write-Log "[干跑] 会发邮件到 $to（经 ${smtpHost}:$smtpPort，$(if ($smtpPort -eq 465) { 'Python SSL' } else { 'STARTTLS' })，附件 $name）"; return }
    if ($smtpPort -eq 465) {
        # .NET 的 SmtpClient 不支持 465 的隐式 SSL，借 AstrBot 自带的 Python 发
        if (-not (Test-Path -LiteralPath $py)) { throw "465 端口需要 Python：$py 不存在；可改用 587（QR_PUSH_SMTP_PORT=587）" }
        $code = @'
import os, smtplib, ssl
from email.message import EmailMessage
e = os.environ
m = EmailMessage()
m["Subject"], m["From"], m["To"] = e["QRP_SUBJECT"], e["QRP_FROM"], e["QRP_TO"]
m.set_content(e["QRP_BODY"])
with open(e["QRP_PNG"], "rb") as f:
    m.add_attachment(f.read(), maintype="image", subtype="png", filename=e["QRP_NAME"])
with smtplib.SMTP_SSL(e["QRP_HOST"], int(e["QRP_PORT"]), context=ssl.create_default_context(), timeout=30) as s:
    s.login(e["QRP_USER"], e["QRP_PASS"])
    s.send_message(m)
'@
        $envs = @{ QRP_SUBJECT = $subject; QRP_FROM = $from; QRP_TO = $to; QRP_BODY = $body; QRP_PNG = $png; QRP_NAME = $name
            QRP_HOST = $smtpHost; QRP_PORT = "$smtpPort"; QRP_USER = $user; QRP_PASS = $pass; PYTHONIOENCODING = 'utf-8' }
        foreach ($k in $envs.Keys) { [Environment]::SetEnvironmentVariable($k, $envs[$k], 'Process') }
        try {
            $out = & $py -I -c $code 2>&1
            if ($LASTEXITCODE -ne 0) { throw "Python 发信失败：$(($out | Select-Object -Last 1))" }
        } finally {
            foreach ($k in $envs.Keys) { [Environment]::SetEnvironmentVariable($k, $null, 'Process') }
        }
    } else {
        $msg = New-Object Net.Mail.MailMessage($from, $to, $subject, $body)
        $msg.SubjectEncoding = $msg.BodyEncoding = [Text.Encoding]::UTF8
        $att = New-Object Net.Mail.Attachment($png, 'image/png')
        $att.Name = $name
        $msg.Attachments.Add($att)
        $smtp = New-Object Net.Mail.SmtpClient($smtpHost, $smtpPort)
        $smtp.EnableSsl = $true
        $smtp.Credentials = New-Object Net.NetworkCredential($user, $pass)
        try { $smtp.Send($msg) } finally { $msg.Dispose(); $smtp.Dispose() }
    }
    Write-Log "已发邮件到 $to"
}

function Send-Webhook([string]$png, [string]$stamp) {
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($png))
    $title = '大肥鱼掉线了，需要扫码登录'
    $md = "NapCat 需要扫码登录（$stamp 生成）。保存下面的图，用小号手机 QQ「扫一扫 → 相册」选它，再在手机上确认。`n`n" +
        "![二维码](data:image/png;base64,$b64)`n`n二维码几分钟就会过期，过期后会再推新的。"
    $payload = [ordered]@{ title = $title; desp = $md; content = $md; text = $md; template = 'markdown' }
    if ($webhook -match '[?&]token=([^&]+)') { $payload['token'] = [Uri]::UnescapeDataString($Matches[1]) }  # PushPlus
    $shown = $webhook -replace '^(https?://[^/]+).*$', '$1/...'   # 不把 key 打进日志
    if ($DryRun) { Write-Log "[干跑] 会推 webhook $shown（字段 $(($payload.Keys) -join ',')，正文含 $($b64.Length) 字符 base64 图）"; return }
    $json = $payload | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 30 | Out-Null
    Write-Log "已推送 webhook $shown"
}

$start = Get-Date
function Test-LoggedIn {
    # 1) NapCat 登录后才初始化 OneBot 适配器，会写进本次启动的文件日志（napcat_<QQ>.json 里开了 fileLog 时）
    if (Test-Path -LiteralPath $LogDir) {
        $fresh = Get-ChildItem -LiteralPath $LogDir -Filter '*.log' -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $start }
        foreach ($f in $fresh) {
            if (Select-String -LiteralPath $f.FullName -Encoding UTF8 -SimpleMatch -Quiet `
                    -Pattern '协议适配器初始化完成', 'OneBot11 适配器初始化完成') { return $true }
        }
    }
    # 2) 本次启动的 QQ 已经连上 AstrBot 的反向 WebSocket
    $conns = Get-NetTCPConnection -RemotePort $WsPort -State Established -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -eq 'QQ' -and $p.StartTime -ge $start.AddSeconds(-30)) { return $true }
    }
    $false
}

Write-Log "开始监视二维码（邮件：$(if ($mailOn) { $to } else { '关' })；webhook：$(if ($webhook) { '开' } else { '关' })）"
$deadline = $start.AddMinutes($TimeoutMinutes)
$lastHash = $null; $lastPush = $null; $gap = [TimeSpan]::Zero; $pushes = 0
try {
    while ((Get-Date) -lt $deadline) {
        if (Test-LoggedIn) { Write-Log "登录成功，退出（推送了 $pushes 次）"; exit 0 }
        $item = Get-Item -LiteralPath $QrPath -ErrorAction SilentlyContinue
        if ($item -and $item.LastWriteTime -ge $start -and $item.Length -gt 0 -and
            (-not $lastPush -or (Get-Date) - $lastPush -ge $gap)) {
            # 复制一份再发：NapCat 随时可能覆盖原文件
            $snap = Join-Path $env:TEMP 'dafeiyu-qrcode.png'
            try {
                Copy-Item -LiteralPath $QrPath -Destination $snap -Force
                $hash = (Get-FileHash -LiteralPath $snap -Algorithm SHA256).Hash
            } catch { $hash = $null }
            if ($hash -and $hash -ne $lastHash) {
                $stamp = $item.LastWriteTime.ToString('HH:mm:ss')
                Write-Log "二维码出现/更新（$stamp）"
                $ok = $false
                if ($mailOn) { try { Send-Mail $snap $stamp; $ok = $true } catch { Write-Log "邮件失败：$($_.Exception.Message)" } }
                if ($webhook) { try { Send-Webhook $snap $stamp; $ok = $true } catch { Write-Log "webhook 失败：$($_.Exception.Message)" } }
                $lastHash = $hash; $lastPush = Get-Date; $pushes++
                $gap = if ($gap -eq [TimeSpan]::Zero) { [TimeSpan]::FromMinutes($RetryMinutes) } else { [TimeSpan]::FromMinutes([Math]::Min(30, $gap.TotalMinutes * 2)) }
                if (-not $ok) { Write-Log '这次没推出去' }
            }
            Remove-Item -LiteralPath $snap -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds $PollSeconds
    }
    Write-Log "超时（$TimeoutMinutes 分钟）退出（推送了 $pushes 次）"
} finally {
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
}
