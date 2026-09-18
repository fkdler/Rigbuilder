<#
.SYNOPSIS
Restart only this checkout's verified backend listeners after deployment.
.DESCRIPTION
Run after approving a brief service interruption. Refuses unrelated listeners,
active jobs, or a database without the administrator workspace migration.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [ValidateSet('127.0.0.1', '0.0.0.0')][string]$BindAddress = '0.0.0.0'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$backendRoot = Join-Path $repoRoot 'backend'
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'

Push-Location $backendRoot
try {
    $check = @'
from app.db.session import engine
from sqlalchemy import select, func, text
from app.models import QueryJob
with engine.connect() as connection:
    revision = connection.execute(text('select version_num from alembic_version')).scalar()
    active = connection.execute(select(func.count()).select_from(QueryJob).where(
        QueryJob.status.in_(['queued', 'running', 'cancel_requested']))).scalar()
print('Schema:', revision, 'Active jobs:', active)
raise SystemExit(0 if revision == '0011_admin_workspace' and active == 0 else 1)
'@
    $check | & $pythonExe -
    if ($LASTEXITCODE -ne 0) { throw 'Restart refused: finish active jobs and apply migration 0011 first.' }

    $listenerIds = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    # Validate the entire set before stopping any process. A Python process alone
    # is not enough: its command must name this checkout's interpreter and app.
    $verified = @()
    foreach ($listenerId in $listenerIds) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerId"
        $command = $candidate.CommandLine
        if (!$command -or !$command.Contains($pythonExe) -or
            $command -notmatch '\buvicorn\s+app\.main:app\b' -or
            $command -notmatch ("--port\s+" + $Port + '(\s|$)')) {
            throw "Restart refused: listener $listenerId is not this checkout's backend."
        }
        $verified += $candidate
    }
    foreach ($candidate in $verified) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.ProcessId)"
        if (!$current -or $current.CreationDate -ne $candidate.CreationDate -or $current.CommandLine -ne $candidate.CommandLine) {
            throw 'Process identity changed; restart aborted.'
        }
        Stop-Process -Id $candidate.ProcessId -ErrorAction Stop
    }

    $deadline = (Get-Date).AddSeconds(15)
    while (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw 'The backend port is still in use; no new process was started.' }
        Start-Sleep -Milliseconds 250
    }
    $logDir = Join-Path $repoRoot '.local-logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $logDir "backend-$stamp.out.log"
    $stderr = Join-Path $logDir "backend-$stamp.err.log"
    $started = Start-Process -FilePath $pythonExe -ArgumentList @('-X', 'utf8', '-m', 'uvicorn', 'app.main:app', '--host', $BindAddress, '--port', $Port, '--workers', '1') -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-Output "Backend launcher PID: $($started.Id)"
    Write-Output "Startup log: $stderr"
    $deadline = (Get-Date).AddSeconds(60)
    do {
        try {
            $schema = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 3
            $paths = @($schema.paths.PSObject.Properties.Name)
            $missing = @('/api/admin/usage', '/api/admin/users', '/api/admin/catalog') | Where-Object { $_ -notin $paths }
            if (!$missing) { Write-Output 'Ready: all three administrator APIs are registered.'; return }
        } catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Backend did not become ready in 60 seconds. Inspect $stderr."
} finally {
    Pop-Location
}
