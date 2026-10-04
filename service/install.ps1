<#
  One-time setup for the Campi timelapse service:
   - config.toml copied from config.example.toml if missing (edit it afterwards)
   - service venv (.venv-service) with opencv-python-headless, numpy, psutil
   - rife-ncnn-vulkan in tools\ (if missing)
   - Task Scheduler task "CampiTimelapse" (install-task.ps1, admin prompt): runs without logon,
     at startup + logon + 5-minute watchdog, no console window
   - `campi` command in %USERPROFILE%\.local\bin
   - with -Sightings: the optional vehicle sightings worker's own venv
     (%USERPROFILE%\CampiTimelapse\venv-sightings: CUDA torch, ultralytics, open_clip, transformers) and its
     model weights (several GB). Then set [sightings] enabled = true in config.toml and run `campi restart`.
   - with -UI: the desktop app's and the API's own venv (%USERPROFILE%\CampiTimelapse\venv-ui: fastapi, uvicorn,
     pywebview, pillow, httpx[http2], pyjwt[crypto], qrcode), a WebView2 runtime check, and a Start Menu shortcut
     "Campi" that runs `campi ui` without a console window. Set [api] enabled = true to run the API for the iPhone
     app under the service (see README "API").
   - with -Meshes: the optional 3D card scans' own venv (%USERPROFILE%\CampiTimelapse\venv-mesh: CUDA torch,
     TripoSR checked out under CampiTimelapse\tools\TripoSR, rembg, trimesh, PyMCubes). Weights (about 1.7 GB)
     download on the first pass. Then set [cards] meshes_enabled = true and run `campi restart`.
#>
param([switch]$Sightings, [switch]$UI, [switch]$Meshes)
$ErrorActionPreference = 'Stop'
$Project = $PSScriptRoot
Set-Location $Project

if (-not (Test-Path 'config.toml')) {
    Copy-Item 'config.example.toml' 'config.toml'
    Write-Warning "created config.toml from config.example.toml: edit it (stream URL, folders), then run campi restart"
}

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

if ($UI) {
    # Separate env so the timelapse env never changes; only `campi ui` and the API (`campi_timelapse api`) use it.
    # pillow: /live.jpg; httpx[http2] + pyjwt[crypto]: APNs push; qrcode: `campi pair`
    $uv = Join-Path $env:USERPROFILE 'CampiTimelapse\venv-ui'
    $upy = Join-Path $uv 'Scripts\python.exe'
    if (-not (Test-Path $upy)) { uv venv $uv --python 3.12 }
    uv pip install --python $upy fastapi uvicorn pywebview pillow 'httpx[http2]' 'pyjwt[crypto]' qrcode
    # The window is Edge WebView2 (preinstalled on Windows 11); `campi ui --browser` works without it
    $wv = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $ver = foreach ($k in "HKLM:\SOFTWARE\WOW6432Node\$wv", "HKLM:\SOFTWARE\$wv", "HKCU:\Software\$wv") {
        $v = (Get-ItemProperty $k -ErrorAction SilentlyContinue).pv
        if ($v -and $v -ne '0.0.0.0') { $v }
    }
    if ($ver) { Write-Host "WebView2 runtime $(@($ver)[0])" }
    else { Write-Warning 'WebView2 runtime not found: winget install Microsoft.EdgeWebView2Runtime (or use campi ui --browser)' }
    # Start Menu shortcut: pythonw, so no console window
    $lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'Campi.lnk'
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $uv 'Scripts\pythonw.exe'
    $sc.Arguments = '-m campi_timelapse ui'
    $sc.WorkingDirectory = $Project
    $sc.IconLocation = Join-Path $Project 'campi_timelapse\ui\static\campi.ico'
    $sc.Description = 'Campi: sightings, timelapse clips, daily videos and highlights'
    $sc.Save()
    Write-Host "Start Menu shortcut: $lnk"
}

if ($Meshes) {
    # TripoSR (image -> 3D mesh) for the Cards tab. Its own env: it needs transformers 4.x (TripoSR's weights use the
    # 4.x ViT names) and would fight the sightings env. torchmcubes doesn't build for the RTX 50 series on Windows,
    # so card_meshes swaps in PyMCubes and nothing is compiled.
    $mv = Join-Path $env:USERPROFILE 'CampiTimelapse\venv-mesh'
    $mpy = Join-Path $mv 'Scripts\python.exe'
    $tsr = Join-Path $env:USERPROFILE 'CampiTimelapse\tools\TripoSR'
    if (-not (Test-Path $tsr)) { git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR $tsr }
    if (-not (Test-Path $mpy)) { uv venv $mv --python 3.12 }
    uv pip install --python $mpy torch torchvision --index-url https://download.pytorch.org/whl/cu128
    uv pip install --python $mpy 'transformers==4.46.3' 'rembg[gpu]' onnxruntime trimesh PyMCubes omegaconf einops huggingface_hub scipy pillow
    Write-Host 'mesh env ready: campi meshes --limit 1 tries it (TripoSR downloads on the first run)'
}

# The task runs whether or not you are logged on, which needs admin rights to register.
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    "`"$Project\install-task.ps1`"", "$env:USERDOMAIN\$env:USERNAME"

$bin = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
Set-Content (Join-Path $bin 'campi.cmd') "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$Project\campi.ps1`" %*" -Encoding ascii
Write-Host "installed $bin\campi.cmd  (try: campi status)"
