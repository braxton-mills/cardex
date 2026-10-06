"""Game detection for the supervisor (stdlib + psutil): defer GPU renders while a game uses the RTX card.

A process is a game when its exe is under a [gaming] game_dirs folder or matches extra_exes, it isn't in
ignore_exes (glob, case-insensitive), and it has kept the NVIDIA GPU's 3D engine above gpu_busy_pct for at least
gpu_busy_s. Per-process GPU use comes from the Windows "GPU Engine" performance counters (the ones Task Manager
shows), read through PDH; the NVIDIA adapter is identified by its LUID via DXGI. If the counters can't be read,
detection falls back to the path match alone and says so.
"""
from __future__ import annotations

import ctypes
import fnmatch
import logging
import os
import re
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("supervisor")

NVIDIA_VENDOR = 0x10DE
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
INSTANCE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_", re.I)


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


def _vcall(obj, index, restype, *argtypes):
    """Call COM method `index` of interface pointer `obj`."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def nvidia_luid() -> tuple[int, int] | None:
    """(high, low) LUID of the first NVIDIA adapter, via IDXGIFactory1::EnumAdapters1 / IDXGIAdapter1::GetDesc1."""
    iid = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    factory = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return None
    try:
        i = 0
        while True:
            adapter = ctypes.c_void_p()
            hr = _vcall(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                factory, i, ctypes.byref(adapter))
            if hr != 0:  # DXGI_ERROR_NOT_FOUND: no more adapters
                return None
            try:
                desc = _ADAPTER_DESC1()
                _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(_ADAPTER_DESC1))(adapter, ctypes.byref(desc))
                if desc.VendorId == NVIDIA_VENDOR:
                    return desc.AdapterLuid.HighPart & 0xFFFFFFFF, desc.AdapterLuid.LowPart
            finally:
                _vcall(adapter, 2, ctypes.c_ulong)(adapter)  # Release
            i += 1
    finally:
        _vcall(factory, 2, ctypes.c_ulong)(factory)


class _FMT_VALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _FMT_ITEM(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FMT_VALUE)]


class PdhArray:
    """All instances of one wildcard PDH counter, e.g. \\GPU Engine(*)\\Utilization Percentage."""

    def __init__(self, path: str):
        self.pdh = ctypes.windll.pdh
        self.query, self.counter = ctypes.c_void_p(), ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery failed")
        rc = self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(self.counter))
        if rc != 0:
            raise OSError(f"PdhAddEnglishCounter({path}) failed: 0x{rc & 0xFFFFFFFF:08X}")
        self.pdh.PdhCollectQueryData(self.query)  # rates need a first sample

    def close(self) -> None:
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()

    def read(self) -> dict[str, float]:
        rc = self.pdh.PdhCollectQueryData(self.query) & 0xFFFFFFFF
        if rc != 0:
            raise OSError(f"PdhCollectQueryData failed: 0x{rc:08X}")
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = self.pdh.PdhGetFormattedCounterArrayW(self.counter, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                   ctypes.byref(count), None) & 0xFFFFFFFF
        if rc not in (0, PDH_MORE_DATA):
            raise OSError(f"PdhGetFormattedCounterArray failed: 0x{rc:08X}")
        buf = ctypes.create_string_buffer(size.value)
        rc = self.pdh.PdhGetFormattedCounterArrayW(self.counter, PDH_FMT_DOUBLE, ctypes.byref(size),
                                                   ctypes.byref(count), buf) & 0xFFFFFFFF
        if rc != 0:
            raise OSError(f"PdhGetFormattedCounterArray failed: 0x{rc:08X}")
        items = ctypes.cast(buf, ctypes.POINTER(_FMT_ITEM))
        return {items[i].szName: items[i].FmtValue.doubleValue for i in range(count.value) if items[i].szName}


ADAPTER = re.compile(r"luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_", re.I)


def counter_nvidia_luid() -> tuple[int, int] | None:
    """The RTX card's LUID as the performance counters name it: the adapter with dedicated memory in use
    (an iGPU has none). Needed in session 0, where DXGI reports a different LUID than the counters use."""
    mem = PdhArray("\\GPU Adapter Memory(*)\\Dedicated Usage")
    time.sleep(0.2)
    try:
        usage = mem.read()
    finally:
        mem.close()
    best, used = None, 64 * 2**20
    for name, v in usage.items():
        m = ADAPTER.search(name)
        if m and v > used:
            best, used = (int(m.group(1), 16), int(m.group(2), 16)), v
    return best


class Gpu3D(PdhArray):
    """Per-pid 3D-engine utilization (%) on one adapter, averaged over the time between polls."""

    def __init__(self, luid: tuple[int, int]):
        super().__init__("\\GPU Engine(*engtype_3D)\\Utilization Percentage")
        self.luid = luid

    def luids(self) -> set[tuple[int, int]]:
        return {(int(m.group(2), 16), int(m.group(3), 16)) for n in self.read() if (m := INSTANCE.match(n))}

    def sample(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for name, v in self.read().items():
            m = INSTANCE.match(name)
            if m and (int(m.group(2), 16), int(m.group(3), 16)) == self.luid:
                pid = int(m.group(1))
                out[pid] = out.get(pid, 0.0) + v
        return out


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


class GameDetector:
    def __init__(self, cfg):
        self.cfg, self.g = cfg, cfg.gaming
        self.dirs = [_norm(d) + os.sep for d in self.g.game_dirs]
        self.extra = [e.lower() for e in self.g.extra_exes]
        self.ignore = [e.lower() for e in self.g.ignore_exes]
        self.busy_since: dict[int, float] = {}
        # pid -> exe of processes that passed the GPU check: they stay games until they exit, whatever their GPU
        # use later (a game in a menu, minimized, or with its display switched away by a KVM draws next to nothing)
        self.games: dict[int, str] = {}
        self.counters_error = ""
        self.gpu: Gpu3D | None = None
        self.gpu_failures = 0
        self.reopen_at = 0.0
        self.open_counters(time.time())

    def open_counters(self, now: float) -> None:
        """(Re)open the GPU Engine counters. The adapter's LUID changes when the driver resets, so it is looked up
        each time; while the counters can't be opened, detection uses the path match and retries every 5 min."""
        if self.gpu:
            self.gpu.close()
            self.gpu = None
        self.gpu_failures = 0
        try:
            dxgi = nvidia_luid()
            probe = Gpu3D(dxgi or (0, 0))
            try:
                time.sleep(0.2)
                present = probe.luids()
            finally:
                probe.close()
            if dxgi in present:
                luid, how = dxgi, "DXGI"
            else:  # session 0: DXGI's LUID differs from the counters'; use the adapter with dedicated memory
                luid, how = counter_nvidia_luid(), "adapter memory counters"
            if luid is None or luid not in present:
                raise OSError(f"NVIDIA adapter not found in the GPU Engine counters (DXGI said {dxgi})")
            self.gpu = Gpu3D(luid)
            self.counters_error = ""
            log.info("game detection: NVIDIA LUID 0x%08X_0x%08X (from %s), GPU Engine counters OK", *luid, how)
        except Exception as e:  # session 0 / missing counters: path match only
            err = f"{type(e).__name__}: {e}"
            if err != self.counters_error:  # retries every 5 min: log each new error once
                log.warning("game detection: GPU counters unavailable (%s); using the path match alone", err)
            self.counters_error = err
            self.reopen_at = now + 300

    def mode(self) -> str:
        try:
            m = (self.cfg.paths.state / "game_mode").read_text(encoding="utf-8").strip().lower()
        except OSError:
            m = "auto"
        return m if m in ("on", "off", "auto") else "auto"

    def is_candidate(self, exe: str) -> bool:
        name = Path(exe).name.lower()
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.ignore):
            return False
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.extra):
            return True
        n = _norm(exe)
        return any(n.startswith(d) for d in self.dirs)

    def poll(self, now: float) -> dict:
        """{'active', 'exe', 'pid', 'mode', 'source', 'counters'} for this poll."""
        import psutil
        mode = self.mode()
        counters = "ok" if self.gpu else f"unavailable (path match only): {self.counters_error}"
        base = {"active": False, "exe": None, "pid": None, "mode": mode, "counters": counters}
        if mode == "on":
            return {**base, "active": True, "exe": "manual (campi game on)", "source": "manual"}
        if mode == "off" or not self.g.auto:
            self.busy_since.clear()
            self.games.clear()
            return {**base, "source": "off" if mode == "off" else "auto disabled"}
        if self.gpu is None and now >= self.reopen_at:
            self.open_counters(now)
        util: dict[int, float] | None = None
        if self.gpu:
            try:
                util = self.gpu.sample()
                self.gpu_failures = 0
            except OSError as e:  # one-offs happen (an instance vanishing mid-read): skip this poll, reopen after 3
                self.gpu_failures += 1
                if self.gpu_failures >= 3:
                    log.warning("game detection: GPU counters failed %d polls in a row (%s); reopening",
                                self.gpu_failures, e)
                    self.open_counters(now)
        best = None
        alive: dict[int, str] = {}
        for p in psutil.process_iter(["pid", "exe"]):
            exe = p.info.get("exe")
            if not exe or not self.is_candidate(exe):
                continue
            pid = p.info["pid"]
            alive[pid] = exe
            if self.games.get(pid) == exe or self.gpu is None:  # a confirmed game, or fallback: path match alone
                best = best or (pid, exe)
            elif util is None:  # the counters failed this poll: keep the timers, decide next poll
                continue
            elif util.get(pid, 0.0) > self.g.gpu_busy_pct:
                self.busy_since.setdefault(pid, now)
                if now - self.busy_since[pid] >= self.g.gpu_busy_s:
                    self.games[pid] = exe
                    best = best or (pid, exe)
            else:
                self.busy_since.pop(pid, None)
        for d in (self.busy_since, self.games):
            for pid in [pid for pid in d if pid not in alive]:
                del d[pid]
        if best:
            return {**base, "active": True, "pid": best[0], "exe": Path(best[1]).name,
                    "source": "gpu counters" if self.gpu else "path match"}
        return {**base, "source": "gpu counters" if self.gpu else "path match"}


def targets_nvidia(rife_gpu) -> bool:
    """Do renders use the NVIDIA card? A name match, or an index (session 0 order varies: assume yes)."""
    return isinstance(rife_gpu, int) or "nvidia" in str(rife_gpu).lower()


if __name__ == "__main__":  # quick manual check: python -m campi_timelapse.gaming
    from .config import load_config
    logging.basicConfig(level=logging.INFO)
    det = GameDetector(load_config())
    for _ in range(3):
        time.sleep(2)
        if det.gpu:
            top = sorted(det.gpu.sample().items(), key=lambda kv: -kv[1])[:3]
            print("top NVIDIA 3D users:", [(pid, round(u, 1)) for pid, u in top])
    print(det.poll(time.time()))
