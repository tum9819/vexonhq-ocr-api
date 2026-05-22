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

# ──────────────────────────────────────────────────────────
# 1. Python syntax check via compileall (built-in, no deps)
# ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/2] Python syntax check (compileall)..." -ForegroundColor Yellow

& python -m compileall -q -x "(\.venv|venv|__pycache__|\.claude|\.git|\.pytest_cache|node_modules)" .

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FAIL: syntax errors found above. NOT safe to push." -ForegroundColor Red
    exit 1
}

Write-Host "  OK: all .py files parse cleanly" -ForegroundColor Green

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
    & python -m pytest --version > $null 2> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  SKIP: pytest not installed. To enable smoke tests run:" -ForegroundColor Yellow
        Write-Host "    pip install pytest requests" -ForegroundColor Yellow
    } else {
        & python -m pytest tests/test_smoke.py -v --tb=short
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
