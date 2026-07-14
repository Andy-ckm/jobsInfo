param([Parameter(Mandatory = $true)][string]$DropDir,[string]$At = "07:15")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path $DropDir | Out-Null
[Environment]::SetEnvironmentVariable("JOB_SOURCE_DROP_DIR", $DropDir, "User")
$taskName = "JobSourceLocalNode"
$script = Join-Path $PSScriptRoot "run_windows.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Authorized read-only BOSS and Liepin job collection node" -Force
Write-Host "Scheduled task installed: $taskName (daily $At)" -ForegroundColor Green
Write-Host "Output directory: $DropDir"
