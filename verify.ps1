# verify.ps1 - VEXONHQ pre-push verification (P0.2, Session 24)
#
# Run this BEFORE every git push to catch syntax errors and endpoint
# regressions. Exits non-zero on any failure.
#
# Usage:
#   .\verify.ps1            # syntax check only (no deps needed)
#   .\verify.ps1 -Smoke     # syntax + live smoke tests vs deployed backend
#
# The smoke flag is normally run AFTER deploy to verify the live backend
# still serves all critical routes. Requires: pip install pytest requests

[CmdletBinding()]
param(
    [switch]$Smoke
)

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "VEXONHQ pre-push verify" -ForegroundColor Cyan
Write-Host "=======================" -ForegroundColor Cyan

# Resolve the Python interpreter. Prefer the project venv so the check never
# fails spuriously when the shell's PATH `python` is a bare/uv interpreter that
# lacks the project deps (cv2, pandas, supabase, ...). Falls back to PATH python.
$Py = "python"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $Py = $venvPy
    Write-Host "  python: $venvPy (.venv)" -ForegroundColor Gray
} else {
    Write-Host "  python: PATH default (no .venv found)" -ForegroundColor Gray
}

# ──────────────────────────────────────────────────────────
# 1. Python syntax check via compileall (built-in, no deps)
# ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/2] Python syntax check (compileall)..." -ForegroundColor Yellow

& $Py -m compileall -q -x "(\.venv|venv|__pycache__|\.claude|\.git|\.pytest_cache|node_modules)" .

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FAIL: syntax errors found above. NOT safe to push." -ForegroundColor Red
    exit 1
}

Write-Host "  OK: all .py files parse cleanly" -ForegroundColor Green

# ──────────────────────────────────────────────────────────
# 1b. Offline unit tests (pure logic, no network/DB — OPS-10 money-path guards)
# ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1b] Offline unit tests..." -ForegroundColor Yellow
& $Py -m pytest --version > $null 2> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  SKIP: pytest not installed (pip install pytest)" -ForegroundColor Yellow
} else {
    & $Py -m pytest tests/ --ignore=tests/test_smoke.py --ignore=tests/test_workflow.py -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "FAIL: offline unit tests failed. NOT safe to push." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: offline unit tests passed" -ForegroundColor Green
}

# ──────────────────────────────────────────────────────────
# 2. Optional live smoke tests (requires pytest + requests installed)
# ──────────────────────────────────────────────────────────
if ($Smoke) {
    Write-Host ""
    Write-Host "[2/2] Live smoke tests against deployed backend..." -ForegroundColor Yellow

    if (-not $env:BACKEND_URL) {
        Write-Host "  BACKEND_URL not set - defaulting to https://api.marastation.com" -ForegroundColor Gray
    } else {
        Write-Host "  BACKEND_URL = $env:BACKEND_URL" -ForegroundColor Gray
    }

    # Check pytest availability via python -m (avoids PATH issues)
    & $Py -m pytest --version > $null 2> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  SKIP: pytest not installed. To enable smoke tests run:" -ForegroundColor Yellow
        Write-Host "    pip install pytest requests" -ForegroundColor Yellow
    } else {
        & $Py -m pytest tests/test_smoke.py -v --tb=short
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "FAIL: smoke tests failed. Investigate before pushing." -ForegroundColor Red
            exit 1
        }
        Write-Host "  OK: smoke tests passed" -ForegroundColor Green
    }
} else {
    Write-Host ""
    Write-Host "[2/2] Skipping smoke tests" -ForegroundColor Gray
    Write-Host "  After pushing and deploy completes, run:" -ForegroundColor Gray
    Write-Host "    .\verify.ps1 -Smoke" -ForegroundColor Gray
}

Write-Host ""
Write-Host "READY: safe to push" -ForegroundColor Green
Write-Host ""
exit 0
