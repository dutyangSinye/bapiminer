$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\uia_tray4.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$root = [System.Windows.Automation.AutomationElement]::RootElement
$chevron = -join (0x663E,0x793A,0x9690,0x85CF,0x7684,0x56FE,0x6807 | ForEach-Object { [char]$_ })

$trayCond = [System.Windows.Automation.PropertyCondition]::new(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
$trays = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trayCond)

$clicked = $false
foreach ($t in $trays) {
    $btnCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $chevron)
    $btn = $t.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    if ($btn) {
        $r = $btn.Current.BoundingRectangle
        $lines.Add(("chevron rect = {0},{1} {2}x{3}" -f $r.X, $r.Y, $r.Width, $r.Height))
        $cx = [int]($r.X + $r.Width / 2)
        $cy = [int]($r.Y + $r.Height / 2)
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($cx, $cy)
        Start-Sleep -Milliseconds 400
        Add-Type -MemberDefinition @'
[DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, IntPtr dwExtraInfo);
'@ -Name Mouse -Namespace Native
        [Native.Mouse]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
        [Native.Mouse]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
        $lines.Add("clicked chevron at $cx,$cy")
        $clicked = $true
        Start-Sleep -Milliseconds 1500
    }
}
if (-not $clicked) { $lines.Add('chevron not found') }

# enumerate all top level windows and dump interesting descendants
$tops = $root.FindAll([System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition)
foreach ($w in $tops) {
    try {
        $cn = $w.Current.ClassName
        $all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        $hits = @()
        foreach ($e in $all) {
            try {
                $en = $e.Current.Name
                if ($en) { $hits += ($e.Current.ClassName + ' :: ' + ($en -replace "`r?`n", ' / ')) }
            } catch {}
        }
        if ($hits.Count -gt 0) {
            $lines.Add("=== TOP class='$cn' name='$($w.Current.Name)' children=$($hits.Count)")
            foreach ($h in $hits) { $lines.Add("    $h") }
        }
    } catch {}
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
