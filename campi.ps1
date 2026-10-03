<#
  campi: control the Campi timelapse service.
    campi status          service, stream, last clip, disk
    campi start           start (and re-enable autostart watchdog)
    campi stop            stop everything; stays stopped until `campi start`
    campi restart
    campi run             run in this window with live logs (Ctrl+C stops); needs `campi stop` first
    campi logs [name] [-f]  name: supervisor|capture|render|daily|housekeep|gaps|gpu (default: summary)
    campi render-now      render the last 10 minutes right now
    campi test            render the last 1 minute
    campi daily [date]    render a daily video (default: yesterday)
    campi samples         save before/after rotation samples from the live stream
    campi archive         append any clips not yet in the long archive video
#>
param([Parameter(Position = 0)][string]$Command = 'status',
      [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest)

$Project = $PSScriptRoot
$Py = Join-Path $Project '.venv-service\Scripts\python.exe'
$PyW = Join-Path $Project '.venv-service\Scripts\pythonw.exe'
$TaskName = 'CampiTimelapse'
Set-Location $Project

$paths = & $Py -c "from campi_timelapse.config import load_config as l; c=l(); print(c.paths.state); print(c.paths.logs)"
$State, $Logs = $paths[0], $paths[1]

function Get-SupervisorPid {
    $s = Get-Content (Join-Path $State 'status.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    if ($s -and $s.supervisor_pid -and (Get-Process -Id $s.supervisor_pid -ErrorAction SilentlyContinue)) { return $s.supervisor_pid }
    return $null
}

function Start-Campi {
    Remove-Item (Join-Path $State 'disabled') -ErrorAction SilentlyContinue
    if (Get-SupervisorPid) { Write-Host 'already running'; return }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Start-Process $PyW -ArgumentList '-m', 'campi_timelapse', 'run' -WorkingDirectory $Project
    }
    for ($i = 0; $i -lt 15 -and -not (Get-SupervisorPid); $i++) { Start-Sleep 1 }
    if (Get-SupervisorPid) { Write-Host 'started' } else { Write-Host "did not start; see $Logs\supervisor.log" }
}

function Stop-Campi {
    New-Item -ItemType File -Force (Join-Path $State 'disabled') | Out-Null
    $p = Get-SupervisorPid
    if (-not $p) { Write-Host 'not running'; return }
    New-Item -ItemType File -Force (Join-Path $State 'stop.request') | Out-Null
    for ($i = 0; $i -lt 20 -and (Get-Process -Id $p -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
    if (Get-Process -Id $p -ErrorAction SilentlyContinue) {
        taskkill /T /F /PID $p | Out-Null
        Write-Host 'stopped (forced)'
    } else { Write-Host 'stopped' }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

switch ($Command) {
    'status' {
        & $Py -m campi_timelapse status
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host ("autostart    : " + $(if ($t) { "task '$TaskName' ($($t.State))" } else { 'NOT INSTALLED (run install.ps1)' }) +
            $(if (Test-Path (Join-Path $State 'disabled')) { ' - disabled by campi stop' } else { '' }))
    }
    'start' { Start-Campi }
    'run' {
        if (Get-SupervisorPid) { Write-Host 'the background service is running; run "campi stop" first'; exit 1 }
        Write-Host 'running in this window - Ctrl+C to stop'
        & $Py -m campi_timelapse run --foreground
    }
    'stop' { Stop-Campi }
    'restart' { Stop-Campi; Start-Campi }
    'logs' {
        $follow = $Rest -contains '-f'
        $name = $Rest | Where-Object { $_ -ne '-f' } | Select-Object -First 1
        if ($name) {
            $f = Join-Path $Logs "$name.log"
            if ($follow) { Get-Content $f -Tail 40 -Wait } else { Get-Content $f -Tail 60 }
        } else {
            foreach ($n in 'supervisor', 'capture', 'render', 'gaps', 'gpu') {
                $f = Join-Path $Logs "$n.log"
                if (Test-Path $f) { Write-Host "== $n ==" -ForegroundColor Cyan; Get-Content $f -Tail 8 }
            }
        }
    }
    'render-now' { & $Py -m campi_timelapse render-clip }
    'test' { & $Py -m campi_timelapse render-clip --minutes 1 }
    'daily' { if ($Rest) { & $Py -m campi_timelapse render-daily --date $Rest[0] } else { & $Py -m campi_timelapse render-daily } }
    'samples' { & $Py -m campi_timelapse samples }
    'archive' { & $Py -m campi_timelapse archive }
    default { Get-Help $PSCommandPath; exit 2 }
}
