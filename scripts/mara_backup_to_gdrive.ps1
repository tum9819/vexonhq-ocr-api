# Mara backup -> Google Drive (G:) wrapper for Windows Task Scheduler.
# Runs scripts/backup.py, zips the result, drops it in the synced Google Drive
# folder (Google Drive Desktop auto-uploads), and prunes old zips.
#   -Mode db    : DB-only (daily; light egress)   -> G:\My Drive\Mara-Backups\daily-db
#   -Mode full  : DB + storage files (weekly)      -> G:\My Drive\Mara-Backups\weekly-full
param([ValidateSet("db","full")][string]$Mode = "db")

$ErrorActionPreference = "Stop"
$repo          = "C:\Users\rapee\vexonhq-ocr-api"
$base          = "G:\My Drive\Mara-Backups"
$dest          = if ($Mode -eq "db") { "$base\daily-db" } else { "$base\weekly-full" }
$retentionDays = if ($Mode -eq "db") { 14 } else { 60 }
$logDir        = "$repo\backups\logs"
$ts            = Get-Date -Format "yyyyMMdd_HHmmss"
$log           = "$logDir\backup_${Mode}_$ts.log"

New-Item -ItemType Directory -Force -Path $dest, $logDir | Out-Null

try {
    Set-Location $repo

    # Load .env into the process environment (backup.py reads these).
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $k = $line.Substring(0, $idx).Trim()
            $v = $line.Substring($idx + 1).Trim()
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }

    $py = if (Test-Path "$repo\.venv\Scripts\python.exe") { "$repo\.venv\Scripts\python.exe" } else { "python" }
    $tmp = "$repo\backups\_tmp_${Mode}_$ts"
    $pyArgs = @("scripts\backup.py", "--out", $tmp)
    if ($Mode -eq "db") { $pyArgs += "--skip-storage" }

    $out = & $py @pyArgs 2>&1
    $out | Out-File -FilePath $log -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "backup.py exited with code $LASTEXITCODE" }

    $produced = Get-ChildItem $tmp -Directory -ErrorAction Stop | Select-Object -First 1
    if (-not $produced) { throw "no backup folder produced under $tmp" }

    $zip = "$dest\mara-$Mode-$ts.zip"
    Compress-Archive -Path "$($produced.FullName)\*" -DestinationPath $zip -Force
    Remove-Item $tmp -Recurse -Force

    # Retention: drop old zips of this mode.
    Get-ChildItem $dest -Filter "mara-$Mode-*.zip" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$retentionDays) } | Remove-Item -Force
    # Prune logs older than 30 days.
    Get-ChildItem $logDir -Filter "backup_*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force

    $sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    "OK  $ts  mode=$Mode  ->  $zip  ($sizeMB MB)" | Out-File -FilePath $log -Append -Encoding utf8
    Write-Output "OK mode=$Mode -> $zip ($sizeMB MB)"
}
catch {
    "FAILED  $ts  mode=$Mode  :  $_" | Out-File -FilePath $log -Append -Encoding utf8
    Write-Output "FAILED mode=$Mode : $_"
    exit 1
}
