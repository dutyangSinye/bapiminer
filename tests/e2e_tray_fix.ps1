$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\e2e_tray_fix.txt'
$lines = New-Object System.Collections.Generic.List[string]

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WFix {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  public delegate bool EnumProc(IntPtr h, IntPtr p);

  public static string DumpMine(string title) {
    StringBuilder sb = new StringBuilder();
    int count = 0;
    EnumWindows(delegate(IntPtr h, IntPtr p) {
      StringBuilder t = new StringBuilder(512);
      GetWindowText(h, t, 512);
      StringBuilder c = new StringBuilder(256);
      GetClassName(h, c, 256);
      if (c.ToString().StartsWith("Chrome_WidgetWin") && IsWindowVisible(h)
          && t.ToString().Trim() == title) {
        uint pid; GetWindowThreadProcessId(h, out pid);
        count++;
        sb.AppendLine("    hwnd=" + h.ToInt64() + " pid=" + pid + " title=[" + t.ToString() + "]");
      }
      return true;
    }, IntPtr.Zero);
    sb.AppendLine("  COUNT=" + count);
    return sb.ToString();
  }
}
'@

$root = [System.Windows.Automation.AutomationElement]::RootElement
$chevron = -join (0x663E,0x793A,0x9690,0x85CF,0x7684,0x56FE,0x6807 | ForEach-Object { [char]$_ })
$mine   = -join (0x6252,0x76AE,0x77FF,0x5DE5 | ForEach-Object { [char]$_ })
$title  = -join (0x6252,0x76AE,0x77FF,0x5DE5,0x00B7,0x77FF,0x673A,0x7AEF,0x7AEF | ForEach-Object { [char]$_ })

function Get-Overflow {
    $c = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'TopLevelWindowForOverflowXamlIsland')
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
}
function Click-At([int]$x, [int]$y) {
    [WFix]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 400
    [WFix]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
    [WFix]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
}
function Open-Overflow {
    if (Get-Overflow) { return }
    $tc = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
    foreach ($t in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $tc)) {
        $bc = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::NameProperty, $chevron)
        $btn = $t.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $bc)
        if ($btn) {
            $r = $btn.Current.BoundingRectangle
            Click-At ([int]($r.X + $r.Width/2)) ([int]($r.Y + $r.Height/2))
            Start-Sleep -Milliseconds 1500
            return
        }
    }
}
function Find-Mine {
    $ov = Get-Overflow
    if (-not $ov) { return $null }
    $cond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button)
    foreach ($b in $ov.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)) {
        try {
            if ($b.Current.Name -and $b.Current.Name.StartsWith($mine)) { return $b }
        } catch {}
    }
    return $null
}

$lines.Add('=== BASELINE ===')
$lines.Add([WFix]::DumpMine($title))

for ($i = 1; $i -le 5; $i++) {
    $lines.Add("")
    $lines.Add("=== TRAY CLICK #$i ===")
    Open-Overflow
    $btn = Find-Mine
    if (-not $btn) { $lines.Add('  tray button NOT FOUND'); continue }
    $r = $btn.Current.BoundingRectangle
    $cx = [int]($r.X + $r.Width/2)
    $cy = [int]($r.Y + $r.Height/2)
    $lines.Add("  click at $cx,$cy")
    Click-At $cx $cy
    Start-Sleep -Seconds 3
    $lines.Add([WFix]::DumpMine($title))
}

[System.IO.File]::WriteAllLines($out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("wrote {0} lines" -f $lines.Count)
