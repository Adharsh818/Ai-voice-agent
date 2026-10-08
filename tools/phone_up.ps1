# Get Emma's phone line ready (docs/TELEPHONY.md, "Every time: phone_up").
#
#   powershell -ExecutionPolicy Bypass -File tools\phone_up.ps1          normal PowerShell, in the repo
#   powershell -ExecutionPolicy Bypass -File tools\phone_up.ps1 -Force   re-render and reinstall the configs anyway
#
# 1. Keeps Ubuntu (WSL) running: WSL stops it about a minute after its last window
#    closes, and Asterisk stops with it (8 Oct). A hidden "sleep infinity" keeps it up
#    until the PC restarts or someone runs "wsl --shutdown".
# 2. If the PC's Wi-Fi address changed (a phone hotspot hands out new ones), binds SIP
#    to the new one (tools/telephony_setup.py --lan) and reinstalls Asterisk's configs.
# 3. Makes sure Asterisk is running and turns on SIP logging (/var/log/asterisk/sip.log).
# 4. Says if the firewall rules still admit an old subnet (the fix needs an admin window).
# 5. Shows which phones are registered.
#
# Needs no admin rights and no Ubuntu password (Windows' own "wsl -u root").

param([switch]$Force)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Distro = "Ubuntu-24.04"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"
$RuleName = "Emma SIP (Wi-Fi only)"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  OK    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  TODO  $text" -ForegroundColor Yellow }
function EnvValue($name) {
    if (-not (Test-Path $EnvFile)) { return "" }
    $m = Select-String -Path $EnvFile -Pattern "^$name=(.*)$" | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Groups[1].Value.Trim() } else { return "" }
}
function Root([string]$command) { wsl.exe -d $Distro -u root -- bash -c $command }

# -- 1. Ubuntu stays up -------------------------------------------------------------
Step "Ubuntu (WSL)"
$keep = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
    Where-Object { $_.CommandLine -like "*$Distro*sleep infinity*" }
if ($keep) {
    Ok "kept running (process $($keep[0].ProcessId))"
} else {
    # Started through WMI so it isn't tied to this window or any tool's session.
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = "wsl.exe -d $Distro --exec sleep infinity" }
    if ($r.ReturnValue -ne 0) { throw "couldn't start the keep-alive (WMI code $($r.ReturnValue))" }
    Start-Sleep -Seconds 5
    Ok "started and kept running (process $($r.ProcessId))"
}

# -- 2. The Wi-Fi address -------------------------------------------------------------
Step "Network"
$net = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1
if (-not $net) { throw "no network with a gateway: connect the PC to the Wi-Fi or the phone's hotspot first" }
$ip = $net.IPv4Address.IPAddress
$netName = (Get-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex).Name
$bound = EnvValue "TELEPHONY_SIP_BIND"
Write-Host "  PC address $ip on '$netName' (SIP bound to: $(if ($bound) { $bound } else { 'loopback only' }))"
# What Asterisk is really listening on (an interrupted run can leave .env ahead of it).
$live = (Root "asterisk -rx 'pjsip show transports' 2>/dev/null" | Select-String "transport-lan\s+udp\s+\d+\s+\d+\s+([\d.]+):5060" |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -First 1)
$changed = $Force -or ($bound -ne $ip) -or ($live -ne $ip)
if ($changed) {
    Write-Host "  Binding SIP to $ip ..."
    & $Python (Join-Path $Root "tools\telephony_setup.py") --lan
    if ($LASTEXITCODE -ne 0) { throw "telephony_setup.py failed" }
    $wslRoot = "/mnt/" + $Root.Substring(0, 1).ToLower() + $Root.Substring(2).Replace("\", "/")   # A:\Voice-Agent -> /mnt/a/Voice-Agent
    Root "bash '$wslRoot/telephony/install_asterisk.sh'"
    Ok "Asterisk configs reinstalled for $ip"
} else {
    Ok "unchanged"
}

# -- 3. Asterisk ----------------------------------------------------------------------
Step "Asterisk"
$state = (Root "systemctl is-active asterisk").Trim()
if ($state -ne "active") {
    Root "systemctl restart asterisk; sleep 3" | Out-Null
    $state = (Root "systemctl is-active asterisk").Trim()
}
if ($state -ne "active") { throw "Asterisk isn't running: wsl -d $Distro -u root -- journalctl -u asterisk -n 40" }
Root "asterisk -rx 'core set verbose 3' >/dev/null; asterisk -rx 'pjsip set logger on' >/dev/null; asterisk -rx 'logger add channel sip.log verbose,notice,warning,error' >/dev/null" | Out-Null
Ok "running; SIP log in /var/log/asterisk/sip.log"
Root "asterisk -rx 'pjsip show transports'" | Select-String "transport-" | ForEach-Object { Write-Host "  $($_.Line.Trim())" }

# -- 4. Firewall (read only; changing it needs an admin window) -------------------------
Step "Firewall"
$subnet = EnvValue "TELEPHONY_SIP_SUBNET"
if ($subnet) {
    $prefix = [int]($subnet.Split("/")[1])
    $mask = ([ipaddress]([uint32]::MaxValue -shl (32 - $prefix) -band [uint32]::MaxValue)).IPAddressToString
    $want = "$($subnet.Split('/')[0])/$mask"
    $rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    $have = if ($rule) { ($rule | Get-NetFirewallAddressFilter).RemoteAddress } else { $null }
    if ($have -eq $want) {
        Ok "admits $subnet"
    } else {
        Warn "the firewall admits '$have', the phone line needs $subnet. In an ADMIN PowerShell run:"
        if ($rule) {
            Write-Host "    Set-NetFirewallRule -DisplayName `"$RuleName`" -RemoteAddress $subnet"
            Write-Host "    Set-NetFirewallHyperVRule -Name EmmaSIP -RemoteAddresses $subnet"
        } else {
            Write-Host "    New-NetFirewallRule -DisplayName `"$RuleName`" -Direction Inbound -Protocol UDP -LocalPort 5060,10000-10200 -RemoteAddress $subnet -Action Allow"
            Write-Host "    New-NetFirewallHyperVRule -Name EmmaSIP -DisplayName `"$RuleName`" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 5060,10000-10200 -RemoteAddresses $subnet -Action Allow"
        }
    }
} else {
    Ok "loopback only: no Wi-Fi rule needed"
}

# -- 5. Phones ------------------------------------------------------------------------
Step "Phones"
$contacts = Root "asterisk -rx 'pjsip show contacts'" | Select-String "Contact:\s+100[12]/"
if ($contacts) { $contacts | ForEach-Object { Write-Host "  $($_.Line.Trim())" } } else { Warn "no phone registered yet" }
Write-Host ""
Write-Host "  Linphone (1001): server / register URI sip:$ip;transport=udp, Media encryption None or SRTP"
Write-Host "  MicroSIP (1002): server and domain 127.0.0.1, Settings > Source Port 5070"
Write-Host "  If a phone isn't listed: restart the app (it gives up while Asterisk is down)."
if ((EnvValue "TELEPHONY_ENABLED") -ne "true") { Warn "TELEPHONY_ENABLED isn't true in .env: Emma won't answer the phone" }
try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8000/" | Out-Null
    Ok "Emma is running"
} catch {
    Warn "Emma isn't running: .\.venv\Scripts\python.exe server.py"
}
