Add-Type -AssemblyName System.Drawing
$ico = [System.Drawing.Icon]::ExtractAssociatedIcon("C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\client\dist\BaPiMiner.exe")
$bmp = $ico.ToBitmap()
$bmp.Save("C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\brand\export\_exe_icon.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output ("ICON " + $bmp.Width + "x" + $bmp.Height)
