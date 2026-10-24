$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\uia_tray2.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement

# Build the Chinese label from code points so this .ps1 stays pure ASCII.
$chevron = -join (0x663E,0x793A,0x9690,0x85CF,0x7684,0x56FE,0x6807 | ForEach-Object { [char]$_ })

function Walk($el, $tag) {
    try {
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button)
        $buttons = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($b in $buttons) {
            try {
                $n = $b.Current.Name
                if ($n) { $n = ($n -replace "`r?`n", ' // ') }
                $lines.Add(("{0} name='{1}' class='{2}'" -f $tag, $n, $b.Current.ClassName))
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
    $btnCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $chevron)
    $btn = $t.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    if ($btn) {
        try {
            $p = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $p.Invoke()
            $lines.Add('clicked show-hidden-icons')
            Start-Sleep -Milliseconds 1200
        } catch { $lines.Add('invoke failed: ' + $_.Exception.Message) }
    } else {
        $lines.Add('chevron button not found')
    }
}

$ovCond = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'NotifyIconOverflowWindow')
$ovs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $ovCond)
foreach ($o in $ovs) {
    $lines.Add('=== NotifyIconOverflowWindow ===')
    Walk $o 'overflow'
}

foreach ($t in $trays) {
    $lines.Add('=== Shell_TrayWnd visible ===')
    Walk $t 'visible'
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
