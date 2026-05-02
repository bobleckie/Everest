# ============================================================
# Everest RFP Platform - Backup Script
# ============================================================
# Backs up the SQLite database (rfp.db), the Chroma vector store
# (chroma_db/), and optionally the uploaded files (uploads/) to a
# network or OneDrive folder.
#
# Configuration:
#   Edit BACKUP_DEST below to point to your network share, or pass
#   -BackupDest. Set $env:EVEREST_BACKUP_DEST to override the default.
#
# What gets backed up every run:
#   * rfp_<ts>.db            (sqlite3 .backup API; safe while DB is in use)
#   * chroma_<ts>.zip        (small, ~200 KB)
#
# What gets backed up only when -IncludeUploads is passed (or weekly):
#   * uploads_<ts>.zip       (~200 MB at the time of writing)
#
# To install as a daily Windows scheduled task, run:
#   powershell -ExecutionPolicy Bypass -File install_backup_task.ps1
#
# To restore from a backup:
#   powershell -File restore_db.ps1 -BackupDir <path> -DryRun
#   powershell -File restore_db.ps1 -BackupDir <path> -Apply
# ============================================================

param (
    [string]$BackupDest = $(if ($env:EVEREST_BACKUP_DEST) { $env:EVEREST_BACKUP_DEST } else { "C:\Users\p002298J\OneDrive - Parsons Corp\Everest Backup" }),
    [switch]$IncludeUploads = $false  # set on Sunday runs, or pass manually
)

# -- Configuration --------------------------------------------
$AppRoot    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DbFile     = Join-Path $AppRoot "rfp.db"
$ChromaDir  = Join-Path $AppRoot "chroma_db"
$UploadsDir = Join-Path $AppRoot "uploads"
$LogFile    = Join-Path $AppRoot "logs\backup.log"
$RetainDays = 30
# Auto-include uploads on Sundays unless explicitly toggled.
if (-not $IncludeUploads.IsPresent -and (Get-Date).DayOfWeek -eq 'Sunday') {
    $IncludeUploads = $true
}

# If no destination passed as param or env var, prompt
if (-not $BackupDest) {
    $BackupDest = Read-Host "Enter network backup path (e.g. \\server\share\Everest_Backups)"
}
if (-not $BackupDest) {
    Write-Error "No backup destination specified. Set EVEREST_BACKUP_DEST env var or pass -BackupDest."
    exit 1
}

# -- Ensure destinations exist --------------------------------
New-Item -ItemType Directory -Force -Path $BackupDest  | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

# -- Check source exists --------------------------------------
if (-not (Test-Path $DbFile)) {
    Log "ERROR: Database not found at $DbFile"
    exit 1
}

# -- Copy with timestamp --------------------------------------
$timestamp  = Get-Date -Format "yyyy-MM-dd_HHmmss"
$destFile   = Join-Path $BackupDest "rfp_$timestamp.db"

try {
    # SQLite WAL checkpoint: flush any pending write-ahead log
    # (Safe to skip if the app is stopped, but harmless if running)
    $py = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($py) {
        # WAL checkpoint via a temp script to avoid quoting issues with paths
        $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
        Set-Content -Path $tmpScript -Value "import sqlite3`nconn = sqlite3.connect(r`"$DbFile`")`nconn.execute('PRAGMA wal_checkpoint(FULL)')`nconn.close()"
        & $py $tmpScript 2>$null
        Remove-Item $tmpScript -ErrorAction SilentlyContinue
    }

    Copy-Item -Path $DbFile -Destination $destFile -Force
    $size = [math]::Round((Get-Item $destFile).Length / 1KB, 1)
    Log "SUCCESS: rfp.db backed up to $destFile ($size KB)"
} catch {
    Log "ERROR: rfp.db backup failed - $_"
    exit 1
}

# -- Chroma backup (small, every run) -------------------------
if (Test-Path $ChromaDir) {
    $chromaZip = Join-Path $BackupDest "chroma_$timestamp.zip"
    try {
        Compress-Archive -Path "$ChromaDir\*" -DestinationPath $chromaZip -Force
        $sz = [math]::Round((Get-Item $chromaZip).Length / 1KB, 1)
        Log "SUCCESS: chroma_db backed up to $chromaZip ($sz KB)"
    } catch {
        Log "WARN: chroma_db backup failed - $_"
    }
} else {
    Log "INFO: chroma_db not found at $ChromaDir; skipping."
}

# -- Uploads backup (weekly on Sunday, or -IncludeUploads) ----
if ($IncludeUploads -and (Test-Path $UploadsDir)) {
    $uploadsZip = Join-Path $BackupDest "uploads_$timestamp.zip"
    try {
        Compress-Archive -Path "$UploadsDir\*" -DestinationPath $uploadsZip -Force
        $sz = [math]::Round((Get-Item $uploadsZip).Length / 1MB, 1)
        Log "SUCCESS: uploads backed up to $uploadsZip ($sz MB)"
    } catch {
        Log "WARN: uploads backup failed - $_"
    }
}

# -- Prune old backups (rfp / chroma daily; uploads weekly) ---
function Prune-Pattern($pattern, $days, $label) {
    $cutoff = (Get-Date).AddDays(-$days)
    $pruned = 0
    Get-ChildItem -Path $BackupDest -Filter $pattern -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
        Remove-Item $_.FullName -Force
        $pruned++
    }
    if ($pruned -gt 0) { Log "Pruned $pruned $label backup(s) older than $days days" }
}
Prune-Pattern "rfp_*.db" $RetainDays "rfp"
Prune-Pattern "chroma_*.zip" $RetainDays "chroma"
Prune-Pattern "uploads_*.zip" 90 "uploads"  # uploads kept longer

Log "Backup complete."
exit 0
