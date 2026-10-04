"""Card meshes: turn each card's best crop into a 3D mesh with TripoSR on the RTX card (optional; [cards]).

Runs in its own venv (install.ps1 -Meshes: CUDA torch, TripoSR checkout under tools\\TripoSR, rembg, trimesh,
PyMCubes) as a short low-priority pass: the supervisor starts it every [cards] mesh_interval_min while no game is
running and no render is using the card, and `campi meshes` runs it by hand.

A label gets a mesh when it has none yet, or when its best crop (confidence x box size, the same pick as the card's
cover) is at least 1.3x bigger than the one its mesh came from. Each GLB is length 1 along +x (the car's front),
ground at y=0, centred, with vertex colours; manifest.json maps the label (lower case) to it. Old GLBs are removed
when a label's mesh is replaced.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import types
from pathlib import Path

log = logging.getLogger("meshes")

REGEN_GROWTH = 1.3
MC_RESOLUTION = 256
MIN_BOX_PX = 160  # smaller crops make blobs
DENSITY_SIGMA = 3.5   # voxels; Gaussian low-pass of the density before marching cubes
SMOOTH_ITERATIONS = 20  # Taubin smoothing passes on the extracted mesh (keeps volume, unlike plain Laplacian)


class _ReadOnlyStore:
    """Just enough of ui.store.Store for Sightings to read ui.db (corrections, hidden) without opening it for writes."""
    def __init__(self, cfg):
        from .ui.store import ui_home
        self.path = ui_home(cfg) / "ui.db"

    def signature(self) -> tuple:
        try:
            s = self.path.stat()
            return (s.st_mtime_ns, s.st_size)
        except OSError:
            return (None, None)


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:48] or "car"


def load_manifest(d: Path) -> dict:
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def save_manifest(d: Path, m: dict) -> None:
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, d / "manifest.json")


def pick_work(cfg, manifest: dict, label: str | None, force: bool) -> list[dict]:
    from .ui.sightings_data import Sightings
    sd = Sightings(cfg, _ReadOnlyStore(cfg))
    known = sd.catalog()["known"]
    stats = sd.card_stats()
    work = []
    for key, s in stats.items():
        best = s["best"]
        if not best or (label and key != label.lower()):
            continue
        box = best["box_px"] or 0
        have = manifest.get(key)
        if box < MIN_BOX_PX and not (label and force):
            continue
        if have and not force and box < (have.get("box_px") or 0) * REGEN_GROWTH:
            continue
        direction = s["dir"].most_common(1)[0][0] if s["dir"] else "LR"
        work.append({"key": key, "label": known.get(key, {}).get("label", key), "sighting_id": best["id"],
                     "crop": sd.root / best["crop"], "box_px": box, "direction": best.get("direction") or direction,
                     "new": not have})
    # new labels first, then the biggest improvement
    work.sort(key=lambda w: (not w["new"], -(w["box_px"] / max((manifest.get(w["key"]) or {}).get("box_px") or 1, 1))))
    return work


class Generator:
    """TripoSR + rembg, loaded once per pass."""

    def __init__(self, tools: Path):
        import mcubes
        import numpy as np
        import torch
        # TripoSR imports torchmcubes (a CUDA extension that doesn't build for sm_120 on Windows); PyMCubes does the
        # same job on the CPU. torchmcubes returns vertices in reversed axis order, which TripoSR swaps back.
        # The density TripoSR infers from a small side-on street crop is noisy (the surface comes out hairy), so it
        # is low-passed before extraction; normalise() smooths the mesh as well.
        from scipy.ndimage import gaussian_filter
        shim = types.ModuleType("torchmcubes")

        def marching_cubes(vol, thresh):
            field = gaussian_filter(vol.cpu().numpy().astype(np.float32), sigma=DENSITY_SIGMA)
            v, f = mcubes.marching_cubes(field, thresh)
            return torch.from_numpy(np.ascontiguousarray(v[:, ::-1])).float(), torch.from_numpy(f.astype(np.int64))
        shim.marching_cubes = marching_cubes
        sys.modules.setdefault("torchmcubes", shim)
        sys.path.insert(0, str(tools / "TripoSR"))
        import rembg
        from tsr.system import TSR
        self.torch, self.np = torch, np
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        if self.device == "cpu":
            log.warning("CUDA not available: TripoSR runs on the CPU (slow)")
        self.model = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
        self.model.renderer.set_chunk_size(8192)
        self.model.to(self.device)
        self.rembg = rembg.new_session("isnet-general-use")
        log.info("TripoSR loaded on %s (%s)", self.device,
                 torch.cuda.get_device_name(0) if self.device != "cpu" else "cpu")

    def prepare(self, crop: Path):
        """Background removed, the largest blob kept (drops signs/poles touching the car), centred on grey."""
        from PIL import Image
        from scipy import ndimage
        from tsr.utils import remove_background, resize_foreground
        np = self.np
        im = remove_background(Image.open(crop).convert("RGB"), self.rembg)
        a = np.array(im)
        mask = a[:, :, 3] > 127
        lab, n = ndimage.label(ndimage.binary_opening(mask, iterations=2))
        if n > 1:
            sizes = ndimage.sum(mask, lab, range(1, n + 1))
            keep = lab == (1 + int(np.argmax(sizes)))
            a[:, :, 3] = np.where(ndimage.binary_dilation(keep, iterations=2), a[:, :, 3], 0)
        im = resize_foreground(Image.fromarray(a), 0.85)
        f = np.array(im).astype(np.float32) / 255.0
        f = f[:, :, :3] * f[:, :, 3:4] + (1 - f[:, :, 3:4]) * 0.5
        return Image.fromarray((f * 255.0).astype(np.uint8))

    def mesh(self, image):
        with self.torch.no_grad():
            codes = self.model([image], device=self.device)
        return self.model.extract_mesh(codes, True, resolution=MC_RESOLUTION)[0]


def normalise(mesh, direction: str):
    """TripoSR's frame (x = depth, y = image right, z = up) -> ours (x = length/front, y = up, z = width), levelled
    (the camera looks down at the street, so crops are tilted), length 1, ground at 0, centred."""
    import numpy as np
    import trimesh
    m = mesh.copy()
    v = m.vertices[:, [1, 2, 0]].copy()  # cyclic swap keeps handedness
    if direction == "RL":  # car faced image left: turn it so the front is +x
        v[:, 0] *= -1
        v[:, 2] *= -1
    m.vertices = v
    m = trim_above_roof(m)  # before levelling too: a pole would tip the level search
    v = m.vertices.copy()
    sample = v[:: max(1, len(v) // 4000)]

    def best_angle(axis_a: int, axis_b: int, minimise: int) -> float:
        best, arg = None, 0.0
        for deg in np.linspace(-35, 35, 141):
            t = np.radians(deg)
            a = sample[:, axis_a] * np.cos(t) - sample[:, axis_b] * np.sin(t)
            b = sample[:, axis_a] * np.sin(t) + sample[:, axis_b] * np.cos(t)
            ext = np.ptp(a if minimise == axis_a else b)
            if best is None or ext < best:
                best, arg = ext, t
        return arg

    def rotate(arr, axis_a: int, axis_b: int, t: float):
        a = arr[:, axis_a] * np.cos(t) - arr[:, axis_b] * np.sin(t)
        b = arr[:, axis_a] * np.sin(t) + arr[:, axis_b] * np.cos(t)
        arr[:, axis_a], arr[:, axis_b] = a, b

    for (aa, ab, mn) in ((0, 1, 1), (2, 1, 1), (0, 2, 2)):  # roll (level), pitch (level), yaw (narrowest)
        t = best_angle(aa, ab, mn)
        rotate(v, aa, ab, t)
        rotate(sample, aa, ab, t)
    m.vertices = v
    m = trim_above_roof(m)
    v = m.vertices.copy()
    lo, hi = v.min(0), v.max(0)
    v -= [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]
    v /= max(hi[0] - lo[0], 1e-6)
    m.vertices = v
    out = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, vertex_colors=m.visual.vertex_colors, process=False)
    trimesh.smoothing.filter_taubin(out, iterations=SMOOTH_ITERATIONS)
    out.fix_normals()
    return out


def trim_above_roof(m):
    """Cut thin things sticking up past the roof (sign poles, posts behind the car survive background removal when
    they overlap it): scanning up from 40% of the height, the first slice whose footprint is under a fifth of the
    car's length (or nearly empty) is above the roof; faces reaching above it go, then the largest piece is kept."""
    import numpy as np
    v = m.vertices
    y = v[:, 1]
    lo, hi = float(y.min()), float(y.max())
    length = float(np.ptp(v[:, 0]))
    edges = np.linspace(lo, hi, 61)
    cut = None
    for i in range(int(60 * 0.4), 60):
        sl = v[(y >= edges[i]) & (y < edges[i + 1])]
        if len(sl) < 20 or np.ptp(sl[:, 0]) < length * 0.2:
            cut = edges[i]
            break
    if cut is None:
        return m
    keep = (m.vertices[m.faces][:, :, 1] <= cut).all(axis=1)
    if keep.all():
        return m
    m = m.copy()
    m.update_faces(keep)
    m.remove_unreferenced_vertices()
    parts = m.split(only_watertight=False)
    return max(parts, key=lambda p: len(p.faces)) if len(parts) else m


def run(cfg, label: str | None = None, force: bool = False, limit: int | None = None) -> dict:
    out = cfg.paths.card_meshes
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out)
    work = pick_work(cfg, manifest, label, force)
    limit = limit or int(cfg.cards.meshes_per_pass)
    todo = work[:limit]
    log.info("%d label(s) could use a mesh; doing %d", len(work), len(todo))
    if not todo:
        return {"done": 0, "pending": len(work)}
    gen = Generator(cfg.paths.data / "tools")
    done, failed = [], []
    for w in todo:
        t0 = time.time()
        try:
            img = gen.prepare(w["crop"])
            mesh = normalise(gen.mesh(img), w["direction"])
            name = f"{slug(w['label'])}-{w['sighting_id'][:8]}.glb"
            tmp = out / (name + ".tmp")
            tmp.write_bytes(mesh.export(file_type="glb", include_normals=True))
            os.replace(tmp, out / name)
            old = manifest.get(w["key"], {}).get("file")
            manifest[w["key"]] = {"label": w["label"], "file": name, "sighting_id": w["sighting_id"],
                                  "box_px": w["box_px"], "faces": int(len(mesh.faces)),
                                  "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            save_manifest(out, manifest)
            if old and old != name:
                (out / old).unlink(missing_ok=True)
            done.append(w["label"])
            log.info("mesh for %s from %s (%d px): %d faces in %.1f s", w["label"], w["crop"].name, w["box_px"],
                     len(mesh.faces), time.time() - t0)
        except Exception:
            failed.append(w["label"])
            log.exception("mesh for %s failed", w["label"])
    return {"done": len(done), "failed": failed, "pending": len(work) - len(done)}
