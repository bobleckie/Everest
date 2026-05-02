# Creates a Desktop shortcut "Everest" that launches start.ps1.
# Run once:  .\install_shortcut.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Everest.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$root\start.ps1`""
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 1  # Normal
$shortcut.Description = "Launch Everest (FastAPI backend + React frontend)"

# Use the PowerShell icon as a default; swap to a custom .ico if you have one
$shortcut.IconLocation = "powershell.exe,0"

$shortcut.Save()

Write-Host "Created shortcut: $lnkPath" -ForegroundColor Green
Write-Host "Double-click 'Everest' on your desktop to launch." -ForegroundColor Cyan
