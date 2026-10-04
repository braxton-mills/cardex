"""Clip and daily rendering: select -> segment -> level/deflicker -> RIFE (per segment) -> NVENC encode."""
from __future__ import annotations

import json
import logging
import os
import shutil
import statistics
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from . import frames as fr
from . import render_index
from .config import NO_WINDOW, FileLock, read_json, side_log, write_json
from .imageproc import LightDimmer, apply_gain, crop_dims, deflicker_gains, level, night_weight, temporal_denoise
from .rife import GpuMonitor, Rife, gpu_sample, summarize

log = logging.getLogger("render")


class EncodeError(RuntimeError):
    pass


class Skip(Exception):
    pass


# ---------------------------------------------------------------- encoding

def nvenc_available(cfg) -> bool:
    cmd = [cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "color=c=gray:s=256x256:r=30", "-t", "0.2", "-c:v", "h264_nvenc", "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ffmpeg_cmd(cfg, encoder: str, w: int, h: int, fps: int, out: Path) -> list[str]:
    vf = []
    if cfg.deflicker.mode == "ffmpeg":
        vf.append("deflicker=mode=pm:size=10")
    vf.append("scale=out_color_matrix=bt709:out_range=tv,format=yuv420p")
    cmd = [cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "-",
           "-vf", ",".join(vf)]
    if encoder == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(cfg.output.cq),
                "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1", "-profile:v", "high"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(max(0, cfg.output.cq - 1)), "-profile:v", "high"]
    cmd += ["-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-r", str(fps), "-movflags", "+faststart", "-f", "mp4", str(out)]
    return cmd


def ffprobe(cfg, path: Path) -> dict:
    out = subprocess.run([cfg.tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, timeout=60,
                         creationflags=NO_WINDOW)
    return json.loads(out.stdout or "{}")


def draw_overlay(cfg, img: np.ndarray, ts: float) -> None:
    o = cfg.overlay
    text = datetime.fromtimestamp(ts).strftime(o.format)
    h, w = img.shape[:2]
    scale = o.scale * h / 1080
    thick = max(1, round(2 * scale))
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    m = round(18 * h / 1080)
    x = m if "left" in o.corner else w - tw - m
    y = m + th if "top" in o.corner else h - m - base
    cv2.putText(img, text, (x, y), font, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), font, scale, (255, 255, 255), thick, cv2.LINE_AA)


def replace_retry(src: Path, dst: Path, tries=5) -> None:
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # a player has the file open
            if i == tries - 1:
                raise
            time.sleep(2)


# ---------------------------------------------------------------- core

def render_frames(cfg, frames: list[fr.Frame], out_path: Path, job_name: str, encoder: str) -> dict:
    t0 = time.monotonic()
    o = cfg.output
    factor = int(o.interp_factor)
    fps = int(o.base_fps) * factor
    w, h = int(o.width), int(o.height)

    deltas = [b.ts - a.ts for a, b in zip(frames, frames[1:])]
    spacing = max(cfg.capture.interval_s, statistics.median(deltas) if deltas else 0)
    max_gap = cfg.render.gap_factor * spacing
    segs = fr.segment(frames, max_gap)

    probe = cv2.imread(str(frames[0].path))
    src_h, src_w = probe.shape[:2]
    if cfg.image.rotation in (90, 270):
        src_w, src_h = src_h, src_w
    if (src_w > src_h) != (w > h):
        raise ValueError(f"output {w}x{h} and the {src_w}x{src_h} (rotated) source differ in orientation; "
                         "swap output.width/height")
    cw, ch = crop_dims(probe.shape[1], probe.shape[0], cfg.image.rotation, cfg.image.level_deg, w, h)
    # Downscale before RIFE when the crop is bigger than the output (less GPU work, same result);
    # otherwise interpolate at native size and upscale afterwards.
    pre_scale = cw > w
    dim = LightDimmer(getattr(cfg.image, "dim_region", []), getattr(cfg.image, "dim_strength", 0.0))
    denoise_radius = max(0, int(getattr(cfg.night, "denoise_frames", 1)) - 1) // 2

    job = cfg.paths.work / job_name
    shutil.rmtree(job, ignore_errors=True)
    job.mkdir(parents=True)
    part = out_path.with_name(out_path.stem + ".part.mp4")
    rife = Rife(cfg) if factor > 1 else None
    baseline = gpu_sample()
    mon = GpuMonitor(lambda: rife.proc_pid if rife else None)
    mon.start()
    ff_log = open(job / "ffmpeg.log", "w+", encoding="utf-8")
    proc = subprocess.Popen(ffmpeg_cmd(cfg, encoder, w, h, fps, part), stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=ff_log, creationflags=NO_WINDOW)
    written = interpolated = 0
    encoded_ts: list[float] = []  # source frames actually encoded, in order (the render index)
    try:
        for si, seg in enumerate(segs):
            gains = (deflicker_gains([f.luma for f in seg], cfg.deflicker.window, cfg.deflicker.min_gain,
                                     cfg.deflicker.max_gain)
                     if cfg.deflicker.mode == "luma" else np.ones(len(seg)))
            sdir = job / f"seg{si:03d}"
            ind = sdir / "in"
            ind.mkdir(parents=True)
            kept = []

            def prepared(seg=seg, gains=gains):
                for f, g in zip(seg, gains):
                    img = cv2.imread(str(f.path), cv2.IMREAD_COLOR)
                    if img is None:
                        log.warning("unreadable frame skipped: %s", f.path)
                        continue
                    img = dim(img, f.luma)
                    img = level(img, cfg.image.rotation, cfg.image.level_deg, w, h)
                    if img.shape[1] != cw:  # a frame with a different source size slipped in
                        img = cv2.resize(img, (cw, ch), interpolation=cv2.INTER_AREA)
                    if pre_scale:
                        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
                    yield f, apply_gain(img, float(g))

            # Within a segment only: never average across a capture gap
            for f, img in temporal_denoise(prepared(), denoise_radius, lambda f: night_weight(f.luma)):
                cv2.imwrite(str(ind / f"{len(kept):08d}.png"), img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                kept.append(f)
            n = len(kept)
            if n == 0:
                continue
            encoded_ts += [f.ts for f in kept]
            if rife and n >= 2:
                outs = rife.interpolate(ind, sdir / "out", n * factor)
                items = [(p, kept[min(k // factor, n - 1)].ts) for k, p in enumerate(outs)]
                interpolated += len(outs)
            else:  # single frame (or RIFE off): hold it for the same duration
                items = [(ind / f"{i:08d}.png", f.ts) for i, f in enumerate(kept) for _ in range(factor)]
            for p, ts in items:
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img.shape[1] != w or img.shape[0] != h:
                    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LANCZOS4)
                if cfg.overlay.enabled:
                    draw_overlay(cfg, img, ts)
                try:
                    proc.stdin.write(img.tobytes())
                except OSError as e:
                    raise EncodeError(f"ffmpeg pipe closed: {e}") from e
                written += 1
            shutil.rmtree(sdir, ignore_errors=True)
        proc.stdin.close()
        rc = proc.wait(timeout=600)
        if rc != 0:
            raise EncodeError(f"ffmpeg exited {rc}")
    except BaseException:
        proc.kill()
        ff_log.seek(0)
        err = ff_log.read()[-1500:]
        if err:
            log.error("ffmpeg output: %s", err)
        part.unlink(missing_ok=True)
        raise
    finally:
        mon.stop()
        ff_log.close()

    info = ffprobe(cfg, part)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v or v.get("codec_name") != "h264" or int(v.get("nb_frames", 0) or 0) < written * 0.95:
        part.unlink(missing_ok=True)
        raise EncodeError(f"output failed validation: {v}")
    replace_retry(part, out_path)
    shutil.rmtree(job, ignore_errors=True)

    gpu = summarize(mon, baseline, rife) if rife else None
    result = {
        "out": str(out_path), "frames_in": len(frames), "segments": len(segs),
        "segment_sizes": [len(s) for s in segs], "max_gap_s": round(max_gap, 1),
        "frames_out": written, "interpolated_frames": interpolated, "fps": fps,
        "duration_s": round(float(info.get("format", {}).get("duration", 0)), 2),
        "size": f"{v['width']}x{v['height']}", "encoder": v.get("codec_name") + f" ({encoder})",
        "elapsed_s": round(time.monotonic() - t0, 1), "gpu": gpu,
        "frame_ts": encoded_ts,
    }
    if gpu:
        side_log(cfg, "gpu").info("%s RIFE %s peak_util=%s%% mean_util=%s%% (baseline %s%%) mem_delta=%sMiB "
                                  "in_nvidia_smi_apps=%s frames=%d in %.1fs", out_path.name, gpu["device"],
                                  gpu["peak_util_pct"], gpu["mean_util_pct"], gpu["baseline_util_pct"],
                                  gpu["mem_delta_mib"], gpu["rife_in_nvidia_smi_apps"], gpu["rife_frames_out"],
                                  gpu["rife_seconds"])
    return result


def render_with_fallback(cfg, frames, out_path: Path, job_name: str) -> dict:
    want = cfg.output.encoder
    encoder = "x264" if want == "x264" else ("nvenc" if nvenc_available(cfg) else "x264")
    if want != "x264" and encoder == "x264":
        log.warning("NVENC unavailable, falling back to libx264")
    try:
        return render_frames(cfg, frames, out_path, job_name, encoder)
    except EncodeError as e:
        if encoder != "nvenc":
            raise
        log.warning("NVENC encode failed (%s); retrying with libx264", e)
        return render_frames(cfg, frames, out_path, job_name, "x264")


# ---------------------------------------------------------------- entry points

def render_clip(cfg, end_ts: float | None = None, minutes: float | None = None) -> dict:
    minutes = minutes or cfg.render.window_min
    end_ts = end_ts or time.time()
    start_ts = end_ts - minutes * 60
    start = datetime.fromtimestamp(start_ts)
    out = cfg.paths.out / f"campi_{start:%Y-%m-%d_%H%M}.mp4"
    state = cfg.paths.state / "last_clip.json"
    lock = FileLock(cfg.paths.state / "render-clip.lock")
    if not lock.acquire():
        raise BlockingIOError("another clip render is running")
    try:
        all_frames = fr.frames_between(cfg, start_ts, end_ts)
        frames = fr.usable(cfg, all_frames)
        expected = minutes * 60 / cfg.capture.interval_s
        need = max(2, int(cfg.render.min_usable_frac * expected))
        if len(frames) < need:
            res = {"status": "skipped", "window_start": start.isoformat(timespec="seconds"),
                   "reason": f"{len(frames)} usable of {len(all_frames)} captured "
                             f"(need {need}; night threshold {cfg.night.luma_threshold})"}
            log.info("clip %s skipped: %s", out.name, res["reason"])
        else:
            log.info("rendering %s from %d frames (%d captured)", out.name, len(frames), len(all_frames))
            res = render_with_fallback(cfg, frames, out, f"clip_{start:%Y%m%d_%H%M}_{os.getpid()}")
            render_index.write(cfg, out, start_ts, end_ts, res.pop("frame_ts"))
            latest_state = cfg.paths.state / "latest.json"
            if start_ts >= (read_json(latest_state, {}) or {}).get("window_start_ts", 0):  # never go back in time
                tmp = cfg.paths.out / "latest.tmp.mp4"
                shutil.copyfile(out, tmp)
                replace_retry(tmp, cfg.paths.out / "latest.mp4")
                write_json(latest_state, {"window_start_ts": start_ts, "clip": str(out)})
            res.update(status="ok", window_start=start.isoformat(timespec="seconds"))
            log.info("clip done: %s", json.dumps(res))
            try:  # the clip is already published; an archive problem must not fail the render
                from .archive import append_pending
                res["archive"] = append_pending(cfg)
            except Exception:
                log.exception("archive append failed; will retry after the next clip")
        res["finished"] = time.time()
        write_json(state, res)
        return res
    finally:
        lock.release()


def render_daily(cfg, day: date | None = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    out = cfg.paths.daily / f"campi_daily_{day.isoformat()}.mp4"
    done_path = cfg.paths.state / "daily_done.json"
    lock = FileLock(cfg.paths.state / "render-daily.lock")
    if not lock.acquire():
        raise BlockingIOError("another daily render is running")
    try:
        all_frames = [f for f in fr.load_day(cfg, day) if f.path.exists()]
        frames = fr.usable(cfg, sorted(all_frames, key=lambda f: f.ts))
        if len(frames) < cfg.daily.min_frames:
            res = {"status": "skipped", "day": day.isoformat(),
                   "reason": f"{len(frames)} usable frames < {cfg.daily.min_frames}"}
            log.info("daily %s skipped: %s", day, res["reason"])
        else:
            target = int(cfg.daily.target_seconds * cfg.output.base_fps)
            sel = fr.sample_evenly(frames, target)
            log.info("rendering daily %s: %d of %d usable frames", day, len(sel), len(frames))
            res = render_with_fallback(cfg, sel, out, f"daily_{day.isoformat()}_{os.getpid()}")
            day_start = datetime.combine(day, datetime.min.time()).timestamp()
            day_end = datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp()
            render_index.write(cfg, out, day_start, day_end, res.pop("frame_ts"))
            res.update(status="ok", day=day.isoformat())
            log.info("daily done: %s", json.dumps(res))
        res["finished"] = time.time()
        done = read_json(done_path, {}) or {}
        done[day.isoformat()] = res["status"]
        write_json(done_path, done)
        write_json(cfg.paths.state / "last_daily.json", res)
        return res
    finally:
        lock.release()
