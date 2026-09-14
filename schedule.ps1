<#
    Register (or remove) the Gmail agent as a Windows scheduled task.

        .\schedule.ps1                 every 2 hours, 120-day window
        .\schedule.ps1 -Hours 6        every 6 hours
        .\schedule.ps1 -Days 180       widen the lookback
        .\schedule.ps1 -Remove         unregister

    Runs as you, in your own session, so the OAuth token in token.json is
    readable. No admin rights needed and no password is stored.
#>
[CmdletBinding()]
param(
    [int]    $Hours = 2,
    [int]    $Days  = 120,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'

$TaskName = 'GmailApplicationAgent'
$Root     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Python   = Join-Path $Root '.venv\Scripts\pythonw.exe'   # windowless

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

if (-not (Test-Path $Python)) {
    throw "Missing $Python. Create the venv first: uv venv .venv; uv pip install --python .venv -r requirements.txt"
}
if (-not (Test-Path (Join-Path $Root 'token.json'))) {
    Write-Warning "token.json not found. Run '.\.venv\Scripts\python.exe -m agent' once by hand to complete the Google sign-in, otherwise every scheduled run will fail silently."
}

$action = New-ScheduledTaskAction -Execute $Python `
                                  -Argument "-m agent --quiet --days $Days" `
                                  -WorkingDirectory $Root

# Repeat indefinitely, starting a few minutes from now so the first run is not
# racing whatever you are doing at the moment you register it.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
                                    -RepetitionInterval (New-TimeSpan -Hours $Hours)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Description "Reads Gmail, classifies recruiting mail, updates data/events.json for Cracker. Lookback: $Days days." `
                       -Force | Out-Null

Write-Host "Registered '$TaskName' - every $Hours hour(s), $Days-day lookback, as $env:USERNAME."
Write-Host "Run now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Inspect:  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "Remove:   .\schedule.ps1 -Remove"
