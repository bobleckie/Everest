# ============================================================
# Installs a daily Windows Task Scheduler job for rfp.db backup.
# Run once as Administrator from the Everest root folder.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install_backup_task.ps1 -BackupDest "\\server\share\Everest_Backups"
# ============================================================

param (
    [Parameter(Mandatory=$true)]
    [string]$BackupDest,

    [string]$RunTime = "02:00",   # 2 AM daily
    [string]$TaskName = "EverestRFP-DBBackup"
)

$ScriptPath = Join-Path $PSScriptRoot "backup_db.ps1"

if (-not (Test-Path $ScriptPath)) {
    Write-Error "backup_db.ps1 not found at $ScriptPath"
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`" -BackupDest `"$BackupDest`""

$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Run as current user (no password stored)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily backup of Everest RFP Platform SQLite database to network drive" `
    -Force

Write-Host ""
Write-Host "Scheduled task '$TaskName' installed successfully." -ForegroundColor Green
Write-Host "  Runs daily at $RunTime"
Write-Host "  Backs up to: $BackupDest"
Write-Host "  Log: $PSScriptRoot\logs\backup.log"
Write-Host ""
Write-Host "To run a backup NOW:  powershell -File backup_db.ps1 -BackupDest `"$BackupDest`""
Write-Host "To remove this task:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
