# Campi for iPhone

The iPhone companion to the Campi timelapse + vehicle-sightings service running on the Windows PC
([`../service`](../service)). Native SwiftUI, iOS 26+, no third-party dependencies.

## Status
| Milestone | State |
|---|---|
| M0 API contract, mock PC, compliance checker | done |
| M1 App skeleton: pairing, Today/status, settings | done |
| M2 Sightings, detail actions, Collection, Highlights | done |
| M3 Timelapse tab, player, Save to Photos / Share | done |
| M4 Live (MJPEG on Wi-Fi, snapshots on cellular) | done |
| M4.5 Cardex cards (on-device card text per label) | done |
| M5 Push notifications + Home Screen widget | done |
| M6 Polish, accessibility, install on device | done |
| M7 Cardex trading cards (3D cars, holo finishes) | done |

The PC side (`campi ui` API, see [`api/api-contract.md`](../api/api-contract.md) Appendix A) isn't built yet; the app runs against
`tools/mock_server.py` until it is.

## What the app does
- **Today:** service health (same data as `campi status`), today's counts, newest clip, latest sightings,
  today's highlights. Refreshes every 30 s while open.
- **Highlights:** new catches, rare labels, busiest 10-minute windows, daily videos and anything starred.
- **Sightings:** grid by day with filters (dates, type, make, who decided the label, starred, unsure, parked,
  hidden). Detail: crop, full frame, clip, runner-up guesses, star, hide, correct the label, and
  "View in Timelapse" (seeks the 10-minute clip, or the daily video once the clip has expired).
- **Cardex** (the Collection tab): every label, caught or not, as a binder of trading cards or the plain grid
  (Cards/Grid). Each caught car gets a Pokémon-style card with a 3D model of its body style, painted the color
  the cloud saw, "SEEN ×N" and its rarity. Finishes: common = plain, uncommon = reverse holo, rare = holo, Gemini
  discoveries = full art, rares seen only once = special illustration rare. Each card also gets a foil pattern
  (sheen, cosmos, cracked ice, starlight, sequin, ripple, energy-symbol, etched, galaxy, ...) and an illustrated
  scene (neon city, mountain pass, coast road, desert night, synthwave, aurora, deep space, ...) picked from its
  label and body style, so a binder shows a mix; the scene's layers drift with the tilt. Tap a card to hold it,
  and drag to tilt it: the foil (`Campi/Cardex/Foil.metal`) and glare follow. Each car is built on the phone from
  its shape (`CampiKit/Cardex/CarShape.swift`): tuned proportions for well-known models (a Wrangler, a Mustang
  and a Model 3 each get their own silhouette), else the body style's preset, which the on-device model picks.
- **Cardex cards** (collection item page and sighting detail): a trading card per label, written on the phone by
  Apple's on-device model (Foundation Models): name, type (Commuter, Work Truck, JDM, ...), three playful 1–10
  ratings and a line of flavor text, never specs. Generated once per label and cached (SwiftData); "Regenerate
  Card" from the card's context menu or the detail's ⋯ menu. Without Apple Intelligence it shows a plain card
  and says why.
- **Timelapse:** last 24 h of clips by hour (with expiry), daily videos, archive parts; save to Photos or share.
- **Notifications** (sent by the PC over APNs): new catches, rare (2nd/3rd) sightings, Gemini discoveries and
  service alerts, each toggled in Settings and stored on the PC. A notification extension attaches the crop,
  fetched with the bearer token from the shared Keychain. Tapping opens the sighting (or the status).
- **Widget** (small, medium): today's count, the latest catch with its crop, health. When the PC can't be
  reached it keeps the last good data and says "PC unreachable · as of …". Tapping opens the sighting.
- **Live** (from Today): MJPEG video at 10 fps on Wi-Fi, the newest saved frame every 2 s on cellular or Low Data
  Mode (or pick one). Disconnects as soon as it's off screen or the app leaves the foreground. Explains
  `live_busy`, an unreachable Pi and "no recent frame", and offers snapshots when video isn't possible.

## How the phone reaches the PC
The PC's API stays on `127.0.0.1:8765` and is published over Tailscale (`tailscale serve`), so the app talks
to `https://<pc>.<tailnet>.ts.net` at home or on cellular. Pair once: run `campi pair` on the PC and scan its
QR code with the Camera app (or type the address and code). The token lives in the Keychain; media URLs are
signed per device. Details: [`api/api-contract.md`](../api/api-contract.md) §1 and §3.

## Requirements
- Xcode 27 (Swift 6), XcodeGen (`brew install xcodegen`), Python 3.11+, ffmpeg (for mock media)
- Your own Apple Developer team (`DEVELOPMENT_TEAM` in `project.yml`), bundle id `com.braxtonmills.campi`
- Installing on an iOS 27.2 beta device may need the matching Xcode beta

| Path | What |
|---|---|
| [`../api/api-contract.md`](../api/api-contract.md) | **Source of truth for the PC's HTTP API.** The PC is built to match it. |
| `docs/spec-questions.md` | Ambiguities in the PC specs and the defaults the contract chose. |
| `docs/pc-sightings-spec.md`, `docs/pc-ui-spec.md` | The PC-side specs the contract builds on. |
| [`../api/contract/schema.json`](../api/contract/schema.json) | Machine-readable response shapes. |
| [`../api/contract/fixtures/`](../api/contract/fixtures) | Example responses, generated by the mock. The Swift tests decode these. |
| `tools/mock_server.py` | Stdlib-only mock PC implementing the contract (synthetic data, Range, MJPEG, pairing). |
| `tools/contract_check.py` | Compliance test. Run it against the mock now and the real PC later. |
| `project.yml` | XcodeGen spec. `Campi.xcodeproj` is generated from it but committed as a fallback. |
| `CampiKit/` | Swift package: contract models, API client, pairing, Keychain storage, image cache (`swift test`). |
| `Campi/` | The SwiftUI app (Today, Highlights, Sightings, Collection, Timelapse, player, pairing, settings). |
| `CampiUITests/` | End-to-end smoke tests against the mock (skipped when it isn't running). |

## Build and test
```sh
xcodebuild -downloadComponent MetalToolchain   # once: Xcode 26+ needs it for the card foil shader
xcodegen generate                       # after editing project.yml or adding files
(cd CampiKit && swift test)             # package tests on the Mac
python3 tools/mock_server.py &          # for the UI tests and running the app in the simulator
# debug builds: -cardexGallery <page> shows sample cards for every holo pattern and scene, 4 per page
xcodebuild -project Campi.xcodeproj -scheme Campi \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro Max' -collect-test-diagnostics never test
```
In the simulator, pair with the mock at `http://127.0.0.1:8765` using the code the mock prints (or
`curl -X POST http://127.0.0.1:8765/mock/pair-code` for a new one).

Testing notes:
- Pairing asks for notification permission; the UI tests tap Allow. `simctl push` (and `/mock/push`) shows the
  banner and tests the tap, but doesn't run the notification extension: check the crop attachment with a real
  APNs push (Apple's Push Notifications Console, sandbox, to the token from Settings → Copy device token).
- The widget test adds the Campi widget to the simulator's home screen once; it stays there.
- The push environment follows the signing (`aps-environment` in the embedded profile), not Debug/Release:
  a Release build installed from Xcode is development-signed and gets sandbox tokens.
- Accessibility: `testAccessibilityAudit` runs Xcode's audit on every screen (contrast, hit areas, labels);
  `testLargestTextSize` screenshots every screen at the largest accessibility text size for review.
  `testNoSightingsDatabase` needs a second mock: `python3 tools/mock_server.py --port 8766 --no-sightings-db`.
- `testUnplayableVideoExplainsItself` needs a mock that cuts byte ranges short, like the PC bug found in integration:
  `python3 tools/mock_server.py --port 8767 --range-cap 4194304`. The player must explain, not just show AVKit's
  crossed-out play button. Player events are logged under subsystem `com.braxtonmills.campi`, category `player`.
- Cardex text is generated in the simulator only when this Mac has Apple Intelligence turned on; otherwise the
  test sees the fallback card. `-cardexFallback` forces the fallback (and an in-memory cache).
- Keep `-collect-test-diagnostics never`: without it, a failing UI test makes xcodebuild run
  `simctl diagnose` for up to 10 minutes, which looks like a hang.
- The Timelapse UI test saves to Photos; grant the simulator permission first:
  `xcrun simctl privacy booted grant photos-add com.braxtonmills.campi`.
- If simulator commands hang (heavy load), restart it: `xcrun simctl shutdown <udid>`, `xcrun simctl boot <udid>`,
  `xcrun simctl bootstatus <udid> -b`.
- `build/` (derived data, test results) is git-ignored; add `build/.metadata_never_index` to keep Spotlight out.

## Mock PC
```sh
python3 tools/mock_server.py               # http://127.0.0.1:8765, bearer token "mock-token"; prints a pairing link
python3 tools/mock_server.py --v1 --sightings-off   # today's service: schema v1, reserved fields null
python3 tools/mock_server.py --write-fixtures       # regenerate ../api/contract/fixtures (fixed clock + seed)
python3 tools/mock_server.py --live busy --rotation 90   # Live: ok | busy | unreachable | unavailable
curl -X POST -d '{"state":"unreachable"}' http://127.0.0.1:8765/mock/live   # change it while running
curl -X POST -d '{"type":"new_catch"}' http://127.0.0.1:8765/mock/push      # simctl push a contract payload
curl -X POST -d '{"seconds":60}' http://127.0.0.1:8765/mock/offline         # act unreachable (widget, Today)
python3 tools/mock_server.py --print-push new_catch > /tmp/p.apns && xcrun simctl push booted com.braxtonmills.campi /tmp/p.apns
```
The first run uses ffmpeg (`brew install ffmpeg`) to generate sample media into `tools/mock_media/`
(git-ignored).

## Contract compliance
```sh
python3 tools/contract_check.py --fixtures                                  # fixtures vs schema
python3 tools/contract_check.py http://127.0.0.1:8765 --token mock-token    # a live server
python3 tools/contract_check.py https://<pc>.<tailnet>.ts.net --pair K3J9-Q2M8   # the real PC, code from `campi pair`
```
Against the real PC, `--pair` pairs a throwaway "contract-check" device with a code from `campi pair` and prints its
id; revoke it afterwards with `campi devices revoke <id>`. The check changes that device's push settings and
restores the stars, hides and label corrections it touches.

## Install on an iPhone
```sh
xcodebuild -project Campi.xcodeproj -scheme Campi -configuration Release -destination 'generic/platform=iOS' \
  -derivedDataPath build/device -allowProvisioningUpdates build
xcrun devicectl list devices
xcrun devicectl device install app --device <UDID> build/device/Build/Products/Release-iphoneos/Campi.app
```
Automatic signing creates the App IDs and profiles for the app and both extensions (App Group, Keychain sharing,
Push). The phone needs Developer Mode on. Xcode 27.0 installs fine on the iOS 27.2 beta.

To try the phone against the mock: `python3 tools/mock_server.py --host 0.0.0.0 --public-url http://<mac-ip>:8765`
(the Mac's Wi-Fi or Tailscale address). The mock prints each device's APNs token when it registers, for a test push
from Apple's Push Notifications Console (sandbox).

## App icon
`swift tools/make_icon.swift` draws the light, dark and tinted icons into `AppIcon.appiconset` (CoreGraphics; no
SF Symbols, which can't be used in app icons).
