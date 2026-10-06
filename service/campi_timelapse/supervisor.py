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

from . import custom_jobs as cj
from . import render_index
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
        self.meshes = None        # optional card meshes pass ([cards] meshes_enabled; TripoSR on the RTX card)
        self.meshes_missing_logged = False
        self.daily_queue: list[date] = []
        # custom timelapses asked for from the desktop UI (custom_jobs); unfinished ones resume after a restart
        self.custom = None
        self.custom_id: str | None = None
        self.custom_queue: list[str] = [j["id"] for j in reversed(cj.jobs(cfg)) if j.get("status") in cj.ACTIVE]
        # optional sightings worker (only touched when [sightings] enabled)
        self.sightings = None
        self.sightings_restarts = 0
        self.sightings_backoff = 10.0
        self.sightings_next_start = 0.0
        self.sightings_started = 0.0
        self.sightings_exits: list[float] = []
        self.sightings_missing_logged = False
        self.sightings_paused = False      # stopped for gaming (not a crash)
        # optional API for the iPhone app / desktop UI (only touched when [api] enabled)
        self.api = None
        self.api_restarts = 0
        self.api_backoff = 10.0
        self.api_next_start = 0.0
        self.api_started = 0.0
        self.api_missing_logged = False
        # render queue (window-end boundaries, oldest first) and game-aware deferral
        self.render_queue_path = cfg.paths.state / "render_queue.json"
        self.render_queue: list[float] = sorted(read_json(self.render_queue_path, []) or [])
        self.game = None                   # GameDetector, created on first poll
        self.game_state: dict = {}
        self.game_active_since = None
        self.game_resume_at = 0.0          # renders may resume at this time after the last game exits
        self.next_game_poll = 0.0
        self.deferred_logged = False
        self.stop_file = cfg.paths.state / "stop.request"
        self.status_path = cfg.paths.state / "status.json"
        self.started = time.time()

    # -- children ---------------------------------------------------------
    def spawn(self, *args: str, low_priority=False, exe: str | None = None, env: dict | None = None) -> subprocess.Popen:
        exe = exe or sys.executable  # pythonw.exe under Task Scheduler, so children get no console either
        flags = NO_WINDOW | (BELOW_NORMAL if low_priority else 0)
        p = subprocess.Popen([exe, "-m", "campi_timelapse", "--config", str(self.cfg.config_path), *args],
                             cwd=str(PROJECT), creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env={**os.environ, **env} if env else None)
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

    def check_sightings(self, now: float) -> None:
        """Keep the optional sightings worker alive (own venv, below-normal priority, same Job Object).
        Restarts with backoff (10 s doubling to 10 min) and kills it if its heartbeat shows a hang."""
        p = self.sightings
        if p and p.poll() is None:
            ws = read_json(self.cfg.paths.state / "sightings.json", {}) or {}
            if p.pid in (ws.get("pid"), ws.get("ppid")) and ws.get("phase") != "loading":
                hung = now - (ws.get("loop_ts") or 0) > 120
            else:  # loading models (first run downloads several GB) or no heartbeat yet
                hung = now - self.sightings_started > 900
            if hung:
                log.error("sightings worker (pid %d) is not responding; killing it", p.pid)
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    pass
            elif now - self.sightings_started > 600:
                self.sightings_backoff = 10.0
            return
        if p and self.sightings_paused:
            self.sightings = None  # stopped by us for gaming: not a crash, no backoff
            return
        if p:
            rc = p.returncode
            self.sightings = None
            self.sightings_restarts += 1
            self.sightings_exits = [t for t in self.sightings_exits if now - t < 900] + [now]
            self.sightings_next_start = now + self.sightings_backoff
            log.error("sightings worker exited with %s; restarting in %.0fs (see sightings.log)", rc,
                      self.sightings_backoff)
            self.sightings_backoff = min(self.sightings_backoff * 2, 600)
            return
        if now < self.sightings_next_start or self.sightings_paused:
            return
        exe = self.cfg.paths.sightings_python
        if not exe.exists():
            if not self.sightings_missing_logged:
                log.error("sightings enabled but %s is missing; run install.ps1 -Sightings", exe)
                self.sightings_missing_logged = True
            return
        # openvino backend: the worker must never touch the RTX card (it also pins an Intel OpenVINO device)
        env = {"CUDA_VISIBLE_DEVICES": ""} if self.cfg.sightings.backend == "openvino" else None
        self.sightings = self.spawn("sightings-worker", low_priority=True, exe=str(exe), env=env)
        self.sightings_started = now
        log.info("sightings worker started (pid %d)", self.sightings.pid)

    def check_api(self, now: float) -> None:
        """Keep the optional API alive (venv-ui, below-normal priority, same Job Object). Restarts with backoff
        (10 s doubling to 10 min, reset after 10 min up). It writes nothing under state\\; its log is ui\\api.log."""
        p = self.api
        if p and p.poll() is None:
            if now - self.api_started > 600:
                self.api_backoff = 10.0
            return
        if p:
            rc = p.returncode
            self.api = None
            self.api_restarts += 1
            self.api_next_start = now + self.api_backoff
            log.error("api exited with %s; restarting in %.0fs (see ui\\api.log)", rc, self.api_backoff)
            self.api_backoff = min(self.api_backoff * 2, 600)
            return
        if now < self.api_next_start:
            return
        exe = self.cfg.paths.ui_python
        if not exe.exists():
            if not self.api_missing_logged:
                log.error("api enabled but %s is missing; run install.ps1 -UI", exe)
                self.api_missing_logged = True
            return
        self.api = self.spawn("api", low_priority=True, exe=str(exe))
        self.api_started = now
        log.info("api started (pid %d, 127.0.0.1:%d)", self.api.pid, self.cfg.api.port)

    # -- gaming ------------------------------------------------------------
    def poll_game(self, now: float) -> None:
        if now < self.next_game_poll:
            return
        self.next_game_poll = now + 15
        try:
            if self.game is None:
                from .gaming import GameDetector
                self.game = GameDetector(self.cfg)
            st = self.game.poll(now)
        except Exception as e:  # detection must never take the supervisor down
            log.warning("game detection failed: %s", e)
            st = {"active": False, "error": str(e)}
        was = bool(self.game_state.get("active"))
        if st.get("active") and not was:
            self.game_active_since = now
            log.info("paused: gaming (%s pid %s, %s)%s%s", st.get("exe"), st.get("pid"), st.get("source"),
                     "; renders deferred" if self.renders_defer_enabled() else "",
                     "; sightings stopped" if self.cfg.gaming.pause_sightings else "")
        elif was and not st.get("active"):
            self.game_resume_at = now + 120
            self.game_active_since = None
            log.info("game ended (%s pid %s exited); renders resume in 2 min", self.game_state.get("exe"),
                     self.game_state.get("pid"))
        self.game_state = {**st, "since": self.game_active_since}
        self.apply_sightings_pause(bool(st.get("active")))

    def renders_defer_enabled(self) -> bool:
        from .gaming import targets_nvidia
        return bool(self.cfg.gaming.defer_renders) and targets_nvidia(self.cfg.tools.rife_gpu)

    def renders_deferred(self, now: float) -> bool:
        deferred = self.renders_defer_enabled() and (bool(self.game_state.get("active")) or now < self.game_resume_at)
        if not deferred and self.deferred_logged:
            log.info("resumed: renders (%d queued)", len(self.render_queue))
        self.deferred_logged = deferred
        return deferred

    def apply_sightings_pause(self, gaming: bool) -> None:
        if not (self.cfg.sightings.enabled and self.cfg.gaming.pause_sightings):
            if self.sightings_paused:
                self.sightings_paused = False
            return
        if gaming and not self.sightings_paused:
            self.sightings_paused = True
            if self.sightings and self.sightings.poll() is None:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.sightings.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
            log.info("paused: gaming; sightings worker stopped")
        elif not gaming and self.sightings_paused:
            self.sightings_paused = False
            self.sightings_next_start = 0.0
            log.info("resumed: sightings worker")

    # -- render queue -------------------------------------------------------
    def save_render_queue(self) -> None:
        write_json(self.render_queue_path, self.render_queue)

    def queue_render(self, boundary: float) -> None:
        if boundary not in self.render_queue:
            self.render_queue.append(boundary)
            self.render_queue.sort()
            self.save_render_queue()

    def drop_stale_renders(self, now: float) -> None:
        """A window can only be rendered while its raw frames are still all there."""
        limit = now - self.cfg.retention.raw_hours * 3600
        stale = [b for b in self.render_queue if b - self.cfg.render.window_min * 60 < limit]
        for b in stale:
            log.warning("dropping queued window ending %s: its frames are older than raw_hours (%dh)",
                        datetime.fromtimestamp(b).strftime("%Y-%m-%d %H:%M"), self.cfg.retention.raw_hours)
        if stale:
            self.render_queue = [b for b in self.render_queue if b not in stale]
            self.save_render_queue()

    def start_queued_render(self, now: float) -> None:
        if self.clip or not self.render_queue or self.renders_deferred(now):
            return
        boundary = self.render_queue.pop(0)  # oldest first: archive appends and latest.mp4 stay in order
        self.save_render_queue()
        p = self.spawn("render-clip", "--end", str(boundary), low_priority=True)
        late = now - boundary - self.cfg.render.delay_s
        self.clip = (p, now, f"clip render ending {datetime.fromtimestamp(boundary):%H:%M}"
                             + (f" (queued {late / 60:.0f} min)" if late > 60 else ""))

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

    def start_meshes(self, now: float) -> float:
        """Start a card meshes pass unless the RTX card is busy (a game, or a clip/daily render with RIFE + NVENC);
        returns when to look again."""
        exe = self.cfg.paths.mesh_python
        if not exe.exists():
            if not self.meshes_missing_logged:
                log.warning("[cards] meshes_enabled but %s is missing (install.ps1 -Meshes)", exe)
                self.meshes_missing_logged = True
            return now + 3600
        gaming = bool(self.game_state.get("active")) or now < self.game_resume_at
        if gaming or self.clip or self.daily or self.custom:
            return now + 300
        self.meshes = (self.spawn("meshes", low_priority=True, exe=str(exe)), now, "card meshes")
        return now + self.cfg.cards.mesh_interval_min * 60

    # -- custom timelapses -------------------------------------------------
    def take_custom_requests(self) -> None:
        """Requests the API dropped in ui\\render_requests\\ (it never writes under state\\ itself). A request
        file is removed once handled; a delete that can't finish yet (file in use) stays for the next pass."""
        d = cj.requests_dir(self.cfg)
        if not d.is_dir():
            return
        try:
            reqs = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return  # one vanished between glob and stat; next pass
        for p in reqs:
            r = read_json(p)
            done = True
            if not isinstance(r, dict) or not cj.ID_RE.match(str(r.get("id"))):
                log.warning("ignoring bad render request %s", p.name)
            elif r.get("action") == "render":
                self.queue_custom(r)
            elif r.get("action") == "delete":
                done = self.delete_custom(r["id"])
            if done:
                p.unlink(missing_ok=True)

    def queue_custom(self, r: dict) -> None:
        try:
            start, end = float(r["start_ts"]), float(r["end_ts"])
            speed = float(r["speed"]) if r.get("speed") is not None else None
            seconds = float(r["seconds"]) if r.get("seconds") is not None else None
        except (KeyError, TypeError, ValueError):
            log.warning("ignoring malformed custom render request %s", r.get("id"))
            return
        err = cj.validate(start, end, speed, seconds)
        if err or cj.read_job(self.cfg, r["id"]):
            log.warning("ignoring custom render request %s: %s", r["id"], err or "already queued")
            return
        job = {**cj.new_job(start, end, speed, seconds), "id": r["id"], "created": r.get("created") or time.time()}
        cj.write_job(self.cfg, job)
        self.custom_queue.append(job["id"])
        log.info("custom render %s queued (%s -> %s, %s)", job["id"], datetime.fromtimestamp(start).isoformat(
            timespec="minutes"), datetime.fromtimestamp(end).isoformat(timespec="minutes"),
            f"{speed:g}x" if speed else f"{seconds:g} s")

    def delete_custom(self, job_id: str) -> bool:
        """Cancel (queued / running) and remove a custom timelapse. False if its video is still in use."""
        if job_id in self.custom_queue:
            self.custom_queue.remove(job_id)
        if self.custom and self.custom_id == job_id:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.custom[0].pid)], capture_output=True,
                           creationflags=NO_WINDOW)
            try:
                self.custom[0].wait(10)
            except subprocess.TimeoutExpired:
                return False
            self.custom = self.custom_id = None
        for v in self.cfg.paths.custom.glob(f"campi_custom_*_{job_id}*.mp4"):  # the video and any .part
            try:
                v.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("custom render %s: could not delete %s yet (%s)", job_id, v.name, e)
                return False
            render_index.delete(self.cfg, v.name)
        cj.job_path(self.cfg, job_id).unlink(missing_ok=True)
        for d in self.cfg.paths.work.glob(f"custom_{job_id}_*"):  # left behind when a running render was killed
            shutil.rmtree(d, ignore_errors=True)
        log.info("custom render %s deleted", job_id)
        return True

    def check_custom(self, now: float) -> None:
        running = self.custom_id if self.custom else None
        if not self.reap("custom", 3 * 3600, now):
            return
        if running:
            self.custom_id = None
            job = cj.read_job(self.cfg, running)
            if job and job.get("status") in cj.ACTIVE:  # killed, crashed or timed out before it could say so
                job.update(status="failed", reason="the render stopped unexpectedly (see custom.log)",
                           finished=time.time())
                cj.write_job(self.cfg, job)
        if not self.custom_queue or self.renders_deferred(now):
            return
        job_id = self.custom_queue.pop(0)
        if cj.read_job(self.cfg, job_id) is None:
            return  # deleted meanwhile
        p = self.spawn("render-custom", "--id", job_id, low_priority=True)
        self.custom, self.custom_id = (p, now, f"custom render {job_id}"), job_id
        log.info("custom render %s started (pid %d)", job_id, p.pid)

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
            **(self.sightings_status(now) if self.cfg.sightings.enabled else {}),
            **({"api_pid": self.api.pid if self.api and self.api.poll() is None else None,
                "api_restarts": self.api_restarts} if self.cfg.api.enabled else {}),
            "render_queue": len(self.render_queue),
            "render_queue_oldest": (datetime.fromtimestamp(self.render_queue[0]).isoformat(timespec="minutes")
                                    if self.render_queue else None),
            "custom_running": self.custom_id if self.custom else None,
            "custom_queue": len(self.custom_queue),
            "renders_deferred": self.deferred_logged,
            "gaming": self.game_state,
        })

    def sightings_status(self, now: float) -> dict:
        recent = [t for t in self.sightings_exits if now - t < 900]
        return {
            "sightings_pid": self.sightings.pid if self.sightings and self.sightings.poll() is None else None,
            "sightings_restarts": self.sightings_restarts,
            "sightings_recent_exits": len(recent),
            "sightings_crash_looping": len(recent) >= 3,
            "sightings_next_start": self.sightings_next_start if not self.sightings else None,
            "sightings_paused": self.sightings_paused,
        }

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
        next_meshes = now + 300
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
                if cfg.sightings.enabled:
                    self.check_sightings(now)
                if cfg.api.enabled:
                    self.check_api(now)

                self.poll_game(now)
                self.reap("clip", cfg.render.timeout_min * 60, now)
                if now >= next_clip + cfg.render.delay_s:
                    boundary = next_clip
                    next_clip = self.next_boundary(now)
                    self.queue_render(boundary)  # rendered when the slot is free and not deferred (gaming)
                self.drop_stale_renders(now)
                self.start_queued_render(now)

                today = date.today()
                if cfg.daily.enabled and last_daily_check != today and \
                        datetime.now() >= datetime.combine(today, datetime.min.time()).replace(hour=hh, minute=mm):
                    last_daily_check = today
                    y = today - timedelta(days=1)
                    if self.daily_due(y) and y not in self.daily_queue:
                        self.daily_queue.append(y)
                if self.reap("daily", 3 * 3600, now) and self.daily_queue and not self.renders_deferred(now):
                    d = self.daily_queue.pop(0)
                    p = self.spawn("render-daily", "--date", d.isoformat(), low_priority=True)
                    self.daily = (p, now, f"daily render {d}")
                    log.info("daily render for %s started (pid %d)", d, p.pid)

                self.take_custom_requests()
                self.check_custom(now)

                if self.reap("housekeep", 1800, now) and now >= next_house:
                    next_house = now + 3600
                    self.housekeep = (self.spawn("housekeep", low_priority=True), now, "housekeeping")

                if cfg.cards.meshes_enabled and self.reap("meshes", cfg.cards.mesh_timeout_min * 60, now)                         and now >= next_meshes:
                    next_meshes = self.start_meshes(now)

                if now - last_status >= 5:
                    self.status(now, next_clip)
                    last_status = now
                time.sleep(1)
        finally:
            if self.capture and self.capture.poll() is None:
                self.capture.terminate()
            for child in (self.sightings, self.api):
                if child and child.poll() is None:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True,
                                   creationflags=NO_WINDOW)
            for slot in ("clip", "daily", "custom", "housekeep", "meshes"):
                cur = getattr(self, slot)
                if cur and cur[0].poll() is None:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(cur[0].pid)], capture_output=True,
                                   creationflags=NO_WINDOW)
            self.stop_file.unlink(missing_ok=True)
            write_json(self.status_path, {"supervisor_pid": None, "stopped": time.time()})
            lock.release()
            log.info("supervisor stopped")
        return 0
