Unregister-ScheduledTask -TaskName 'OmniFlow Schedule Agent' -Confirm:$false -ErrorAction SilentlyContinue
Write-Host 'OmniFlow scheduled task removed. Existing jobs and workflows remain untouched.'
