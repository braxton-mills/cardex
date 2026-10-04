Add an on-demand desktop app, `campi ui`, for browsing everything: sightings, timelapse clips, daily videos and highlights. Build it after the sightings retarget is merged, but tolerate sightings.db schema v1 and v2 (missing columns or tables = empty). Read config.py, __main__.py (cmd_status, sightings_status), sightings_db.py, sightings.py (Labels), frames.py, archive.py, render.py (render_clip, replace_retry), housekeeping.py, campi.ps1 and install.ps1 first and follow their patterns.

WHY IT'S SEPARATE
The service runs in session 0 under the scheduled task and can't show windows, so the UI is its own process in my normal desktop session that only reads what the service writes.

HARD RULES
- Never launching the UI changes nothing. Its dependencies are imported only by `campi ui`, from its own venv.
- campi status output stays byte-identical. If you factor the data gathering out of cmd_status / sightings_status so the UI can reuse it, keep the CLI text exactly the same.
- Read-only toward the service: open sightings.db only through sightings_db.connect_ro, and never write to state\, frames\, the clips folder or the sightings media folder. Everything I create in the UI (stars, hidden items, label corrections) goes in %USERPROFILE%\CampiTimelapse\ui\ui.db.
- Windows file locks. An open file can't be replaced or deleted, and the service relies on both: render_clip replaces latest.mp4 (replace_retry gives up after ~10 s), housekeeping deletes campi_*.mp4 after retention.clips_hours, archive.py rewrites the current part, and sightings clips expire after keep_clips_days. So:
  - never hold a file handle open between requests (open, serve the range, close);
  - never serve latest.mp4 itself; resolve it to the dated clip named in state\latest.json;
  - never serve the current archive part (the "part" in state\archive.json); older parts are fine.
- Bind to 127.0.0.1 by default.
- Works whether sightings is enabled or not; sightings views show a clear "sightings is off" empty state.

STACK
- FastAPI + uvicorn, shown in a native window with pywebview (Edge WebView2). Closing the window shuts the server down with no orphan processes.
- Frontend: plain HTML/CSS/JS served from the package, no Node build step. Vendor any library locally so it works offline.
- Video endpoints support HTTP Range requests so seeking works.
- Serve media only from the configured roots (cfg.paths.out, cfg.paths.daily, cfg.paths.archive, cfg.paths.sightings) and reject any other path. Sightings media paths in the DB are relative to the sightings folder.
- The sightings_labels.txt parser (Labels) lives in sightings.py next to cv2/numpy imports. Move it into a stdlib-only module that sightings.py imports, with identical behavior, so the UI can use it without the sightings venv.
- Own venv %USERPROFILE%\CampiTimelapse\venv-ui (fastapi, uvicorn, pywebview), created by install.ps1 -UI, which also checks for the WebView2 runtime and adds a Start Menu shortcut "Campi" that launches `campi ui` without a console window.
- `campi ui` in campi.ps1, with --browser (open in the default browser instead of a window) and --port N.
- A clean, documented JSON API. An iPhone app will use this same API later, so keep it stable and free of UI-specific shapes:
  /api/status, /api/sightings (filters + paging), /api/sightings/{id}, POST /api/sightings/{id}/star | hide | label,
  /api/collection, /api/highlights, /api/clips, /api/daily, /api/archive, /api/seek?ts=, /media/..., /live.mjpg
- /live.mjpg proxies the Pi stream (Pi host from the same discovery as capture: stream.pi_mac / state\pi_host.json), one upstream connection per viewer, closed the moment the viewer disconnects.

VIEWS (sidebar nav, dark theme; keys: J/K next/prev, Space play/pause, S star)
- Today: status strip (same data as campi status, refreshed every 30 s, including sightings backend/device, gaming state and render queue when present), the newest 10-minute clip playing, today's counts, latest sightings, today's highlights.
- Highlights, newest first, filterable by type:
  - new catch: first-ever sighting of a label
  - rare: label seen 3 times or fewer in total
  - busiest windows: the 10-minute clips with the most sightings
  - each day's daily video
  - anything I starred
- Sightings: grid of cards (crop, label, confidence, local time, direction, year_range, color, and who decided the label: SigLIP or Gemini). Filters: date range, make, class, source, hide unsure / stationary. Detail view: crop, full frame, clip, runner-up guesses, "View in timelapse", star, hide, and "correct label" (stored in ui.db as an override that the API applies everywhere, including counts and the collection).
- View in timelapse: open the 10-minute clip covering the sighting and seek to it. Seek offset = (number of usable frames in that window before the sighting time) / output.base_fps, using frames.frames_between + frames.usable, which mirrors how render_frames lays a clip out (each usable frame becomes interp_factor output frames at base_fps * interp_factor). If that clip has expired, open the daily video and seek proportionally, labeled approximate.
- Collection: every label in sightings_labels.txt as a tile, caught or not, with count, first seen, last seen, and a rarity tier computed from counts at query time (not stored). Also include discovered_labels (cars Gemini named that aren't in sightings_labels.txt), marked as discovered. Progress bar: X of Y caught.
- Timelapse: the last 24 h of 10-minute clips grouped by hour, the daily videos, the archive parts (all playable except the current one), and "Show in folder" on every file.
- Live: the stream via /live.mjpg. Only connected while this view is visible; disconnect when I switch views or minimize, since every viewer is another full stream over the Pi's Wi-Fi.

DONE WHEN (show me each)
- `campi ui` opens the window in a few seconds, with sightings on and with sightings off.
- campi status output is identical before and after (diff it).
- While a clip plays in the UI: `campi render-now` completes and replaces latest.mp4, housekeeping deletes an expired clip, and the archive append happens instead of deferring (show the log lines).
- Seeking works on a 10-minute clip and a daily video, and "View in timelapse" lands within a couple of seconds of the car.
- Closing the window leaves no UI python processes behind.
- README has a UI section: install, launch, the views, and the API endpoints.
