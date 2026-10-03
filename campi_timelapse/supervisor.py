"""Single long-running entry point: keeps capture alive and launches renders/housekeeping as child processes."""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from .config import BELOW_NORMAL, NO_WINDOW, PROJECT, FileLock, read_json, write_json

log = logging.getLogger("supervisor")


class Job:
    """Windows Job Object with KILL_ON_JOB_CLOSE: children (and their ffmpeg/rife) die with the supervisor."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        k32 = ctypes.windll.kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = ctypes.c_void_p
        self.handle = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(ctypes.c_void_p(self.handle), 9, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            log.warning("SetInformationJobObject failed: %d", k32.GetLastError())

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle:
            ctypes.windll.kernel32.AssignProcessToJobObject(ctypes.c_void_p(self.handle),
                                                            ctypes.c_void_p(int(proc._handle)))


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.job = Job()
        self.capture = None
        self.capture_restarts = 0
        self.capture_backoff = 2.0
        self.capture_next_start = 0.0
        self.capture_started = 0.0
        self.clip = None          # (proc, started)
        self.daily = None
        self.housekeep = None
        self.daily_queue: list[date] = []
        self.stop_file = cfg.paths.state / "stop.request"
        self.status_path = cfg.paths.state / "status.json"
        self.started = time.time()

    # -- children ---------------------------------------------------------
    def spawn(self, *args: str, low_priority=False) -> subprocess.Popen:
        exe = sys.executable  # pythonw.exe under Task Scheduler, so children get no console either
        flags = NO_WINDOW | (BELOW_NORMAL if low_priority else 0)
        p = subprocess.Popen([exe, "-m", "campi_timelapse", "--config", str(self.cfg.config_path), *args],
                             cwd=str(PROJECT), creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.job.add(p)
        return p

    def check_capture(self, now: float) -> None:
        if self.capture and self.capture.poll() is None:
            return
        if self.capture:
            rc = self.capture.returncode
            self.capture = None
            self.capture_restarts += 1
            self.capture_next_start = now + self.capture_backoff
            log.error("capture exited with %s; restarting in %.0fs", rc, self.capture_backoff)
            self.capture_backoff = min(self.capture_backoff * 2, 60)
            return
        if now >= self.capture_next_start:
            self.capture = self.spawn("capture")
            self.capture_started = now
            log.info("capture started (pid %d)", self.capture.pid)

    def reset_backoff(self, now: float) -> None:
        """Forget earlier crashes once capture has stayed up for 5 minutes."""
        if self.capture and self.capture.poll() is None and now - self.capture_started > 300:
            self.capture_backoff = 2.0

    def reap(self, slot: str, timeout_s: float, now: float) -> bool:
        """True if the slot is free. Kills a child that exceeded its timeout."""
        cur = getattr(self, slot)
        if not cur:
            return True
        proc, started, label = cur
        if proc.poll() is not None:
            if proc.returncode != 0:
                log.error("%s exited with %d (see render/daily/housekeep log)", label, proc.returncode)
            else:
                log.info("%s finished in %.0fs", label, now - started)
            setattr(self, slot, None)
            return True
        if now - started > timeout_s:
            log.error("%s exceeded %.0f min; killing", label, timeout_s / 60)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
            setattr(self, slot, None)
            return True
        return False

    # -- schedule ---------------------------------------------------------
    def next_boundary(self, now: float) -> float:
        step = int(self.cfg.render.interval_min) * 60
        dt = datetime.fromtimestamp(now)
        midnight = datetime.combine(dt.date(), datetime.min.time()).timestamp()
        return midnight + ((now - midnight) // step + 1) * step

    def daily_due(self, d: date) -> bool:
        done = read_json(self.cfg.paths.state / "daily_done.json", {}) or {}
        if d.isoformat() in done:
            return False
        if (self.cfg.paths.daily / f"campi_daily_{d.isoformat()}.mp4").exists():
            return False
        return (self.cfg.paths.frames / d.isoformat() / "index.csv").exists()

    def status(self, now: float, next_clip: float) -> None:
        free = shutil.disk_usage(self.cfg.paths.frames).free / 1e9
        write_json(self.status_path, {
            "supervisor_pid": os.getpid(), "started": self.started, "updated": now,
            "capture_pid": self.capture.pid if self.capture and self.capture.poll() is None else None,
            "capture_restarts": self.capture_restarts,
            "clip_running": bool(self.clip), "daily_running": bool(self.daily),
            "next_clip": datetime.fromtimestamp(next_clip).isoformat(timespec="seconds"),
            "free_gb": round(free, 1), "output_dir": str(self.cfg.paths.out),
        })

    def run(self, foreground: bool = False) -> int:
        # `disabled` is set by `campi stop` and cleared by `campi start`; it only blocks background launches.
        if not foreground and (self.cfg.paths.state / "disabled").exists():
            log.info("service is disabled (campi stop); exiting")
            return 0
        lock = FileLock(self.cfg.paths.state / "supervisor.lock")
        if not lock.acquire():
            log.info("another supervisor is already running; exiting")
            return 0
        self.stop_file.unlink(missing_ok=True)
        cfg = self.cfg
        log.info("supervisor started (pid %d); clips every %d min -> %s", os.getpid(), cfg.render.interval_min,
                 cfg.paths.out)
        now = time.time()
        next_clip = self.next_boundary(now)
        next_house = now + 60
        hh, mm = (int(x) for x in cfg.daily.run_at.split(":"))
        last_daily_check = None
        if cfg.daily.enabled:  # catch up anything missed while the PC was off
            for back in (2, 1):
                d = date.today() - timedelta(days=back)
                if self.daily_due(d):
                    self.daily_queue.append(d)
        last_status = 0.0
        try:
            while True:
                now = time.time()
                if self.stop_file.exists():
                    log.info("stop requested")
                    break
                self.check_capture(now)
                self.reset_backoff(now)

                if now >= next_clip + cfg.render.delay_s:
                    boundary = next_clip
                    next_clip = self.next_boundary(now)
                    if self.reap("clip", cfg.render.timeout_min * 60, now):
                        p = self.spawn("render-clip", "--end", str(boundary), low_priority=True)
                        self.clip = (p, now, f"clip render ending {datetime.fromtimestamp(boundary):%H:%M}")
                    else:
                        log.warning("previous clip render still running; skipping window ending %s",
                                    datetime.fromtimestamp(boundary).strftime("%H:%M"))
                else:
                    self.reap("clip", cfg.render.timeout_min * 60, now)

                today = date.today()
                if cfg.daily.enabled and last_daily_check != today and \
                        datetime.now() >= datetime.combine(today, datetime.min.time()).replace(hour=hh, minute=mm):
                    last_daily_check = today
                    y = today - timedelta(days=1)
                    if self.daily_due(y) and y not in self.daily_queue:
                        self.daily_queue.append(y)
                if self.reap("daily", 3 * 3600, now) and self.daily_queue:
                    d = self.daily_queue.pop(0)
                    p = self.spawn("render-daily", "--date", d.isoformat(), low_priority=True)
                    self.daily = (p, now, f"daily render {d}")
                    log.info("daily render for %s started (pid %d)", d, p.pid)

                if self.reap("housekeep", 1800, now) and now >= next_house:
                    next_house = now + 3600
                    self.housekeep = (self.spawn("housekeep", low_priority=True), now, "housekeeping")

                if now - last_status >= 5:
                    self.status(now, next_clip)
                    last_status = now
                time.sleep(1)
        finally:
            if self.capture and self.capture.poll() is None:
                self.capture.terminate()
            for slot in ("clip", "daily", "housekeep"):
                cur = getattr(self, slot)
                if cur and cur[0].poll() is None:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(cur[0].pid)], capture_output=True,
                                   creationflags=NO_WINDOW)
            self.stop_file.unlink(missing_ok=True)
            write_json(self.status_path, {"supervisor_pid": None, "stopped": time.time()})
            lock.release()
            log.info("supervisor stopped")
        return 0
