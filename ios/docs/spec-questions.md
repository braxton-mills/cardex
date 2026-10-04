# Open questions on the PC specs

These are ambiguities and conflicts found while writing `../../api/api-contract.md` against `docs/pc-ui-spec.md`,
`docs/pc-sightings-spec.md` and the PC repo as it is today. Each one has the default the contract adopted.
**Bold** items change or add to a PC spec, and need a yes/no before the PC side is built.

## Conflicts with pc-ui-spec

1. **The phone can't reach the API as specified.** pc-ui-spec binds the server to 127.0.0.1 and shuts it down
   when the window closes.
   **Default:** the server stays on 127.0.0.1, gets published over Tailscale (`tailscale serve`, never Funnel),
   and runs always-on under the supervisor when `[api] enabled = true` (contract §1, A.2). `campi ui` attaches to
   it when it's running.
2. **pc-ui-spec has no authentication.** Once the API is reachable over the tailnet, that's not acceptable.
   **Default:** a per-device bearer token on every request, including the desktop UI, plus device-bound signed
   media URLs, because `<video>` / `AVPlayer` can't send headers (contract §3).
3. **"Never hold a file handle open between requests" isn't enough.** A single long response (a phone
   downloading a clip on cellular, AVPlayer reading `bytes=0-`) can keep a handle open for minutes, so `os.replace`
   fails and `housekeeping.expire_clips` raises `PermissionError`, which aborts that whole hourly run.
   **Default:** media is read with `FILE_SHARE_DELETE` handles (§7.1), and housekeeping tolerates a locked file
   (A.6).
4. **Star and hide read as toggles.** `POST /api/sightings/{id}/star | hide` sounds like flipping state.
   **Default:** explicit-set bodies (`{"starred": true}`), so retries from a phone on a flaky network are safe.
5. **What can be starred or hidden?** "Anything I starred" and the global `S` key suggest any item.
   **Default:** sightings, clips and daily videos can be starred; only sightings can be hidden. A starred clip
   outlives its file as a window highlight.

## Under-specified in pc-ui-spec

6. **Rarity tiers.** Only "rare = seen 3 times or fewer" is defined.
   **Default:** uncaught 0 / rare 1–3 / uncommon 4–20 / common 21+, returned by the API (§6.2).
7. **What counts toward the collection, "new catch", "rare" and busiest windows?**
   **Default:** hidden rows never count; `unsure` rows count only if Gemini or you decided the label; stationary
   rows count (§6.1).
8. **The "rare" highlight is computed at query time**, so it disappears once the label passes 3 sightings.
   **Default:** kept as specified (a "new catch" stays forever).
9. **Busiest windows: over what period, and how many?**
   **Default:** the top 3 windows per local day with at least 3 sightings, aligned like the supervisor's 10-minute
   boundaries (§6.3).
10. **The collection tile image isn't specified.**
    **Default:** the crop of the most confident counted sighting.
11. **Label corrections: to what?**
    **Default:** only labels in the collection (labels file + discovered + labels from history); anything else →
    422. A correction applies everywhere, including which sighting counts as a label's "first".
12. **Daily-video seek "proportionally"**: proportional to what?
    **Default:** to usable minutes of the day, because frames are thinned to 1/min after 48 h, which skews any
    frame-count proportion. It's exact once the render index (13) exists.
13. **The seek math needs data the renderer throws away.** Unreadable frames are skipped. `campi test` (1 min) and
    `campi render-now` produce unaligned windows whose real span isn't in the file name. `render_daily` samples
    frames evenly.
    **Default:** `render_clip` / `render_daily` write a small render index (`state\render_index\*.json`) with the
    encoded frame timestamps (A.4). Video output is unchanged.
14. **DST fall-back:** during the repeated hour, two different 10-minute windows produce the same
    `campi_YYYY-MM-DD_HHMM.mp4` name, and the second render overwrites the first.
    **Default:** accepted. The render index's window is used when present. Worth fixing in the PC naming one
    day.
15. **Which instant does "View in timelapse" seek to?**
    **Default:** the midpoint of the pass. Copy says "around this time": with a 2 s capture interval, a fast car
    may not be in any frame.
16. **"Today": in which time zone?**
    **Default:** the PC's local day, with every timestamp carrying the PC's offset (§2.2).

## Under-specified in pc-sightings-spec

17. **The cloud answer.**
    - Which columns change when Gemini answers? The contract assumes `label/make/model/confidence` take Gemini's
      answer, `source = 'cloud'`, and `siglip_*` keep SigLIP's.
    - Is `unsure` recomputed? The contract shows it as stored, and a Gemini decision overrides it for counting.
    - Gemini's self-reported `confidence` isn't on the same scale as SigLIP's softmax share, but both are shown as
      a percentage, labeled by source.
    - How is a discovered car's `label` string built? The contract assumes the same rule as the labels file
      (`"Make Model"`, or just the model if it starts with the make).
18. **Cloud request state isn't persisted per sighting** (pending / failed / over the daily cap), yet the app shows
    "Asking Gemini…" and pushes wait for it.
    **Default:** add a `cloud_status` column (A.5). Until then `cloud_status` is `null`.
19. **There's no `updated_at` on sightings**, so a Gemini relabel minutes or hours later can't be fetched
    incrementally.
    **Default:** add `updated_at` in the v2 migration (A.5). The app refetches what's on screen meanwhile.
20. **`discovered_labels.count` drifts from reality** once label corrections exist.
    **Default:** the API counts from rows, and the table only marks a label as discovered.
21. **New `campi status` values aren't persisted anywhere.** Backend/device, CPU fallback, classify queue, cloud
    calls today, gaming state and the render queue are said to appear in `campi status`, but the spec doesn't say
    where they're persisted. Today they would exist only in process memory.
    **Default:** the PC persists them in `state\` (A.5). The contract reserves the fields as `null` until then.
22. **`pause_sightings` stops the worker while gaming.**
    **Default:** shown as sightings state `paused_gaming` (reserved), not as `not_running`.
23. **A gaming-deferred or queued window has no clip yet.**
    **Default:** seek answers `none` / `pending_render`.

## Environment and operations

24. **Tailscale disconnects at logoff by default on Windows**, but the service runs whether or not anyone is logged
    on.
    **Default:** `tailscale up --unattended` (A.8).
25. **Push needs a sender that survives restarts without duplicates or a burst of old sightings.**
    **Default:** a rowid high-water mark plus a `pushes` table in ui.db (§9.2). If the whole supervisor is down,
    nothing can push. The app and widget show "PC unreachable or Campi stopped".
26. **Low-bandwidth Live from the Pi's `/snapshot.jpg` would compete with capture.** That endpoint software-encodes
    under a lock, and capture already uses it every 2 s.
    **Default:** `/live.jpg` serves the newest frame capture already saved, downscaled on the PC (§8.2).
27. **OneDrive Files On-Demand** can make old daily videos cloud-only placeholders that a session-0 process can't
    read.
    **Default:** documented. Keep the campi output folder "Always keep on this device".
28. **Archive part race.** `archive.append_pending` creates a new part before `archive.json` says so.
    **Default:** the current part is the max of both (§6.5).

## Noted, no action

- `sightings.synced_at` exists in schema v1 but nothing writes it. The API ignores it.
- `kind` is always `vehicle` today. Other values are passed through.
- The `sightings-test` database is never exposed.
