"""Retention: expire clips, thin old raw frames to N per minute, keep disk usage in check."""
from __future__ import annotations

import csv
import logging
import os
import shutil
import time
from datetime import date, datetime, timedelta

from . import render_index
from .frames import INDEX_HEADER

log = logging.getLogger("housekeep")

THINNED_MARK = ".thinned"


def _unlink(p, what: str) -> bool:
    """Delete one file; a file that is open elsewhere (PermissionError) is left for the next hourly run."""
    try:
        p.unlink(missing_ok=True)
        return True
    except PermissionError:
        log.warning("%s %s is in use; will retry next run", what, p.name)
    except OSError as e:
        log.warning("could not delete %s %s: %s", what, p.name, e)
    return False


def expire_clips(cfg, now: float) -> int:
    n = 0
    cutoff = now - cfg.retention.clips_hours * 3600
    for p in cfg.paths.out.glob("campi_*.mp4"):  # latest.mp4 and daily\ never match
        try:
            old = p.stat().st_mtime < cutoff
        except OSError:
            continue  # gone since the glob
        if old and _unlink(p, "clip"):
            render_index.delete(cfg, p.name)
            n += 1
    for p in [*cfg.paths.out.glob("*.part.mp4"), *cfg.paths.daily.glob("*.part.mp4"),
              *cfg.paths.custom.glob("*.part.mp4")]:
        try:
            old = p.stat().st_mtime < now - 3600
        except OSError:
            continue
        if old:
            _unlink(p, "partial render")
    for d in cfg.paths.work.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < now - 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue
    expire_orphan_indexes(cfg)
    return n


def expire_orphan_indexes(cfg) -> None:
    """Render indexes whose clip / daily / custom video no longer exists (deleted by hand, or a crash mid-expiry)."""
    idir = render_index.index_dir(cfg)
    if not idir.is_dir():
        return
    for p in idir.glob("*.json"):
        name = p.stem + ".mp4"
        video = (cfg.paths.daily if name.startswith("campi_daily_") else
                 cfg.paths.custom if name.startswith("campi_custom_") else cfg.paths.out) / name
        if not video.exists():
            _unlink(p, "render index")


def thin_day(cfg, ddir, cutoff: float) -> tuple[int, int]:
    """Keep the first N frames of each minute for rows older than cutoff. Returns (kept, deleted)."""
    idx = ddir / "index.csv"
    if not idx.exists():
        return 0, 0
    with open(idx, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("ts")]
    keep_n = max(1, int(cfg.retention.thin_keep_per_minute))
    per_minute: dict[int, int] = {}
    kept, deleted = [], 0
    for r in rows:
        try:
            ts = float(r["ts"])
        except ValueError:
            continue
        if ts >= cutoff:
            kept.append(r)
            continue
        minute = int(ts // 60)
        if per_minute.get(minute, 0) < keep_n and (ddir / r["file"]).exists():
            per_minute[minute] = per_minute.get(minute, 0) + 1
            kept.append(r)
        else:
            (ddir / r["file"]).unlink(missing_ok=True)
            deleted += 1
    if deleted:
        tmp = idx.with_suffix(".csv.tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(INDEX_HEADER + "\n")
            for r in kept:
                f.write(f"{r['ts']},{r['file']},{r['luma']},{r['sha1']},{r['bytes']}\n")
        os.replace(tmp, idx)
        for h in ddir.iterdir():
            if h.is_dir() and not any(h.iterdir()):
                h.rmdir()
    return len(kept), deleted


def day_dirs(cfg):
    out = []
    for d in cfg.paths.frames.iterdir():
        try:
            out.append((date.fromisoformat(d.name), d))
        except ValueError:
            continue
    return sorted(out)


def thin_frames(cfg, now: float) -> None:
    cutoff = now - cfg.retention.raw_hours * 3600
    for day, ddir in day_dirs(cfg):
        if (ddir / THINNED_MARK).exists():
            continue
        day_start = datetime.combine(day, datetime.min.time()).timestamp()
        if day_start >= cutoff:
            break  # this day and later are entirely within the raw window
        kept, deleted = thin_day(cfg, ddir, cutoff)
        if deleted:
            log.info("thinned %s: kept %d, deleted %d", day, kept, deleted)
        if day_start + 86400 + 3600 < cutoff:  # whole day (plus DST slack) is past the raw window
            (ddir / THINNED_MARK).write_text(datetime.now().isoformat())


def expire_thinned(cfg, now: float) -> None:
    days = cfg.retention.thinned_retention_days
    if days <= 0:
        return
    limit = date.fromtimestamp(now) - timedelta(days=days)
    for day, ddir in day_dirs(cfg):
        if day < limit and (ddir / THINNED_MARK).exists():
            shutil.rmtree(ddir, ignore_errors=True)
            log.info("deleted thinned frames for %s (older than %d days)", day, days)


def disk_guard(cfg) -> float:
    free_gb = shutil.disk_usage(cfg.paths.frames).free / 1e9
    if free_gb >= cfg.retention.min_free_gb:
        return free_gb
    log.error("LOW DISK: %.1f GB free on frames drive (min %d GB)", free_gb, cfg.retention.min_free_gb)
    for day, ddir in day_dirs(cfg):  # oldest first; only thinned days are eligible
        if not (ddir / THINNED_MARK).exists():
            continue
        shutil.rmtree(ddir, ignore_errors=True)
        free_gb = shutil.disk_usage(cfg.paths.frames).free / 1e9
        log.error("disk guard deleted thinned frames for %s; now %.1f GB free", day, free_gb)
        if free_gb >= cfg.retention.min_free_gb:
            break
    return free_gb


def run(cfg) -> None:
    now = time.time()
    n = expire_clips(cfg, now)
    if n:
        log.info("expired %d clips older than %dh", n, cfg.retention.clips_hours)
    thin_frames(cfg, now)
    expire_thinned(cfg, now)
    free = disk_guard(cfg)
    used = sum(f.stat().st_size for f in cfg.paths.frames.rglob("*.jpg")) / 1e9
    log.info("housekeeping done: frames use %.1f GB, %.1f GB free", used, free)
    expire_sighting_clips(cfg, now)


def expire_sighting_clips(cfg, now: float) -> None:
    """Optional sightings: delete clips older than keep_clips_days (crops, frames and rows are kept).
    Only runs if the sightings database exists; a problem here never fails the rest of housekeeping."""
    from . import sightings_db
    if not sightings_db.db_path(cfg.paths.sightings).exists():
        return
    try:
        sightings_db.expire_clips(cfg.paths.sightings, cfg.sightings.keep_clips_days, now)
    except Exception:
        log.exception("sightings clip expiry failed")
