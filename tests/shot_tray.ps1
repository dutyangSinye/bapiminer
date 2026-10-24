param()
$ErrorActionPreference = 'Stop'
$out = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\tray_shot.png'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$screen = [System.Windows.Forms.Screen]::PrimaryScreen
if (-not $screen) { Write-Output 'no screen'; exit 1 }
$bounds = $screen.Bounds
Write-Output ("screen {0}x{1}" -f $bounds.Width, $bounds.Height)

$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$g.Dispose()

# crop bottom-right corner (notification area)
$cropW = [Math]::Min(700, $bounds.Width)
$cropH = [Math]::Min(120, $bounds.Height)
$rect = New-Object System.Drawing.Rectangle(($bounds.Width - $cropW), ($bounds.Height - $cropH), $cropW, $cropH)
$crop = $bmp.Clone($rect, $bmp.PixelFormat)
$crop.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$crop.Dispose()
$bmp.Dispose()
Write-Output ("saved {0}" -f $out)
