$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\uia_tray.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement

function Walk($el, $tag) {
    try {
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button)
        $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($b in $buttons) {
            try {
                $lines.Add(("{0} BUTTON name='{1}' class='{2}' pid={3}" -f $tag, $b.Current.Name, $b.Current.ClassName, $b.Current.ProcessId))
            } catch {}
        }
    } catch {
        $lines.Add(("{0} walk-error: {1}" -f $tag, $_.Exception.Message))
    }
}

$trayCond = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
$trays = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trayCond)
foreach ($t in $trays) {
    $lines.Add('=== Shell_TrayWnd ===')
    Walk $t 'visible'
}

$ovCond = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'NotifyIconOverflowWindow')
$ovs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $ovCond)
foreach ($o in $ovs) {
    $lines.Add('=== NotifyIconOverflowWindow ===')
    Walk $o 'overflow'
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
