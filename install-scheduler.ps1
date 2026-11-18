# Run after setup.bat, as the Windows user who owns the emulators.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$agent = Join-Path $root 'schedule_agent.py'
if (!(Test-Path $python)) { throw 'Run setup.bat first.' }
$name = 'OmniFlow Schedule Agent'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $agent + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description 'OmniFlow local MuMu automation scheduler' -Force | Out-Null
Write-Host "Installed task '$name'. Runs under your Windows session at next logon."
Write-Host 'Use start-scheduler.vbs to launch it now; do not start multiple agents manually.'
