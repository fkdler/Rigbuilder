param(
    [Parameter(Mandatory=$true)][string]$Release,
    [Parameter(Mandatory=$true)][string]$BackupFile,
    [Parameter(Mandatory=$true)][string]$BackupSha256,
    [string]$ReportDirectory = "v3-cutover-reports"
)

$ErrorActionPreference = "Stop"
$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $BackendRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Alembic = Join-Path $RepoRoot ".venv\Scripts\alembic.exe"
$ReleasePath = (Resolve-Path $Release).Path
$BackupPath = (Resolve-Path $BackupFile).Path
$ReportDirectory = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $ReportDirectory))
$ActualHash = (Get-FileHash -LiteralPath $BackupPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualHash -ne $BackupSha256.ToLowerInvariant()) { throw "Backup SHA-256 mismatch; refusing cutover." }
if (-not (Test-Path -LiteralPath (Join-Path $BackendRoot ".env"))) { throw "backend/.env is missing." }
New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null

Push-Location $BackendRoot
try {
    & $Python -m scripts.import_v3 --release $ReleasePath --validate-only --report (Join-Path $ReportDirectory "00-release-validation.json")
    if ($LASTEXITCODE -ne 0) { throw "offline Release validation failed" }
    & $Python -m scripts.preflight_v3 --report (Join-Path $ReportDirectory "01-preflight.json")
    if ($LASTEXITCODE -ne 0) { throw "preflight_v3 failed" }
    & $Alembic -x allow_truth_rebuild=true upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed" }
    & $Python -m scripts.import_v3 --release $ReleasePath --dry-run --report (Join-Path $ReportDirectory "02-import-dry-run.json")
    if ($LASTEXITCODE -ne 0) { throw "V3 dry-run failed" }
    & $Python -m scripts.import_v3 --release $ReleasePath --apply --report (Join-Path $ReportDirectory "03-import-apply.json")
    if ($LASTEXITCODE -ne 0) { throw "V3 apply failed" }
    & $Python -m scripts.create_agent_readonly --apply
    if ($LASTEXITCODE -ne 0) { throw "readonly role update failed" }
    & $Python -m scripts.check_v3 --strict --report (Join-Path $ReportDirectory "04-check.json")
    if ($LASTEXITCODE -ne 0) { throw "check_v3 failed" }
    & $Python -m scripts.import_v3 --release $ReleasePath --dry-run --report (Join-Path $ReportDirectory "05-idempotency.json")
    if ($LASTEXITCODE -ne 0) { throw "idempotency dry-run failed" }
} finally {
    Pop-Location
}

Write-Host "Truth V3 cutover completed. Reports: $ReportDirectory"
