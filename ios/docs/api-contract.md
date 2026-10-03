# Campi API contract (v1)

This document is the **source of truth for the Campi PC's HTTP API**. The PC side (the `campi ui` FastAPI
server from `docs/pc-ui-spec.md`, plus the always-on mode added here) is built to match it, and the Campi iPhone
app and the desktop UI both consume it.

- Machine-readable shapes: `contract/schema.json`. Example responses: `contract/fixtures/`.
- Compliance test: `python3 tools/contract_check.py <base-url> --token <token>` must pass against the PC.
- Reference implementation for clients: `python3 tools/mock_server.py`.
- If this document and `schema.json` disagree, this document wins and `schema.json` gets fixed.
- Open questions about the PC specs, and the defaults this contract chose, are in `docs/spec-questions.md`.

Everything below is normative unless marked *(note)*.

---

## 1. Deployment

| | |
|---|---|
| Bind | `127.0.0.1:8765` (`[api] port`). Never a LAN or public interface. |
| Remote access | `tailscale serve --bg --https=443 http://127.0.0.1:8765` → `https://<pc>.<tailnet>.ts.net`. Tailscale Funnel is never used. |
| Process | Always-on: a child of the service supervisor when `[api] enabled = true` (see Appendix A). `campi ui` opens a window onto it. |
| TLS | Terminated by Tailscale (real certificate). The server itself speaks plain HTTP on loopback. |

Clients store one **base URL** (e.g. `https://campi-pc.tail1234.ts.net`) and resolve every path in this document,
and every URL the API returns, against it.

---

## 2. Conventions

### 2.1 JSON
- UTF-8 JSON, `Content-Type: application/json`. Object keys are `snake_case`.
- **Every documented key is always present.** A key marked nullable (`?` in the type tables) is `null` when there
  is no value; it is never omitted. Clients must ignore unknown keys.
- Numbers: `int` is a JSON integer, `number` may be fractional. Confidences and probabilities are `0.0–1.0`.

### 2.2 Time
- **Instants** (`datetime`) are RFC 3339 with milliseconds and **the PC's local UTC offset at that instant**:
  `2026-10-03T14:05:12.345-05:00`. Never `Z`, never a bare local time. A DST change is visible in the offset.
  *(note: this lets the phone show the same wall-clock time as the video overlays, even when the phone is
  elsewhere, without needing the PC's IANA zone, which stdlib Python on Windows can't produce.)*
- **Days** (`date`) are PC-local calendar dates `YYYY-MM-DD`. "Today" always means the PC's local day.
- Query parameters that take an instant accept RFC 3339 (any offset, `Z` allowed) or Unix seconds (`1791054312.345`).

### 2.3 IDs
| Thing | ID | Example |
|---|---|---|
| Sighting | `sightings.id` (UUID string) | `5f0c2a9e-1b7d-4c3e-9a51-0d6c8e2f4b11` |
| Clip | file name without `campi_` and `.mp4` | `2026-10-03_1410` |
| Daily video | its day | `2026-10-02` |
| Archive part | part number | `3` |
| Device | `dev_` + 16 lowercase base32 chars | `dev_k3j9q2m8x4c7v1bz` |

### 2.4 Errors
Every non-2xx response has this body:
```json
{"error": {"code": "not_found", "message": "no sighting 5f0c2a9e-..."}}
```
`message` is for humans and logs. Clients branch on `code` only:

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_param` | malformed or out-of-range parameter or body |
| 401 | `unauthorized` | missing, unknown or revoked token / bad signature |
| 401 | `invalid_code` | pairing code wrong, used or expired |
| 403 | `expired` | signed URL past its `exp` |
| 404 | `not_found` | unknown id, or a media file that no longer exists (e.g. an expired clip) |
| 416 | `invalid_range` | unsatisfiable Range |
| 422 | `unknown_label` | label correction to a label not in the collection |
| 429 | `rate_limited` | pairing attempts, `/live.jpg` |
| 502 | `pi_unreachable` | live stream: the Pi didn't answer |
| 503 | `live_busy` | live stream: `[api] max_live_viewers` reached |
| 500 | `internal` | anything else (the server logs the traceback) |

### 2.5 Lists and paging
Paged lists return `{"items": [...], "next_cursor": "...", "total": 123}`.
- Order is newest first unless stated.
- `limit` defaults to 50, max 200. `cursor` is opaque; pass `next_cursor` back unchanged to get the next page.
  `next_cursor` is `null` on the last page.
- `total` is the number of items matching the filters (all pages).
- A cursor stays valid while new items arrive (no duplicates or gaps for items that existed when the first page
  was fetched).

### 2.6 Enums
Enum values are listed per field. Clients must treat an unknown value as "unknown" and keep working (new values
are an additive change). The compliance check is strict: the PC must only send the listed values.

### 2.7 Versioning
`GET /api/status` returns `api_version: 1`. Additive changes (new keys, endpoints, enum values) keep version 1 and
are recorded in the changelog (§10). A breaking change means new paths, never a silent change to these.

### 2.8 Sightings off, schema v1
- With `[sightings] enabled = false` every endpoint still answers 200. If `sightings.db` exists, its history is
  served as usual (and `status.sightings.state` is `disabled`). If it doesn't exist, sighting lists are empty,
  counts are 0, and `sightings_count` fields are `null`.
- The API works on sightings.db schema v1 and v2. Columns or tables missing in v1 read as `null` / empty, with the
  defaults in §5.3.

---

## 3. Authentication

### 3.1 Devices and tokens
- Every request needs `Authorization: Bearer <token>`, **including requests from 127.0.0.1**. *(note: traffic
  from `tailscale serve` arrives from loopback, so there is no loopback exemption.)* The only exceptions:
  `POST /api/pair` (no auth) and signed URLs (§3.3).
- A token belongs to one **device**. Tokens are 32 random bytes, base64url without padding (43 chars). The
  server stores only `SHA-256(token)` in `ui\ui.db`.
- `campi devices` (PC CLI) lists devices; `campi devices revoke <id>` revokes one. A revoked token, and every URL
  signed for it, fails with 401 at once.
- The desktop UI is a device too (name `desktop`, created on first `campi ui`); `campi ui` hands its token to the
  webview.

### 3.2 Pairing
1. On the PC, `campi pair` creates a one-time **pairing code** (8 chars, Crockford base32, shown as `XXXX-XXXX`,
   valid 10 minutes, single use) and prints a QR code of
   `campi://pair?u=<urlencoded base URL>&c=<code>` plus the URL and code as text.
2. The phone opens that link (iOS Camera app) or the user types URL + code.
3. The phone calls `POST /api/pair` and stores the returned token in its Keychain.

Codes are matched case-insensitively, ignoring `-` and spaces. `POST /api/pair` is rate-limited to 10 attempts per
10 minutes in total (429 `rate_limited`).

### 3.3 Signed URLs
Every URL the API returns under `/media/...` and `/live...` is **pre-signed** for the requesting device:
```
/media/sightings/2026-10-03/140512_5f0c2a9e_toyota-camry_crop.jpg?d=dev_k3j9q2m8x4c7v1bz&exp=1791097512&sig=Qm9...
```
- `d` = device id, `exp` = Unix seconds, `sig` = base64url (no padding) of
  `HMAC-SHA256(server_secret, "<d>|<path>|<exp>")`. `<path>` is the URL path exactly as sent (percent-encoded),
  without the query.
- `server_secret` is 32 random bytes kept in `ui\ui.db`. `exp` = now + `[api] media_url_ttl_h` (default 12 h).
- The server accepts a request to `/media/...` or `/live...` if it carries a valid bearer token **or** a valid
  signature for a non-revoked device. An expired signature → 403 `expired`; a bad one → 401 `unauthorized`.
- Clients treat `d`, `exp`, `sig` as opaque, never build media paths themselves, and may append only the
  documented query parameters (`max_fps`, `w`). Caches should key media on the path without `d`, `exp`, `sig`.
- The signature covers the path only, so documented parameters can be appended without re-signing.

*(note: signed URLs let `AVPlayer`, the desktop `<video>`/`<img>` tags and image loaders fetch media without
custom headers.)*

---

## 4. Endpoints

| Method | Path | Returns |
|---|---|---|
| POST | `/api/pair` | `PairResult` (201) |
| GET | `/api/devices/me` | `Device` |
| PUT | `/api/devices/me/push` | `Device` |
| DELETE | `/api/devices/me` | 204 |
| GET | `/api/status` | `Status` |
| GET | `/api/sightings` | `Page<Sighting>` |
| GET | `/api/sightings/{id}` | `Sighting` |
| POST | `/api/sightings/{id}/star` | `Sighting` |
| POST | `/api/sightings/{id}/hide` | `Sighting` |
| POST | `/api/sightings/{id}/label` | `Sighting` |
| GET | `/api/collection` | `Collection` |
| GET | `/api/highlights` | `Page<Highlight>` |
| GET | `/api/clips` | `ClipList` |
| GET | `/api/clips/{id}` | `Clip` |
| POST | `/api/clips/{id}/star` | `Clip` |
| GET | `/api/daily` | `Page<Daily>` |
| GET | `/api/daily/{day}` | `Daily` |
| POST | `/api/daily/{day}/star` | `Daily` |
| GET | `/api/archive` | `ArchiveList` |
| GET | `/api/seek?ts=` | `Seek` |
| GET, HEAD | `/media/{root}/{path}` | file bytes (§7) |
| GET | `/live.mjpg` | MJPEG stream (§8) |
| GET | `/live.jpg` | JPEG (§8) |

Desktop-only endpoints (e.g. "show in folder") may exist. They aren't part of this contract, must be refused for
every device except `desktop`, and must never be needed by the phone.

### 4.1 `POST /api/pair`
Body: `{"code": "K3J9-Q2M8", "device_name": "Braxton's iPhone", "platform": "ios"}`
(`platform`: `ios` | `desktop` | `other`).
→ 201 `PairResult`. Errors: 400 `invalid_param`, 401 `invalid_code`, 429 `rate_limited`.

### 4.2 Devices
- `GET /api/devices/me` → the calling device.
- `PUT /api/devices/me/push` body:
  ```json
  {"apns_token": "a1b2...hex", "environment": "sandbox",
   "prefs": {"new_catch": true, "rare": false, "discovered": true, "service_alerts": true}}
  ```
  `apns_token: null` turns push off for this device. `environment`: `sandbox` (Xcode debug builds) | `production`
  (TestFlight / release). All `prefs` keys are required. → `Device`.
- `DELETE /api/devices/me` → 204. Revokes the calling device ("Unpair this phone").

### 4.3 `GET /api/status`
→ `Status` (§5.1). Cheap enough to poll every 30 s. It reads the same state files as `campi status`
(`state\status.json`, `capture.json`, `last_clip.json`, `last_daily.json`, `sightings.json`, `latest.json`) and
`sightings_db.summary`; see Appendix A for the reserved fields.

### 4.4 `GET /api/sightings`
Query parameters (all optional, combined with AND; repeatable ones are OR within themselves):

| Param | Meaning |
|---|---|
| `from`, `to` | `date`, inclusive, PC-local (`day` of the sighting) |
| `class` | repeatable; `yolo_class` value (`car`, `motorcycle`, `bus`, `truck`, ...) |
| `make` | repeatable; effective make, exact match |
| `label` | effective label, exact match |
| `decided_by` | repeatable; `siglip` \| `cloud` \| `user` |
| `starred` | `true` → only starred |
| `hide_unsure` | `true` → drop `unsure` rows unless `decided_by` is `cloud` or `user` |
| `hide_stationary` | `true` → drop stationary rows |
| `include_hidden` | `true` → include hidden rows (default: hidden rows are excluded) |
| `limit`, `cursor` | §2.5 |

Order: `started_at` desc, then `id` desc. → `Page<Sighting>`.

### 4.5 `GET /api/sightings/{id}` and actions
- `GET` → `Sighting`; hidden sightings are returned too.
- `POST .../star` body `{"starred": true|false}` → updated `Sighting`.
- `POST .../hide` body `{"hidden": true|false}` → updated `Sighting`.
- `POST .../label` body `{"label": "Toyota GR86"}` sets a correction; `{"label": null}` removes it → updated
  `Sighting`. The label must be the `label` of an item in `GET /api/collection`, else 422 `unknown_label`.

All three set state explicitly (they are not toggles) and are idempotent. They write only to `ui\ui.db`.

### 4.6 `GET /api/collection`
→ `Collection` (§5.6, rules in §6.1–6.2). Not paged (a few hundred items).

### 4.7 `GET /api/highlights`
| Param | Meaning |
|---|---|
| `type` | repeatable; `new_catch` \| `rare` \| `busiest` \| `daily` \| `starred`. Default: all. An item matches if any of its `types` matches. |
| `from`, `to` | `date`, inclusive, on the item's `day` |
| `limit`, `cursor` | §2.5 |

Order: `at` desc, then `id` desc. → `Page<Highlight>` (§5.7, rules in §6.3).

### 4.8 Clips
- `GET /api/clips` → `ClipList`: every existing 10-minute clip, newest `window_start` first, not paged (≤ ~150).
- `GET /api/clips/{id}` → `Clip`, or 404 `not_found` if the file is gone.
- `POST /api/clips/{id}/star` body `{"starred": bool}` → `Clip`. A star outlives the file (see §6.3).

Only files in `[output] dir` whose name matches `^campi_(\d{4}-\d{2}-\d{2})_(\d{4})\.mp4$` (`archive.CLIP_RE`) are
clips. `latest.mp4`, `latest.tmp.mp4` and `*.part.mp4` are never listed or served.

### 4.9 Daily videos
- `GET /api/daily` (`limit`, `cursor`) → `Page<Daily>`, newest day first. Files:
  `daily\campi_daily_YYYY-MM-DD.mp4` (not `*.part.mp4`).
- `GET /api/daily/{day}` → `Daily` or 404.
- `POST /api/daily/{day}/star` body `{"starred": bool}` → `Daily`.

### 4.10 `GET /api/archive`
→ `ArchiveList`: every `campi_archive_NNN.mp4` in `[archive] dir`, highest part first. The current part (§6.5) is
listed with `current: true` and `media.video: null` and is never served.

### 4.11 `GET /api/seek?ts=<instant>`
→ `Seek`: where in the timelapse an instant is, by the rules in §6.4. For a sighting, clients pass the midpoint of
`started_at` and `ended_at`.

---

## 5. Types

`url` = a path-absolute URL (`/media/...`, `/live...`) to resolve against the base URL; media and live URLs are
signed (§3.3). `?` = nullable.

### 5.1 `Status`
| Key | Type | Meaning |
|---|---|---|
| `api_version` | int | `1` |
| `server_name` | string | PC host name |
| `server_time` | datetime | now |
| `service` | `ServiceStatus` | |
| `capture` | `CaptureStatus` | |
| `clips` | `ClipsStatus` | |
| `daily` | `DailyStatus` | |
| `sightings` | `SightingsStatus` | |
| `gaming` | `GamingStatus?` | **reserved**: `null` until the PC persists gaming state |
| `disk` | `{free_gb: number, drive: string}` | frames drive, e.g. `"C:"` |
| `latest_clip_id` | string? | clip named in `state\latest.json`, if that file still exists |
| `live` | `LiveStatus` | |

`ServiceStatus`
| Key | Type | Meaning |
|---|---|---|
| `state` | enum `running` \| `stopped` | running = supervisor pid set and heartbeat (`status.json` `updated`) < 30 s old (same test as `campi status`) |
| `pid` | int? | supervisor pid when running |
| `started_at` | datetime? | supervisor start |
| `heartbeat_at` | datetime? | `status.json` `updated` |
| `disabled` | bool | `state\disabled` exists (`campi stop`) |

`CaptureStatus` (from `capture.json` and `status.json`)
| Key | Type |
|---|---|
| `connected` | bool |
| `host` | string? |
| `last_frame_at` | datetime? |
| `saved`, `rejected`, `reconnects` | int (0 when unknown) |
| `restarts` | int? (`capture_restarts`, null when the service is stopped) |
| `last_error` | string? (`null` for `""`) |

`ClipsStatus`
| Key | Type | Meaning |
|---|---|---|
| `last` | `RenderResult?` | from `last_clip.json` |
| `next_at` | datetime? | `status.json` `next_clip`, when running |
| `running` | bool? | `clip_running`, when running |
| `queue` | `RenderQueue?` | **reserved** (render queue from pc-sightings-spec) |

`RenderQueue` (reserved): `{length: int, oldest_window_start: datetime?, deferred: bool, deferred_reason: enum gaming?}`

`DailyStatus`: `{last: RenderResult?, running: bool?}`

`RenderResult`
| Key | Type | Meaning |
|---|---|---|
| `status` | enum `ok` \| `skipped` \| `error` | |
| `finished_at` | datetime? | `finished` |
| `clip_id` | string? | clips: from `out` when `status = ok` |
| `day` | date? | daily: `day` |
| `detail` | string? | `reason` (skipped) or `error` (error) |

`SightingsStatus`
| Key | Type | Meaning |
|---|---|---|
| `enabled` | bool | `[sightings] enabled` |
| `state` | enum `disabled` \| `not_installed` \| `service_stopped` \| `not_running` \| `starting` \| `running` \| `crash_looping` \| `paused_gaming` | same decision as `sightings_status()` in `__main__.py`; `paused_gaming` reserved |
| `phase` | enum? `loading` \| `disconnected` \| `running` \| `paused_dark` \| `error` | worker heartbeat `phase` |
| `restarts` | int? | |
| `last_error` | string? | |
| `next_retry_at` | datetime? | when crash-looping |
| `has_history` | bool | `sightings.db` exists |
| `today`, `total` | int | raw row counts, as `campi status` prints them (no hidden/override rules) |
| `last_sighting_at` | datetime? | |
| `backend` | enum? `openvino` \| `cuda` | **reserved** |
| `device` | string? | **reserved**: device name in use, e.g. `"Intel(R) UHD Graphics 770"` |
| `cpu_fallback` | bool? | **reserved** |
| `classify_queue` | int? | **reserved** |
| `cloud` | `{enabled: bool, calls_today: int, cap: int}?` | **reserved** |

`GamingStatus` (reserved)
| Key | Type |
|---|---|
| `mode` | enum `auto` \| `on` \| `off` |
| `active` | bool |
| `exe` | string? (process that triggered it) |
| `since` | datetime? |
| `detection` | enum `counters` \| `path_only` |
| `renders_deferred` | bool |

`LiveStatus`
| Key | Type | Meaning |
|---|---|---|
| `available` | bool | Pi host known and the API can reach it (best effort, cached ≤ 30 s) |
| `mjpeg` | url? | signed `/live.mjpg` |
| `snapshot` | url? | signed `/live.jpg` |
| `rotation` | int | `[image] rotation`, `/live.mjpg` is not rotated, `/live.jpg` already is |
| `max_viewers` | int | |

### 5.2 `Sighting`
| Key | Type | Meaning |
|---|---|---|
| `id` | string | |
| `kind` | string | `vehicle` today; other values must be tolerated |
| `started_at`, `ended_at` | datetime | |
| `day` | date | local day of `started_at` |
| `yolo_class` | string? | |
| `label` | string? | **effective** label (correction applied) |
| `make`, `model` | string? | effective; `null` for generic types (e.g. `dump truck`) |
| `confidence` | number? | confidence of the machine verdict (`machine.confidence`) |
| `decided_by` | enum `siglip` \| `cloud` \| `user` | `user` when a correction exists, else `machine.source` |
| `machine` | `MachineVerdict` | what the worker stored (SigLIP's or Gemini's answer) |
| `siglip` | `{label: string?, confidence: number?}` | SigLIP's own answer (`siglip_label`, `siglip_confidence`; v1: `label`, `confidence`) |
| `runner_ups` | `[{label: string, p: number}]` | SigLIP runner-ups (`[]` when none) |
| `year_range` | string? | e.g. `"2017-2022"` (cloud) |
| `color` | string? | (cloud) |
| `unsure` | bool | as stored |
| `stationary` | bool | |
| `direction` | enum? `LR` \| `RL` | left→right / right→left in the (rotated) frame |
| `cloud_status` | enum? `pending` \| `done` \| `failed` \| `capped` \| `skipped` | **reserved** until the PC persists it; `null` = never sent / unknown |
| `starred`, `hidden` | bool | from ui.db |
| `correction` | `{label: string, at: datetime}?` | the user's label correction |
| `track_frames` | int? | |
| `max_box_px` | int? | |
| `media` | `{crop: url?, frame: url?, clip: url?}` | `clip` is `null` when not saved or expired |

`MachineVerdict`: `{label: string?, make: string?, model: string?, confidence: number?, source: enum siglip|cloud}`
(v1 rows: `source = siglip`).

Effective `make`/`model` for a correction come from the collection item with that label.

### 5.3 v1 defaults
On a v1 database: `source` = `siglip`, `siglip` = `{label, confidence}`, `year_range` = `color` = `null`,
`cloud_status` = `null`, no discovered labels.

### 5.4 `Clip`, `ClipList`
`Clip`
| Key | Type | Meaning |
|---|---|---|
| `id` | string | `2026-10-03_1410` |
| `day` | date | local day of `window_start` |
| `window_start`, `window_end` | datetime | from the render index (§A.4) when present, else name + `[render] window_min` |
| `exact_window` | bool | `true` when taken from the render index |
| `duration_s` | number? | from the MP4 `mvhd` box (read from the file head; no ffprobe) |
| `size_bytes` | int | |
| `modified_at` | datetime | file mtime |
| `expires_after` | datetime | approximate: `modified_at + [retention] clips_hours`; housekeeping runs hourly, so the file may live up to ~1 h longer |
| `sightings_count` | int? | counted sightings (§6.1) with `started_at` in the window; `null` with no sightings.db |
| `starred` | bool | |
| `media` | `{video: url, poster: url}` | poster: §7.2 |

`ClipList`: `{latest_id: string?, retention_hours: int, window_min: int, items: [Clip]}`

### 5.5 `Daily`, `ArchivePart`
`Daily`: `{day: date, duration_s: number?, size_bytes: int, modified_at: datetime, sightings_count: int?,
starred: bool, media: {video: url, poster: url}}`

`ArchiveList`: `{enabled: bool, items: [ArchivePart]}`
`ArchivePart`: `{part: int, size_bytes: int, modified_at: datetime, duration_s: number?, current: bool,
media: {video: url?}}`

### 5.6 `Collection`
| Key | Type | Meaning |
|---|---|---|
| `total` | int | number of items |
| `caught` | int | items with `count ≥ 1` |
| `tiers` | `[{tier: enum, min: int, max: int?}]` | thresholds in use (§6.2), in order |
| `items` | `[CollectionItem]` | labels-file order, then `discovered` by `first_seen_at`, then `other` by label |

`CollectionItem`
| Key | Type | Meaning |
|---|---|---|
| `label` | string | |
| `make`, `model` | string? | |
| `generic` | bool | a plain type line in `sightings_labels.txt` (no `Make \| Model`) |
| `origin` | enum `labels_file` \| `discovered` \| `other` | `other`: seen in history but no longer in the labels file nor discovered |
| `count` | int | counted per §6.1 |
| `first_seen_at`, `last_seen_at` | datetime? | over counted sightings |
| `tier` | enum `uncaught` \| `rare` \| `uncommon` \| `common` | §6.2 |
| `cover` | `{sighting_id: string, crop: url}?` | the counted sighting with the highest `confidence` (ties: newest) |

### 5.7 `Highlight`
| Key | Type | Meaning |
|---|---|---|
| `id` | string | stable: `sighting:<id>`, `clip:<id>`, `daily:<day>`, `window:<clip id>` |
| `types` | `[enum new_catch \| rare \| busiest \| daily \| starred]` | all that apply, non-empty |
| `at` | datetime | sort key (§6.3) |
| `day` | date | |
| `sighting` | `Sighting?` | for `sighting:` items |
| `clip` | `Clip?` | for `clip:` items, and `window:` items whose clip still exists |
| `daily` | `Daily?` | for `daily:` items |
| `window` | `{start: datetime, end: datetime, sightings_count: int}?` | for `window:` items |

Exactly one of `sighting`, `clip` (alone) / `window` (with optional `clip`), `daily` describes the item.

### 5.8 `Seek`
| Key | Type | Meaning |
|---|---|---|
| `ts` | datetime | the instant asked for |
| `target` | enum `clip` \| `daily` \| `none` | |
| `clip_id` | string? | when `target = clip` |
| `day` | date? | when `target = daily` |
| `video` | url? | the video to open |
| `offset_s` | number? | seek position in that video |
| `approximate` | bool | `true` when the offset is an estimate (§6.4) |
| `reason` | enum? `pending_render` \| `not_rendered` \| `no_frames` \| `expired` | when `target = none` |

### 5.9 Pairing and devices
`PairResult`: `{token: string, device: Device, server_name: string, api_version: int}`

`Device`
| Key | Type |
|---|---|
| `id` | string |
| `name` | string |
| `platform` | enum `ios` \| `desktop` \| `other` |
| `created_at` | datetime |
| `last_seen_at` | datetime? |
| `push` | `{enabled: bool, environment: enum? sandbox\|production, prefs: PushPrefs}` |

`PushPrefs`: `{new_catch: bool, rare: bool, discovered: bool, service_alerts: bool}`; defaults for a new device:
`true, false, true, true`.

---

## 6. Computation rules

These are computed **at query time** from sightings.db plus ui.db. Nothing derived is stored.

### 6.1 Counting sightings
A sighting **counts** (toward the collection, `new_catch`, `rare`, busiest windows and every `sightings_count`)
when:
- it is not hidden, and
- it has an effective label, and
- it is not `unsure`, or its `decided_by` is `cloud` or `user`.

Stationary sightings count. The effective label (with correction) is used everywhere, so a correction moves the
sighting between collection items, and can change which sighting is a label's first.
`discovered_labels.count` is not used; counts always come from rows.

### 6.2 Tiers
By `count`: `uncaught` 0, `rare` 1–3, `uncommon` 4–20, `common` 21+. The API returns the thresholds in
`Collection.tiers`; clients must not hardcode them.

### 6.3 Highlights
| Type | Item | Rule | `at` |
|---|---|---|---|
| `new_catch` | sighting | the earliest counted sighting of each effective label | `started_at` |
| `rare` | sighting | every counted sighting whose label's total count is 1–3 (re-evaluated at query time: it disappears once the label passes 3) | `started_at` |
| `busiest` | window | per local day, the 3 windows with the most counted sightings, minimum 3 sightings; windows aligned like `Supervisor.next_boundary` (local midnight + k·`interval_min`) | window start |
| `daily` | daily | every existing daily video | next local midnight after `day` |
| `starred` | sighting, clip or daily | anything starred. A starred clip whose file is gone becomes a `window:` item with `clip: null` | as above |

One item per thing: a first catch of a label seen once has `types: ["new_catch", "rare"]`; a starred busiest
window that still has its clip is one `window:` item with `types: ["busiest", "starred"]`.

### 6.4 Seek
Given instant `ts` (in the future → 400 `invalid_param`):
0. **Night / gap.** If the frames index for `ts`'s day exists and has no usable frame within ±`window_min` of `ts`
   → `none` / `no_frames`.
1. **Clip.** Find existing clips whose window contains `ts`; prefer an exact (render-index) window, then a
   window aligned to `interval_min`, then the newest file.
   - With a render index (§A.4): `offset_s = k / base_fps`, where `k` = number of indexed frames with timestamp
     `< ts`. `approximate = false`.
   - Without: `k` = number of usable frames (`frames.frames_between` + `frames.usable`) in
     `[window_start, ts)`. `offset_s = k / base_fps`, `approximate = false`.
   *(note: this mirrors `render_frames`: each usable frame becomes `interp_factor` output frames at
   `base_fps × interp_factor`, i.e. `1/base_fps` seconds, across segments.)*
2. **Daily.** Else, if the daily video for `ts`'s day exists:
   - With a render index: as above, `approximate = false`.
   - Without: `offset_s = duration_s × (usable minutes of the day before ts) / (usable minutes of the day)`,
     where a usable minute is a minute with at least one usable frame in that day's `index.csv`.
     `approximate = true`. *(note: minutes survive the 48 h thinning to 1 frame/min; frame counts don't.)*
3. **None**, with `reason`:
   - `pending_render`: the window hasn't been rendered yet (it closed less than `delay_s + timeout_min` ago, a
     render is running, or it's in the reserved render queue);
   - `expired`: the clip is gone (older than `clips_hours`) and that day's daily video doesn't exist (skipped,
     not rendered yet, or deleted);
   - `not_rendered`: anything else (e.g. the clip was skipped for too few frames).

`offset_s` is clamped to `[0, duration_s − 0.05]` when the duration is known.

### 6.5 Current archive part
The current part is the higher of `state\archive.json` `part` and the highest-numbered `campi_archive_NNN.mp4`
on disk. *(note: `archive.append_pending` creates part N+1 before it updates `archive.json`.)* `*.tmp.mp4` files
are never listed.

---

## 7. Media

### 7.1 `/media/{root}/{path}`
| `root` | Folder | Files |
|---|---|---|
| `clips` | `[output] dir` | clip files only (§4.8) |
| `daily` | `[output] dir\daily` | `campi_daily_YYYY-MM-DD.mp4` |
| `archive` | `[archive] dir` | `campi_archive_NNN.mp4` except the current part |
| `sightings` | `CampiTimelapse\sightings` | paths from sightings.db (`crop_path`, `frame_path`, `clip_path`), relative, posix `/` |
| `posters` | `ui\cache\posters` | §7.2 |

- **Path safety.** The resolved file must be inside its root, after resolving `..`, symlinks and junctions;
  anything else → 404.
- **Methods.** `GET` and `HEAD`. A single byte range (`Range: bytes=a-b`, `a-`, `-n`) → 206 with
  `Content-Range`. Multi-range requests may be answered with 200 and the full body. An unsatisfiable range →
  416 `invalid_range`.
- **Headers.** Always `Accept-Ranges: bytes`, `Content-Length`, `Content-Type` (`video/mp4`, `image/jpeg`),
  `ETag: "<size>-<mtime_ns>"`, `Last-Modified`. Honour `If-None-Match` (304) and `If-Range`.
- **Caching.** `Cache-Control: private, max-age=300` for videos and posters, `private, max-age=86400` for
  sightings JPEGs. Never `immutable`: daily videos can be re-rendered in place.
- **File handles (Windows).** Media is read through handles opened with `FILE_SHARE_READ | FILE_SHARE_DELETE`
  (`CreateFileW` + `msvcrt.open_osfhandle`), so `os.replace` / `unlink` by the service succeed even mid-response.
  No handle stays open after its response ends. If the file disappears mid-response, the connection is closed.

### 7.2 Posters
`Clip.media.poster` and `Daily.media.poster` are always present. The server makes the poster on first request:
the frame at 50 % of the video, scaled to 640 px wide, JPEG, using `[tools] ffmpeg` with the source opened only
for that run. It's cached as `ui\cache\posters\{clips|daily}\<id>-<mtime_ns>.jpg`. Posters whose source no longer
exists are deleted on the hourly cleanup. If generation fails → 404; clients show a placeholder.

---

## 8. Live

### 8.1 `GET /live.mjpg[?max_fps=N]`
- A proxy of the Pi's `stream.mjpg`, using the same host discovery as capture (`discover.current_host`; the API
  never calls `find_pi`).
- Response `multipart/x-mixed-replace; boundary=campiframe`. Each part has `Content-Type: image/jpeg` and
  `Content-Length`. Frames are passed through unchanged (1920×1440, not rotated, about 30 fps).
- `max_fps` (1–30): the server drops frames so that at most N per second reach this viewer. No transcoding.
- **One upstream connection per viewer**, opened when the request arrives and closed as soon as the viewer
  disconnects (detected within 2 s).
- More than `[api] max_live_viewers` (default 3) concurrent viewers → 503 `live_busy`.
- If the Pi doesn't answer within `[stream] timeout_s` → 502 `pi_unreachable`, sent before any part.

### 8.2 `GET /live.jpg[?w=N]`
- The **newest frame capture saved** (the last row of today's `frames\YYYY-MM-DD\index.csv`, read from the end of
  the file), rotated by `[image] rotation`, scaled to width `w` (default 1080, max: native width), JPEG q80.
  No request goes to the Pi.
- Headers: `X-Frame-Time: <datetime>` and `Cache-Control: no-store`.
- Rate-limited to 4 requests per second across all devices (429 `rate_limited`).
- No frame in the last 10 minutes → 404 `not_found`.

*(note: capture saves a frame every `interval_s` = 2 s, so clients poll every 2 s.)*

---

## 9. Push notifications

Sent by the PC (supervised API only, never a second `campi ui` server) to APNs with an ES256 `.p8` token key, to
every device with `push.enabled` whose prefs match.

### 9.1 Events
| `campi.type` | Pref | When |
|---|---|---|
| `new_catch` | `new_catch` | a counted sighting becomes the first of its label |
| `rare` | `rare` | a counted sighting is the 2nd or 3rd of its label |
| `discovered` | `discovered` | Gemini named a car that isn't in `sightings_labels.txt` (a new `discovered_labels` row) |
| `service` | `service_alerts` | see `campi.alert` below; each fires once per incident, then `recovered` once it clears |

Service alerts (`campi.alert`):
- `capture_disconnected`: no saved frame for 10 min while it isn't dark
- `sightings_crash_looping`
- `disk_low`: below `[retention] min_free_gb`
- `render_failing`: 2 clip renders in a row with `status = error`
- `recovered`

### 9.2 Rules
- The sender reads new sightings every ~10 s by **rowid high-water mark**, kept in ui.db. On the very first run,
  the mark starts at the current maximum, so no old sightings are pushed.
- `pushes(sighting_id, type)` in ui.db makes each push happen at most once, across restarts.
- When `[sightings.cloud] enabled` and the daily cap isn't reached, `new_catch` / `rare` pushes wait until the
  sighting's cloud answer is in (at most 60 s), so the push names Gemini's label rather than a SigLIP guess.
- Device tokens answering APNs 410 (`Unregistered`) are cleared. The JWT is reused for ~30 min.
  `environment` selects `api.sandbox.push.apple.com` or `api.push.apple.com`.

### 9.3 Payload
```json
{
  "aps": {
    "alert": {"title": "New catch: Toyota GR86", "body": "2:05 PM · left to right · Gemini 93%"},
    "sound": "default", "mutable-content": 1, "thread-id": "sightings", "category": "SIGHTING"
  },
  "campi": {
    "type": "new_catch",
    "sighting_id": "5f0c2a9e-1b7d-4c3e-9a51-0d6c8e2f4b11",
    "label": "Toyota GR86",
    "day": "2026-10-03",
    "crop": "/media/sightings/2026-10-03/140512_5f0c2a9e_toyota-camry_crop.jpg",
    "alert": null
  }
}
```
- `campi.crop` is an **unsigned** path. The app's notification extension fetches it with the bearer token.
- For `service` pushes: `thread-id` = `service`, `category` = `SERVICE`, `sighting_id` / `label` / `crop` = `null`,
  and `alert` set.
- The texts are the PC's to choose. Clients use only the `campi` fields.

---

## 10. Changelog
- **v1** (2026-10-03): initial contract.

---

## Appendix A. PC-side requirements implied by this contract

These are additions to `docs/pc-ui-spec.md` / `docs/pc-sightings-spec.md`. Where this appendix conflicts with
pc-ui-spec, this appendix wins for the API (see spec-questions.md).

**A.1 Config** (new sections, defaults shown; with `enabled = false` nothing changes):
```toml
[api]
enabled = false            # run the API headless under the supervisor (needs install.ps1 -UI)
port = 8765                # always bound to 127.0.0.1
public_url = ""            # base URL put in pairing QR codes; "" = https://<tailscale DNSName> from `tailscale status --json`
max_live_viewers = 3
media_url_ttl_h = 12

[api.push]
enabled = false
key_file = "{home}\\CampiTimelapse\\secrets\\apns_AuthKey.p8"   # never logged; outside the repo
key_id = ""
team_id = ""
bundle_id = "com.braxtonmills.campi"
```

**A.2 Process**
- With `[api] enabled = true`, the supervisor starts `venv-ui\Scripts\pythonw.exe -m campi_timelapse api` as a
  below-normal-priority child in its Job Object, restarting it with backoff like the sightings worker.
  `campi status` output stays byte-identical (pc-ui-spec rule), so nothing about the API is printed there.
- `campi ui` opens its window onto the supervised API when it's running. Otherwise it starts its own server on a
  free loopback port, which runs **no push sender**.
- The API process never writes under `state\`, `frames\`, the clips/daily/archive folders or the sightings
  folder. It opens sightings.db only with `sightings_db.connect_ro`, never imports `render`, `sightings`,
  `capture` or anything needing cv2/numpy, and calls `discover.current_host` but never `find_pi`. All its own data
  is in `%USERPROFILE%\CampiTimelapse\ui\` (ui.db, cache\).
- venv-ui adds: `pillow` (live.jpg), `httpx[http2]` + `pyjwt[crypto]` (APNs), `qrcode` (`campi pair`).

**A.3 Commands** (campi.ps1): `campi pair`, `campi devices [revoke <id>]`, `campi api` (foreground, for debugging).

**A.4 Render index** (small service change; output videos unchanged): `render_clip` and `render_daily` also write
`state\render_index\<output file stem>.json`:
```json
{"output": "campi_2026-10-03_1410.mp4", "window_start_ts": 1791054600.0, "window_end_ts": 1791055200.0,
 "base_fps": 30, "interp_factor": 2, "frame_ts": [1791054601.02, 1791054603.04]}
```
`frame_ts` = the timestamps of the frames actually encoded, in order (after unreadable frames are skipped). The
file is written after the video is replaced, and housekeeping deletes it together with its video. This makes
seeks exact and gives the real window of `campi test` / `campi render-now` clips.

**A.5 Sightings (v2)**: persist a per-sighting cloud status (`pending | done | failed | capped | skipped`, e.g. a
`cloud_status` column in `sightings`), and add an `updated_at` column (set on insert and whenever the cloud
updates the row). Persist the backend/device/cpu_fallback/classify-queue, gaming and render-queue state in
`state\` (e.g. `status.json` and `sightings.json`), so the API can fill the reserved `Status` fields.

**A.6 Housekeeping**: `expire_clips` should catch `PermissionError` per file and retry next hour. Today, one
locked clip aborts the whole run (frame thinning, disk guard, sighting-clip expiry).

**A.7 Labels**: the stdlib-only Labels module required by pc-ui-spec is what the API uses for the collection,
`generic`, and label validation.

**A.8 Tailscale on the PC**
- Install Tailscale and run `tailscale up --unattended`, so it stays connected while nobody is logged on (the
  service runs as S4U).
- Enable MagicDNS and HTTPS certificates in the tailnet admin console.
- Run `tailscale serve --bg --https=443 http://127.0.0.1:8765`.
- Never enable Funnel.
- *(note: OneDrive "Files On-Demand" can turn old daily videos into cloud-only placeholders that a session-0
  process can't read; keep the campi folder "Always keep on this device".)*
