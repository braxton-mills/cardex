"""OpenVINO backend for the sightings worker: everything runs on the Intel UHD 770 (or the CPU), never the RTX card.

- Device: resolved by name from OpenVINO's device list. OpenVINO can also see the NVIDIA card (as GPU.1 through
  OpenCL), so a device whose name contains "NVIDIA" is never used; no Intel GPU -> CPU with 2 threads.
- Detection: YOLO26 exported to OpenVINO IR (FP16 or NNCF INT8) under sightings/models/, run by ultralytics.
- Classification: only SigLIP 2's vision tower as FP16 IR, compiled at LOW priority so detection always wins.
  Preprocessing is open_clip's (384x384 squash, bicubic, mean = std = 0.5) in numpy. Label text embeddings are
  computed once on the CPU with the full model by a separate `sightings-worker --prep` process and cached.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from .config import NO_WINDOW, side_log

log = logging.getLogger("sightings")

CPU_THREADS = 2
SIGLIP_SIZE = 384


def models_dir(cfg) -> Path:
    d = cfg.paths.sightings / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- device

def resolve_device(core, want: str) -> tuple[str, str, bool]:
    """(OpenVINO device id, full name, cpu_fallback). Never an NVIDIA device."""
    names = {d: core.get_property(d, "FULL_DEVICE_NAME") for d in core.available_devices}
    want = want.upper()
    if want != "CPU":
        cands = [d for d in names if (d == want or d.startswith(want + ".")) and "nvidia" not in names[d].lower()]
        cands.sort(key=lambda d: ("intel" not in names[d].lower(), d))  # prefer the Intel iGPU
        if cands:
            return cands[0], names[cands[0]], False
    return "CPU", names.get("CPU", "CPU"), want != "CPU"


def ov_report(cfg, core, device: str, name: str, fallback: bool, why: str = "") -> dict:
    import openvino as ov
    devs = {d: core.get_property(d, "FULL_DEVICE_NAME") for d in core.available_devices}
    listing = ", ".join(f"{d}={n}" + (" (never used)" if "nvidia" in n.lower() else "") for d, n in devs.items())
    msg = (f"sightings worker (openvino {ov.__version__.split('-')[0]}): devices: {listing}; using {device} = {name}"
           + (f"  CPU FALLBACK ({why or 'no Intel GPU'}; {CPU_THREADS} threads)" if fallback else ""))
    (log.warning if fallback else log.info)(msg)
    side_log(cfg, "gpu").info(msg)
    return {"backend": "openvino", "device": device, "device_name": name, "cpu_fallback": fallback,
            "devices": devs}


def compile_config(core, device: str, priority: str | None = None) -> dict:
    cfg = {"PERFORMANCE_HINT": "LATENCY"}
    supported = core.get_property(device, "SUPPORTED_PROPERTIES")
    if device == "CPU":
        cfg["INFERENCE_NUM_THREADS"] = CPU_THREADS
    if priority and "MODEL_PRIORITY" in supported:
        cfg["MODEL_PRIORITY"] = priority
    return cfg


def encoder_available(cfg, codec: str) -> bool:
    cmd = [cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "color=c=gray:s=256x256:r=30", "-t", "0.2", "-c:v", codec, "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- ROI

def roi_box(roi, w: int, h: int, margin: float = 0.05) -> tuple[int, int, int, int] | None:
    """Pixel bounding box of the normalized ROI polygon plus a margin, clamped; None = whole frame."""
    if len(roi) < 3:
        return None
    xs, ys = [p[0] for p in roi], [p[1] for p in roi]
    mx, my = (max(xs) - min(xs)) * margin, (max(ys) - min(ys)) * margin
    x0, x1 = max(0.0, min(xs) - mx), min(1.0, max(xs) + mx)
    y0, y1 = max(0.0, min(ys) - my), min(1.0, max(ys) + my)
    return int(x0 * w), int(y0 * h), int(math.ceil(x1 * w)), int(math.ceil(y1 * h))


# ---------------------------------------------------------------- detection model

def precision_file(cfg) -> Path:
    return models_dir(cfg) / "precision.json"


def detect_precision(cfg) -> str:
    p = cfg.sightings.detect_precision
    if p in ("fp16", "int8"):
        return p
    try:
        choice = json.loads(precision_file(cfg).read_text(encoding="utf-8")).get("choice")
    except (OSError, ValueError):
        choice = None
    return choice if choice in ("fp16", "int8") else "fp16"


def detect_dir(cfg, precision: str) -> Path:
    s = cfg.sightings
    return models_dir(cfg) / f"{s.detect_model}_{s.detect_imgsz}_{precision}_openvino_model"


def calib_frames(cfg, n: int = 300) -> list[Path]:
    """~n recent daytime frames (luma >= the night threshold) from the raw frame index, spread over the days."""
    rows = []
    for idx in sorted(cfg.paths.frames.glob("*/index.csv"))[-7:]:
        with open(idx, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    if float(r["luma"]) >= cfg.night.luma_threshold and (idx.parent / r["file"]).exists():
                        rows.append(idx.parent / r["file"])
                except (KeyError, ValueError):
                    continue
    random.Random(0).shuffle(rows)
    return rows[:n]


def build_calibration(cfg, n: int = 300) -> Path:
    """ROI-cropped calibration images + a dataset yaml for ultralytics' NNCF INT8 export."""
    import yaml
    root = models_dir(cfg) / "calib"
    shutil.rmtree(root, ignore_errors=True)
    (root / "images").mkdir(parents=True)
    frames = calib_frames(cfg, n)
    for i, p in enumerate(frames):
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        box = roi_box(cfg.sightings.roi, img.shape[1], img.shape[0])
        if box:
            img = img[box[1]:box[3], box[0]:box[2]]
        cv2.imwrite(str(root / "images" / f"{i:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    names = {i: n for i, n in enumerate(_coco_names())}
    yml = root / "calib.yaml"
    yml.write_text(yaml.safe_dump({"path": str(root), "train": "images", "val": "images", "names": names}),
                   encoding="utf-8")
    log.info("INT8 calibration set: %d daytime frames -> %s", len(frames), root)
    return yml


def _coco_names() -> list[str]:
    import yaml
    import ultralytics
    data = yaml.safe_load((Path(ultralytics.__file__).parent / "cfg" / "datasets" / "coco.yaml")
                          .read_text(encoding="utf-8"))
    return [data["names"][i] for i in sorted(data["names"])]


def export_detector(cfg, precision: str) -> Path:
    """Export detect_model to OpenVINO IR (FP16, or INT8 calibrated on our own daytime frames)."""
    from ultralytics import YOLO
    out = detect_dir(cfg, precision)
    if (out / "metadata.yaml").exists():
        return out
    mdir = models_dir(cfg)
    cwd = os.getcwd()
    os.chdir(mdir)  # ultralytics downloads a bare weights name into the current directory
    try:
        model = YOLO(f"{cfg.sightings.detect_model}.pt")
        kw = dict(format="openvino", imgsz=cfg.sightings.detect_imgsz, batch=1, dynamic=False, device="cpu")
        if precision == "int8":
            kw.update(quantize=8, data=str(build_calibration(cfg)))
        else:
            kw.update(quantize=16)
        t0 = time.time()
        produced = Path(model.export(**kw))
        produced = produced if produced.is_absolute() else mdir / produced  # ultralytics returns it cwd-relative
    finally:
        os.chdir(cwd)
    shutil.rmtree(out, ignore_errors=True)
    shutil.move(str(produced), str(out))
    log.info("exported %s %s @%d -> %s in %.0fs", cfg.sightings.detect_model, precision,
             cfg.sightings.detect_imgsz, out.name, time.time() - t0)
    return out


def load_detector(cfg, precision: str, device: str):
    """ultralytics YOLO on an exported IR, pinned to `device` (e.g. GPU.0 or CPU), never AUTO."""
    from ultralytics import YOLO
    d = detect_dir(cfg, precision)
    if not (d / "metadata.yaml").exists():
        raise FileNotFoundError(f"{d} missing: run `campi_timelapse sightings-worker --check`")
    yolo = YOLO(str(d), task="detect")
    yolo.predict(np.zeros((64, 64, 3), np.uint8), device=f"intel:{device.lower()}", imgsz=cfg.sightings.detect_imgsz,
                 verbose=False)  # builds the predictor and compiles on the device
    be = yolo.predictor.model.backend
    ran_on = list(be.ov_compiled_model.get_property("EXECUTION_DEVICES"))
    if device == "CPU":  # ultralytics compiles without a thread cap; recompile with ours
        import openvino as ov
        core = ov.Core()
        xml = next(d.glob("*.xml"))
        be.ov_compiled_model = core.compile_model(core.read_model(str(xml)), "CPU", compile_config(core, "CPU"))
    return yolo, ran_on


# ---------------------------------------------------------------- SigLIP 2

def siglip_xml(cfg) -> Path:
    return models_dir(cfg) / f"{_slug(cfg.sightings.clip_model)}_visual_fp16.xml"


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower())


def export_siglip_visual(cfg, pretrained: str) -> Path:
    """Only the vision tower, FP16 IR, static 1x3x384x384."""
    import open_clip
    import openvino as ov
    import torch
    xml = siglip_xml(cfg)
    if xml.exists():
        return xml
    t0 = time.time()
    model, _, _ = open_clip.create_model_and_transforms(cfg.sightings.clip_model, pretrained=pretrained, device="cpu")
    visual = model.visual.eval()
    with torch.no_grad():
        ovm = ov.convert_model(visual, example_input=torch.zeros(1, 3, SIGLIP_SIZE, SIGLIP_SIZE),
                               input=[1, 3, SIGLIP_SIZE, SIGLIP_SIZE])
    ov.save_model(ovm, str(xml), compress_to_fp16=True)
    log.info("exported %s vision tower -> %s in %.0fs", cfg.sightings.clip_model, xml.name, time.time() - t0)
    return xml


def preprocess(crop_bgr: np.ndarray) -> np.ndarray:
    """open_clip's SigLIP 2 384 transform (Resize((384, 384), bicubic) on PIL, ToTensor, Normalize(0.5, 0.5))."""
    from PIL import Image
    img = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)).resize((SIGLIP_SIZE, SIGLIP_SIZE),
                                                                             Image.BICUBIC)
    x = np.asarray(img, np.float32) / 255.0
    return ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]


def text_cache_path(cfg, labels_path: Path, prompts: list[str]) -> Path:
    h = hashlib.sha1()
    h.update(labels_path.read_bytes())
    h.update(cfg.sightings.clip_model.encode())
    h.update("\n".join(prompts).encode())
    return models_dir(cfg) / f"text_{_slug(cfg.sightings.clip_model)}_{h.hexdigest()[:12]}.npz"


def build_text_cache(cfg, labels, prompts: list[str], pretrained: str) -> Path:
    """Label text embeddings with the full model on the CPU (same prompt averaging as the CUDA classifier)."""
    import open_clip
    import torch
    out = text_cache_path(cfg, labels.path, prompts)
    if out.exists():
        return out
    torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))  # one-off; the worker isn't running models yet
    t0 = time.time()
    model, _, _ = open_clip.create_model_and_transforms(cfg.sightings.clip_model, pretrained=pretrained, device="cpu")
    model.eval()
    tok = open_clip.get_tokenizer(cfg.sightings.clip_model)
    with torch.no_grad():
        per_prompt = []
        for tmpl in prompts:
            f = model.encode_text(tok([tmpl.format(n) for n in labels.names])).float()
            per_prompt.append(f / f.norm(dim=-1, keepdim=True))
        text = torch.stack(per_prompt).mean(0)
        text = text / text.norm(dim=-1, keepdim=True)
    tmp = out.with_name(out.stem + ".tmp.npz")
    np.savez(tmp, text=text.numpy().astype(np.float32), scale=np.float32(model.logit_scale.exp().item()))
    os.replace(tmp, out)
    for old in out.parent.glob(f"text_{_slug(cfg.sightings.clip_model)}_*.npz"):
        if old != out:
            old.unlink(missing_ok=True)
    log.info("label text embeddings (%d labels) -> %s in %.0fs", len(labels.names), out.name, time.time() - t0)
    return out


def ensure_text_cache(cfg, labels, prompts: list[str]) -> Path:
    """Build the cache in a separate process so the worker never loads the text tower."""
    out = text_cache_path(cfg, labels.path, prompts)
    if out.exists():
        return out
    import sys
    log.info("label list changed (or first run): building text embeddings in a separate process...")
    r = subprocess.run([sys.executable, "-m", "campi_timelapse", "--config", str(cfg.config_path),
                        "sightings-worker", "--prep"], creationflags=NO_WINDOW, capture_output=True, text=True,
                       env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"text embedding prep failed ({r.returncode}): {(r.stderr or r.stdout)[-800:]}")
    return out


class SiglipOV:
    """SigLIP 2 vision tower on an OpenVINO device; scores crops against the cached label text embeddings."""

    def __init__(self, cfg, labels, prompts: list[str], core, device: str):
        xml = siglip_xml(cfg)
        if not xml.exists():
            raise FileNotFoundError(f"{xml} missing: run `campi_timelapse sightings-worker --check`")
        cache = np.load(ensure_text_cache(cfg, labels, prompts))
        self.text, self.scale = cache["text"], float(cache["scale"])
        self.compiled = core.compile_model(core.read_model(str(xml)), device,
                                           compile_config(core, device, priority="LOW"))
        self.req = self.compiled.create_infer_request()  # its own request: never shares detection's
        self.device = device
        self.ms_total, self.crops = 0.0, 0

    def embed(self, crops_bgr) -> np.ndarray:
        out = []
        for c in crops_bgr:
            t0 = time.perf_counter()
            f = np.asarray(self.req.infer({0: preprocess(c)})[0], np.float32)[0]
            self.ms_total += (time.perf_counter() - t0) * 1000
            self.crops += 1
            out.append(f / np.linalg.norm(f))
        return np.stack(out)

    def score(self, crops_bgr) -> np.ndarray:
        """One probability row over the labels per crop (softmax(scale * cos), as the CUDA classifier)."""
        logits = self.scale * self.embed(crops_bgr) @ self.text.T
        logits -= logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        return e / e.sum(axis=1, keepdims=True)
