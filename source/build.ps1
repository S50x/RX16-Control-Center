# Builds "RX16 Control Center.exe" with the C# compiler that ships with Windows (.NET Framework 4.8).
# Run again after changing RX16.cs / Overlay.cs:  powershell -ExecutionPolicy Bypass -File source\build.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
Add-Type -AssemblyName System.Drawing

# ---- multi-size icon (PNG frames) -> rx16.ico
function New-Frame([int]$s) {
    $bmp = New-Object System.Drawing.Bitmap $s, $s, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
    $r = [single]($s * 0.22)
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddArc(0, 0, $r*2, $r*2, 180, 90); $p.AddArc($s-$r*2-1, 0, $r*2, $r*2, 270, 90)
    $p.AddArc($s-$r*2-1, $s-$r*2-1, $r*2, $r*2, 0, 90); $p.AddArc(0, $s-$r*2-1, $r*2, $r*2, 90, 90); $p.CloseFigure()
    $br = New-Object System.Drawing.Drawing2D.LinearGradientBrush (New-Object System.Drawing.Point 0,0), (New-Object System.Drawing.Point $s,$s), ([System.Drawing.Color]::FromArgb(255,74,80)), ([System.Drawing.Color]::FromArgb(179,15,22))
    $g.FillPath($br, $p)
    $f = New-Object System.Drawing.Font 'Segoe UI', ([single]($s*0.42)), ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object System.Drawing.StringFormat; $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'
    $g.DrawString('RX', $f, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, ([single]($s*0.03)), $s, $s), $sf)
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream; $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
    return ,$ms.ToArray()
}
$sizes = 16, 20, 24, 32, 40, 48, 64, 128, 256
$frames = $sizes | % { ,(New-Frame $_) }
$ico = New-Object System.IO.MemoryStream; $w = New-Object System.IO.BinaryWriter $ico
$w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]; $len = $frames[$i].Length
    $w.Write([byte]($(if ($s -ge 256) {0} else {$s}))); $w.Write([byte]($(if ($s -ge 256) {0} else {$s})))
    $w.Write([byte]0); $w.Write([byte]0); $w.Write([uint16]1); $w.Write([uint16]32)
    $w.Write([uint32]$len); $w.Write([uint32]$offset); $offset += $len
}
foreach ($fr in $frames) { $w.Write($fr) }
[IO.File]::WriteAllBytes((Join-Path $root 'app\rx16.ico'), $ico.ToArray())

# ---- compile
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$fw  = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
$out = Join-Path $root 'RX16 Control Center.exe'
$wv  = Join-Path $root 'app\webview2'   # WebView2 SDK (Microsoft, from NuGet)
& $csc /nologo /target:winexe /optimize+ "/out:$out" "/win32icon:$(Join-Path $root 'app\rx16.ico')" "/win32manifest:$(Join-Path $here 'app.manifest')" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll "/r:$fw\System.Web.Extensions.dll" "/r:$wv\Microsoft.Web.WebView2.Core.dll" "/r:$wv\Microsoft.Web.WebView2.WinForms.dll" (Join-Path $here 'RX16.cs') (Join-Path $here 'Overlay.cs') (Join-Path $here 'Window.cs')
if ($LASTEXITCODE -ne 0) { throw "build failed" }
"built: $out"
