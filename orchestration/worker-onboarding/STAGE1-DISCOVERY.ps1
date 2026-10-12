# STAGE 1 - READ-ONLY DISCOVERY on pc-mini
# Nothing here modifies anything. Safe to run.
# Run in PowerShell, ideally ELEVATED (needed for the capability query).
# Paste the whole output back to the hub agent.

Write-Output "=== 1. IDENTITY ==="
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Output ("Hostname : " + $env:COMPUTERNAME)
Write-Output ("Account  : " + $id.Name)
Write-Output ("Elevated : " + $isAdmin)
$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (Test-Path $ts) {
    Write-Output ("TS name  : " + ((& $ts status --json | ConvertFrom-Json).Self.DNSName))
    Write-Output ("TS ipv4  : " + (& $ts ip -4))
    Write-Output ("TS state : " + (& $ts status --json | ConvertFrom-Json).Self.Online)
} else { Write-Output "TAILSCALE NOT FOUND" }

Write-Output ""
Write-Output "=== 2. OPENSSH SERVER (drives stage 2) ==="
try { Get-WindowsCapability -Online -Name OpenSSH.Server* | Format-List Name,State | Out-String | Write-Output }
catch { Write-Output ("capability query FAILED: " + $_.Exception.Message) }

$svc = Get-Service sshd -ErrorAction SilentlyContinue
if ($svc) { Write-Output ("sshd service : " + $svc.Status + " / start=" + (Get-CimInstance Win32_Service -Filter "Name='sshd'").StartMode) }
else { Write-Output "sshd service : NOT INSTALLED" }

$l = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if ($l) { $l | ForEach-Object { Write-Output ("port22 listen: " + $_.LocalAddress) } } else { Write-Output "port22 listen: NONE" }

$fw = Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue
if ($fw) { Write-Output ("ssh fw rule : Enabled=" + $fw.Enabled + " Action=" + $fw.Action + " Profile=" + $fw.Profile) }
else { Write-Output "ssh fw rule : NOT FOUND" }

Write-Output ""
Write-Output "=== 3. EXISTING AUTHORIZED KEYS (so we do not duplicate/overwrite) ==="
foreach ($p in @((Join-Path $HOME ".ssh\authorized_keys"), "C:\ProgramData\ssh\administrators_authorized_keys")) {
    if (Test-Path $p) {
        $k = @(Get-Content $p | Where-Object { $_.Trim() -ne "" })
        Write-Output ($p + "  -> " + $k.Count + " key(s)")
        foreach ($line in $k) { $f = $line.Trim() -split '\s+'; Write-Output ("     " + $f[0] + " " + $f[$f.Count-1]) }
    } else { Write-Output ($p + "  -> absent") }
}

Write-Output ""
Write-Output "=== 4. ORCA ==="
$o = $null
foreach ($c in @((Join-Path $env:LOCALAPPDATA "Programs\orca\resources\bin\orca.exe"), "C:\Program Files\orca\orca.exe")) { if (Test-Path $c) { $o = $c; break } }
if (-not $o) { $g = Get-Command orca -ErrorAction SilentlyContinue; if ($g) { $o = $g.Source } }
if ($o) {
    Write-Output ("orca path : " + $o)
    Write-Output ("orca ver  : " + (& $o --version))
    Write-Output "orca environments saved here:"
    (& $o environment list --json) | Write-Output
} else { Write-Output "ORCA CLI NOT FOUND" }

Write-Output ""
Write-Output "=== 5. OPENCODE ==="
$g = Get-Command opencode -ErrorAction SilentlyContinue
if ($g) { Write-Output ("opencode : " + $g.Source + "  ver=" + (& opencode --version)) } else { Write-Output "OPENCODE NOT ON PATH" }

Write-Output ""
Write-Output "=== 6. REPO (read-only; must stay untouched) ==="
$r = "C:\Users\Dilip\Documents\git repos\client-theater"
if (Test-Path $r) {
    Write-Output ("path     : " + $r)
    Write-Output ("toplevel : " + (git -C $r rev-parse --show-toplevel 2>&1))
    Write-Output ("HEAD     : " + (git -C $r rev-parse HEAD 2>&1))
    Write-Output ("branch   : " + (git -C $r branch --show-current 2>&1))
    Write-Output "status:";    (git -C $r status --short --branch 2>&1) | ForEach-Object { Write-Output ("   " + $_) }
    Write-Output "remotes:";  (git -C $r remote -v 2>&1) | ForEach-Object { Write-Output ("   " + $_) }
} else { Write-Output ("REPO NOT FOUND at " + $r) }

Write-Output ""
Write-Output "=== 7. NETWORK PROFILE + DISK ==="
Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, NetworkCategory | Format-Table -AutoSize | Out-String | Write-Output
Write-Output ("free C: " + [math]::Round((Get-PSDrive C).Free/1GB,2) + " GB")
Write-Output ""
Write-Output "=== STAGE 1 COMPLETE ==="