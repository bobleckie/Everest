# Everest launcher — one-click: starts backend + frontend, waits for both, opens browser.
# Usage: double-click the "Everest" desktop shortcut, or run .\start.ps1
#
# Behavior:
#   * If something is already listening on :8000 or :3000, ASKS before
#     killing it (so we don't surprise-kill a VS Code task).
#   * Validates preconditions (py, npm, node_modules, .env) up front
#     and tells the user what's missing instead of failing silently.
#   * Hits /healthz (added in #14) instead of /docs — faster, no
#     FastAPI internal warmup needed.

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

function Test-Port {
    param([int]$Port)
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($c) { return $c[0].OwningProcess } else { return $null }
}

function Stop-PortOwner {
    # NOTE: parameter is intentionally NOT named $Pid — that's a PowerShell
    # automatic, read-only variable (the current shell's process id) and
    # binding to it throws "Cannot overwrite variable Pid because it is
    # read-only or constant."
    param([int]$Port, [int]$ProcessId)
    try {
        Write-Host ("  Stopping PID {0} on port {1}" -f $ProcessId, $Port) -ForegroundColor DarkYellow
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    } catch {}
}

function Wait-ForUrl {
    param([string]$Url, [int]$TimeoutSec = 180)
    $end = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $end) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 1500
    }
    return $false
}

Write-Host "==== Everest launcher ====" -ForegroundColor Cyan

# ── Preconditions ───────────────────────────────────────────────────
$missing = @()
if (-not (Get-Command py -ErrorAction SilentlyContinue))   { $missing += "py launcher (Python 3.13)" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue))  { $missing += "npm (Node.js 18+)" }
if (-not (Test-Path (Join-Path $root "frontend\rfp-ui\node_modules"))) { $missing += "frontend\rfp-ui\node_modules - run 'npm install' there once" }
if (-not (Test-Path (Join-Path $root ".env"))) { $missing += ".env (copy from .env.example and fill in)" }
if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing prerequisites:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Press any key to close..." -ForegroundColor DarkGray
    [void][System.Console]::ReadKey($true)
    exit 1
}

# ── Detect already-running servers ─────────────────────────────────
$existing8000 = Test-Port 8000
$existing3000 = Test-Port 3000
if ($existing8000 -or $existing3000) {
    Write-Host ""
    Write-Host "Existing Everest processes detected:" -ForegroundColor Yellow
    if ($existing8000) { Write-Host ("  Port 8000 owned by PID {0}" -f $existing8000) -ForegroundColor Yellow }
    if ($existing3000) { Write-Host ("  Port 3000 owned by PID {0}" -f $existing3000) -ForegroundColor Yellow }
    Write-Host ""
    $resp = Read-Host "Kill them and restart? (y/N)"
    if ($resp -notmatch '^[yY]') {
        Write-Host "Leaving everything alone. Open http://localhost:3000 to use the existing servers." -ForegroundColor Cyan
        Start-Process "http://localhost:3000"
        Start-Sleep -Seconds 2
        exit 0
    }
    if ($existing8000) { Stop-PortOwner 8000 $existing8000 }
    if ($existing3000) { Stop-PortOwner 3000 $existing3000 }
    Start-Sleep -Seconds 1
}

# ── Start backend ──────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting backend (FastAPI :8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    "`$Host.UI.RawUI.WindowTitle='Everest Backend'; cd '$root'; py -m uvicorn app.main:app --port 8000 --host 127.0.0.1"
) | Out-Null

# ── Start frontend ─────────────────────────────────────────────────
# CI=true makes react-scripts non-interactive (no port prompts).
Write-Host "Starting frontend (React :3000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    "`$Host.UI.RawUI.WindowTitle='Everest Frontend'; cd '$root\frontend\rfp-ui'; `$env:BROWSER='none'; `$env:PORT='3000'; `$env:CI='true'; npm start"
) | Out-Null

# ── Wait + open browser ────────────────────────────────────────────
Write-Host ""
Write-Host "Waiting for backend (first boot ~90s, later ~6s)..." -ForegroundColor Cyan
if (Wait-ForUrl -Url "http://127.0.0.1:8000/healthz" -TimeoutSec 180) {
    Write-Host "  Backend ready: http://localhost:8000" -ForegroundColor Green
} else {
    Write-Host "  Backend did not respond in time. Check the 'Everest Backend' window." -ForegroundColor Red
}

Write-Host "Waiting for frontend..." -ForegroundColor Cyan
if (Wait-ForUrl -Url "http://127.0.0.1:3000" -TimeoutSec 180) {
    Write-Host "  Frontend ready: http://localhost:3000" -ForegroundColor Green
    Start-Process "http://localhost:3000"
} else {
    Write-Host "  Frontend did not respond in time. Check the 'Everest Frontend' window." -ForegroundColor Red
}

Write-Host ""
Write-Host "Everest is running. Close the 'Everest Backend' and 'Everest Frontend' windows to stop." -ForegroundColor Cyan
Write-Host "This launcher window will close in 5 seconds..." -ForegroundColor DarkGray
Start-Sleep -Seconds 5
