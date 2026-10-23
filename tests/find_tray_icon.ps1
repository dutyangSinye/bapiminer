Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'Shell_TrayWnd')
$tray = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $tray) { Write-Output "NO_TRAY"; exit 1 }

$tcond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, 'TrayNotifyWnd')
$tn = $tray.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $tcond)
if (-not $tn) { Write-Output "NO_TRAYNOTIFY"; exit 1 }
$all = $tn.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$names = @()
foreach ($el in $all) {
  $n = $el.Current.Name
  $c = $el.Current.ClassName
  $aid = $el.Current.AutomationId
  $names += ($n + " <" + $c + "> id=" + $aid)
}
if ($names) { Write-Output ("TRAYNOTIFY: " + ($names -join ' | ')) } else { Write-Output "TRAYNOTIFY_EMPTY" }
# Win11: 图标可能挂在 Shell_TrayWnd 的 SystemTray 区域，把整个任务栏里 ClassName 带 Icon 的都列出来
$all2 = $tray.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all2) {
  $c = $el.Current.ClassName
  if ($c -like "*Icon*") {
    Write-Output ("ICONNODE: " + $el.Current.Name + " <" + $c + "> id=" + $el.Current.AutomationId)
  }
}
