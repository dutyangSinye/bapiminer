$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\uia_tray5.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$root = [System.Windows.Automation.AutomationElement]::RootElement
$chevron = -join (0x663E,0x793A,0x9690,0x85CF,0x7684,0x56FE,0x6807 | ForEach-Object { [char]$_ })

function Get-Overflow {
    $c = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'TopLevelWindowForOverflowXamlIsland')
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
}

function Dump-Buttons($el, $tag) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    foreach ($b in $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)) {
        try {
            $n = $b.Current.Name
            if (-not $n) { $n = '' }
            $n = ($n -replace "`r?`n", ' / ')
            $lines.Add(("{0} name='{1}' class='{2}'" -f $tag, $n, $b.Current.ClassName))
        } catch {}
    }
}

# make sure overflow panel is OPEN (toggle only if closed)
$ov = Get-Overflow
if (-not $ov) {
    $trayCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
    $trays = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trayCond)
    foreach ($t in $trays) {
        $btnCond = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, $chevron)
        $btn = $t.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
        if ($btn) {
            $r = $btn.Current.BoundingRectangle
            $cx = [int]($r.X + $r.Width / 2)
            $cy = [int]($r.Y + $r.Height / 2)
            [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($cx, $cy)
            Start-Sleep -Milliseconds 400
            Add-Type -MemberDefinition @'
[DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
'@ -Name Mouse5 -Namespace Native5
            [Native5.Mouse5]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
            [Native5.Mouse5]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
            $lines.Add("clicked chevron at $cx,$cy")
            Start-Sleep -Milliseconds 1500
            break
        }
    }
}

$ov = Get-Overflow
if ($ov) {
    $lines.Add('=== OVERFLOW PANEL ===')
    Dump-Buttons $ov 'ovf'
} else {
    $lines.Add('overflow panel not present')
}

# visible tray buttons
$trayCond2 = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
foreach ($t in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trayCond2)) {
    $lines.Add('=== VISIBLE TRAY ===')
    Dump-Buttons $t 'vis'
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
