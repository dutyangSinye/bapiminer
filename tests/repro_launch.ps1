$ErrorActionPreference = 'Stop'
$exe = 'D:\mine\BaPiMiner.exe'
$log = 'D:\mine\client.log'

Write-Output '--- BEFORE ---'
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'BaPiMiner*' } | ForEach-Object {
    "  pid=$($_.ProcessId) ppid=$($_.ParentProcessId) cmd=$($_.CommandLine)"
}
$before = (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'BaPiMiner*' }).Count
Write-Output "  process count = $before"

for ($i = 1; $i -le 3; $i++) {
    Write-Output "--- launch #$i ---"
    Start-Process -FilePath $exe -WorkingDirectory 'D:\mine'
    Start-Sleep -Seconds 6
    $p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'BaPiMiner*' }
    Write-Output ("  process count = " + $p.Count)
    $p | ForEach-Object { "    pid=$($_.ProcessId) ppid=$($_.ParentProcessId) cmd=$($_.CommandLine)" }
}

Write-Output '--- log tail ---'
Get-Content $log -Tail 14 | ForEach-Object { "  $_" }
