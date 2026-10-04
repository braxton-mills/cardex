"""Optional vehicle sightings worker (ported from car_id.py): YOLO + ByteTrack on the Pi's MJPEG stream,
SigLIP 2 zero-shot make/model scoring averaged over each vehicle's pass, one SQLite row per pass plus its best
crop, the full frame it came from, and a short clip.

Backends: "openvino" (YOLO26 + SigLIP 2 vision tower on the Intel UHD 770, see sightings_ov.py; never the RTX card)
and "cuda" (YOLO11m + full SigLIP 2 on the RTX card). Crops are collected during a pass and scored when it ends,
on a classify thread, so classification never slows detection.

Runs in its own venv (torch etc.) as a supervised child; the timelapse never imports this module.
All timing uses frame timestamps, so `sightings-test` replays a recording deterministically.
"""
from __future__ import annotations

import http.client
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import sightings_db as db
from .capture import StreamError, iter_jpegs, validate
from .config import NO_WINDOW, side_log, write_json
from .discover import current_host, with_host
from .imageproc import ROTATE
from .labels import Labels, labels_path

log = logging.getLogger("sightings")
for _noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):  # model-download chatter
    logging.getLogger(_noisy).setLevel(logging.WARNING)

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}  # COCO class ids
CLIP_PRETRAINED = "webli"
PROMPTS = ["a photo of a {}.", "a {} driving on the road.", "a low resolution photo of a {}."]
MAX_CLIP_S = 60       # clips stop this long after a vehicle is first seen (bounds memory for parked cars)
SPOT_EMPTY_S = 600    # a stationary spot is forgotten after it has been empty this long
SPOT_IOU = 0.5
NIGHT_CHECK_S = 60
# The Pi stretches exposures at night (up to 1 s); below this frame rate the frames are too blurred to track
SLOW_STREAM_FPS = 4
CPU_THREADS = 2       # torch / OpenCV threads in the worker
STREAM_ERRORS = (OSError, StreamError, http.client.HTTPException, StopIteration)


# ---------------------------------------------------------------- labels and models

class MakeModelClassifier:
    """Zero-shot make/model scoring with SigLIP 2 (same as car_id.py)."""

    def __init__(self, model_name: str, labels: Labels, device: str):
        import open_clip
        import torch
        self.torch = torch
        self.device = device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=CLIP_PRETRAINED, device=device)
        self.model.eval()
        tokenizer = open_clip.get_tokenizer(model_name)
        with torch.no_grad(), self._autocast():
            per_prompt = []
            for tmpl in PROMPTS:
                tokens = tokenizer([tmpl.format(label) for label in labels.names]).to(device)
                f = self.model.encode_text(tokens).float()
                per_prompt.append(f / f.norm(dim=-1, keepdim=True))
            text = torch.stack(per_prompt).mean(0)
            self.text = text / text.norm(dim=-1, keepdim=True)
        self.scale = self.model.logit_scale.exp().item()

    def _autocast(self):
        return self.torch.autocast("cuda", dtype=self.torch.float16, enabled=self.device == "cuda")

    def score(self, crops_bgr):
        """One probability row over the labels per crop."""
        from PIL import Image
        with self.torch.no_grad():
            imgs = [self.preprocess(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))) for c in crops_bgr]
            x = self.torch.stack(imgs).to(self.device)
            with self._autocast():
                f = self.model.encode_image(x).float()
            f = f / f.norm(dim=-1, keepdim=True)
            return (self.scale * f @ self.text.T).softmax(dim=-1).cpu().numpy()


def gpu_report(cfg) -> dict:
    import torch
    info = {"torch": torch.__version__, "cuda_build": torch.version.cuda, "cuda": torch.cuda.is_available()}
    if info["cuda"]:
        free, total = torch.cuda.mem_get_info()
        info.update(device=torch.cuda.get_device_name(0), free_gib=round(free / 2**30, 1),
                    total_gib=round(total / 2**30, 1))
    msg = (f"sightings worker: torch {info['torch']} (CUDA build {info['cuda_build']}) "
           f"cuda_available={info['cuda']}"
           + (f" device=[0 {info['device']}] free_vram={info['free_gib']}/{info['total_gib']} GiB"
              if info["cuda"] else ""))
    (log.info if info["cuda"] else log.error)(msg)
    side_log(cfg, "gpu").info(msg)
    return info


class Models:
    def __init__(self, cfg, work_dir: Path, backend: str | None = None):
        self.backend = backend or cfg.sightings.backend
        if self.backend == "openvino":
            os.environ["CUDA_VISIBLE_DEVICES"] = ""  # before torch (via ultralytics) loads: the RTX card stays invisible
        import torch
        import yaml
        import ultralytics
        # The work is on a GPU; by default torch/OpenCV burst across every core, competing with renders/capture.
        torch.set_num_threads(CPU_THREADS)
        cv2.setNumThreads(CPU_THREADS)
        s = cfg.sightings
        self.labels = Labels(labels_path(cfg))

        # ultralytics' ByteTrack keeps a lost track for track_buffer *frames*; size it to lost_after_s at our
        # detect rate, so an ID we already reported isn't revived as the same vehicle.
        base = yaml.safe_load((Path(ultralytics.__file__).parent / "cfg" / "trackers" / "bytetrack.yaml")
                              .read_text(encoding="utf-8"))
        base["track_buffer"] = max(1, math.ceil(s.lost_after_s * s.detect_fps))
        work_dir.mkdir(parents=True, exist_ok=True)
        self.tracker_yaml = work_dir / "bytetrack_campi.yaml"
        self.tracker_yaml.write_text(yaml.safe_dump(base), encoding="utf-8")
        self.info: dict = {"backend": self.backend}
        if self.backend == "openvino":
            self._init_openvino(cfg)
        else:
            self._init_cuda(cfg)
        log.info("models ready: %s, tracker buffer %d frames", self.desc, base["track_buffer"])

    def _init_openvino(self, cfg):
        import openvino as ov
        from . import sightings_ov as sov
        s = cfg.sightings
        core = ov.Core()
        dev, name, fallback = sov.resolve_device(core, s.ov_device)
        precision = sov.detect_precision(cfg)
        why = "" if not fallback else f"no Intel device for ov_device={s.ov_device!r}"
        try:
            self.yolo, ran_on = sov.load_detector(cfg, precision, dev)
            self.clf = sov.SiglipOV(cfg, self.labels, PROMPTS, core, dev)
        except FileNotFoundError:
            raise
        except Exception as e:  # e.g. the GPU plugin can't run from this session: CPU with 2 threads
            if dev == "CPU":
                raise
            why = f"{dev} failed: {type(e).__name__}: {str(e)[:200]}"
            dev, name, fallback = "CPU", core.get_property("CPU", "FULL_DEVICE_NAME"), True
            self.yolo, ran_on = sov.load_detector(cfg, precision, dev)
            self.clf = sov.SiglipOV(cfg, self.labels, PROMPTS, core, dev)
        names = {d: core.get_property(d, "FULL_DEVICE_NAME") for d in core.available_devices}
        if any("nvidia" in names.get(d, "").lower() for d in ran_on):  # belt and braces: never the RTX card
            raise RuntimeError(f"OpenVINO compiled on {ran_on}: refusing to run on the NVIDIA GPU")
        self.info = {**sov.ov_report(cfg, core, dev, name, fallback, why), "precision": precision,
                     "execution_devices": ran_on}
        self.track_kw = dict(device=f"intel:{dev.lower()}", imgsz=s.detect_imgsz)
        self.desc = f"{s.detect_model} {precision} @{s.detect_imgsz} + {s.clip_model} vision on {dev} ({name})"

    def _init_cuda(self, cfg):
        from ultralytics import YOLO
        s = cfg.sightings
        weights = Path(s.yolo_weights)
        if not weights.is_absolute() and weights.parent == Path("."):
            mdir = cfg.paths.sightings / "models"
            mdir.mkdir(parents=True, exist_ok=True)
            cwd = os.getcwd()
            os.chdir(mdir)  # ultralytics downloads a bare weights name into the current directory
            try:
                self.yolo = YOLO(weights.name)
            finally:
                os.chdir(cwd)
        else:
            self.yolo = YOLO(str(weights))
        log.info("loading %s (%d labels)...", s.clip_model, len(self.labels.names))
        self.clf = MakeModelClassifier(s.clip_model, self.labels, "cuda")
        # agnostic NMS: one box per vehicle (a van is often both "car" and "bus"); YOLO26 is NMS-free
        self.track_kw = dict(device=0, imgsz=s.yolo_imgsz, agnostic_nms=True)
        self.desc = f"{weights.name} @{s.yolo_imgsz} + {s.clip_model} on CUDA"
        self.info.update(device="cuda:0", cpu_fallback=False)

    def reset_tracker(self):
        pred = getattr(self.yolo, "predictor", None)
        for t in getattr(pred, "trackers", None) or []:
            t.reset()


# ---------------------------------------------------------------- tracks

def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Track:
    """Everything we know about one vehicle while it's in view (car_id.py's Track plus pass geometry)."""

    def __init__(self, yolo_class: str, now: float):
        self.uid = str(uuid.uuid4())
        self.yolo_class = yolo_class
        self.first_seen = self.last_seen = self.last_seen_prev = now
        self.box = None
        self.n = 0                          # candidate crops offered (>= min_crop_px); min_samples counts these
        self.cands: list[tuple] = []        # best classify_crops: (area, sharpness, crop, source jpeg)
        self.frames = 0
        self.max_box_px = 0
        self.first_cx = self.last_cx = self.last_cy = None
        self.vx = self.vy = 0.0             # px/s, from the last two sightings (for stitching)
        self.cx_min = self.cy_min = math.inf
        self.cx_max = self.cy_max = -math.inf
        self.in_roi = False
        self.clip_part: Path | None = None  # set when a long pass's clip was cut at MAX_CLIP_S

    def see(self, box, now: float):
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        self.last_seen, self.box = now, box
        self.frames += 1
        self.max_box_px = max(self.max_box_px, x2 - x1, y2 - y1)
        if self.first_cx is None:
            self.first_cx = cx
        elif now > self.last_seen_prev:
            self.vx = (cx - self.last_cx) / (now - self.last_seen_prev)
            self.vy = (cy - self.last_cy) / (now - self.last_seen_prev)
        self.last_seen_prev = now
        self.last_cx, self.last_cy = cx, cy
        self.cx_min, self.cx_max = min(self.cx_min, cx), max(self.cx_max, cx)
        self.cy_min, self.cy_max = min(self.cy_min, cy), max(self.cy_max, cy)
        return cx, cy

    def offer(self, area: int, crop, jpeg: bytes, keep: int):
        """Keep the best `keep` crops: largest box first, ties broken by sharpness (Laplacian variance)."""
        self.n += 1
        if len(self.cands) >= keep and area < self.cands[-1][0]:
            return
        sharp = float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        self.cands.append((area, sharp, crop.copy(), jpeg))
        self.cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
        del self.cands[keep:]

    @property
    def best_crop(self):
        return self.cands[0][2] if self.cands else None

    @property
    def best_jpeg(self):
        return self.cands[0][3] if self.cands else None

    def extent(self) -> float:
        return max(self.cx_max - self.cx_min, self.cy_max - self.cy_min)


def day_and_stem(t: Track, label: str = "") -> tuple[str, str]:
    dt = datetime.fromtimestamp(t.first_seen)
    return dt.date().isoformat(), f"{dt:%H%M%S}_{t.uid[:8]}" + (f"_{slug(label)}" if label else "")


class Ring:
    """Recent stream frames as (ts, jpeg bytes), for clips with pre-roll."""

    def __init__(self):
        self.q: deque[tuple[float, bytes]] = deque()

    def add(self, ts, jpeg):
        self.q.append((ts, jpeg))

    def trim(self, keep_from: float):
        while self.q and self.q[0][0] < keep_from:
            self.q.popleft()

    def window(self, start: float, end: float) -> list[tuple[float, bytes]]:
        return [(t, j) for t, j in self.q if start <= t <= end]


# ---------------------------------------------------------------- media + db writer

class Writer(threading.Thread):
    """Single background thread: media first, then the row, so a row never points at a missing file."""

    def __init__(self, cfg, root: Path, backend: str = "cuda", cloud: bool = False, labels=None):
        super().__init__(daemon=True, name="sightings-writer")
        self.cfg, self.root = cfg, root
        self.q: queue.Queue = queue.Queue()
        self.written = 0
        self.encode_ms, self.encoded = 0.0, 0
        self.cloud = cloud
        self.label_set = set(labels.names) if labels else set()
        if backend == "openvino":  # Quick Sync on the Intel iGPU; never NVENC on this backend
            from .sightings_ov import encoder_available
            self.encoders = (["qsv"] if encoder_available(cfg, "h264_qsv") else []) + ["x264"]
        else:
            from .render import nvenc_available
            self.encoders = (["nvenc"] if nvenc_available(cfg) else []) + ["x264"]
        log.info("clip encoder: %s", " -> ".join({"qsv": "h264_qsv", "nvenc": "h264_nvenc", "x264": "libx264"}[e]
                                                    for e in self.encoders))
        self.start()

    def put(self, job):
        self.q.put(job)

    def close(self, timeout=300):
        self.q.put(None)
        self.join(timeout)

    def run(self):
        con = db.connect(self.root)
        while True:
            job = self.q.get()
            if job is None:
                break
            try:
                kind = job[0]
                if kind == "clip":
                    self.encode(job[1], job[2])
                elif kind == "discard":
                    if job[1]:
                        job[1].unlink(missing_ok=True)
                elif kind == "sighting":
                    self.sighting(con, *job[1:])
            except Exception:
                log.exception("sightings writer: %s job failed", job[0])
        con.close()

    def encode(self, frames: list[tuple[float, bytes]], out: Path) -> bool:
        """Pipe the stream's own JPEGs to ffmpeg -> h264 (NVENC, libx264 fallback), +faststart."""
        if len(frames) < 2:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        span = frames[-1][0] - frames[0][0]
        fps = max(1.0, min(60.0, (len(frames) - 1) / span)) if span > 0 else 30.0
        vf = {90: ["-vf", "transpose=1"], 180: ["-vf", "transpose=1,transpose=1"],
              270: ["-vf", "transpose=2"]}.get(self.cfg.image.rotation, [])
        tmp = out.with_name(out.stem + ".tmp.mp4")
        t0 = time.perf_counter()
        for enc in self.encoders:
            codec = {"nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                               "-pix_fmt", "yuv420p"],
                     "qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23", "-pix_fmt", "nv12"],
                     "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]}[enc]
            cmd = [self.cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "image2pipe", "-framerate", f"{fps:.3f}", "-c:v", "mjpeg", "-i", "-", *vf, *codec,
                   "-movflags", "+faststart", "-f", "mp4", str(tmp)]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 creationflags=NO_WINDOW)
            try:
                for _, jpeg in frames:
                    p.stdin.write(jpeg)
                p.stdin.close()
            except OSError:
                pass
            err = p.stderr.read().decode(errors="replace")
            if p.wait(timeout=300) == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                self.encode_ms += (time.perf_counter() - t0) * 1000
                self.encoded += 1
                return True
            tmp.unlink(missing_ok=True)
            log.warning("clip encode with %s failed: %s", enc, err[-500:])
        return False

    def _write_jpeg(self, path: Path, data: bytes):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def sighting(self, con, rec: dict, crop, frame_jpeg: bytes, clip_frames, clip_part: Path | None):
        day = self.root / rec.pop("_day")
        day.mkdir(parents=True, exist_ok=True)
        stem = rec.pop("_stem")
        ok, crop_jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError("crop encode failed")
        crop_p, frame_p, clip_p = day / f"{stem}_crop.jpg", day / f"{stem}_frame.jpg", day / f"{stem}.mp4"
        self._write_jpeg(crop_p, crop_jpg.tobytes())
        rot = ROTATE.get(self.cfg.image.rotation)
        if rot is not None:  # store the frame the way it was analysed
            img = cv2.rotate(cv2.imdecode(np.frombuffer(frame_jpeg, np.uint8), cv2.IMREAD_COLOR), rot)
            frame_jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        self._write_jpeg(frame_p, frame_jpeg)
        clip_ok = False
        if clip_part is not None:
            if clip_part.exists():
                os.replace(clip_part, clip_p)
                clip_ok = True
        elif clip_frames:
            clip_ok = self.encode(clip_frames, clip_p)
        rel = lambda p: p.relative_to(self.root).as_posix()  # noqa: E731
        rec.update(crop_path=rel(crop_p), frame_path=rel(frame_p), clip_path=rel(clip_p) if clip_ok else None)
        ask_cloud = rec.pop("_ask_cloud", False)
        if self.cloud and not ask_cloud and not db.label_seen_before(con, rec["label"]):
            ask_cloud = True  # first-ever sighting of this label
        rec["cloud_status"] = ("pending" if ask_cloud else "skipped") if self.cloud else None
        rec["updated_at"] = db.utc_iso(time.time())
        db.insert(con, rec)
        self.written += 1
        if self.cloud and ask_cloud:
            db.enqueue_cloud(con, rec["id"])


# ---------------------------------------------------------------- pipeline

class Pipeline:
    """Feed every stream frame to process(ts, jpeg); finished passes go to the Writer."""

    def __init__(self, cfg, models: Models, writer: Writer, classifier: "Classifier"):
        self.cfg, self.s, self.m, self.writer, self.classifier = cfg, cfg.sightings, models, writer, classifier
        self.crop_box = None                 # openvino: detection runs on the ROI's bounding box only
        self.decode_ms = self.detect_ms = 0.0
        self.tracks: dict[int, Track] = {}
        self.ring = Ring()
        self.spots: list[dict] = []          # remembered stationary vehicles: {"box", "last_occupied"}
        self.last_detect = -math.inf
        self.roi = None
        self.frames_in = self.detect_frames = self.stitched = self.split = 0
        self.frame_wh = (0, 0)
        self.last_sighting_ts = None
        self.end_after = max(self.s.lost_after_s, self.s.post_roll_s if self.s.save_clips else 0)

    # -- per frame
    def process(self, ts: float, jpeg: bytes):
        self.frames_in += 1
        if self.s.save_clips:
            self.ring.add(ts, jpeg)
        if ts - self.last_detect >= 1.0 / self.s.detect_fps - 1e-3:
            t0 = time.perf_counter()
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            self.decode_ms += (time.perf_counter() - t0) * 1000
            if img is not None:
                self.last_detect = ts
                self.detect(ts, jpeg, img)
        self.finish_lost(ts)
        self.cut_long_clips(ts)
        self.trim_ring(ts)

    def detect(self, now: float, jpeg: bytes, frame):
        rot = ROTATE.get(self.cfg.image.rotation)
        if rot is not None:
            frame = cv2.rotate(frame, rot)
        self.detect_frames += 1
        h, w = frame.shape[:2]
        if self.roi is None and len(self.s.roi) >= 3:
            self.roi = (np.array(self.s.roi, np.float32) * [w, h]).astype(np.float32)
            if self.m.backend == "openvino":
                from .sightings_ov import roi_box
                self.crop_box = roi_box(self.s.roi, w, h)
        x0, y0 = 0, 0
        sub = frame
        if self.crop_box:  # detect on the ROI's bounding box; boxes are mapped back to full-frame pixels below
            x0, y0, x1c, y1c = self.crop_box
            sub = frame[y0:y1c, x0:x1c]
        t0 = time.perf_counter()
        r = self.m.yolo.track(sub, persist=True, tracker=str(self.m.tracker_yaml), classes=list(VEHICLE_CLASSES),
                              verbose=False, **self.m.track_kw)[0]
        self.detect_ms += (time.perf_counter() - t0) * 1000
        boxes_now = []
        b = r.boxes
        dets = ([((bx1 + x0, by1 + y0, bx2 + x0, by2 + y0), tid, cls) for (bx1, by1, bx2, by2), tid, cls in
                 zip(b.xyxy.int().tolist(), b.id.int().tolist(), b.cls.int().tolist())]
                if b is not None and b.id is not None else [])
        self.frame_wh = (w, h)
        # Two IDs of one vehicle in the same frame: a wrong stitch, or ByteTrack recycling a lost ID for another
        # vehicle. Detach the later ID; below it is handled like a new one (stitched or a new vehicle).
        owner: dict[int, int] = {}
        for _, tid, _ in dets:
            t = self.tracks.get(tid)
            if t is not None and owner.setdefault(id(t), tid) != tid:
                del self.tracks[tid]
                self.split += 1
        seen = {id(self.tracks[tid]) for _, tid, _ in dets if tid in self.tracks}
        for (x1, y1, x2, y2), tid, cls in dets:
            box = (x1, y1, x2, y2)
            boxes_now.append(box)
            t = self.tracks.get(tid)
            if t is None:
                t = self.stitch(box, now, seen)
                self.stitched += t is not None
                if t is None:
                    t = Track(VEHICLE_CLASSES.get(cls, "vehicle"), now)
                seen.add(id(t))
                self.tracks[tid] = t
            cx, cy = t.see(box, now)
            if not t.in_roi:
                t.in_roi = self.roi is None or cv2.pointPolygonTest(self.roi, (float(cx), float(cy)), False) >= 0
            if not t.in_roi:
                continue  # only collect crops once a vehicle is inside the ROI
            bw, bh = x2 - x1, y2 - y1
            if bw < self.s.min_crop_px:
                continue
            px, py = int(bw * 0.06), int(bh * 0.06)  # a little context around the box
            crop = frame[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
            if crop.size:  # scored when the pass ends (classify thread), never during it
                t.offer(bw * bh, crop, jpeg, self.s.classify_crops)
        for spot in self.spots:  # a remembered stationary spot stays "occupied" while anything sits on it
            if any(iou(spot["box"], bx) >= SPOT_IOU for bx in boxes_now):
                spot["last_occupied"] = now
        self.spots = [sp for sp in self.spots if now - sp["last_occupied"] <= SPOT_EMPTY_S]

    def stitch(self, box, now: float, seen: set) -> Track | None:
        """ByteTrack drops fast vehicles (its Kalman lags a car crossing the frame in ~2 s) and gives them a new
        ID. Re-attach a new ID to a vehicle lost < lost_after_s ago if the box is where that vehicle should be:
        about one box width from its extrapolated position if it was moving, or the same spot (IoU) if not."""
        x1, y1, x2, y2 = box
        cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
        fw, fh = self.frame_wh
        best, best_d = None, math.inf
        for t in self.open_tracks():
            gap = now - t.last_seen
            # gone for at least ~2 detect frames (one missed frame + a new ID right there = a different vehicle)
            if id(t) in seen or t.box is None or not 1.5 / self.s.detect_fps < gap <= self.s.lost_after_s:
                continue
            tw, th = t.box[2] - t.box[0], t.box[3] - t.box[1]
            if not 1 / 3 <= w / max(tw, 1) <= 3:  # loose: tracked boxes lag the true size of a car entering
                continue
            if math.hypot(t.vx, t.vy) * gap < self.s.stationary_px and t.extent() < self.s.stationary_px:
                ok, d = iou(box, t.box) >= SPOT_IOU, 0.0  # not moving: must be the same spot
            else:
                px, py = t.last_cx + t.vx * gap, t.last_cy + t.vy * gap
                inside = 0 <= px <= fw and 0 <= py <= fh  # predicted off-screen = it left
                dx, dy = abs(cx - px), abs(cy - py)
                ok, d = inside and dx <= 1.5 * max(w, tw) and dy <= 0.75 * max(h, th), dx + dy
            if ok and d < best_d:
                best, best_d = t, d
        return best

    def open_tracks(self) -> list[Track]:
        return list({id(t): t for t in self.tracks.values()}.values())

    # -- clip bookkeeping
    def clip_window(self, t: Track) -> tuple[float, float]:
        return t.first_seen - self.s.pre_roll_s, min(t.last_seen + self.s.post_roll_s, t.first_seen + MAX_CLIP_S)

    day_and_stem = staticmethod(lambda t, label="": day_and_stem(t, label))

    def cut_long_clips(self, now: float):
        """A pass longer than MAX_CLIP_S (e.g. a parked car): encode its clip now and stop pinning frames."""
        if not self.s.save_clips:
            return
        for t in self.open_tracks():
            if t.clip_part is None and now >= t.first_seen + MAX_CLIP_S:
                day, stem = self.day_and_stem(t)
                t.clip_part = self.writer.root / day / f"{stem}.part.mp4"
                self.writer.put(("clip", self.ring.window(*self.clip_window(t)), t.clip_part))

    def trim_ring(self, now: float):
        if not self.s.save_clips:
            return
        keep = now - self.s.pre_roll_s
        for t in self.tracks.values():
            if t.clip_part is None:
                keep = min(keep, t.first_seen - self.s.pre_roll_s)
        self.ring.trim(keep)

    # -- end of a pass
    def end(self, t: Track, now: float):
        """Finish a pass and drop every tracker ID that was stitched into it."""
        for tid in [tid for tid, x in self.tracks.items() if x is t]:
            del self.tracks[tid]
        self.finish(t, now)

    def finish_lost(self, now: float):
        for t in self.open_tracks():
            if now - t.last_seen > self.end_after:
                self.end(t, now)

    def flush(self, now: float, reason: str):
        """End every open pass (stream gap, night pause, end of a test file) and restart tracking."""
        if self.tracks:
            log.info("flushing %d open track(s): %s", len(self.open_tracks()), reason)
        for t in self.open_tracks():
            self.end(t, now)
        self.m.reset_tracker()
        self.last_detect = -math.inf

    def finish(self, t: Track, now: float):
        s = self.s
        if t.n < s.min_samples or t.best_crop is None or not t.in_roi:
            self.writer.put(("discard", t.clip_part))
            return
        stationary = t.extent() < s.stationary_px
        if stationary:
            if any(iou(sp["box"], t.box) >= SPOT_IOU for sp in self.spots):
                self.writer.put(("discard", t.clip_part))
                log.info("stationary %s at a remembered spot; not logged again", t.yolo_class)
                return
            self.spots.append({"box": t.box, "last_occupied": now})
        dx = (t.last_cx - t.first_cx) if t.first_cx is not None else 0
        direction = None if stationary or abs(dx) < s.stationary_px else ("LR" if dx > 0 else "RL")
        clip_frames = (self.ring.window(*self.clip_window(t)) if s.save_clips and t.clip_part is None else None)
        self.classifier.put((t, stationary, direction, clip_frames))
        self.last_sighting_ts = now


class Classifier(threading.Thread):
    """Scores a finished pass's candidate crops (area-weighted average, as before) and hands it to the Writer."""

    def __init__(self, cfg, models: Models, writer: Writer):
        super().__init__(daemon=True, name="sightings-classify")
        self.cfg, self.s, self.m, self.writer = cfg, cfg.sightings, models, writer
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.ms, self.crops, self.done = 0.0, 0, 0
        self.start()

    def put(self, job):
        self.q.put(job)

    def depth(self) -> int:
        return self.q.qsize() + int(self.busy)

    def close(self, timeout=600):
        self.q.put(None)
        self.join(timeout)

    def run(self):
        while True:
            job = self.q.get()
            if job is None:
                break
            self.busy = True
            try:
                self.classify(*job)
            except Exception:
                log.exception("classify failed")
            finally:
                self.busy = False

    def classify(self, t: Track, stationary: bool, direction, clip_frames):
        s, labels = self.s, self.m.labels
        areas = np.array([c[0] for c in t.cands], np.float64)
        t0 = time.perf_counter()
        probs = self.m.clf.score([c[2] for c in t.cands])
        ms = (time.perf_counter() - t0) * 1000
        self.ms += ms
        self.crops += len(t.cands)
        avg = (probs * areas[:, None]).sum(0) / areas.sum()  # closer, bigger views count more
        top = [(int(i), float(avg[i])) for i in np.argsort(avg)[::-1][:3]]
        best_i, p = top[0]
        label = labels.names[best_i]
        p2 = top[1][1] if len(top) > 1 else 0.0
        day, stem = day_and_stem(t, label)
        rec = {
            "_day": day, "_stem": stem,
            "_ask_cloud": p < s.unsure_below or (p - p2) < s.cloud.cloud_margin,
            "id": t.uid, "kind": "vehicle",
            "started_at": db.utc_iso(t.first_seen), "ended_at": db.utc_iso(t.last_seen),
            "yolo_class": t.yolo_class, "label": label, "make": labels.make[best_i], "model": labels.model[best_i],
            "confidence": round(p, 4),
            "runner_ups": json.dumps([{"label": labels.names[i], "p": round(q, 4)} for i, q in top[1:]]),
            "unsure": int(p < s.unsure_below), "stationary": int(stationary), "direction": direction,
            "track_frames": t.frames, "max_box_px": int(t.max_box_px),
            "source": "siglip", "siglip_label": label, "siglip_confidence": round(p, 4),
        }
        self.writer.put(("sighting", rec, t.best_crop, t.best_jpeg, clip_frames, t.clip_part))
        self.done += 1
        verdict = f"{label} ({p:.0%})" if p >= s.unsure_below else f"unsure, best guess {label} ({p:.0%})"
        others = "  ·  ".join(f"{labels.names[i]} {q:.0%}" for i, q in top[1:])
        log.info("%s %-10s %s%s%s   [%s]  (%d crops, %.0f ms/crop)",
                 datetime.fromtimestamp(t.first_seen).strftime("%H:%M:%S"), t.yolo_class, verdict,
                 "  stationary" if stationary else "", f"  {direction}" if direction else "", others,
                 len(t.cands), ms / max(1, len(t.cands)))


# ---------------------------------------------------------------- live worker

class Heartbeat(threading.Thread):
    """state/sightings.json every 5 s; `loop_ts` only moves when the main loop does (hang detection)."""

    def __init__(self, cfg):
        super().__init__(daemon=True, name="sightings-heartbeat")
        self.path = cfg.paths.state / "sightings.json"
        # ppid: the supervisor holds the venv launcher's pid (uv's pythonw.exe starts the real interpreter)
        self.data = {"pid": os.getpid(), "ppid": os.getppid(), "phase": "loading", "loop_ts": time.time(), "connected": False,
                     "host": None, "last_error": "", "last_sighting_ts": None, "started": time.time()}
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.write()
        self.start()

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)

    def write(self):
        with self.lock:
            write_json(self.path, {**self.data, "updated": time.time()})

    def run(self):
        while not self.stop_evt.wait(5):
            try:
                self.write()
            except OSError:
                pass


def open_stream(cfg):
    host = current_host(cfg)
    req = urllib.request.Request(with_host(cfg.stream.url, host), headers={"User-Agent": "campi-sightings"})
    resp = urllib.request.urlopen(req, timeout=cfg.stream.timeout_s)
    ctype = resp.headers.get("Content-Type", "")
    if "boundary=" not in ctype:
        resp.close()
        raise StreamError(f"unexpected content type {ctype!r}")
    return host, resp, iter_jpegs(resp, ctype.split("boundary=", 1)[1].strip().strip('"').encode())


def load_labels(cfg) -> Labels:
    return Labels(labels_path(cfg))


def clean_partial_media(root: Path):
    for p in [*root.glob("*/*.tmp"), *root.glob("*/*.part.mp4"), *root.glob("*/*.tmp.mp4")]:
        if p.stat().st_mtime < time.time() - 600:  # half-written media from a killed worker (no row has them)
            p.unlink(missing_ok=True)


def check(cfg, backend: str) -> int:
    """One-time model prep + a device report (`sightings-worker --check`)."""
    s = cfg.sightings
    if backend == "cuda":
        info = gpu_report(cfg)
        if not info["cuda"]:
            return 2
        models = Models(cfg, cfg.paths.sightings, "cuda")
        dummy = np.zeros((480, 640, 3), np.uint8)
        models.yolo.predict(dummy, imgsz=s.yolo_imgsz, verbose=False, device=0)
        models.clf.score([dummy[:128, :128]])
        free = __import__("torch").cuda.mem_get_info()[0] / 2**30
        print(f"sightings check OK (cuda): {info['device']}, {len(models.labels.names)} labels, "
              f"{free:.1f} GiB VRAM free with models loaded")
        return 0
    from . import sightings_ov as sov
    t0 = time.time()
    sov.export_detector(cfg, "fp16")
    if s.detect_precision in ("int8", "auto"):
        sov.export_detector(cfg, "int8")
    sov.export_siglip_visual(cfg, CLIP_PRETRAINED)
    sov.build_text_cache(cfg, load_labels(cfg), PROMPTS, CLIP_PRETRAINED)
    models = Models(cfg, cfg.paths.sightings, "openvino")
    frame = np.zeros((s.detect_imgsz, s.detect_imgsz, 3), np.uint8)
    t1 = time.perf_counter()
    for _ in range(10):
        models.yolo.predict(frame, verbose=False, **models.track_kw)
    det_ms = (time.perf_counter() - t1) * 100
    models.clf.score([frame[:200, :300]] * 3)
    i = models.info
    print(f"sightings check OK (openvino): device {i['device']} = {i['device_name']}"
          f"{'  CPU FALLBACK' if i['cpu_fallback'] else ''}; execution devices {i['execution_devices']}; "
          f"detect {s.detect_model} {i['precision']} @{s.detect_imgsz}: {det_ms:.0f} ms/frame; "
          f"SigLIP vision: {models.clf.ms_total / models.clf.crops:.0f} ms/crop; "
          f"{len(models.labels.names)} labels; prep took {time.time() - t0:.0f}s")
    return 0


def run_worker(cfg, check_only: bool = False, prep: bool = False) -> int:
    s, st = cfg.sightings, cfg.stream
    if s.backend == "openvino":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""  # the RTX card stays invisible to torch in this process
    cfg.paths.sightings.mkdir(parents=True, exist_ok=True)
    if prep:  # separate process: the running worker never loads SigLIP's text tower
        from . import sightings_ov as sov
        sov.build_text_cache(cfg, load_labels(cfg), PROMPTS, CLIP_PRETRAINED)
        return 0
    if check_only:
        return check(cfg, s.backend)
    hb = Heartbeat(cfg)
    if s.backend == "cuda":
        info = gpu_report(cfg)
        if not info["cuda"]:
            log.error("no CUDA GPU: refusing to run YOLO + SigLIP on the CPU (it would starve capture and renders)")
            hb.set(phase="error", last_error="CUDA not available")
            hb.write()
            return 2
    clean_partial_media(cfg.paths.sightings)
    models = Models(cfg, cfg.paths.sightings)
    hb.set(**{k: models.info.get(k) for k in ("backend", "device", "device_name", "cpu_fallback", "precision")})
    writer = Writer(cfg, cfg.paths.sightings, models.backend, cloud=s.cloud.enabled, labels=models.labels)
    classifier = Classifier(cfg, models, writer)
    pipe = Pipeline(cfg, models, writer, classifier)
    cloud = None
    if s.cloud.enabled:
        from .sightings_cloud import CloudWorker
        cloud = CloudWorker(cfg, cfg.paths.sightings, models.labels, hb.set)
    hb.set(phase="disconnected")
    backoff = st.backoff_initial_s
    last_frame = None
    log.info("sightings worker running: %s @ %g fps, roi=%s, clips=%s, cloud=%s", st.url, s.detect_fps,
             s.roi or "whole frame", s.save_clips, s.cloud.enabled)
    try:
        while True:
            resp = None
            try:
                host, resp, frames = open_stream(cfg)
                hb.set(phase="running", connected=True, host=host, loop_ts=time.time())
                log.info("connected: %s (%s)", st.url, host)
                next_night_check = 0.0
                recent = deque(maxlen=8)
                while True:
                    data, length = next(frames)
                    ts = time.time()
                    hb.set(loop_ts=ts, classify_queue=classifier.depth())
                    if length is not None and len(data) != length:
                        continue
                    if last_frame is not None and ts - last_frame > s.lost_after_s * 2:
                        log.warning("stream gap of %.0fs ended", ts - last_frame)
                        pipe.flush(last_frame, "stream gap")
                    last_frame = ts
                    backoff = st.backoff_initial_s
                    recent.append(ts)
                    if s.pause_at_night:
                        if len(recent) < recent.maxlen:
                            continue  # rate unknown yet (~0.25 s at 30 fps): don't feed night frames to tracking
                        fps = (len(recent) - 1) / max(recent[-1] - recent[0], 1e-3)
                        if fps < SLOW_STREAM_FPS:
                            raise _Dark(f"stream at {fps:.1f} fps (long night exposures)")
                    if s.pause_at_night and ts >= next_night_check:
                        next_night_check = ts + NIGHT_CHECK_S
                        luma = validate(data, length, 0, 0)
                        if luma is not None and luma < cfg.night.luma_threshold:
                            raise _Dark(f"luma {luma:.0f} < {cfg.night.luma_threshold}")
                    pipe.process(ts, data)
                    hb.set(last_sighting_ts=pipe.last_sighting_ts)
            except _Dark as d:
                if resp:
                    resp.close()
                pipe.flush(time.time(), "dark")
                if hb.data["phase"] != "paused_dark":
                    log.info("dark (%s): paused; checking every %ds", d.why, NIGHT_CHECK_S)
                hb.set(phase="paused_dark", connected=False)
                last_frame = None
                _sleep(hb, NIGHT_CHECK_S, classifier)
                continue
            except STREAM_ERRORS as e:
                err = f"{type(e).__name__}: {e}"
                hb.set(phase="disconnected", connected=False, last_error=err)
                if pipe.tracks:
                    pipe.flush(time.time(), "stream lost")
                delay = min(backoff, st.backoff_max_s)
                log.warning("stream error: %s; reconnecting in %.0fs", err, delay)
                _sleep(hb, delay, classifier)
                backoff = min(backoff * 2, st.backoff_max_s)
            finally:
                if resp:
                    resp.close()
    finally:
        try:  # after a GPU error this may fail too; don't mask the original exception
            pipe.flush(time.time(), "worker stopping")
        except Exception:
            log.exception("final flush failed")
        classifier.close(120)
        writer.close(60)
        if cloud:
            cloud.stop()
        hb.stop_evt.set()


class _Dark(Exception):
    def __init__(self, why: str):
        super().__init__(f"dark ({why})")
        self.why = why


def _sleep(hb: Heartbeat, seconds: float, classifier=None):
    """Sleep while telling the supervisor we are alive (not hung)."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        hb.set(loop_ts=time.time(), **({"classify_queue": classifier.depth()} if classifier else {}))
        time.sleep(min(1.0, left))


# ---------------------------------------------------------------- record / test

def record(cfg, seconds: float, out: Path | None = None) -> dict:
    """Save the raw stream (multipart parts + an X-Timestamp header each) for `sightings-test`."""
    out = out or cfg.paths.sightings / "recordings" / f"rec_{datetime.now():%Y%m%d_%H%M%S}.mjpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    host, resp, frames = open_stream(cfg)
    n = nbytes = 0
    t0 = time.time()
    try:
        with open(out, "wb") as f:
            while time.time() - t0 < seconds:
                data, _ = next(frames)
                ts = time.time()
                f.write(b"--FRAME\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(data)}\r\nX-Timestamp: {ts:.3f}\r\n\r\n".encode() + data + b"\r\n")
                n += 1
                nbytes += len(data)
    finally:
        resp.close()
    el = time.time() - t0
    return {"file": str(out), "host": host, "frames": n, "seconds": round(el, 1), "fps": round(n / el, 1),
            "mbit_s": round(nbytes * 8 / el / 1e6, 1), "mb": round(nbytes / 1e6, 1)}


def iter_recording(path: Path):
    """(ts, jpeg) from a sightings-record .mjpg file."""
    with open(path, "rb") as f:
        while True:
            line = f.readline()
            if not line:
                return
            if not line.startswith(b"--"):
                continue
            length, ts = None, None
            while True:
                h = f.readline().strip()
                if not h:
                    break
                k, _, v = h.partition(b":")
                k = k.strip().lower()
                if k == b"content-length":
                    length = int(v)
                elif k == b"x-timestamp":
                    ts = float(v)
            if length is None:
                return
            data = f.read(length)
            if len(data) < length:
                return
            yield ts, data


def iter_video(path: Path):
    """(ts, jpeg) from any video ffmpeg/OpenCV can read; timestamps from the file, anchored to now."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"can't open {path}")
    base = time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            ts = base + cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            yield ts, cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
    finally:
        cap.release()


def run_test(cfg, source: Path, backend: str | None = None, cloud: bool = False) -> tuple[list, dict]:
    """Run the pipeline on a file into a fresh test DB + folder per backend (no night pause).
    Returns (rows, per-stage timings)."""
    backend = backend or cfg.sightings.backend
    if backend == "openvino":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    root = cfg.paths.data / "sightings-test" / backend
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    if backend == "cuda" and not gpu_report(cfg)["cuda"]:
        raise RuntimeError("CUDA not available")
    t_load = time.time()
    models = Models(cfg, root, backend)
    t_load = time.time() - t_load
    writer = Writer(cfg, root, backend, cloud=cloud, labels=models.labels)
    classifier = Classifier(cfg, models, writer)
    pipe = Pipeline(cfg, models, writer, classifier)
    worker = None
    if cloud:
        from .sightings_cloud import CloudWorker
        worker = CloudWorker(cfg, root, models.labels, lambda **kw: None, poll_s=1.0)
    frames = iter_recording(source) if source.suffix.lower() in (".mjpg", ".mjpeg") else iter_video(source)
    n, t0, first, last = 0, time.time(), None, None
    for i, (ts, jpeg) in enumerate(frames):
        ts = ts if ts is not None else t0 + i / 30.0
        pipe.process(ts, jpeg)
        first = ts if first is None else first
        last = ts
        n += 1
    if last is not None:
        pipe.flush(last + pipe.end_after + 1, "end of file")
    detect_wall = time.time() - t0
    classifier.close()
    writer.close()
    cloud_info = {}
    if worker:
        deadline = time.time() + 180
        while worker.pending() and time.time() < deadline:
            time.sleep(1)
        worker.stop()
        cloud_info = {"cloud_requests": worker.requests, "cloud_ms_per_request": round(worker.ms / max(1, worker.requests)),
                      "cloud_updated": worker.updated, "cloud_capped": worker.capped, "cloud_errors": worker.errors}
    el = time.time() - t0
    timings = {
        "backend": backend, "device": models.info.get("device"), "device_name": models.info.get("device_name"),
        "cpu_fallback": models.info.get("cpu_fallback"), "precision": models.info.get("precision"),
        "model_load_s": round(t_load, 1), "frames": n, "detect_frames": pipe.detect_frames,
        "wall_s": round(el, 1), "source_s": round((last - first), 1) if n else 0,
        "realtime_x": round((last - first) / detect_wall, 2) if n and detect_wall else 0,
        "decode_ms_per_frame": round(pipe.decode_ms / max(1, pipe.detect_frames), 1),
        "detect_ms_per_frame": round(pipe.detect_ms / max(1, pipe.detect_frames), 1),
        "classify_ms_per_crop": round(classifier.ms / max(1, classifier.crops), 1), "crops": classifier.crops,
        "encode_ms_per_clip": round(writer.encode_ms / max(1, writer.encoded)), "clips": writer.encoded,
        "encoders": writer.encoders, "stitched": pipe.stitched, "split": pipe.split, **cloud_info,
    }
    log.info("test (%s): %s", backend, json.dumps(timings))
    con = db.connect_ro(root)
    rows = db.recent(con, 1000)[::-1] if con else []
    return rows, timings


# ---------------------------------------------------------------- benchmarks (openvino)

def run_bench(cfg, parity: bool = False) -> dict:
    """Detection FPS for fp16 and int8 at detect_imgsz (+ int8 recall vs fp16), SigLIP ms/crop, optional parity
    against torch on the CPU. Writes models/precision.json, which detect_precision = "auto" reads."""
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import openvino as ov
    from . import sightings_ov as sov
    s = cfg.sightings
    core = ov.Core()
    dev, name, fallback = sov.resolve_device(core, s.ov_device)
    out: dict = {"device": dev, "device_name": name, "cpu_fallback": fallback, "detect_imgsz": s.detect_imgsz}
    imgs = []
    for p in sov.calib_frames(cfg, 200):
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        box = sov.roi_box(s.roi, img.shape[1], img.shape[0])
        imgs.append(img[box[1]:box[3], box[0]:box[2]] if box else img)
    out["frames"] = len(imgs)
    boxes: dict[str, list] = {}
    for prec in ("fp16", "int8"):
        if not (sov.detect_dir(cfg, prec) / "metadata.yaml").exists():
            continue
        yolo, ran_on = sov.load_detector(cfg, prec, dev)
        kw = dict(device=f"intel:{dev.lower()}", imgsz=s.detect_imgsz, classes=list(VEHICLE_CLASSES), verbose=False)
        for im in imgs[:5]:
            yolo.predict(im, **kw)  # warm up
        res, t0 = [], time.perf_counter()
        for im in imgs:
            r = yolo.predict(im, **kw)[0]
            res.append([b for b in r.boxes.xyxy.int().tolist() if b[2] - b[0] >= s.min_crop_px])
        el = time.perf_counter() - t0
        boxes[prec] = res
        out[prec] = {"fps": round(len(imgs) / el, 1), "ms_per_frame": round(el * 1000 / len(imgs), 1),
                     "execution_devices": ran_on}
    if "fp16" in boxes and "int8" in boxes:
        ref = sum(len(b) for b in boxes["fp16"])
        hit = sum(any(iou(a, c) >= 0.5 for c in i8) for f16, i8 in zip(boxes["fp16"], boxes["int8"]) for a in f16)
        out["int8"]["recall_vs_fp16"] = round(hit / ref, 3) if ref else None
        out["int8"]["vehicles_compared"] = ref
    i8 = out.get("int8", {})
    ok = bool(i8) and i8["fps"] >= s.detect_fps and (i8.get("recall_vs_fp16") or 0) >= 0.97
    out["auto_choice"] = "int8" if ok else "fp16"
    sov.precision_file(cfg).write_text(json.dumps({"choice": out["auto_choice"], "measured": time.time(),
                                                   "fp16": out.get("fp16"), "int8": i8}, indent=2), encoding="utf-8")
    labels = load_labels(cfg)
    clf = sov.SiglipOV(cfg, labels, PROMPTS, core, dev)
    crops = sorted(cfg.paths.sightings.glob("*/*_crop.jpg"))[-50:]
    crop_imgs = [im for im in (cv2.imread(str(c), cv2.IMREAD_COLOR) for c in crops) if im is not None]
    if not crop_imgs:
        crop_imgs = [im[:300, :400] for im in imgs[:20]]
    clf.embed(crop_imgs[:3])  # warm up
    clf.ms_total, clf.crops = 0.0, 0
    ov_emb = clf.embed(crop_imgs)
    out["siglip_ms_per_crop"] = round(clf.ms_total / clf.crops, 1)
    if parity:
        out["parity"] = _parity(cfg, crop_imgs, ov_emb, clf)
    return out


def _parity(cfg, crop_imgs, ov_emb, clf) -> dict:
    """OpenVINO vs torch-CPU vision embeddings on real crops (open_clip's own preprocessing on the torch side)."""
    import open_clip
    import torch
    from PIL import Image
    torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
    model, _, preprocess = open_clip.create_model_and_transforms(cfg.sightings.clip_model, pretrained=CLIP_PRETRAINED,
                                                                 device="cpu")
    model.eval()
    ref = []
    with torch.no_grad():
        for c in crop_imgs:
            x = preprocess(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))).unsqueeze(0)
            f = model.encode_image(x).float()[0].numpy()
            ref.append(f / np.linalg.norm(f))
    ref = np.stack(ref)
    cos = (ref * ov_emb).sum(1)
    top_ref = np.argmax(ref @ clf.text.T, axis=1)
    top_ov = np.argmax(ov_emb @ clf.text.T, axis=1)
    return {"crops": len(crop_imgs), "cosine_mean": round(float(cos.mean()), 4),
            "cosine_min": round(float(cos.min()), 4), "top1_agreement": round(float((top_ref == top_ov).mean()), 3)}
