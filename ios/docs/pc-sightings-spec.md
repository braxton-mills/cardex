Retarget the sightings worker so it never touches the RTX 5070 (I game on it), and make rendering game-aware. Today the worker (campi_timelapse/sightings.py, sightings_db.py, the supervisor's check_sightings, install.ps1 -Sightings) runs YOLO11m @1280 + the full SigLIP 2 model on CUDA. Read sightings.py, sightings_db.py, supervisor.py, render.py, rife.py, __main__.py, campi.ps1, install.ps1 and config.toml first, and keep their patterns: Writer ordering (media before row), heartbeat + hang detection, track stitching/splitting, stationary spots, night pause, record/test file formats.

HARD RULES
- With [sightings] enabled = false, nothing changes from today.
- Capture, render output, daily, archive and housekeeping behave exactly as today, except the render queue and game deferral under RENDERS.
- With backend = "openvino" the worker never uses the NVIDIA GPU: the supervisor launches it with CUDA_VISIBLE_DEVICES set to empty, no torch CUDA calls, no NVENC.
- Keep the current CUDA path working as backend = "cuda" so I can A/B both on the same recording.

HARDWARE
- i7-12700K: Intel UHD Graphics 770 (OpenVINO device "GPU", Ultralytics device "intel:gpu"), no NPU, Quick Sync for encoding.
- On startup, log OpenVINO's available devices and the full name of the one in use, to sightings.log and gpu.log like gpu_report does now. Prove the Intel GPU works from session 0 under the scheduled task. If it doesn't, fall back to CPU (2 inference threads) and show CPU FALLBACK in campi status.

ENVIRONMENT
- install.ps1 -Sightings adds openvino, nncf and google-genai to the existing venv-sightings. Keep the CUDA torch build there for backend = "cuda".
- sightings-worker --check reports the backend and device and does the one-time model prep below.

DETECTION (backend = openvino)
- YOLO26s (detect_model) exported to OpenVINO under sightings\models\, running on the UHD 770, tracked with the existing ByteTrack setup (track_buffer still sized from lost_after_s and detect_fps).
- ROI crop: if roi is set, crop each frame to the ROI's bounding box (plus a small margin) before detection, run at detect_imgsz, and map boxes back to full-frame coordinates so stitching, spots, the ROI test and crops work unchanged. Empty roi = full frame.
- detect_precision: fp16 | int8 | auto. INT8 is calibrated on ~300 recent daytime frames from CampiTimelapse\frames, ROI-cropped. auto picks INT8 only if sightings-bench shows it holds detect_fps and the A/B below shows no meaningful drop.
- YOLO26 is NMS-free: verify on a recording that a van boxed as both car and bus still ends up as one track (the duplicate-ID split in detect() should catch it).
- Classification must never starve detection: use OpenVINO's model priority hint or separate infer requests.

CLASSIFICATION (backend = openvino)
- Convert only the SigLIP 2 vision tower to OpenVINO IR (FP16) and run it on the UHD 770, reproducing open_clip's preprocessing exactly. Verify parity on 50 real crops: OpenVINO vs torch-CPU embedding cosine >= 0.99, and report top-1 label agreement.
- Compute the label text embeddings once on CPU with the full model (same PROMPTS averaging as today). Cache them under sightings\models\ keyed by labels-file hash + model name + prompts, rebuild automatically when sightings_labels.txt changes, and never load the text tower in the running worker.
- Stop scoring crops during the pass. While a vehicle is in view, keep its best classify_crops crops (largest box first, ties broken by sharpness via Laplacian variance, only crops >= min_crop_px). When the pass ends, score them on a background classify thread with today's area-weighted averaging, then hand the result to the Writer. min_samples now means candidate crops collected.
- Log ms per crop. Heartbeat and campi status show the classify queue depth.

CLOUD SECOND OPINION (off by default)
- With [sightings.cloud] enabled = true, send a sighting to Gemini when SigLIP's top share < unsure_below, or top-1 minus top-2 < cloud_margin, or it's the first-ever sighting of that label.
- Send the best crop resized so its longest side is 384 px, plus SigLIP's top 3 labels. Get JSON via structured output: make, model, year_range, color, confidence, in_candidates. Use the lowest thinking setting the model allows. One request at a time, 10 s timeout.
- API key from api_key_file (outside the repo; {home} expanded like other paths). Never log it.
- Daily cap cloud_max_per_day, counted in the DB and reset at local midnight. Over the cap, keep SigLIP's answer.
- The row is written with SigLIP's answer first (unchanged Writer flow). The request waits in a cloud_queue table with retry/backoff, surviving restarts and internet outages, and the row is updated when the answer arrives. Don't rename media files when the label changes.
- If the cloud names a car not in sightings_labels.txt, record it in a discovered_labels table (label, make, model, first_seen, count).

DATABASE (sightings_db.py stays stdlib-only)
- Schema v2 migration, run by the worker, safe on the live DB: add year_range, color, source ('siglip' | 'cloud'), siglip_label, siglip_confidence to sightings. Backfill existing rows with source='siglip', siglip_label=label, siglip_confidence=confidence. Add the cloud_queue and discovered_labels tables.
- format_rows: add a G flag for rows decided by the cloud.

CLIPS
- With backend = openvino, encode sighting clips with h264_qsv (Quick Sync, probed like nvenc_available), libx264 fallback. Never NVENC on this backend.

RENDERS + GAMING MODE
- Render queue: when a 10-minute window closes while a render is running or deferred, queue it instead of skipping it. Render queued windows one at a time, oldest first, so archive appends and latest.mp4 stay in order. Drop a queued window only once its frames are older than retention.raw_hours, with a log line.
- Game detection, every 15 s in the supervisor (psutil): a process counts as a game when its exe is under a game_dirs folder or matches extra_exes, it isn't in ignore_exes (glob patterns, case-insensitive), and it has used the NVIDIA GPU's 3D engine above gpu_busy_pct for at least gpu_busy_s (Windows "GPU Engine" performance counters, per pid). If those counters can't be read from session 0, fall back to the path match alone and say so in campi status.
- While a game runs:
  - defer_renders: if rife_gpu matches the NVIDIA GPU, clip and daily renders wait (capture never pauses) and resume 2 minutes after the last game exits.
  - pause_sightings: stop the sightings worker while gaming (off by default, since the worker no longer touches the 5070).
  Log these as "paused: gaming" / "resumed", never as crashes, and don't grow the restart backoff.
- campi game on | off | auto: manual override, stored in state\ and read by the supervisor.
- campi rife-bench: render the same recent 10-minute window with rife_gpu = "NVIDIA" and with rife_gpu = "Intel" into the work folder (not Downloads, latest.mp4 or the archive) and report wall time for each. Don't change the configured rife_gpu; I'll decide.

CONFIG (new keys and sections with these defaults; existing keys unchanged)
[sightings]
backend = "openvino"           # openvino (UHD 770, never the 5070) | cuda (current behavior)
detect_model = "yolo26s"
detect_imgsz = 640
detect_precision = "auto"      # fp16 | int8 | auto
ov_device = "GPU"              # OpenVINO device; CPU fallback is automatic
classify_crops = 5

[sightings.cloud]
enabled = false
model = "gemini-3.1-flash-lite"
api_key_file = "{home}\\CampiTimelapse\\secrets\\gemini.key"
cloud_margin = 0.15
cloud_max_per_day = 400

[gaming]
auto = true
game_dirs = [
  "C:\\Program Files (x86)\\Steam\\steamapps\\common",
  "D:\\SteamLibrary\\steamapps\\common",
  "C:\\Program Files\\Epic Games",
  "D:\\Epic Games",
  "D:\\EA Games",
  "D:\\Rockstar Games",
  "C:\\XboxGames",
  "C:\\Riot Games",
]
extra_exes = []
ignore_exes = ["wallpaper32.exe", "wallpaper64.exe", "Launcher.exe", "LauncherPatcher.exe", "RockstarService.exe",
               "SocialClubHelper.exe", "EADesktop.exe", "EABackgroundService.exe", "EpicGamesLauncher.exe",
               "EasyAntiCheat*.exe", "UnityCrashHandler*.exe", "CrashReportClient.exe"]
gpu_busy_pct = 20              # a matching process must use the 5070's 3D engine above this ...
gpu_busy_s = 30                # ... for this long before it counts as a game
defer_renders = true
pause_sightings = false

CLI (wire new commands into campi.ps1)
- campi status adds: sightings backend + device (or CPU FALLBACK), classify queue depth, cloud calls today / cap, gaming state + which exe triggered it, render queue length + oldest window.
- campi sightings-bench: detection FPS for fp16 and int8 at detect_imgsz, ms per SigLIP crop, device names.
- campi sightings-test --source FILE [--backend cuda|openvino] [--cloud]: also prints per-stage timings.
- campi rife-bench and campi game on|off|auto.

DONE WHEN (show me each)
- With enabled = false: campi restart, campi status and campi test behave as before.
- sightings-worker --check and sightings-bench output from the UHD 770.
- SigLIP parity numbers (cosine and top-1 agreement).
- A/B on one daytime recording (sightings-record 600): rows from --backend cuda vs --backend openvino, with matched passes, label agreement, and passes only one of them found.
- With backend = openvino under the scheduled task, Task Manager's GPU engine column never shows the worker on the NVIDIA GPU, and nvidia-smi lists no process from it.
- --cloud updates at least one row and respects the cap; with the network unplugged, requests queue and drain afterward.
- Running a game from one of the game_dirs defers renders and closing it catches them up in order (log lines + archive order); a launcher sitting idle in the tray does not trigger gaming mode.
- rife-bench times for both GPUs.
- README updated with the new sections, commands and benchmark results.
