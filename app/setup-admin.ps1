# RX16 Control Center - one-time admin setup (run elevated)
#  1) tests RyzenAdj (writes ryzenadj-test.txt)
#  2) registers a logon task that runs the service with highest privileges (no UAC prompt each time)
#  3) restarts the service elevated
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\RX16Power\app
$root = Split-Path -Parent $dir                             # ...\RX16Power (where the exe lives)
$log = Join-Path $dir 'setup-admin.log'
"=== $(Get-Date) ===" | Out-File $log -Encoding utf8

# 1) RyzenAdj test
$ra = Join-Path $dir 'ryzenadj\ryzenadj.exe'
if (Test-Path $ra) {
    Push-Location (Split-Path $ra)
    & $ra --info *>&1 | Out-File (Join-Path $dir 'ryzenadj-test.txt') -Encoding utf8
    "ryzenadj exit: $LASTEXITCODE" | Out-File $log -Append -Encoding utf8
    Pop-Location
}

# 2) logon task with highest privileges
$exe = Join-Path $root 'RX16 Control Center.exe'
if (Test-Path $exe) {
    # native host: own name in notifications, restarts the service after updates
    $action = New-ScheduledTaskAction -Execute $exe -Argument '--background' -WorkingDirectory $root
} else {
    $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    $action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $dir 'x16power.pyw')`" --background" -WorkingDirectory $dir
}
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'RX16 Control Center' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force *>&1 | Out-File $log -Append -Encoding utf8

# old non-elevated startup shortcut is replaced by the task
Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'X16 Power.lnk') -ErrorAction SilentlyContinue

# shortcuts always point at THIS folder (so renaming/moving the folder only needs this file run again)
if (Test-Path $exe) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'RX16 Control Center.lnk'))
    $lnk.TargetPath = $exe; $lnk.Arguments = ''; $lnk.WorkingDirectory = $root; $lnk.IconLocation = "$exe,0"; $lnk.Save()
    "desktop shortcut -> $exe" | Out-File $log -Append -Encoding utf8
}
Remove-Item (Join-Path $env:APPDATA 'X16Power\open-window.lnk') -ErrorAction SilentlyContinue   # recreated by the app

# 3) restart service elevated
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47816/api/quit -ContentType 'application/json' -Body '{}' -TimeoutSec 3 | Out-Null } catch {}
Start-Sleep -Seconds 2
Get-Process 'RX16 Control Center' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName 'RX16 Control Center'
"done" | Out-File $log -Append -Encoding utf8
