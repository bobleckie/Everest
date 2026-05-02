# ============================================================
# Everest RFP Platform - Restore Script
# ============================================================
# Restores rfp.db (and optionally chroma_db / uploads) from a backup
# folder. Defaults to DRY-RUN; pass -Apply to actually overwrite live
# files. Always takes a "before-restore" copy of the current rfp.db
# to db_backups/ first when -Apply is used.
#
# Usage:
#   # See what would change (safe, default):
#   powershell -File restore_db.ps1 -BackupDir "C:\path\to\backups"
#
#   # Actually restore (interactive confirmation required):
#   powershell -File restore_db.ps1 -BackupDir "C:\path\to\backups" -Apply
#
#   # Pick a specific timestamp instead of the latest:
#   powershell -File restore_db.ps1 -BackupDir "..." -Timestamp 2026-04-28_020000
#
# ============================================================

param (
    [Parameter(Mandatory=$true)]
    [string]$BackupDir,

    [string]$Timestamp = "",          # empty = pick most recent
    [switch]$Apply = $false,
    [switch]$IncludeChroma = $true,
    [switch]$IncludeUploads = $false  # uploads are big; opt-in only
)

$ErrorActionPreference = "Stop"
$AppRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$LiveDb    = Join-Path $AppRoot "rfp.db"
$LiveCh    = Join-Path $AppRoot "chroma_db"
$LiveUp    = Join-Path $AppRoot "uploads"
$SafeStash = Join-Path $AppRoot "db_backups"

if (-not (Test-Path $BackupDir)) {
    Write-Error "Backup folder not found: $BackupDir"; exit 1
}

# -- Pick a backup set ----------------------------------------
function Find-Latest($pattern) {
    Get-ChildItem -Path $BackupDir -Filter $pattern -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

if ($Timestamp) {
    $dbFile      = Join-Path $BackupDir "rfp_$Timestamp.db"
    $chromaFile  = Join-Path $BackupDir "chroma_$Timestamp.zip"
    $uploadsFile = Join-Path $BackupDir "uploads_$Timestamp.zip"
} else {
    $latest      = Find-Latest "rfp_*.db";       $dbFile      = if ($latest) { $latest.FullName } else { $null }
    $latest      = Find-Latest "chroma_*.zip";   $chromaFile  = if ($latest) { $latest.FullName } else { $null }
    $latest      = Find-Latest "uploads_*.zip";  $uploadsFile = if ($latest) { $latest.FullName } else { $null }
}

if (-not $dbFile -or -not (Test-Path $dbFile)) {
    Write-Error "No rfp_*.db backup found in $BackupDir"; exit 1
}

Write-Host ""
Write-Host "Restore plan:" -ForegroundColor Cyan
Write-Host "  rfp.db <- $dbFile"
if ($IncludeChroma -and $chromaFile -and (Test-Path $chromaFile)) {
    Write-Host "  chroma_db/ <- $chromaFile"
} else {
    Write-Host "  chroma_db/   (skipped)"
}
if ($IncludeUploads -and $uploadsFile -and (Test-Path $uploadsFile)) {
    Write-Host "  uploads/ <- $uploadsFile"
} else {
    Write-Host "  uploads/     (skipped)"
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "DRY RUN. Re-run with -Apply to actually restore." -ForegroundColor Yellow
    exit 0
}

# -- Safety stash of current live files -----------------------
New-Item -ItemType Directory -Force -Path $SafeStash | Out-Null
$stashTs = Get-Date -Format "yyyy-MM-dd_HHmmss"
$stash   = Join-Path $SafeStash "pre-restore-$stashTs"
New-Item -ItemType Directory -Force -Path $stash | Out-Null
if (Test-Path $LiveDb) {
    Write-Host "Stashing current rfp.db -> $stash" -ForegroundColor Yellow
    Copy-Item $LiveDb (Join-Path $stash "rfp.db") -Force
}
if ($IncludeChroma -and (Test-Path $LiveCh)) {
    Copy-Item $LiveCh (Join-Path $stash "chroma_db") -Recurse -Force
}

# -- Confirm with user ----------------------------------------
Write-Host ""
$ans = Read-Host "Type 'restore' to overwrite live files"
if ($ans -ne 'restore') {
    Write-Host "Aborted by user." -ForegroundColor Yellow
    exit 2
}

# -- Apply ----------------------------------------------------
Write-Host "Restoring rfp.db..." -ForegroundColor Cyan
Copy-Item $dbFile $LiveDb -Force

if ($IncludeChroma -and $chromaFile -and (Test-Path $chromaFile)) {
    Write-Host "Restoring chroma_db..." -ForegroundColor Cyan
    if (Test-Path $LiveCh) { Remove-Item $LiveCh -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $LiveCh | Out-Null
    Expand-Archive -Path $chromaFile -DestinationPath $LiveCh -Force
}

if ($IncludeUploads -and $uploadsFile -and (Test-Path $uploadsFile)) {
    Write-Host "Restoring uploads..." -ForegroundColor Cyan
    if (Test-Path $LiveUp) { Remove-Item $LiveUp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $LiveUp | Out-Null
    Expand-Archive -Path $uploadsFile -DestinationPath $LiveUp -Force
}

Write-Host ""
Write-Host "Restore complete." -ForegroundColor Green
Write-Host "Pre-restore stash: $stash"
Write-Host "Restart the backend to pick up the restored DB."
