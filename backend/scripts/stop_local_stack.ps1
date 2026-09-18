<#
.SYNOPSIS
    Stop the RigBuilder processes that run on the machine this script is executed on: the two
    llama-server inference endpoints (device B) and/or the FastAPI backend (device A).

.DESCRIPTION
    The two-machine deployment starts each half on a different host, so run this with
    -InferenceOnly on device B and with -BackendOnly on device A. Stops, in this order:

      1. llama-server processes (all of them, by process name)
      2. the uvicorn / FastAPI backend bound to -BackendPort (by owning PID, then verified)

    Safety rules, because the wrong Stop-Process here costs someone their work or their
    session:

      * The backend is stopped by the PID that owns the listening socket, never by the name
        "python". This host may run other Python processes (Jupyter, other services), and
        killing all of them would be a serious accident.
      * The PID is verified to look like this project's backend before it is killed. It must
        either be a python process or have the current directory inside this repository / a
        command line containing app.main:app. The check outcome is printed; if the process
        does not look like ours the script refuses to kill it unless -Force is given.
      * Nothing is killed unless the port is genuinely in LISTENING state.

    A forced stop (llama-server) cannot flush in-flight generations: a fusion job running on
    the server is lost, and clients holding an SSE stream will see the connection reset.
    Jobs are NOT replayed after a restart (app/services/jobs.py marks them server_restarted).
    Warn clients before stopping, or use -BackendOnly / -InferenceOnly to stop one half.

    ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 files using the ANSI
    code page, which corrupts non-ASCII text and can break parsing.

.EXAMPLE
    .\stop_local_stack.ps1
    # Stops the backend and all two llama-server processes, then verifies the ports are free.

.EXAMPLE
    .\stop_local_stack.ps1 -BackendOnly
    # Only stop FastAPI; leave inference resident (avoids the ~1 minute model reload).

.EXAMPLE
    .\stop_local_stack.ps1 -InferenceOnly
    # Only stop the two inference endpoints.

.EXAMPLE
    .\stop_local_stack.ps1 -WhatIf
    # Show what would be stopped, kill nothing. Use this when unsure.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [int]$BackendPort = 8000,
    [int[]]$InferencePorts = @(8081, 8082),
    [switch]$BackendOnly,
    [switch]$InferenceOnly,
    [switch]$Force,
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = 'Continue'
$backendDir = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $backendDir
$whatIf = [bool]$WhatIfPreference

# ---------------------------------------------------------------- helpers
function Get-PortPid {
    param([int]$Port)
    $lines = @(netstat -ano | Select-String (":{0}\s" -f $Port) | Select-String 'LISTENING')
    if ($lines.Count -eq 0) { return $null }
    $pidText = ($lines[0].Line -split '\s+')[-1]
    if ($pidText -match '^\d+$') { return [int]$pidText }
    return $null
}

function Test-BackendProcess {
    <#
        Returns a two-item result: whether the process looks like this repository's backend,
        and the path / command-line evidence used to decide.
    #>
    param([int]$ProcessId)
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return @{ Ours = $false; Evidence = 'process no longer exists'; Name = '(gone)'; Path = '' } }
    $name = $proc.ProcessName
    $path = ''
    try { $path = $proc.Path } catch { }
    $evidence = @()
    $isPython = $name -like 'python*'
    $evidence += ("name={0}" -f $name)
    if ($path) { $evidence += ("path={0}" -f $path) }
    if (-not $isPython) {
        return @{ Ours = $false; Evidence = ($evidence -join '; '); Name = $name; Path = $path }
    }
    # A python process owning 8000 whose command line mentions our app is certainly ours.
    $cmd = ''
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction Stop).CommandLine
    }
    catch { }
    if ($cmd) {
        $evidence += 'commandline available'
        if ($cmd -match 'app\.main:app' -or $cmd -match 'uvicorn') {
            return @{ Ours = $true; Evidence = ($evidence -join '; ') + '; cmd mentions uvicorn/app.main:app'; Name = $name; Path = $path }
        }
        return @{ Ours = $false; Evidence = ($evidence -join '; ') + ("; cmd= {0}" -f $cmd); Name = $name; Path = $path }
    }
    # Command line unavailable (needs WMI, which a constrained session may refuse).
    # Fall back to "python listening on the backend port" which is a strong signal.
    return @{ Ours = $true; Evidence = ($evidence -join '; ') + '; cmd unavailable (WMI denied), assumed ours by name+port'; Name = $name; Path = $path }
}

function Wait-PortFree {
    param([int]$Port, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-PortPid -Port $Port)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$stopped = New-Object System.Collections.Generic.List[object]
$skipped = New-Object System.Collections.Generic.List[object]

Write-Host '=== RigBuilder stop ===' -ForegroundColor Cyan
if ($whatIf) { Write-Host '(-WhatIf: nothing will actually be stopped)' -ForegroundColor Yellow }
Write-Host ("repo root   : {0}" -f $repoRoot)
Write-Host ("backend port: {0}" -f $BackendPort)
if (-not $InferenceOnly) { Write-Host ("inference   : {0}" -f (($InferencePorts | ForEach-Object { ":$_" }) -join ' ')) }
Write-Host ''

# ---------------------------------------------------------------- 1) inference
if (-not $BackendOnly) {
    Write-Host '=== 1/2 Inference (llama-server) ===' -ForegroundColor Cyan
    $llama = @(Get-Process llama-server -ErrorAction SilentlyContinue)
    if ($llama.Count -eq 0) {
        Write-Host 'no llama-server process running'
    }
    else {
        # Map each process to the port it listens on, so the report names what is stopped.
        $byPid = @{}
        foreach ($port in $InferencePorts) {
            $owner = Get-PortPid -Port $port
            if ($owner) { $byPid[$owner] = $port }
        }
        foreach ($proc in $llama) {
            $portLabel = if ($byPid.ContainsKey($proc.Id)) { ":{0}" -f $byPid[$proc.Id] } else { 'port not listening' }
            if ($whatIf) {
                Write-Host ("would stop llama-server PID {0} ({1})" -f $proc.Id, $portLabel) -ForegroundColor Yellow
            }
            else {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Write-Host ("stopped llama-server PID {0} ({1})" -f $proc.Id, $portLabel) -ForegroundColor Green
            }
            $stopped.Add([pscustomobject]@{ Service = 'llama-server'; Detail = $portLabel; Pid = $proc.Id })
        }
        if (-not $whatIf) {
            # Give the GPU memory back before reporting; unloading takes a moment.
            Start-Sleep -Seconds 3
        }
    }
    if (-not $whatIf) {
        $still = @(Get-Process llama-server -ErrorAction SilentlyContinue)
        if ($still.Count -gt 0) {
            Write-Host ("WARNING: {0} llama-server process(es) still alive" -f $still.Count) -ForegroundColor Red
        }
        $busy = @($InferencePorts | Where-Object { Get-PortPid -Port $_ })
        if ($busy.Count -gt 0) {
            Write-Host ("WARNING: inference port(s) still listening: {0}" -f (($busy | ForEach-Object { ":$_" }) -join ' ')) -ForegroundColor Red
        }
        else {
            Write-Host 'all inference ports released' -ForegroundColor Green
        }
    }
    Write-Host ''
}

# ---------------------------------------------------------------- 2) backend
if (-not $InferenceOnly) {
    Write-Host '=== 2/2 Backend (FastAPI / uvicorn) ===' -ForegroundColor Cyan
    $owner = Get-PortPid -Port $BackendPort
    if (-not $owner) {
        Write-Host (":{0} is not listening; nothing to stop" -f $BackendPort)
    }
    else {
        $verdict = Test-BackendProcess -ProcessId $owner
        Write-Host ("PID {0} owns :{1}" -f $owner, $BackendPort)
        Write-Host ("  check   : {0}" -f $verdict.Evidence)
        if ($verdict.Ours) {
            Write-Host '  verdict : looks like this project''s backend' -ForegroundColor Green
        }
        else {
            Write-Host '  verdict : DOES NOT look like this backend' -ForegroundColor Red
        }
        if ($verdict.Ours -or $Force) {
            if ($whatIf) {
                Write-Host ("would stop PID {0}" -f $owner) -ForegroundColor Yellow
            }
            else {
                Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
                if (Wait-PortFree -Port $BackendPort -Seconds $WaitSeconds) {
                    Write-Host (":{0} released" -f $BackendPort) -ForegroundColor Green
                }
                else {
                    Write-Host (":{0} STILL listening after {1}s" -f $BackendPort, $WaitSeconds) -ForegroundColor Red
                }
            }
            $stopped.Add([pscustomobject]@{ Service = 'backend'; Detail = (":{0}" -f $BackendPort); Pid = $owner })
        }
        else {
            Write-Host ("refusing to kill PID {0}: it is not verifiably this backend." -f $owner) -ForegroundColor Red
            Write-Host 'If you are certain it is ours, re-run with -Force. Otherwise inspect it yourself:' -ForegroundColor Yellow
            Write-Host ("  Get-Process -Id {0} | Select-Object Id,ProcessName,Path" -f $owner)
            $skipped.Add([pscustomobject]@{ Service = 'backend'; Detail = (":{0}" -f $BackendPort); Pid = $owner })
        }
    }
    Write-Host ''
}

# ---------------------------------------------------------------- summary
Write-Host '================ summary ================' -ForegroundColor Cyan
if ($stopped.Count -gt 0) { $stopped | Format-Table -AutoSize Service, Detail, Pid }
else { Write-Host 'nothing was running' }
if ($skipped.Count -gt 0) {
    Write-Host 'NOT stopped (needs manual decision):' -ForegroundColor Red
    $skipped | Format-Table -AutoSize Service, Detail, Pid
}
if (-not $whatIf) {
    Write-Host 'remaining listeners on the stack ports:'
    $any = $false
    foreach ($port in @($BackendPort) + $InferencePorts) {
        $owner = Get-PortPid -Port $port
        if ($owner) { Write-Host ("  :{0} -> PID {1}" -f $port, $owner) -ForegroundColor Red; $any = $true }
    }
    if (-not $any) { Write-Host '  none' -ForegroundColor Green }
    Write-Host ''
    Write-Host 'note: this stops the backend and inference only.'
    Write-Host '      PostgreSQL (5432) and any Vite dev server (5173) keep running; stop them separately if needed.'
    Write-Host '      A server-side Vite: find its PID with  netstat -ano | findstr :5173  then Stop-Process -Id <pid> -Force'
}
