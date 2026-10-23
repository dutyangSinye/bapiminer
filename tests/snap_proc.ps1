# 秒级 PID 快照：观察 BaPiMiner 进程何时消失/是否被重启（ASCII only）
$log = "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\proc_snap.txt"
Set-Content -Path $log -Value "=== snap start $(Get-Date -Format HH:mm:ss) ===" -Encoding UTF8
Start-Process "D:\mine\BaPiMiner.exe" -WorkingDirectory "D:\mine"
Add-Content $log "launched $(Get-Date -Format HH:mm:ss)" -Encoding UTF8
$prev = ""
$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 180) {
    $pids = (Get-Process BaPiMiner -ErrorAction SilentlyContinue |
             ForEach-Object { "$($_.Id)($($_.MainWindowTitle))" }) -join ","
    if ($pids -ne $prev) {
        Add-Content $log ("[{0:mm\:ss}] {1}" -f $sw.Elapsed, $(if ($pids) { $pids } else { "NONE" })) -Encoding UTF8
        $prev = $pids
    }
    Start-Sleep -Milliseconds 800
}
Add-Content $log "=== end ===" -Encoding UTF8
Get-Content $log
