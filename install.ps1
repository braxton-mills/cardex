<#
  One-time setup for the Campi timelapse service:
   - service venv (.venv-service) with opencv-python-headless, numpy, psutil
   - rife-ncnn-vulkan in tools\ (if missing)
   - Task Scheduler task "CampiTimelapse" (install-task.ps1, admin prompt): runs without logon,
     at startup + logon + 5-minute watchdog, no console window
   - `campi` command in %USERPROFILE%\.local\bin
   - with -Sightings: the optional vehicle sightings worker's own venv
     (%USERPROFILE%\CampiTimelapse\venv-sightings: CUDA torch, ultralytics, open_clip, transformers) and its
     model weights (several GB). Then set [sightings] enabled = true in config.toml and run `campi restart`.
#>
param([switch]$Sightings)
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

if ($Sightings) {
    # Separate env so the timelapse env never changes. torch first, from the CUDA 12.8 index (RTX 50-series
    # needs cu128+); the later install then sees torch as satisfied instead of pulling the CPU build from PyPI.
    $sv = Join-Path $env:USERPROFILE 'CampiTimelapse\venv-sightings'
    $spy = Join-Path $sv 'Scripts\python.exe'
    if (-not (Test-Path $spy)) { uv venv $sv --python 3.12 }
    uv pip install --python $spy torch torchvision --index-url https://download.pytorch.org/whl/cu128
    uv pip install --python $spy ultralytics lap open_clip_torch 'transformers[sentencepiece]' opencv-python-headless
    # OpenVINO backend (Intel UHD 770: detection + SigLIP vision; NNCF for INT8) and the optional Gemini client
    uv pip install --python $spy openvino nncf google-genai
    # ultralytics depends on opencv-python, which clashes with the headless build (same cv2 module)
    uv pip uninstall --python $spy opencv-python
    uv pip install --python $spy --reinstall opencv-python-headless
    # One-time model prep for the configured backend (openvino: YOLO26 FP16/INT8 export, SigLIP 2 vision IR,
    # label text embeddings) and a device report
    & $spy -m campi_timelapse sightings-worker --check
    if ($LASTEXITCODE -ne 0) { throw 'sightings check failed (see above)' }
}

# The task runs whether or not you are logged on, which needs admin rights to register.
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    "`"$Project\install-task.ps1`"", "$env:USERDOMAIN\$env:USERNAME"

$bin = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
Set-Content (Join-Path $bin 'campi.cmd') "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$Project\campi.ps1`" %*" -Encoding ascii
Write-Host "installed $bin\campi.cmd  (try: campi status)"
