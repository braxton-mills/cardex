"""Config loading, path resolution, logging and small shared helpers."""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT / "config.toml"

# Child processes (ffmpeg, rife, nvidia-smi) must not flash console windows when we run under pythonw.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
BELOW_NORMAL = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0

# [sightings] defaults, so a config.toml without the section still loads (worker stays off).
SIGHTINGS_DEFAULTS = {
    "enabled": False, "detect_fps": 10, "yolo_weights": "yolo11m.pt", "yolo_imgsz": 1280,
    "clip_model": "ViT-SO400M-16-SigLIP2-384", "labels_file": "sightings_labels.txt", "min_crop_px": 64,
    "min_samples": 3, "lost_after_s": 2.0, "unsure_below": 0.30, "stationary_px": 40, "roi": [],
    "pause_at_night": True, "save_clips": True, "pre_roll_s": 2, "post_roll_s": 3, "keep_clips_days": 30,
    "backend": "openvino", "detect_model": "yolo26s", "detect_imgsz": 640, "detect_precision": "auto",
    "ov_device": "GPU", "classify_crops": 5,
}
CLOUD_DEFAULTS = {
    "enabled": False, "model": "gemini-3.1-flash-lite", "api_key_file": "{home}\\CampiTimelapse\\secrets\\gemini.key",
    "cloud_margin": 0.15, "cloud_max_per_day": 400,
}
GAMING_DEFAULTS = {
    "auto": True, "game_dirs": [], "extra_exes": [], "ignore_exes": [], "gpu_busy_pct": 20, "gpu_busy_s": 30,
    "defer_renders": True, "pause_sightings": False,
}

FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def downloads_dir() -> Path:
    """Real Downloads folder, honouring OneDrive / Known Folder redirection."""
    if os.name == "nt":
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(FOLDERID_DOWNLOADS)
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    return Path.home() / "Downloads"


def _ns(d):
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in d.items()})
    return d


def _expand(s: str) -> str:
    return (s.replace("{home}", str(Path.home()))
             .replace("{downloads}", str(downloads_dir()))
             .replace("{project}", str(PROJECT))
             .replace("{localappdata}", os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))))


def _tool(path: str, name: str) -> str:
    p = _expand(path)
    if Path(p).is_file():
        return p
    found = shutil.which(Path(p).name) or shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found at {p} or on PATH")
    return found


def load_config(path: Path | None = None) -> SimpleNamespace:
    path = Path(path or os.environ.get("CAMPI_CONFIG") or DEFAULT_CONFIG)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    raw["sightings"] = {**SIGHTINGS_DEFAULTS, **raw.get("sightings", {})}
    raw["sightings"]["cloud"] = {**CLOUD_DEFAULTS, **raw["sightings"].get("cloud", {})}
    raw["gaming"] = {**GAMING_DEFAULTS, **raw.get("gaming", {})}
    cfg = _ns(raw)
    cfg.sightings.cloud.api_key_file = _expand(cfg.sightings.cloud.api_key_file)
    cfg.config_path = path

    if cfg.image.rotation not in (0, 90, 180, 270):
        raise ValueError("image.rotation must be 0, 90, 180 or 270")
    if cfg.output.width % 2 or cfg.output.height % 2:
        raise ValueError("output.width/height must be even")
    if int(cfg.output.interp_factor) < 1:
        raise ValueError("output.interp_factor must be >= 1")

    data = Path(_expand(cfg.capture.data_dir))
    cfg.paths = SimpleNamespace(
        data=data,
        frames=data / "frames",
        logs=data / "logs",
        state=data / "state",
        work=data / "work",
        samples=data / "samples",
        out=Path(_expand(cfg.output.dir)),
    )
    cfg.paths.daily = cfg.paths.out / "daily"
    # [archive] dir: keep the long archive elsewhere (e.g. off a synced folder: each append rewrites the part)
    adir = getattr(cfg.archive, "dir", "")
    cfg.paths.archive = Path(_expand(adir)) if adir else cfg.paths.out / "archive"
    for p in vars(cfg.paths).values():
        p.mkdir(parents=True, exist_ok=True)
    # Optional sightings worker: not created here, so nothing new appears on disk while it is disabled.
    cfg.paths.sightings = data / "sightings"
    cfg.paths.sightings_python = data / "venv-sightings" / "Scripts" / "pythonw.exe"

    cfg.tools.ffmpeg = _tool(cfg.tools.ffmpeg, "ffmpeg")
    cfg.tools.ffprobe = _tool(cfg.tools.ffprobe, "ffprobe")
    cfg.tools.rife = _expand(cfg.tools.rife)
    return cfg


def setup_logging(cfg, name: str) -> logging.Logger:
    """One rotating log file per component; also echo to the console when there is one."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(cfg.paths.logs / f"{name}.log", maxBytes=10_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return logging.getLogger(name)


def side_log(cfg, name: str) -> logging.Logger:
    """Append-only log shared by several processes (gaps.log, gpu.log); never rotated, stays small."""
    log = logging.getLogger(f"side.{name}")
    if not log.handlers:
        h = logging.FileHandler(cfg.paths.logs / f"{name}.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(h)
    return log


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class FileLock:
    """Non-blocking exclusive lock; released automatically if the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        import msvcrt
        self.fh = open(self.path, "a+")
        try:
            self.fh.seek(0)
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self) -> None:
        if self.fh:
            import msvcrt
            try:
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.fh.close()
                self.fh = None

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError(f"locked: {self.path}")
        return self

    def __exit__(self, *exc):
        self.release()
