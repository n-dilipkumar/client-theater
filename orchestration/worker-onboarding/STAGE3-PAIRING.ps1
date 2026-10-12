# STAGE 3 - ORCA RUNTIME PAIRING on pc-mini
# Run ELEVATED on pc-mini. Starts an Orca runtime server that the hub pairs to
# via a pairing code. Idempotent-ish: refuses to start a second server.

$TS_IP = '100.78.82.20'
$PORT = 6768
$ORCA = Join-Path $env:LOCALAPPDATA "Programs\orca\resources\bin\orca.exe"
$LOG  = Join-Path $env:USERPROFILE "orca-serve.log"

if (-not (Test-Path $ORCA)) { Write-Output "ORCA NOT FOUND at $ORCA"; exit 1 }
Write-Output ("orca : " + $ORCA + "  ver " + (& $ORCA --version))

# 1. Firewall: narrowly scoped inbound for the Orca serve port only.
$ruleName = 'Orca-Serve-In-TCP'
$fw = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($fw) {
    Write-Output "firewall rule '$ruleName' already exists - no duplicate created"
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName 'Orca Runtime Server (serve)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
        -LocalPort $PORT -Profile Any | Out-Null
    Write-Output "created firewall rule '$ruleName' for TCP $PORT"
}

# 2. Refuse to start a duplicate server.
$existing = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output ("port $PORT ALREADY LISTENING - a server is already running; not starting another")
    Write-Output "See $LOG for its pairing code."
} else {
    Write-Output "starting orca serve in background..."
    # --pairing-address makes the advertised endpoint the Tailscale IP so the
    # hub can dial it over the tailnet rather than a LAN address.
    Start-Process -FilePath $ORCA `
        -ArgumentList @('serve','--port',"$PORT",'--pairing-address',$TS_IP) `
        -RedirectStandardOutput $LOG `
        -RedirectStandardError "$LOG.err" `
        -WindowStyle Hidden
    Start-Sleep -Seconds 6
    Write-Output "serve launched; waiting for it to bind..."
}

# 3. Wait for the listener (bounded: 30s max).
$ok = $false
for ($i=0; $i -lt 30; $i++) {
    $l = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
    if ($l) { $ok = $true; break }
    Start-Sleep -Seconds 1
}

Write-Output ""
Write-Output "=== RESULT ==="
if ($ok) {
    Write-Output "LISTENER: up on TCP $PORT"
} else {
    Write-Output "LISTENER: NOT UP after 30s - see logs below"
}

Write-Output ""
Write-Output "=== PAIRING CODE (copy this to the hub agent) ==="
if (Test-Path $LOG) {
    $raw = Get-Content $LOG -Raw
    $m = [regex]::Match($raw, 'orca://pair\?code=[^\s"'']+')
    if ($m.Success) { Write-Output $m.Value }
    else {
        Write-Output "(no orca://pair code matched; raw log below)"
        Write-Output $raw
    }
} else { Write-Output "no log file at $LOG" }

Write-Output ""
Write-Output "=== LISTENING SOCKETS ==="
Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Output ("  " + $_.LocalAddress + ":" + $_.LocalPort) }

Write-Output ""
Write-Output "=== RAW LOG ==="
if (Test-Path $LOG) { Get-Content $LOG }
if (Test-Path "$LOG.err") { Write-Output "--- stderr ---"; Get-Content "$LOG.err" }

Write-Output ""
Write-Output "=== STAGE 3 COMPLETE ==="