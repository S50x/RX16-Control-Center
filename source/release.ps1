# Publishes the built installer as a GitHub Release (the installed app checks these for updates).
# Order:  source\build.ps1  ->  source\build-setup.ps1  ->  git commit + tag v<ver>  ->  this script
#   powershell -ExecutionPolicy Bypass -File source\release.ps1 -Notes "what changed"
param([Parameter(Mandatory = $true)][string]$Notes)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$gh   = 'C:\Program Files\GitHub CLI\gh.exe'
$ver  = [regex]::Match((Get-Content "$root\app\x16power.pyw" -Raw), 'VERSION = "([\d.]+)"').Groups[1].Value
$setup = Join-Path $root "dist\RX16 Control Center Setup $ver.exe"
if (-not (Test-Path $setup)) { throw "missing $setup - run build-setup.ps1 first" }
if ((Get-Item $setup).VersionInfo.FileVersion -ne "$ver.0") { throw "setup file version doesn't match $ver" }

Push-Location $root
try {
    if (git status --porcelain) { throw "uncommitted changes - commit (and tag v$ver) first" }
    if (-not (git tag --list "v$ver")) { throw "tag v$ver missing" }
    git push origin main --follow-tags
    $sha = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
    $body = "$Notes`n`n---`nSHA256: $sha"
    $tmp = Join-Path $env:TEMP 'rx16-release-notes.md'
    [IO.File]::WriteAllText($tmp, $body, (New-Object Text.UTF8Encoding $false))
    & $gh release create "v$ver" $setup --title "RX16 Control Center $ver" --notes-file $tmp --verify-tag
    "released v$ver  sha256 $sha"
} finally { Pop-Location }
