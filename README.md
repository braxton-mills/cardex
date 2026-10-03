# Campi timelapse

Turns the Pi camera stream (`http://campi.local:8000/stream.mjpg`) into a 60 fps timelapse clip every 10 minutes
and one ~60 s video per day. All settings are in `config.toml`; after editing it, run `campi restart`.

| What | Where |
|---|---|
| 10-minute clips (kept 24 h) | `Downloads\campi-timelapse\campi_YYYY-MM-DD_HHMM.mp4` (HHMM = window start) |
| Newest clip | `Downloads\campi-timelapse\latest.mp4` |
| Daily videos (kept forever) | `Downloads\campi-timelapse\daily\campi_daily_YYYY-MM-DD.mp4` |
| Long archive (every clip appended, new part at 2 GB) | `Downloads\campi-timelapse\archive\campi_archive_001.mp4`, `_002`, ... |
| Raw frames (all for 48 h, then 1/min) | `%USERPROFILE%\CampiTimelapse\frames\YYYY-MM-DD\HH\` + `index.csv` |
| Logs | `%USERPROFILE%\CampiTimelapse\logs\` (`gaps.log` = capture gaps, `gpu.log` = RIFE GPU proof) |

## Commands
```
campi status        # running?, stream, last frame, last clip, disk
campi stop          # stops everything; stays stopped (even after reboot) until `campi start`
campi start
campi restart
campi logs [name] [-f]   # supervisor | capture | render | daily | housekeep | gaps | gpu
campi render-now    # render the last 10 minutes now
campi test          # render the last 1 minute
campi daily [YYYY-MM-DD]
campi samples       # before/after rotation samples from the live stream
campi archive       # append any clips not yet in the archive (runs automatically after every clip)
campi sightings [N] # optional vehicle sightings (see Sightings below)
```

Archive notes: appending is a stream copy (no re-encode), but MP4 can't grow in place, so each append rewrites
the current part (a few seconds). If a part is open in a player, the clip is appended after the next clip instead.
Clips are only appended in time order; a re-rendered older clip is not inserted. A clip with a different format
(e.g. a libx264 fallback) starts a new part.

## How it runs
Task Scheduler task `CampiTimelapse` (registered by `install-task.ps1`, needs admin) starts
`pythonw -m campi_timelapse run` at startup and at logon, plus a 5-minute watchdog trigger. It runs whether or not
anyone is logged on (S4U, no stored password), so capture resumes by itself after a Windows Update reboot. It runs in
the background session (session 0), where the Intel iGPU is Vulkan device 0, so `rife_gpu` selects the GPU by name. The supervisor keeps the capture process alive and launches each render and housekeeping pass as a
separate low-priority process, so a failed render never touches capture. All children belong to a Job Object, so
stopping the supervisor kills them too.

Pipeline: q90 JPEG snapshots from the Pi saved as-is -> drop night frames -> split at capture gaps (hard cuts) ->
level 9 deg and crop to 4:3 with no black corners (1600x1200 of the 1920x1440 frame) -> luminance deflicker ->
scale to 1440x1080 -> RIFE 2x on the RTX 5070 (rife-ncnn-vulkan, Vulkan) per segment -> timestamp ->
h264_nvenc CQ 19, yuv420p, bt709, +faststart.

Setup from scratch: `powershell -ExecutionPolicy Bypass -File install.ps1`.

## Pi side (campi)
- `~/timelapse/mjpeg_server_2.py` (source: `pi/mjpeg_server_2.py`): 1920x1440 from the full-view 2028x1520 sensor
  mode, `/stream.mjpg` (hardware MJPEG, 30 fps, quality HIGH; the Pi 4 encoder caps at 25 Mbps) and
  `/snapshot.jpg` (software JPEG, q90 by default, `?q=NN` to override), which the timelapse uses.
  1920 is the widest the Pi 4 hardware JPEG encoder can produce (2028 came out as a cropped 1920x1520).
- Runs as a systemd user service (no sudo; starts at boot because linger is enabled):
  `systemctl --user status|restart campi-stream` and `journalctl --user -u campi-stream`.
- If `campi.local` stops resolving or the Pi gets a new DHCP address, capture finds the Pi by its MAC
  (`stream.pi_mac`). It probes port 8000 on the local /24 and checks `arp -a`, then remembers the IP in
  `state\pi_host.json`. `campi status` shows the host in use.
- If the server is replaced by one without `/snapshot.jpg`, capture falls back to the MJPEG stream automatically.
- `pi/setup_campi_stream.sh` patches a stock server script and installs the service.

## Sightings (optional)
A separate worker that logs vehicles passing the camera: YOLO11m + ByteTrack on `/stream.mjpg`, SigLIP 2 zero-shot
make/model guess averaged over each vehicle's pass, **one row per pass**. Off by default; with
`[sightings] enabled = false` nothing starts and nothing extra is imported.

Enable:
1. `powershell -ExecutionPolicy Bypass -File install.ps1 -Sightings` creates its own env
   (`%USERPROFILE%\CampiTimelapse\venv-sightings`: CUDA 12.8 torch, ultralytics, open_clip, transformers), downloads
   the YOLO + SigLIP 2 weights (a few GB) and checks the GPU. The timelapse env is untouched.
2. Set `enabled = true` under `[sightings]` in `config.toml`, then `campi restart`.

| What | Where |
|---|---|
| Database (SQLite, WAL) | `%USERPROFILE%\CampiTimelapse\sightings\sightings.db` (table `sightings`, plus `schema_version`) |
| Media per sighting | `...\sightings\YYYY-MM-DD\HHMMSS_<id>_<label>_crop.jpg`, `_frame.jpg`, `.mp4` (paths in the DB are relative to `sightings\`) |
| Models | `...\sightings\models\` (YOLO), Hugging Face cache (SigLIP 2) |
| Log | `campi logs sightings [-f]`; GPU line also in `gpu.log` |
| Test runs | `%USERPROFILE%\CampiTimelapse\sightings-test\` (fresh each run) |

Commands:
```
campi sightings [N]                  # last N sightings (local time)
campi sightings-record SECONDS       # save the raw stream (.mjpg with timestamps) to sightings\recordings\
campi sightings-test --source FILE   # run the pipeline on a recording or any video into the test DB; prints rows
```
`campi status` shows the worker (disabled / running / CRASH-LOOPING), the last sighting and today's count.

How it behaves:
- Own child process of the supervisor (own venv, below-normal priority, 2 CPU threads, same Job Object). A crash
  or GPU error restarts it with backoff (10 s doubling to 10 min); a hang (no main-loop progress for 2 min) is killed.
  Capture and renders are separate processes and never wait on it. It refuses to run without CUDA.
- Reads the stream at ~30 fps (about 23 Mbit/s over the Pi's Wi-Fi), runs YOLO at `detect_fps`, uses the same Pi
  host lookup as capture (`stream.pi_mac`), pauses while the frame is darker than `[night] luma_threshold`.
- A pass ends `max(lost_after_s, post_roll_s)` after the vehicle was last seen. Tracker ID switches on fast cars
  are stitched back into one pass by position and velocity. Optional `roi` polygon: only vehicles whose center
  enters it count.
- Stationary vehicles (center moved < `stationary_px`) are logged once per spot, then not again until the spot has
  been empty for 10 minutes. Clips are capped at 60 s.
- Media is written before the row, so a row never points at a missing file. Housekeeping deletes clips older than
  `keep_clips_days` (and clears `clip_path`); crops, frames and rows are kept forever.
- Labels: `sightings_labels.txt` (`Make | Model` or a generic type per line). `car_id.py` is the original
  standalone script, kept for reference.
