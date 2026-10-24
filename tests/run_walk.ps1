# Kill leftover walk-edge processes, clean profile, relaunch, run node CDP script.
$prof = Join-Path $env:TEMP "bapi_walk_9351"
# kill edges whose command line references the walk profile (single-instance guard)
Get-CimInstance Win32_Process -Filter "name='msedge.exe'" |
  Where-Object { $_.CommandLine -like "*bapi_walk_9351*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
# keep the profile (deleting it triggers Edge first-run welcome page); process cleanup above
# is what actually fixes the single-instance takeover
if (Test-Path $prof) { Remove-Item -Recurse -Force "$prof/Default/Service Worker" -ErrorAction SilentlyContinue }

$cands = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
)
$edge = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { Write-Output "NO EDGE"; exit 1 }
$p = Start-Process -FilePath $edge -ArgumentList @(
  "--headless=new",
  "--remote-debugging-port=9351",
  "--user-data-dir=$prof",
  "--no-proxy-server",
  "--no-first-run",
  "--disable-features=msFirstRunExperience,msSyncPromo,EdgeSyncPromo",
  "--window-size=1440,1000",
  "about:blank"
) -PassThru
Write-Output "edge pid: $($p.Id)"
Start-Sleep -Seconds 3
& "C:\Users\Admin\.workbuddy\binaries\node\versions\22.22.2-6\node.exe" "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\walk_wallet_group.js" 9351 "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\wallet_group.png" $args[0]
Get-CimInstance Win32_Process -Filter "name='msedge.exe'" |
  Where-Object { $_.CommandLine -like "*bapi_walk_9351*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
