# Builds the installer: dist\RX16 Control Center Setup <version>.exe
# Run build.ps1 first (so the exe is current), then:  powershell -ExecutionPolicy Bypass -File source\build-setup.ps1
#   -Preview  builds a non-admin copy (no requireAdministrator) only for looking at the wizard pages.
param([switch]$Preview)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$ver  = [regex]::Match((Get-Content "$root\app\x16power.pyw" -Raw), 'VERSION = "([\d.]+)"').Groups[1].Value

# ---- stage exactly what gets installed (no source code, no logs, no caches)
$stage = Join-Path $env:TEMP 'rx16-setup-stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Copy-Item "$root\RX16 Control Center.exe", "$root\اقرأني.txt", "$root\تفعيل صلاحيات المدير.bat" $stage
Copy-Item "$root\app" "$stage\app" -Recurse
Get-ChildItem "$stage\app" -Recurse -Include '__pycache__', '*.log', 'ryzenadj-test.txt' -Force |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$payload = Join-Path $env:TEMP 'rx16-payload.zip'
if (Test-Path $payload) { Remove-Item $payload -Force }
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $payload, [IO.Compression.CompressionLevel]::Optimal, $false)

# ---- compile
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$fw  = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'
New-Item -ItemType Directory -Force "$root\dist" | Out-Null
$out = if ($Preview) { Join-Path $env:TEMP 'rx16-setup-preview.exe' } else { Join-Path $root "dist\RX16 Control Center Setup $ver.exe" }
$manifest = if ($Preview) { Join-Path $here 'app.manifest' } else { Join-Path $here 'setup.manifest' }
& $csc /nologo /target:winexe /optimize+ "/out:$out" "/win32icon:$root\app\rx16.ico" "/win32manifest:$manifest" `
    "/resource:$payload,payload.zip" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Core.dll `
    "/r:$fw\System.IO.Compression.dll" "/r:$fw\System.IO.Compression.FileSystem.dll" "/r:$fw\System.Management.dll" `
    (Join-Path $here 'Setup.cs')
if ($LASTEXITCODE -ne 0) { throw "build failed" }
"built: $out  ({0:N1} MB, payload {1:N1} MB, {2} files)" -f ((Get-Item $out).Length / 1MB), ((Get-Item $payload).Length / 1MB),
    (Get-ChildItem $stage -Recurse -File).Count
