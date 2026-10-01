<#
.SYNOPSIS
    Start the Digital Sales Room on this machine: Vite on :5173, FastAPI on :8000.

.DESCRIPTION
    One command, or double-click dsr.cmd. Bootstraps .venv and node_modules when
    they are missing or stale, starts both servers detached, waits until they
    actually answer HTTP, then opens your browser.

    Detached by design: the servers outlive this script, so you can close the
    window. Use -Restart to stop and start again, -Stop to shut down.

.PARAMETER Restart
    Stop anything this launcher previously started, then start again.

.PARAMETER Stop
    Stop and exit without starting.

.PARAMETER NoBrowser
    Do not open the browser even when both servers are ready.

.NOTES
    Design decisions and the measurements behind them are recorded in
    .scratch/local-dev-loop/ and summarised in the answer sections of those
    tickets. Three of them are load-bearing and non-obvious:

      * uvicorn --reload is a THREE-level process tree. The PID returned at spawn
        is the reloader parent; a multiprocessing.spawn grandchild actually holds
        the socket. Killing the recorded PID leaves that grandchild serving HTTP.
        Stop walks the tree and kills every descendant, deepest first.

      * Vite listens on ::1 (IPv6). http://127.0.0.1:5173/ is REFUSED. Probe and
        print localhost, never 127.0.0.1.

      * The repo path contains a space, and Start-Process -ArgumentList silently
        splits on it. --app-dir is passed relative to -WorkingDirectory.
#>
[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$Stop,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------
# Paths and ports
# --------------------------------------------------------------------------

$Repo         = $PSScriptRoot
$VenvPy       = Join-Path $Repo '.venv\Scripts\python.exe'
$VenvCfg      = Join-Path $Repo '.venv\pyvenv.cfg'
$DistInfo     = Get-ChildItem (Join-Path $Repo '.venv\Lib\site-packages') -Filter 'dsr-*.dist-info' -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
$PyProject    = Join-Path $Repo 'backend\pyproject.toml'
$LockFile     = Join-Path $Repo 'frontend\package-lock.json'
$NpmStamp     = Join-Path $Repo 'frontend\node_modules\.package-lock.json'
$DataDir      = Join-Path $Repo 'data'
$LogDir       = Join-Path $DataDir 'logs'
$PidFile      = Join-Path $DataDir 'dev-processes.json'
$BootstrapLock = Join-Path $DataDir '.bootstrap.lock'

$BackendPort  = 8000
$FrontendPort = 5173
$FrontendUrl  = "http://localhost:$FrontendPort"

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

$script:StartedAt = Get-Date

function Write-Step([string]$Message) {
    $elapsed = ((Get-Date) - $script:StartedAt).TotalSeconds
    Write-Host ("  [{0,5:0.0}s] {1}" -f $elapsed, $Message) -ForegroundColor Cyan
}

function Write-Note([string]$Message) {
    Write-Host "           $Message" -ForegroundColor DarkGray
}

function Write-Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
}

function Write-Done([string]$Message) {
    Write-Host "  OK  $Message" -ForegroundColor Green
}

# --------------------------------------------------------------------------
# Port inspection
#
# -State Listen is mandatory. An unfiltered query also returns TIME_WAIT rows
# whose OwningProcess is 0, and killing PID 0 is not a thing you want to do.
# --------------------------------------------------------------------------

function Get-PortListener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-ProcessCommandLine([int]$ProcessId) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($p) { $p.CommandLine } else { $null }
}

# Does the occupying process look like one of ours? Used to decide whether
# -Restart may kill it. Command-line matching only: it is specific enough for
# our own spawns and cannot produce a false positive on an unrelated server.
#
# 'multiprocessing.spawn' matters as much as 'dsr.api:app'. uvicorn --reload puts
# the socket in a spawned grandchild whose command line is
#   python.exe -c "from multiprocessing.spawn import spawn_main; ..."
# which names neither uvicorn nor this app. Matching only the parent would make
# --restart refuse every time a backend is left running.
function Test-IsProjectProcess([int]$ProcessId) {
    $cmd = Get-ProcessCommandLine $ProcessId
    if (-not $cmd) { return $false }
    return ($cmd -like '*dsr.api:app*') -or
           ($cmd -like '*multiprocessing.spawn*') -or
           ($cmd -like '*vite*') -or
           ($cmd -like '*npm-cli.js*')
}

# --------------------------------------------------------------------------
# Process tree
#
# Windows does not reparent orphans, so a recorded ParentProcessId goes stale
# and Get-NetTCPConnection can name a process that is already dead. The only
# reliable identity is the live descendant set rooted at our recorded PID.
# --------------------------------------------------------------------------

function Get-DescendantPids([int]$RootPid) {
    $byParent = Get-CimInstance Win32_Process |
        Group-Object -Property ParentProcessId -AsHashTable -AsString

    $found = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootPid)

    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        $key = [string]$current
        if (-not $byParent.ContainsKey($key)) { continue }
        foreach ($child in $byParent[$key]) {
            $childPid = [int]$child.ProcessId
            if ($childPid -ne $RootPid -and -not $found.Contains($childPid)) {
                $found.Add($childPid)
                $queue.Enqueue($childPid)
            }
        }
    }
    return $found
}

function Stop-ProcessTree([int]$RootPid) {
    $all = [System.Collections.Generic.List[int]]::new()
    $all.Add($RootPid)
    foreach ($d in Get-DescendantPids -RootPid $RootPid) { $all.Add($d) }

    # Deepest first: killing a parent can take its children with it mid-loop.
    foreach ($id in ($all | Sort-Object -Descending)) {
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-PortFree([int]$Port, [int]$TimeoutSeconds = 10) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-PortListener -Port $Port)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return (-not (Get-PortListener -Port $Port))
}

# --------------------------------------------------------------------------
# Recorded process identity
#
# A bare PID cannot distinguish "my dead backend" from "an unrelated program
# that later received the same PID". The triple is captured at spawn because
# StartTime cannot be reconstructed afterwards.
# --------------------------------------------------------------------------

function Read-PidFile {
    if (Test-Path $PidFile) {
        try { return Get-Content $PidFile -Raw | ConvertFrom-Json }
        catch { return $null }
    }
    return $null
}

function Write-PidFile($Records) {
    $Records | ConvertTo-Json -Depth 5 | Set-Content -Path $PidFile -Encoding utf8
}

function Test-ProcessAlive($Record) {
    if (-not $Record) { return $false }
    $proc = Get-Process -Id $Record.Pid -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ((Get-ProcessCommandLine $Record.Pid) -ne $Record.CommandLine) { return $false }
    try {
        $delta = [Math]::Abs((($proc.StartTime).ToUniversalTime() -
                              ([datetime]$Record.StartTimeUtc).ToUniversalTime()).TotalSeconds)
        return $delta -lt 2
    } catch { return $false }
}

function Stop-RecordedProcesses {
    $records = Read-PidFile
    if (-not $records) {
        Write-Note 'nothing recorded from a previous launch'
        return
    }

    foreach ($name in @('backend', 'frontend')) {
        $record = $records.$name
        if (-not $record) { continue }

        if (-not (Test-ProcessAlive $record)) {
            # A dead PID is the normal state after a crash or a reboot.
            # Delete it silently rather than making you look at it.
            Write-Note "$name : recorded PID $($record.Pid) is gone, nothing to stop"
            continue
        }

        Write-Step "stopping $name (PID $($record.Pid))"
        Stop-ProcessTree -RootPid ([int]$record.Pid)
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# --------------------------------------------------------------------------
# Logs
#
# Two files per server, because Start-Process THROWS at parameter binding if
# stdout and stderr point at the same path - it compares by value, so two
# variables holding one path still throw and no process starts at all.
# Splitting by stream is also how uvicorn uses them: lifecycle on stderr,
# requests on stdout.
#
# Truncate BEFORE spawning. Start-Process holds an exclusive lock, so a
# SharingViolation here is a reliable signal that the old server is still up.
# --------------------------------------------------------------------------

function Reset-Log([string]$Path) {
    try {
        [System.IO.File]::WriteAllText($Path, '')
    } catch [System.IO.IOException] {
        Write-Fail "cannot truncate $Path - a previous server still holds it. Run: $Repo\dsr.cmd --restart"
        exit 1
    }
}

function Show-LogTail([string[]]$Paths, [int]$Lines = 20) {
    foreach ($path in $Paths) {
        if (-not (Test-Path $path)) { continue }
        Write-Host ""
        Write-Host "  --- $(Split-Path $path -Leaf) (last $Lines lines) ---" -ForegroundColor DarkYellow
        Get-Content $path -Tail $Lines -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
}

# --------------------------------------------------------------------------
# Bootstrap
#
# Readiness is a marker check, never a speculative reinstall:
#   backend  : dsr-*.dist-info newer than backend/pyproject.toml
#   frontend : node_modules/.package-lock.json newer than package-lock.json
# Both markers are written by the tools themselves.
#
# Do NOT substitute these:
#   pip install --dry-run   - always says "Would install dsr-0.1.0", costs ~7s
#   pip list --outdated     - public PyPI has an unrelated `dsr` at 1.0.4, so it
#                             reports this project permanently stale
#   npm ci unconditionally  - it DELETES node_modules and rebuilds, ~11s warm
# --------------------------------------------------------------------------

function Get-BootstrapAction {
    $actions = [System.Collections.Generic.List[string]]::new()

    $venvValid = (Test-Path $VenvCfg) -and (Test-Path $VenvPy)
    if (-not $venvValid) {
        $actions.Add('venv')
    } elseif (-not $DistInfo) {
        $actions.Add('venv')
    } elseif ((Get-Item $PyProject).LastWriteTimeUtc -gt $DistInfo.LastWriteTimeUtc) {
        $actions.Add('venv')
    }

    if (-not (Test-Path $NpmStamp)) {
        $actions.Add('npm')
    } elseif ((Get-Item $LockFile).LastWriteTimeUtc -gt (Get-Item $NpmStamp).LastWriteTimeUtc) {
        $actions.Add('npm')
    }

    # The comma keeps the list a single object. Without it PowerShell unrolls
    # a one-element collection to a scalar, and .Count is then missing under
    # Set-StrictMode.
    return , $actions.ToArray()
}

function Invoke-Bootstrap {
    $actions = Get-BootstrapAction

    if ($actions.Count -eq 0) {
        Write-Done 'environment is up to date'
        return
    }

    # npm ci deletes node_modules before reinstalling, and this runs before the
    # port check, so two rapid launches would race destructively against the
    # same directory. A stale lock is removed on the previous run's exit.
    if (Test-Path $BootstrapLock) {
        Write-Fail 'another launch is bootstrapping right now. Wait for it to finish, then retry.'
        exit 1
    }

    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    [System.IO.File]::WriteAllText($BootstrapLock, "$PID")

    try {
        if ($actions -contains 'venv') {
            $fresh = -not (Test-Path $VenvCfg) -or -not (Test-Path $VenvPy)
            if ($fresh) {
                Write-Step 'creating .venv (about 20 seconds)'
                if (Test-Path (Join-Path $Repo '.venv')) {
                    Remove-Item (Join-Path $Repo '.venv') -Recurse -Force
                }
                & python -m venv (Join-Path $Repo '.venv')
                if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
            } else {
                Write-Step 'backend dependencies are out of date, reinstalling (about a minute)'
            }

            # Quote the extras or the shell globs the brackets.
            & $VenvPy -m pip install --quiet --disable-pip-version-check -e 'backend[dev]'
            if ($LASTEXITCODE -ne 0) { throw 'pip install -e "backend[dev]" failed' }
            Write-Done 'backend dependencies installed'
        }

        if ($actions -contains 'npm') {
            Write-Step 'installing frontend dependencies (about 20 seconds)'
            # npm.cmd, not npm.ps1: the .ps1 shim is subject to ExecutionPolicy
            # and does not survive being a detached child. This is a
            # short-lived call from inside the launcher, not a detached server,
            # so the cmd.exe "Terminate batch job" prompt does not apply here.
            Push-Location (Join-Path $Repo 'frontend')
            try {
                & 'C:\Program Files\nodejs\npm.cmd' ci --no-audit --no-fund
                if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
            } finally { Pop-Location }
            Write-Done 'frontend dependencies installed'
        }
    } catch {
        Write-Fail $_.Exception.Message
        Write-Host ""
        Write-Host "  Retry by hand:" -ForegroundColor DarkYellow
        if ($actions -contains 'venv') {
            Write-Host "    $VenvPy -m pip install -e `"backend[dev]`"" -ForegroundColor DarkGray
        }
        if ($actions -contains 'npm') {
            Write-Host "    cd frontend; npm ci --no-audit --no-fund" -ForegroundColor DarkGray
        }
        Write-Host ""
        Write-Host "  If this persists, delete .venv and run dsr.cmd again." -ForegroundColor DarkGray
        exit 1
    } finally {
        Remove-Item $BootstrapLock -Force -ErrorAction SilentlyContinue
    }
}

# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

# Readiness makes a claim about the app, not about a socket. Two separate
# claims, deliberately not conflated:
#
#   "up"     - the process is bound and answering HTTP
#   "ready"  - the thing you came to use will actually work
#
# The wait helper reports the first. The gates below upgrade it to the second.
#
# Per-server budgets share one overall cap. The two servers start together and
# boot in parallel, so independent 90s budgets would let a wedged frontend hide
# behind a wedged backend for three minutes.
$ReadyBudgetBackend  = 90
$ReadyBudgetFrontend = 90
$ReadyOverallCap     = 150
$ProgressThreshold   = 12   # seconds before a "still waiting" counter appears

function Wait-Http([string]$Url, [int]$TimeoutSeconds, [string]$Label) {
    $started  = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    $reported = $false

    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -eq 200) {
                return [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
            }
        } catch { }

        # Stay quiet when it comes up quickly - the fast case is the norm and a
        # counter that runs 0.0s to 6.6s is noise. Once it is genuinely slow,
        # silence reads as a hang, so show where it is stuck.
        if (-not $reported) {
            $waited = ((Get-Date) - $started).TotalSeconds
            if ($waited -ge $ProgressThreshold) {
                Write-Note "$Label still starting, ${waited}s elapsed..."
                $reported = $true
            }
        }
        Start-Sleep -Milliseconds 400
    }
    return -1
}

# A probe that must return 200, but whose failure is a warning rather than a
# reason to refuse to open the browser. This is the plugin-host check: a
# feature that fails to import is skipped silently by the host, so the app
# looks perfectly healthy and only breaks later as a page that 404s.
function Test-Endpoint([string]$Url) {
    try {
        return (Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop).StatusCode -eq 200
    } catch { return $false }
}

function Get-FailedFeatures {
    try {
        $payload = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/features" -TimeoutSec 5 -ErrorAction Stop
        return , @($payload.failed)
    } catch { return , @() }
}

# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------

function Start-Backend {
    # DSR_DB_PATH is respected if you set it; otherwise the default is passed
    # explicitly. Never forced, so you can still point at a scratch database.
    if (-not $env:DSR_DB_PATH) {
        $env:DSR_DB_PATH = Join-Path $DataDir 'dsr.db'
    }
    if (-not $env:DSR_AUDIT_DIR) {
        $env:DSR_AUDIT_DIR = Join-Path $DataDir 'audit'
    }

    # Relative --app-dir, absolute -WorkingDirectory: an absolute --app-dir
    # would be split on the space in "dummy repo" and uvicorn would die.
    $args = @('-m', 'uvicorn', 'dsr.api:app', '--app-dir', 'backend',
              '--host', '127.0.0.1', '--port', "$BackendPort", '--reload')

    $proc = Start-Process -FilePath $VenvPy -ArgumentList $args `
        -WorkingDirectory $Repo `
        -RedirectStandardOutput (Join-Path $LogDir 'backend.log') `
        -RedirectStandardError  (Join-Path $LogDir 'backend.err.log') `
        -WindowStyle Hidden -PassThru
    $spawnedPid = $proc.Id
    $proc.Dispose()

    return @{
        Pid           = $spawnedPid
        StartTimeUtc  = (Get-Process -Id $spawnedPid).StartTime.ToUniversalTime().ToString('o')
        CommandLine   = (Get-ProcessCommandLine $spawnedPid)
    }
}

function Start-Frontend {
    # node runs vite DIRECTLY. Do not go via npm.cmd: that is a cmd.exe batch
    # shim, and a cmd.exe with a redirected stdin prompts "Terminate batch job
    # (Y/N)?" - which kills vite and hangs the launcher. Spawning node removes
    # every cmd.exe from the chain and puts the recorded PID two levels from
    # the socket instead of four.
    $node = (Get-Command node -ErrorAction Stop).Source
    # RELATIVE to the working directory, not absolute. An absolute path here
    # contains a space ("dummy repo") and Start-Process does not quote its
    # argument list, so node would receive a split path and die with
    # MODULE_NOT_FOUND.
    $viteJs = 'node_modules/vite/bin/vite.js'
    $frontendDir = Join-Path $Repo 'frontend'
    if (-not (Test-Path (Join-Path $frontendDir 'node_modules\vite\bin\vite.js'))) {
        throw "vite is not installed - run: cd frontend; npm ci --no-audit --no-fund"
    }

    $proc = Start-Process -FilePath $node -ArgumentList @($viteJs) `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput (Join-Path $LogDir 'frontend.log') `
        -RedirectStandardError  (Join-Path $LogDir 'frontend.err.log') `
        -WindowStyle Hidden -PassThru
    $spawnedPid = $proc.Id
    $proc.Dispose()

    return @{
        Pid           = $spawnedPid
        StartTimeUtc  = (Get-Process -Id $spawnedPid).StartTime.ToUniversalTime().ToString('o')
        CommandLine   = (Get-ProcessCommandLine $spawnedPid)
    }
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

Write-Host ""
Write-Host "  Digital Sales Room - local dev" -ForegroundColor White
Write-Host "  -----------------------------" -ForegroundColor DarkGray

# Stop-only path.
if ($Stop) {
    Write-Step 'stopping'
    Stop-RecordedProcesses
    foreach ($port in @($BackendPort, $FrontendPort)) {
        if (Wait-PortFree -Port $port) { Write-Done "port $port free" }
        else { Write-Fail "port $port is still held - find it with: Get-NetTCPConnection -LocalPort $port -State Listen" }
    }
    exit 0
}

# Refuse occupied ports before doing any work.
$blocked = @()
foreach ($port in @($BackendPort, $FrontendPort)) {
    $listener = Get-PortListener -Port $port
    if ($listener) {
        $cmd = Get-ProcessCommandLine $listener.OwningProcess
        $mine = Test-IsProjectProcess $listener.OwningProcess
        $blocked += [pscustomobject]@{ Port = $port; Pid = $listener.OwningProcess; Cmd = $cmd; Mine = $mine }
    }
}

if ($blocked.Count -gt 0) {
    $canRestart = $true
    foreach ($b in $blocked) { if (-not $b.Mine) { $canRestart = $false } }

    if (-not ($Restart -and $canRestart)) {
        foreach ($b in $blocked) {
            Write-Fail "port $($b.Port) is already in use (PID $($b.Pid))"
            if ($b.Cmd) { Write-Note $b.Cmd }
        }
        Write-Host ""
        if ($Restart) {
            Write-Note "That port is held by a process that is not one of this project's."
            Write-Note "Refusing to kill it. Stop it yourself, or re-run without --restart."
        } else {
            Write-Note "Nothing was started and nothing was changed. This is not a crash -"
            Write-Note "the app is very likely already running. If that is what you wanted,"
            Write-Note "just open the browser."
            Write-Note ""
            Write-Note "If it is a stale leftover of yours:  $Repo\dsr.cmd --restart"
            foreach ($b in $blocked) {
                Write-Note "See who holds port $($b.Port):"
                Write-Note "  Get-NetTCPConnection -LocalPort $($b.Port) -State Listen"
            }
        }
        exit 1
    }

    Write-Step 'restart: stopping existing servers'
    Stop-RecordedProcesses
    foreach ($port in @($BackendPort, $FrontendPort)) {
        $listener = Get-PortListener -Port $port
        if ($listener) {
            Write-Note "port $port held by PID $($listener.OwningProcess) - not recorded, killing by port"
            Stop-ProcessTree -RootPid $listener.OwningProcess
        }
    }
    foreach ($port in @($BackendPort, $FrontendPort)) {
        if (-not (Wait-PortFree -Port $port)) { Write-Fail "port $port did not free up"; exit 1 }
    }
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Invoke-Bootstrap

# Truncate before spawning; a SharingViolation here means a server is still up.
Reset-Log (Join-Path $LogDir 'backend.log')
Reset-Log (Join-Path $LogDir 'backend.err.log')
Reset-Log (Join-Path $LogDir 'frontend.log')
Reset-Log (Join-Path $LogDir 'frontend.err.log')

Write-Step 'starting backend on 127.0.0.1:8000'
$backend = Start-Backend

Write-Step 'starting frontend on localhost:5173'
$frontend = Start-Frontend

Write-PidFile -Records @{ backend = $backend; frontend = $frontend }

# The overall cap starts now, at the moment both servers are live, and bounds
# the two waits below however long the first one takes.
$readyStarted = Get-Date
function Get-RemainingBudget {
    $left = $ReadyOverallCap - ((Get-Date) - $readyStarted).TotalSeconds
    if ($left -lt 5) { return 5 }
    return [int][Math]::Floor($left)
}

# Gate 1: the backend must answer its own health route. Without this nothing
# else can be trusted - the plugin host check below runs against this process.
Write-Step 'waiting for the backend'
$backendElapsed = Wait-Http -Url "http://127.0.0.1:$BackendPort/api/health" `
                           -TimeoutSeconds ([Math]::Min($ReadyBudgetBackend, (Get-RemainingBudget))) `
                           -Label 'backend'
if ($backendElapsed -lt 0) {
    Write-Fail "backend did not answer /api/health within $([Math]::Min($ReadyBudgetBackend, (Get-RemainingBudget)))s"
    Show-LogTail @((Join-Path $LogDir 'backend.err.log'), (Join-Path $LogDir 'backend.log'))
    exit 1
}
Write-Done "backend healthy in ${backendElapsed}s (health 200)"

# Gate 2: the frontend must serve its shell. Probed on localhost, never
# 127.0.0.1 - vite binds ::1 and the IPv4 address is actively refused, so an
# IPv4 probe would report "not ready" against a perfectly working app.
Write-Step 'waiting for the frontend'
$frontendElapsed = Wait-Http -Url $FrontendUrl `
                             -TimeoutSeconds ([Math]::Min($ReadyBudgetFrontend, (Get-RemainingBudget))) `
                             -Label 'frontend'
if ($frontendElapsed -lt 0) {
    Write-Fail 'frontend did not serve its shell in time'
    Show-LogTail @((Join-Path $LogDir 'frontend.err.log'), (Join-Path $LogDir 'frontend.log'))
    exit 1
}
Write-Done "frontend serving in ${frontendElapsed}s"

# Gate 3 (informational, non-fatal): prove the plugin host mounted routes. The
# host silently SKIPS a feature that fails to import and carries on, so this
# can pass while a page is broken - which is precisely why the failure is
# reported loudly rather than being folded into the exit code. Refusing to open
# the browser over one bad workflow would hide the 42 that work.
$hostMounted = Test-Endpoint "http://127.0.0.1:$BackendPort/api/features"
if ($hostMounted) {
    Write-Done 'plugin host mounted its routes'
} else {
    Write-Note 'WARNING: /api/features did not respond - the plugin host may not have loaded'
}

$failed = Get-FailedFeatures
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "  WARNING: $($failed.Count) backend feature(s) failed to load." -ForegroundColor Yellow
    Write-Host "  The app is running and the other features work, but these" -ForegroundColor DarkGray
    Write-Host "  pages will not. Full detail: http://127.0.0.1:$BackendPort/api/features" -ForegroundColor DarkGray
    foreach ($f in $failed) {
        $label = $f.module
        if (-not $label) { $label = $f.name }
        if (-not $label) { $label = 'unknown' }
        $why = $f.error
        if ($why) { Write-Host "    - $label  ($why)" -ForegroundColor Yellow }
        else { Write-Host "    - $label" -ForegroundColor Yellow }
    }
    Write-Host ""
}

# A first launch costs ~107s of venv creation, pip and npm, most of it silent
# because package managers print almost nothing while they work. Naming it up
# front stops that being mistaken for a hang.
$dbPathForDisplay = if ($env:DSR_DB_PATH) { $env:DSR_DB_PATH } else { Join-Path $DataDir 'dsr.db' }
$dbExists = Test-Path $dbPathForDisplay
$roomCount = 0
if ($dbExists) {
    # The list route returns a wrapper object, not a bare array, so the count
    # has to be read from the field. Wrapping the response in @() would count
    # the wrapper and report 1 room even when there are none.
    try {
        $listing = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/records/room?limit=1" -TimeoutSec 5
        if ($null -ne $listing.count) { $roomCount = [int]$listing.count }
    } catch { }
}

Write-Host ""
Write-Host "  Ready." -ForegroundColor Green
Write-Host ""
Write-Host "    Browser    $FrontendUrl" -ForegroundColor White
Write-Host "    API        http://127.0.0.1:$BackendPort   (API only; its root 404s by design)" -ForegroundColor DarkGray
Write-Host "    Database   $dbPathForDisplay" -ForegroundColor DarkGray
Write-Host "    Logs       data\logs\backend.log  +  data\logs\frontend.log" -ForegroundColor DarkGray
Write-Host "    Processes  backend PID $($backend.Pid), frontend PID $($frontend.Pid)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    Stop       $Repo\dsr.cmd --stop" -ForegroundColor DarkGray
Write-Host "    Restart    $Repo\dsr.cmd --restart" -ForegroundColor DarkGray
Write-Host ""

# A fresh database has no accounts, and a room needs an account to bind to, so
# the first run of the app is a create-account flow rather than a populated app.
# Templates ship with the code; accounts do not, and the seeder refuses to run
# against a populated database - which is why the launcher reports and never acts.
if (-not $dbExists) {
    Write-Host "  No database yet - a new one was just created." -ForegroundColor Yellow
    Write-Host "  To fill it with demo data (4 rooms, 8 documents, 140 activities):" -ForegroundColor DarkGray
    Write-Host "      .venv\Scripts\python backend\seed.py" -ForegroundColor DarkGray
    Write-Host "  Creating a room needs an account to bind to, so the app will" -ForegroundColor DarkGray
    Write-Host "  start empty. That is expected." -ForegroundColor DarkGray
    Write-Host ""
} elseif ($roomCount -eq 0) {
    Write-Host "  The database has no rooms yet. Create an account first, then a" -ForegroundColor DarkGray
    Write-Host "  room can bind to it. Or run backend\seed.py for demo data." -ForegroundColor DarkGray
    Write-Host ""
}

Write-Host "  You can close this window; both servers keep running." -ForegroundColor DarkGray
Write-Host ""

# The papercut charting flagged as fog. The feature registry is a process-global
# singleton that short-circuits (features/__init__.py:109,162), so a feature file
# added since the last start is silently absent until the backend restarts --
# and a skipped feature is exactly what a silently broken page looks like.
# Detected by mtime, which is a hint rather than a proof, so it is phrased as one.
$newFeatures = Get-ChildItem (Join-Path $Repo 'backend\dsr\features') -Filter '*.py' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '__*' -and $_.LastWriteTimeUtc -gt $readyStarted.ToUniversalTime() }
if ($newFeatures) {
    Write-Host "  NOTE: $($newFeatures.Count) backend feature file(s) changed since the last start." -ForegroundColor Yellow
    Write-Host "  uvicorn --reload will pick up edits to an EXISTING feature, but a"
    Write-Host "  brand-new file needs a restart to be discovered:"
    Write-Host "      $Repo\dsr.cmd --restart" -ForegroundColor DarkGray
    Write-Host ""
}

if (-not $NoBrowser) {
    Start-Process $FrontendUrl
}

exit 0
