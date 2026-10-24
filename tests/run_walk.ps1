# Launch headless Edge with isolated profile for CDP walk, then run node script.
$cands = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
)
$edge = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { Write-Output "NO EDGE"; exit 1 }
$prof = Join-Path $env:TEMP "bapi_walk_9351"
$p = Start-Process -FilePath $edge -ArgumentList @(
  "--headless=new",
  "--remote-debugging-port=9351",
  "--user-data-dir=$prof",
  "--no-proxy-server",
  "--no-first-run",
  "--window-size=1440,1000",
  "about:blank"
) -PassThru
Write-Output "edge pid: $($p.Id)"
Start-Sleep -Seconds 3
& "C:\Users\Admin\.workbuddy\binaries\node\versions\22.22.2-6\node.exe" "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\walk_wallet_group.js" 9351 "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\wallet_group.png" $args[0]
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
