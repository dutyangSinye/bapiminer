param()
$ErrorActionPreference = 'Stop'
$dir = 'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$i = 0
foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $i++
    $b = $s.Bounds
    $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $g.Dispose()
    $out = Join-Path $dir ("screen{0}.png" -f $i)
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output ("{0}: {1} x {2} at ({3},{4}) primary={5} -> {6}" -f $i, $b.Width, $b.Height, $b.X, $b.Y, $s.Primary, $out)
    $bmp.Dispose()
}
