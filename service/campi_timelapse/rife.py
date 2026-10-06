"""rife-ncnn-vulkan wrapper, with nvidia-smi sampling as proof the GPU did the work."""
from __future__ import annotations

import logging
import re
import statistics
import subprocess
import threading
import tempfile
import time
from pathlib import Path

from .config import NO_WINDOW

log = logging.getLogger("rife")


def _smi(args: list[str]) -> list[str]:
    try:
        out = subprocess.run(["nvidia-smi", *args, "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=5, creationflags=NO_WINDOW).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def gpu_sample() -> tuple[float, float] | None:
    rows = _smi(["--query-gpu=utilization.gpu,memory.used", "-i", "0"])
    try:
        util, mem = rows[0].split(",")
        return float(util), float(mem)
    except (IndexError, ValueError):
        return None


class GpuMonitor(threading.Thread):
    """Polls nvidia-smi while RIFE runs."""

    def __init__(self, pid_getter, period=0.5):
        super().__init__(daemon=True)
        self.pid_getter, self.period = pid_getter, period
        self.samples: list[tuple[float, float]] = []
        self.rife_seen = False
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.is_set():
            pid = self.pid_getter()
            if pid:  # only sample while a RIFE process is running
                s = gpu_sample()
                if s:
                    self.samples.append(s)
            if pid and not self.rife_seen:
                for row in _smi(["--query-compute-apps=pid,process_name"]):
                    if row.split(",")[0].strip() == str(pid) or "rife" in row.lower():
                        self.rife_seen = True
            self.stop_evt.wait(self.period)

    def stop(self):
        self.stop_evt.set()
        self.join(5)


class Rife:
    def __init__(self, cfg):
        self.exe = Path(cfg.tools.rife)
        self.model = self.exe.parent / cfg.tools.rife_model
        if not self.exe.is_file() or not self.model.is_dir():
            raise FileNotFoundError(f"RIFE not found: {self.exe} / {self.model}")
        self.device = ""
        want = cfg.tools.rife_gpu
        self.gpu = want if isinstance(want, int) else self._find_gpu(str(want))
        self.proc_pid = None
        self.busy_s = 0.0
        self.frames_out = 0

    def _find_gpu(self, name: str) -> int:
        """Vulkan index of the first device whose name contains `name`.

        Device order is not stable: under a no-logon scheduled task (session 0) the Intel iGPU is device 0.
        rife-ncnn-vulkan lists every device when it starts, so run it once on two tiny frames.
        """
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="rife_probe_") as td:
            src, dst = Path(td) / "in", Path(td) / "out"
            src.mkdir()
            dst.mkdir()
            for i in range(2):
                cv2.imwrite(str(src / f"{i:08d}.png"), np.full((64, 64, 3), 100 * i, np.uint8))
            out = subprocess.run([str(self.exe), "-i", str(src), "-o", str(dst), "-m", str(self.model),
                                  "-n", "3", "-f", "%08d.png"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=120,
                                 creationflags=NO_WINDOW).stdout
        devices = dict((int(i), n) for i, n in re.findall(r"^\[(\d+) ([^\]]+)\]", out or "", re.M))
        for i, n in sorted(devices.items()):
            if name.lower() in n.lower():
                log.info("RIFE device %r -> %d (%s); all: %s", name, i, n, devices)
                return i
        raise RuntimeError(f"no Vulkan device matching {name!r}; rife-ncnn-vulkan sees: {devices or out[-1000:]}")

    def interpolate(self, in_dir: Path, out_dir: Path, n_out: int) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # -j load:proc:save threads: PNG saving is the bottleneck (2:2:2 left the GPU ~90% idle; 4:4:8 is 2.6x faster)
        cmd = [str(self.exe), "-i", str(in_dir), "-o", str(out_dir), "-m", str(self.model),
               "-g", str(self.gpu), "-n", str(n_out), "-f", "%08d.png", "-j", "4:4:8"]
        t0 = time.monotonic()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             creationflags=NO_WINDOW)
        self.proc_pid = p.pid
        out, _ = p.communicate()
        self.proc_pid = None
        self.busy_s += time.monotonic() - t0
        if not self.device:
            dev = [l for l in out.splitlines() if l.startswith(f"[{self.gpu} ")]
            self.device = dev[0].split("]")[0] + "]" if dev else ""
        if p.returncode != 0:
            raise RuntimeError(f"rife-ncnn-vulkan failed ({p.returncode}): {out[-2000:]}")
        frames = sorted(out_dir.glob("*.png"))
        if len(frames) != n_out:
            raise RuntimeError(f"rife produced {len(frames)} frames, expected {n_out}")
        self.frames_out += n_out
        return frames


def summarize(mon: GpuMonitor, baseline: tuple[float, float] | None, rife: Rife) -> dict:
    utils = [u for u, _ in mon.samples]
    mems = [m for _, m in mon.samples]
    return {
        "device": rife.device or f"vulkan gpu {rife.gpu}",
        "baseline_util_pct": baseline[0] if baseline else None,
        "peak_util_pct": max(utils) if utils else None,
        "mean_util_pct": round(statistics.mean(utils), 1) if utils else None,
        "peak_mem_mib": max(mems) if mems else None,
        "mem_delta_mib": (max(mems) - baseline[1]) if mems and baseline else None,
        "rife_in_nvidia_smi_apps": mon.rife_seen,
        "samples": len(utils),
        "rife_seconds": round(rife.busy_s, 1),
        "rife_frames_out": rife.frames_out,
    }
