# STAGE 2 - SSH ENABLEMENT on pc-mini
# Enables the OpenSSH Server (already installed), installs the HUB'S PUBLIC KEY, fixes ACLs.
# Idempotent: safe to run more than once. Never overwrites existing keys.
# Requires ELEVATED PowerShell.

$HUB_PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILqPzhoHFrdJnn8AYY/MEHbewaZVO+39nqMV4YjJkWwq orca-hub'
$me = $env:USERNAME
$id = [Security.Principal.WindowsIdentity]::GetCurrent()

Write-Output "=== A. ACCOUNT CLASS ==="
$isElevated = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$sids = @($id.Groups | ForEach-Object { $_.Value })
$isAdminMember = ($sids -contains 'S-1-5-32-544')
Write-Output ("Elevated now        : " + $isElevated)
Write-Output ("Member of Admin grp : " + $isAdminMember)
# Windows OpenSSH reads administrators_authorized_keys for members of the
# Administrators group, and %USERPROFILE%\.ssh\authorized_keys for everyone else.
if ($isAdminMember) { $AUTH = "C:\ProgramData\ssh\administrators_authorized_keys"; Write-Output "=> will use ADMINISTRATORS file" }
else { $AUTH = Join-Path $HOME ".ssh\authorized_keys"; Write-Output "=> will use USER file" }

Write-Output ""
Write-Output "=== B. START SSH SERVICE ==="
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Start-Sleep -Seconds 2
$s = Get-Service sshd
Write-Output ("sshd status  : " + $s.Status)
Write-Output ("sshd start   : " + (Get-CimInstance Win32_Service -Filter "Name='sshd'").StartMode)

Write-Output ""
Write-Output "=== C. FIREWALL (enable if present; do not duplicate) ==="
$fw = Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue
if ($fw) {
    if ($fw.Enabled -ne 'True') { Enable-NetFirewallRule -Name OpenSSH-Server-In-TCP; Write-Output "enabled existing rule" }
    else { Write-Output "existing rule already enabled (no change)" }
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any | Out-Null
    Write-Output "created OpenSSH-Server-In-TCP rule"
}

Write-Output ""
Write-Output "=== D. INSTALL HUB PUBLIC KEY ==="
if ($isAdminMember) {
    $dir = "C:\ProgramData\ssh"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
} else {
    $dir = Join-Path $HOME ".ssh"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}
if (-not (Test-Path $AUTH)) { New-Item -ItemType File -Force -Path $AUTH | Out-Null }

$existing = @(Get-Content $AUTH | Where-Object { $_.Trim() -ne "" })
$already = $existing | Where-Object { $_.Trim() -eq $HUB_PUBKEY }
if ($already) {
    Write-Output "hub key ALREADY present - not appending again"
} else {
    Add-Content -Path $AUTH -Value $HUB_PUBKEY
    Write-Output "hub key APPENDED"
}
Write-Output ("pre-existing keys preserved: " + $existing.Count)

Write-Output ""
Write-Output "=== E. FIX ACLs (Windows OpenSSH rejects keys on loose ACLs) ==="
if ($isAdminMember) {
    # Administrators file: SYSTEM + Administrators (by SID, avoids localized group names)
    icacls $AUTH /inheritance:r | Out-Null
    icacls $AUTH /grant:r "*S-1-5-18:F" | Out-Null
    icacls $AUTH /grant:r "*S-1-5-32-544:F" | Out-Null
    Write-Output "applied administrators-file ACLs (SYSTEM + Administrators, inheritance removed)"
} else {
    $meSid = $id.User.Value
    icacls $dir /inheritance:r | Out-Null
    icacls $dir /grant:r "*${meSid}:(OI)(CI)F" | Out-Null
    icacls $AUTH /inheritance:r | Out-Null
    icacls $AUTH /grant:r "*${meSid}:F" | Out-Null
    Write-Output "applied standard-user ACLs on $dir and $AUTH"
}

Write-Output ""
Write-Output "=== F. VERIFY ==="
Write-Output ("sshd running   : " + (Get-Service sshd).Status)
$l = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if ($l) { $l | ForEach-Object { Write-Output ("listening on   : " + $_.LocalAddress + ":" + $_.LocalPort) } } else { Write-Output "listening on   : NONE  <-- PROBLEM" }
Write-Output ("auth file      : " + $AUTH)
Write-Output ("auth keys now  : " + (@(Get-Content $AUTH | Where-Object { $_.Trim() -ne "" }).Count))
icacls $AUTH
Write-Output ""
Write-Output "=== STAGE 2 COMPLETE - report output to the hub agent ==="