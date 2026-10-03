<#
  Registers the "CampiTimelapse" scheduled task (needs an elevated PowerShell; install.ps1 asks for it).
  Runs whether or not you are logged on (S4U: no stored password; local network only, which is all it needs),
  so capture resumes after a Windows Update reboot without anyone logging in.
  Triggers: at startup, at logon, and a 5-minute watchdog.
#>
$ErrorActionPreference = 'Stop'
$Project = $PSScriptRoot
$user = if ($args[0]) { $args[0] } else { "$env:USERDOMAIN\$env:USERNAME" }

$action = New-ScheduledTaskAction -Execute (Join-Path $Project '.venv-service\Scripts\pythonw.exe') `
    -Argument '-m campi_timelapse run' -WorkingDirectory $Project
$atStartup = New-ScheduledTaskTrigger -AtStartup
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Watchdog: relaunch within 5 min if the supervisor ever dies (ignored while it is running).
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName 'CampiTimelapse' -Action $action -Trigger $atStartup, $atLogon, $watchdog `
    -Settings $settings -Principal $principal -Force `
    -Description 'Campi Pi camera timelapse (capture + 10-minute clips + daily video); runs without logon' | Out-Null
Write-Host "registered scheduled task CampiTimelapse (S4U, at startup + logon + watchdog) for $user"
