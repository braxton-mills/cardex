# Campi timelapse

Turns the Pi camera stream (`http://campi.local:8000/stream.mjpg`) into a 60 fps timelapse clip every 10 minutes
and one ~60 s video per day. All settings are in `config.toml`; after editing it, run `campi restart`.

| What | Where |
|---|---|
| 10-minute clips (kept 24 h) | `OneDrive\Videos\campi\campi_YYYY-MM-DD_HHMM.mp4` (HHMM = window start) |
| Newest clip | `OneDrive\Videos\campi\latest.mp4` |
| Daily videos (kept forever) | `OneDrive\Videos\campi\daily\campi_daily_YYYY-MM-DD.mp4` |
| Long archive (every clip appended, new part at 2 GB) | `Downloads\campi-timelapse\archive\campi_archive_001.mp4`, `_002`, ... (kept local: each append rewrites the part, too big to re-sync every 10 min; `[archive] dir`) |
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
campi game on|off|auto   # gaming mode override (see Gaming below)
campi rife-bench    # render one recent window with RIFE on the NVIDIA and the Intel GPU (work folder only)
campi ui [--browser] [--port N]   # desktop app for sightings, clips, daily videos (see UI below)
```

## Gaming and the render queue
Renders run RIFE (and NVENC) on the RTX 5070, so they wait while you play:
- **Render queue**: when a 10-minute window closes while a render is running or deferred, it is queued (never
  skipped) and rendered later, one at a time, oldest first, so the archive and `latest.mp4` stay in order. A queued
  window is dropped (with a log line) only once its frames are older than `retention.raw_hours`.
- **Game detection** (`[gaming]`, every 15 s): a process counts as a game when its exe is under one of `game_dirs`
  (or matches `extra_exes`), isn't in `ignore_exes` (launchers, anti-cheat, crash handlers; globs, case-insensitive),
  and has kept the RTX card's 3D engine above `gpu_busy_pct` for `gpu_busy_s`. GPU use per process comes from the
  same Windows "GPU Engine" counters as Task Manager; a launcher idling in the tray never qualifies. If the counters
  can't be read, it falls back to the path match and `campi status` says so.
- While a game runs (`defer_renders`, when `rife_gpu` is the NVIDIA card): clip and daily renders wait; capture
  never pauses. Renders resume 2 minutes after the last game exits and catch up in order. `pause_sightings`
  (off by default; the openvino worker doesn't touch the RTX card) also stops the sightings worker.
- `campi game on|off|auto` overrides detection (stored in `state\game_mode`). `campi status` shows the gaming state,
  which exe triggered it, and the render queue (length, oldest window). Log lines: `paused: gaming (...)`,
  `game ended`, `resumed: renders`.
- `campi rife-bench` renders the same recent window with `rife_gpu = "NVIDIA"` and `"Intel"` into
  `CampiTimelapse\work\rife-bench\` and prints both times; it doesn't change your config. Result: RTX 5070 61 s (RIFE 24.8 s), UHD 770 142 s (RIFE 105.5 s), both well inside the 10-minute window.

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
A separate worker that logs vehicles passing the camera: YOLO + ByteTrack on `/stream.mjpg`, SigLIP 2 zero-shot
make/model guess averaged over each vehicle's pass, **one row per pass**. Off by default; with
`[sightings] enabled = false` nothing starts and nothing extra is imported.

Backends (`[sightings] backend`):
- **`openvino`** (default): YOLO26s + SigLIP 2's vision tower on the **Intel UHD 770**, so the RTX 5070 is left for
  games. The worker is launched with `CUDA_VISIBLE_DEVICES` empty and picks the OpenVINO device by name; OpenVINO
  can see the RTX card too (as `GPU.1`), and an NVIDIA device is never used. If the Intel GPU can't be used, it
  falls back to the CPU (2 threads) and `campi status` says `CPU FALLBACK`. Clips are encoded with Quick Sync
  (`h264_qsv`, libx264 fallback), never NVENC.
- **`cuda`**: the original path, YOLO11m @1280 + the full SigLIP 2 on the RTX card (NVENC clips). Kept for A/B tests.

Enable:
1. `powershell -ExecutionPolicy Bypass -File install.ps1 -Sightings` creates its own env
   (`%USERPROFILE%\CampiTimelapse\venv-sightings`: CUDA 12.8 torch, ultralytics, open_clip, transformers, openvino,
   nncf, google-genai) and runs `sightings-worker --check`, the one-time model prep: YOLO26s exported to OpenVINO IR
   (FP16, plus INT8 calibrated on ~300 of your own daytime frames), SigLIP 2's vision tower as FP16 IR, and the
   label text embeddings (computed once on the CPU; rebuilt automatically when `sightings_labels.txt` changes).
   The timelapse env is untouched.
2. Set `enabled = true` under `[sightings]` in `config.toml`, then `campi restart`.

Detection: `detect_imgsz` 640; with a `roi`, each frame is cropped to the ROI's bounding box (+5%) before detection
and boxes are mapped back. `detect_precision = "auto"` uses INT8 only if `sightings-bench` showed it keeps up with
`detect_fps` and finds at least 97% of what FP16 finds (it writes `models\precision.json`).

Classification: while a vehicle is in view only its best `classify_crops` crops are kept (largest box first, then
sharpest); they are scored when the pass ends on a separate classify thread at low priority, so classification
never slows detection. `min_samples` counts collected crops.

Cloud second opinion (`[sightings.cloud]`, off by default): Gemini (`model`) is asked about a sighting when SigLIP's
top share is below `unsure_below`, the top two are within `cloud_margin`, or it's the first-ever row with that label.
The row is written with SigLIP's answer first; the request waits in the `cloud_queue` table (survives restarts and
outages, retries 30 s doubling to 1 h) and the row is updated when the answer arrives (`source = 'cloud'`, flag `G`;
SigLIP's answer stays in `siglip_label` / `siglip_confidence`; media is not renamed). Cars the cloud names that
aren't in the label list go to `discovered_labels`. At most `cloud_max_per_day` requests (counted in the DB, reset at
local midnight). The key is read from `api_key_file` (outside the repo) and never logged. Note: on the free tier
Google may use the requests (your car crops) to improve its models.

Benchmarks on this PC (i7-12700K, UHD 770 / RTX 5070):

| | |
|---|---|
| YOLO26s @640 on the UHD 770 | FP16 28.4 fps (35 ms), INT8 37.9 fps (26 ms) but only 93.6% of FP16's vehicles -> `auto` = FP16 |
| SigLIP 2 vision on the UHD 770 | 644 ms per crop (FP16; ~3 s per vehicle at 5 crops, after the pass) |
| OpenVINO vs torch-CPU embeddings (50 real crops) | cosine mean 0.9995, min 0.9937; top-1 label agreement 100% |
| A/B, 10-min daytime recording | 36 matched passes, label agreement 94% (cuda 39 rows, openvino 42). openvino: 2.9x realtime, detect 37 ms/frame, SigLIP 1.4 s/crop while detecting; cuda: 1.55x realtime, 74.5 ms/frame @1280, 67 ms/crop |
| RTX 5070 use by the openvino worker (150 s, all engines) | 0.0% (UHD 770 mean 57%); not listed by nvidia-smi |
| RIFE for one 10-min clip (300 -> 600 frames) | RTX 5070 61 s (RIFE 24.8 s), UHD 770 142 s (RIFE 105.5 s), both well inside the 10-minute window |

| What | Where |
|---|---|
| Database (SQLite, WAL, schema v2) | `%USERPROFILE%\CampiTimelapse\sightings\sightings.db` (`sightings`, `cloud_queue`, `discovered_labels`, `schema_version`) |
| Media per sighting | `...\sightings\YYYY-MM-DD\HHMMSS_<id>_<label>_crop.jpg`, `_frame.jpg`, `.mp4` (paths in the DB are relative to `sightings\`) |
| Models | `...\sightings\models\` (YOLO weights + OpenVINO IR, SigLIP vision IR, text embeddings, `precision.json`), Hugging Face cache (SigLIP 2) |
| Log | `campi logs sightings [-f]`; device line also in `gpu.log` |
| Test runs | `%USERPROFILE%\CampiTimelapse\sightings-test\<backend>\` (fresh each run) |

Commands:
```
campi sightings [N]                  # last N sightings (local time); flags U unsure, S stationary, C clip, G cloud
campi sightings-record SECONDS       # save the raw stream (.mjpg with timestamps) to sightings\recordings\
campi sightings-test --source FILE [--backend cuda|openvino] [--cloud]
                                     # pipeline on a recording or any video into a test DB; prints rows + timings
campi sightings-bench [--parity]     # detection fps fp16/int8, SigLIP ms/crop, OpenVINO vs torch parity
```
`campi status` shows the worker (disabled / running / paused: gaming / CRASH-LOOPING), its backend and device (or
CPU FALLBACK), the classify queue, cloud calls today / cap, the last sighting and today's count.

How it behaves:
- Own child process of the supervisor (own venv, below-normal priority, 2 CPU threads, same Job Object). A crash
  or GPU error restarts it with backoff (10 s doubling to 10 min); a hang (no main-loop progress for 2 min) is killed.
  Capture and renders are separate processes and never wait on it. The cuda backend refuses to run without CUDA.
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

## UI (optional)
`campi ui` opens a desktop window for browsing everything the service makes: sightings, 10-minute clips, daily
videos, the archive, highlights and the live camera. The service runs in the background session (session 0) and
can't show windows, so the UI is a separate, on-demand process in your desktop session. It only reads what the
service writes; closing the window ends it.

Install: `powershell -ExecutionPolicy Bypass -File install.ps1 -UI` creates its own env
(`%USERPROFILE%\CampiTimelapse\venv-ui`: fastapi, uvicorn, pywebview), checks for the Edge WebView2 runtime
(preinstalled on Windows 11; otherwise `winget install Microsoft.EdgeWebView2Runtime`) and adds a Start Menu
shortcut **Campi**. The service env is untouched.

Launch: the **Campi** Start Menu shortcut (no console window), or
```
campi ui                 # window (Edge WebView2); the console returns immediately
campi ui --browser       # in your default browser instead; Ctrl+C stops it
campi ui --port 8800     # default 8765; it listens on 127.0.0.1 only (--host to change, not recommended)
```
Launching it again brings the open window to the front.

What it never does: write to `state\`, `frames\`, the clips folder or the sightings media folder; open
`sightings.db` read-write (it uses `sightings_db.connect_ro`); keep a file open between requests. Video is served
in HTTP ranges, each read opening and closing the file within a few ms (delete access shared), so renders, the
archive append and housekeeping never wait on it. `latest.mp4` is never served (it resolves to the dated clip in
`state\latest.json`), nor is the archive part still being appended to (`part` in `state\archive.json`). Never
launching it changes nothing: only `campi ui` imports it, from its own env.

Its own data, in `%USERPROFILE%\CampiTimelapse\ui\`: `ui.db` (your stars, hidden sightings and label
corrections), `ui.log`, and `webview\` (the window's browser profile).

Views (sidebar; dark theme). Keys: `J`/`K` next/previous, `Space` play/pause, `S` star, `Enter` open, `Esc` close.
- **Today**: status strip (the same data as `campi status`, refreshed every 30 s: service, stream, last/next
  clip, sightings backend and device, Gemini calls, gaming, render queue, disk), the newest 10-minute clip playing,
  today's counts, the latest sightings and today's highlights.
- **Highlights**, newest first, filterable: *new catches* (first-ever sighting of a label), *rare* (labels seen 3
  times or fewer), *busiest windows* (the 3 ten-minute windows per day with the most moving vehicles), each day's
  *daily video*, and anything you *starred*.
- **Sightings**: card grid (crop, label, confidence, time, direction, years, color, and who decided the label:
  SigLIP, Gemini or You), filters for dates, make, class, source, unsure, parked, starred and hidden. A card opens
  the detail panel: crop, full frame, clip, runner-up guesses, **View in timelapse**, star, hide, and **correct
  label** (stored in `ui.db`; the API applies it everywhere, including counts and the collection).
- **View in timelapse** opens the 10-minute clip covering the car, paused on the frame captured during the pass:
  offset = usable frames in that window before the car / `output.base_fps`, the same layout `render_frames` uses
  (exact while the clip exists; its raw frames outlive it). Once the clip has expired it opens that day's daily
  video at the proportional position, marked *approximate*.
- **Collection**: every label in `sightings_labels.txt` as a tile, caught or not, with count, first and last seen,
  and a rarity tier computed from the counts (legendary 1, rare 2-3, uncommon 4-15, common more), plus cars Gemini
  named that aren't in the list (*discovered*). Progress bar: X of Y caught. A caught tile opens its sightings.
- **Timelapse**: the last 24 h of 10-minute clips grouped by hour, the daily videos and the archive parts (all
  playable except the part still being written), each with **Show in folder**.
- **Live**: the camera through `/live.mjpg`, leveled like the renders. Connected only while this view is showing
  and the window isn't minimized: every viewer is another full stream over the Pi's Wi-Fi.

With sightings off, the sightings views say so (and still show any history already recorded).

### API
The window uses a plain JSON API, meant to stay stable for other clients (an iPhone app later). Interactive docs:
`http://127.0.0.1:8765/api/docs`. Times are ISO 8601: `*_at` in UTC as stored, `*_local` with the PC's offset.
Lists are `{"items": [...], "next_cursor": ...}`; pass `cursor` (or `before` for highlights) to get the next page.

| Endpoint | Returns |
|---|---|
| `GET /api/status` | everything `campi status` shows, structured (`sightings.line` / `gaming.line` are the CLI's text), plus `sightings_enabled` and a few config values |
| `GET /api/sightings?from&to&make&class&source&label&hide_unsure&hide_stationary&starred&include_hidden&cursor&limit` | sightings, newest first (`from`/`to` are local dates; `source` = `siglip`, `cloud` or `user`) |
| `GET /api/sightings/{id}` | one sighting with `frame_url`, `clip_url` (null once expired), `runner_ups`, SigLIP's answer, cloud state, `seek_ts` |
| `POST /api/sightings/{id}/star`, `/hide` | body `{"value": true}`; returns the sighting |
| `POST /api/sightings/{id}/label` | body `{"label": "Honda Civic"}` (or `null` to undo); returns the sighting |
| `GET /api/today` | today's counts (total, by class, by label source, different labels, new catches) |
| `GET /api/collection` | `{caught, total, discovered, items: [{label, make, model, generic, discovered, count, tier, first_seen, last_seen}]}` |
| `GET /api/highlights?type=new_catch,rare,busy_window,daily,starred&before&limit` | highlight items `{type, types, at, title, sighting / window / daily / clip}` |
| `GET /api/clips?hours=24`, `GET /api/clips/newest` | 10-minute clips (start, end, size, sighting count, starred, `url`) |
| `GET /api/daily` | daily videos |
| `GET /api/archive` | archive parts; the current one has `current: true` and `url: null` |
| `GET /api/seek?ts=` | `{kind: clip/daily, name, url, offset_s, duration_s, fraction, frame_at, approximate}` for a time (epoch or ISO), or 404 |
| `POST /api/videos/{clips,daily}/{name}/star` | body `{"value": true}` |
| `POST /api/desktop/reveal` | body `{"root", "path"}`: Show in folder (this PC only) |
| `GET /media/{clips,daily,archive,sightings}/{path}` | the files, with HTTP Range; nothing outside those folders, never `latest.mp4` or the current archive part |
| `GET /live.mjpg` | the camera stream (at most 2 viewers; one Pi connection each, closed when the viewer leaves) |
