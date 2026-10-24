$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\uia_tray3.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$chevron = -join (0x663E,0x793A,0x9690,0x85CF,0x7684,0x56FE,0x6807 | ForEach-Object { [char]$_ })

$trayCond = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
$trays = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trayCond)

foreach ($t in $trays) {
    $btnCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $chevron)
    $btn = $t.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    if ($btn) {
        try {
            $p = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $p.Invoke()
            $lines.Add('clicked chevron')
            Start-Sleep -Milliseconds 1500
        } catch { $lines.Add('invoke failed: ' + $_.Exception.Message) }
    }
}

# enumerate EVERY top level window, look for the flyout
$tops = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition)
foreach ($w in $tops) {
    try {
        $cn = $w.Current.ClassName
        $nm = $w.Current.Name
        if (-not $nm) { $nm = '' }
        $nm = ($nm -replace "`r?`n", ' // ')
        $lines.Add(("TOP class='{0}' name='{1}'" -f $cn, $nm))
        if ($cn -like '*CoreWindow*' -or $cn -like '*Xaml*' -or $cn -like '*Popup*') {
            $all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($e in $all) {
                try {
                    $en = $e.Current.Name
                    if (-not $en) { continue }
                    $en = ($en -replace "`r?`n", ' // ')
                    $lines.Add(("   EL class='{0}' name='{1}'" -f $e.Current.ClassName, $en))
                } catch {}
            }
        }
    } catch {}
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
