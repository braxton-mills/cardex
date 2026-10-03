<#
  One-time setup for the Campi timelapse service:
   - service venv (.venv-service) with opencv-python-headless, numpy, psutil
   - rife-ncnn-vulkan in tools\ (if missing)
   - Task Scheduler task "CampiTimelapse" (install-task.ps1, admin prompt): runs without logon,
     at startup + logon + 5-minute watchdog, no console window
   - `campi` command in %USERPROFILE%\.local\bin
#>
$ErrorActionPreference = 'Stop'
$Project = $PSScriptRoot
Set-Location $Project

if (-not (Test-Path '.venv-service\Scripts\python.exe')) {
    uv venv .venv-service --python 3.12
}
uv pip install --python .venv-service\Scripts\python.exe opencv-python-headless numpy psutil

if (-not (Test-Path 'tools\rife-ncnn-vulkan\rife-ncnn-vulkan.exe')) {
    New-Item -ItemType Directory -Force tools | Out-Null
    $u = 'https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip'
    Invoke-WebRequest $u -OutFile tools\rife.zip
    Expand-Archive tools\rife.zip -DestinationPath tools -Force
    Rename-Item tools\rife-ncnn-vulkan-20221029-windows rife-ncnn-vulkan
    Remove-Item tools\rife.zip
}

# The task runs whether or not you are logged on, which needs admin rights to register.
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    "`"$Project\install-task.ps1`"", "$env:USERDOMAIN\$env:USERNAME"

$bin = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
Set-Content (Join-Path $bin 'campi.cmd') "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$Project\campi.ps1`" %*" -Encoding ascii
Write-Host "installed $bin\campi.cmd  (try: campi status)"
