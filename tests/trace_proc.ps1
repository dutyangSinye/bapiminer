# WMI 进程跟踪：记录 BaPiMiner 相关进程的启动/退出事件（含退出码与父 PID）
# 需落盘为 ASCII 脚本由 powershell.exe -File 执行
$log = "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\proc_trace.txt"
"=== trace start $(Get-Date -Format HH:mm:ss) ===" | Out-File -Encoding UTF8 $log

# 启动目标
Start-Process "D:\mine\BaPiMiner.exe" -WorkingDirectory "D:\mine"
"launched $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding UTF8 $log

$job = Start-Job -ScriptBlock {
    # 后台 job 注册 WMI 事件，退出/启动都记
    $q = @()
    Register-CimIndicationEvent -Query "SELECT * FROM Win32_ProcessStopTrace WHERE ProcessName='BaPiMiner.exe'" -SourceIdentifier StopEvt -ErrorAction SilentlyContinue | Out-Null
    Register-CimIndicationEvent -Query "SELECT * FROM Win32_ProcessStartTrace WHERE ProcessName='BaPiMiner.exe'" -SourceIdentifier StartEvt -ErrorAction SilentlyContinue | Out-Null
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 160) {
        $s = Wait-Event -Timeout 2
        foreach ($e in $s) {
            $t = "{0:mm\:ss}" -f $sw.Elapsed
            "$t $($e.SourceIdentifier) PID=$($e.SourceEventProperties['ProcessID']) Parent=$($e.SourceEventProperties['ParentProcessID']) ExitCode=$($e.SourceEventProperties['ExitStatus'])" | Out-File -Append -Encoding UTF8 "C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\proc_trace.txt"
            Remove-Event -EventIdentifier $e.EventIdentifier
        }
    }
}

Start-Sleep -Seconds 165
Get-Job | Stop-Job | Out-Null
Get-Job | Remove-Job -Force | Out-Null
"=== trace end $(Get-Date -Format HH:mm:ss) ===" | Out-File -Append -Encoding UTF8 $log
Get-Content $log
