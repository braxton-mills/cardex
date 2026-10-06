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
from dataclasses import dataclass, fields
from pathlib import Path

log = logging.getLogger("meshes")

REGEN_GROWTH = 1.3
MC_RESOLUTION = 256
MIN_BOX_PX = 160  # smaller crops make blobs
DENSITY_SIGMA = 3.5   # voxels; Gaussian low-pass of the density before marching cubes
SMOOTH_ITERATIONS = 20  # Taubin smoothing passes on the extracted mesh (keeps volume, unlike plain Laplacian)
# TripoSR's input view: camera on +x at this distance looking at the origin, z up, image right = +y
COND_DISTANCE, COND_FOVY_DEG = 1.9, 40.0
SYM_PITCH = 1 / 192     # voxel size (car length = 1) when the visible half is mirrored and re-meshed
MAX_FACES = 80_000      # mirrored meshes are decimated to this (the grid would give ~250k)
# typical width / length by YOLO class: the visible side is real, the far side is guessed, so the width is set
WIDTH_RATIO = {"car": 0.41, "truck": 0.37, "bus": 0.30}


@dataclass
class Settings:
    """How a mesh is made; `campi meshes --set k=v,...` overrides these (with --trial, to compare)."""
    sigma: float = DENSITY_SIGMA
    smooth: int = SMOOTH_ITERATIONS
    paint: bool = False      # colour the visible side from the photo (TripoSR's own colours come out grey)
    sym: bool = False        # mirror the visible side onto the far side
    width: bool = False      # set width/length from WIDTH_RATIO, measured from the visible side
    pick: str = "card"       # source photo: "card" (the card's cover) | "photo" (photo_score: sharp, clear of mesh_avoid)
    relief: float = 4.0      # sym: blur of the seen side's depth map, in SYM_PITCH cells (TripoSR's depth is noisy)

    @classmethod
    def parse(cls, spec: str | None) -> "Settings":
        st = cls()
        types_ = {f.name: f.type for f in fields(cls)}
        for part in filter(None, (spec or "").split(",")):
            k, _, v = part.partition("=")
            k = k.strip()
            if k not in types_:
                raise ValueError(f"unknown mesh setting {k!r} (have: {', '.join(types_)})")
            t = types_[k]
            v = v.strip()
            setattr(st, k, v.lower() in ("1", "true", "yes", "on", "") if t in (bool, "bool")
                    else float(v) if t in (float, "float") else int(v) if t in (int, "int") else v)
        return st


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


SHARP_REF = 30.0     # variance of the horizontal gradient of a crisp crop at SCORE_WIDTH px: cars move sideways, so
OFF_COLOUR = 0.35    # score factor for a photo whose colour isn't the label's most common one
MIN_SHARP = 8.0      # motion blur (long night exposures) shows up there; below MIN_SHARP no scan is made at all
SCORE_WIDTH = 192


def _point_in(poly, x, y):
    """Even-odd test of points (x, y arrays, 0-1) against a polygon [[x, y], ...]."""
    import numpy as np
    inside = np.zeros(np.shape(x), bool)
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        cross = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1)
        inside ^= cross
    return inside


def locate(crop, frame):
    """Where a crop sits in its frame, as (x, y, w, h) in 0-1: SSD template match at quarter size (FFT)."""
    import numpy as np
    from scipy.signal import fftconvolve
    f = frame[::4, ::4].astype(np.float32)
    t = crop[::4, ::4].astype(np.float32)
    th, tw = t.shape
    if th < 4 or tw < 4 or th > f.shape[0] or tw > f.shape[1]:
        return None
    ones = np.ones_like(t)
    ssd = fftconvolve(f * f, ones, "valid") - 2 * fftconvolve(f, t[::-1, ::-1], "valid")
    y, x = np.unravel_index(int(np.argmin(ssd)), ssd.shape)
    return (x / f.shape[1], y / f.shape[0], tw / f.shape[1], th / f.shape[0])


def photo_score(root: Path, cand: dict, avoid: list) -> dict:
    """Sharpness (horizontal gradient variance at SCORE_WIDTH) and how much of the car's box falls in [cards]
    mesh_avoid (things in front of the road, like a street tree) for one sighting's crop."""
    import numpy as np
    from PIL import Image
    im = Image.open(root / cand["crop"]).convert("L")
    small = np.asarray(im.resize((SCORE_WIDTH, max(8, round(im.height * SCORE_WIDTH / im.width))), Image.BILINEAR),
                       np.float32)
    out = {"sharp": float(np.diff(small, axis=1).var()), "occluded": 0.0}
    if avoid and cand.get("frame") and (root / cand["frame"]).is_file():
        box = locate(np.asarray(im, np.uint8), np.asarray(Image.open(root / cand["frame"]).convert("L"), np.uint8))
        if box:
            x, y, w, h = box
            gx, gy = np.meshgrid(x + w * (np.arange(16) + 0.5) / 16, y + h * (np.arange(8) + 0.5) / 8)
            out["box"] = [round(v, 4) for v in box]
            out["occluded"] = float(max(_point_in(poly, gx, gy).mean() for poly in avoid))
    return out


def score_of(sc: dict, box_px: int) -> float:
    """One number to rank source photos: size, crispness, and not hidden behind mesh_avoid (0 = unusable)."""
    if sc["sharp"] < MIN_SHARP:
        return 0.0
    return box_px * min(sc["sharp"] / SHARP_REF, 1.0) ** 0.5 * (1 - sc["occluded"]) ** 2


def pick_work(cfg, manifest: dict, labels: list[str] | None, force: bool, st: Settings | None = None) -> list[dict]:
    from .ui.sightings_data import Sightings
    st = st or Settings()
    sd = Sightings(cfg, _ReadOnlyStore(cfg))
    known = sd.catalog()["known"]
    stats = sd.card_stats()
    only = {x.lower() for x in labels or []}
    cands = sd.mesh_candidates() if st.pick == "photo" else {}
    avoid = [poly for poly in (cfg.cards.mesh_avoid or []) if len(poly) >= 3]
    if avoid and not isinstance(avoid[0][0], (list, tuple)):
        avoid = [cfg.cards.mesh_avoid]  # a single polygon
    cache_p = cfg.paths.card_meshes / "scores.json"
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    cache_dirty = False
    work = []
    for key, s in stats.items():
        best = s["best"]
        if not best or (only and key not in only):
            continue
        score = None
        colour = s["color"].most_common(1)[0][0] if s["color"] else None
        if st.pick == "photo" and cands.get(key):
            ranked = []
            for c in cands[key]:
                if c["box_px"] < MIN_BOX_PX * 0.75:
                    continue
                sc = cache.get(c["id"])
                if sc is None:
                    try:
                        sc = cache[c["id"]] = photo_score(sd.root, c, avoid)
                        cache_dirty = True
                    except OSError:
                        continue
                # the card shows the label's usual colour: a photo of another colour only wins if it's far better
                off = colour and c["color"] and c["color"] != colour
                ranked.append((score_of(sc, c["box_px"]) * (OFF_COLOUR if off else 1.0), c))
            score, top = max(ranked, key=lambda r: r[0]) if ranked else (0.0, None)
            if not score:
                continue  # every photo is blurred or behind mesh_avoid: keep the procedural car
            best = top
        box = best["box_px"] or 0
        have = manifest.get(key)
        if box < MIN_BOX_PX and not (only and force):
            continue
        if have and not force:
            if score is not None and have.get("score") is not None:
                if score < have["score"] * REGEN_GROWTH:
                    continue
            elif box < (have.get("box_px") or 0) * REGEN_GROWTH:
                continue
        direction = s["dir"].most_common(1)[0][0] if s["dir"] else "LR"
        work.append({"key": key, "label": known.get(key, {}).get("label", key), "sighting_id": best["id"],
                     "crop": sd.root / best["crop"], "box_px": box, "direction": best.get("direction") or direction,
                     "cls": s["cls"].most_common(1)[0][0] if s["cls"] else "car", "score": score,
                     "new": not have})
    if cache_dirty:
        cfg.paths.card_meshes.mkdir(parents=True, exist_ok=True)
        tmp = cache_p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, cache_p)
    # new labels first, then the biggest improvement
    work.sort(key=lambda w: (not w["new"], -(w["box_px"] / max((manifest.get(w["key"]) or {}).get("box_px") or 1, 1))))
    return work


class Generator:
    """TripoSR + rembg, loaded once per pass."""

    def __init__(self, tools: Path, sigma: float = DENSITY_SIGMA):
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
            field = gaussian_filter(vol.cpu().numpy().astype(np.float32), sigma=sigma)
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
        """Background removed, the largest blob kept (drops signs/poles touching the car), centred on grey. Returns
        (model input, the same square as RGBA with the background transparent, for paint())."""
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
        rgba = np.array(resize_foreground(Image.fromarray(a), 0.85))
        f = rgba.astype(np.float32) / 255.0
        f = f[:, :, :3] * f[:, :, 3:4] + (1 - f[:, :, 3:4]) * 0.5
        return Image.fromarray((f * 255.0).astype(np.uint8)), rgba

    def mesh(self, image):
        with self.torch.no_grad():
            codes = self.model([image], device=self.device)
        return self.model.extract_mesh(codes, True, resolution=MC_RESOLUTION)[0]


def paint(mesh, rgba):
    """Colour the vertices TripoSR's input camera saw from the photo itself (TripoSR frame, before normalise()).
    Alpha 255 = painted, 0 = not seen (facing away, or off the car in the photo); later steps fill those."""
    import numpy as np
    v, n = mesh.vertices, mesh.vertex_normals
    depth = COND_DISTANCE - v[:, 0]
    t = np.tan(np.radians(COND_FOVY_DEG / 2))
    size = rgba.shape[0]
    u = ((v[:, 1] / (depth * t) + 1) / 2 * size).astype(int)
    w = ((1 - v[:, 2] / (depth * t)) / 2 * size).astype(int)
    to_cam = np.stack([depth, -v[:, 1], -v[:, 2]], 1)
    facing = (n * to_cam).sum(1) / np.linalg.norm(to_cam, axis=1) > 0.15
    inside = (u >= 0) & (u < size) & (w >= 0) & (w < size)
    ok = facing & inside
    px = rgba[w[ok], u[ok]]
    ok[np.flatnonzero(ok)[px[:, 3] < 200]] = False
    col = np.zeros((len(v), 4), np.uint8)
    col[ok, :3] = rgba[w[ok], u[ok], :3]
    col[ok, 3] = 255
    m = mesh.copy()
    m.visual.vertex_colors = col
    return m


def symmetrize(m, near: int, plane: float, relief: float = 4.0):
    """Mirror the half the camera saw across z = plane and re-mesh. The seen side is treated as a depth map: for each
    cell of the side profile (x, y; SYM_PITCH), how far the surface reaches out toward the camera. The solid runs from
    the plane out to that depth, mirrored to the far side, so the result is always closed and the side profile the
    photo shows best carries through exactly. Colours come from the nearest painted (alpha 255) original vertex on the
    seen side; with nothing painted, from any seen-side vertex."""
    import mcubes
    import numpy as np
    import trimesh
    from scipy import ndimage
    from scipy.spatial import cKDTree
    p = SYM_PITCH
    pts, _ = trimesh.sample.sample_surface(m, 600_000)
    lo = pts[:, :2].min(0) - 4 * p
    nx, ny = (np.ceil((pts[:, :2].max(0) + 4 * p - lo) / p).astype(int) + 1)
    ix = np.floor((pts[:, :2] - lo) / p).astype(int)
    zz = (pts[:, 2] - plane) * near                       # + toward the camera
    depth = np.zeros((nx, ny), np.float32)
    np.maximum.at(depth, (ix[:, 0], ix[:, 1]), np.maximum(zz, 0).astype(np.float32))
    profile = np.zeros((nx, ny), bool)
    profile[ix[:, 0], ix[:, 1]] = True
    profile = ndimage.binary_fill_holes(ndimage.binary_closing(profile, iterations=2))
    depth = ndimage.grey_closing(depth, size=3)          # cells the samples missed
    # smooth inside the profile only (normalised convolution, so the edge isn't pulled toward zero)
    w = ndimage.gaussian_filter(profile.astype(np.float32), relief)
    depth = np.where(profile, ndimage.gaussian_filter(depth * profile, relief) / np.maximum(w, 1e-3), 0)
    nz = int(np.ceil(depth.max() / p)) + 3
    zc = (np.arange(nz) + 0.5) * p                        # voxel centres, distance from the plane
    # a soft field rather than voxels: the 0.5 level sits at the exact depth (no terracing) and on the profile's edge
    edge = ndimage.gaussian_filter(profile.astype(np.float32), 0.8)
    half = np.clip((depth[:, :, None] - zc[None, None, :]) / p + 0.5, 0, 1) * edge[:, :, None]
    field = np.pad(np.concatenate([half[:, :, ::-1], half], axis=2), 2)
    vv, ff = mcubes.marching_cubes(field, 0.5)
    vv -= 2  # the pad
    out = np.empty_like(vv)
    out[:, 0] = vv[:, 0] * p + lo[0] + p / 2
    out[:, 1] = vv[:, 1] * p + lo[1] + p / 2
    out[:, 2] = plane + near * (vv[:, 2] - nz + 0.5) * p
    src_v, src_c = m.vertices, np.asarray(m.visual.vertex_colors)
    side = (src_v[:, 2] - plane) * near >= -2 * p
    painted = side & (src_c[:, 3] == 255)
    use = painted if painted.sum() >= 50 else side
    q = out.copy()
    q[:, 2] = plane + np.abs(q[:, 2] - plane) * near  # far-side vertices look up their mirror image
    _, nearest = cKDTree(src_v[use]).query(q)
    col = src_c[use][nearest].copy()
    col[:, 3] = 255
    mesh = trimesh.Trimesh(vertices=out, faces=ff, vertex_colors=col, process=False)
    if len(mesh.faces) > MAX_FACES:
        mesh = decimate(mesh, MAX_FACES)
    return mesh


def decimate(mesh, faces: int):
    """Quadric decimation (fast_simplification); vertex colours carried over from the nearest original vertex."""
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree
    try:
        small = mesh.simplify_quadric_decimation(face_count=faces)
    except Exception:  # fast_simplification missing: keep the full mesh
        log.warning("decimation unavailable (pip install fast_simplification); keeping %d faces", len(mesh.faces))
        return mesh
    _, nearest = cKDTree(mesh.vertices).query(small.vertices)
    return trimesh.Trimesh(vertices=small.vertices, faces=small.faces,
                           vertex_colors=np.asarray(mesh.visual.vertex_colors)[nearest], process=False)


def fill_unpainted(m):
    """Vertices paint() couldn't see take the colour of the nearest painted one."""
    import numpy as np
    from scipy.spatial import cKDTree
    c = np.asarray(m.visual.vertex_colors).copy()
    painted = c[:, 3] == 255
    if painted.sum() >= 50 and not painted.all():
        _, nearest = cKDTree(m.vertices[painted]).query(m.vertices[~painted])
        c[~painted] = c[painted][nearest]
    c[:, 3] = 255
    m.visual.vertex_colors = c
    return m


def normalise(mesh, direction: str, st: Settings | None = None, cls: str = "car"):
    """TripoSR's frame (x = depth, y = image right, z = up) -> ours (x = length/front, y = up, z = width), levelled
    (the camera looks down at the street, so crops are tilted), length 1, ground at 0, centred. TripoSR's camera is
    on its +x, so the side the photo shows ends up on +z (LR) or -z (RL). With st.sym the far side is replaced by a
    mirror image of that side; st.width sets the width from WIDTH_RATIO (measured from the seen side)."""
    import numpy as np
    st = st or Settings()
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
    near = -1 if direction == "RL" else 1
    ratio = WIDTH_RATIO.get(cls, WIDTH_RATIO["car"])
    if st.sym:
        # the mirror plane: half the set width in from the seen side's outer edge, or the bounding-box centre
        plane = near * (np.percentile(v[:, 2] * near, 99) - ratio / 2) if st.width else 0.0
        out = symmetrize(out, near, plane, st.relief)
        sv = out.vertices.copy()
        lo, hi = sv.min(0), sv.max(0)
        sv -= [(lo[0] + hi[0]) / 2, lo[1], plane]
        out.vertices = sv / max(hi[0] - lo[0], 1e-6)
    else:
        if st.width:
            out.vertices[:, 2] *= ratio / max(np.ptp(out.vertices[:, 2]), 1e-6)
        if st.paint:
            out = fill_unpainted(out)
    trimesh.smoothing.filter_taubin(out, iterations=st.smooth)
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


def run(cfg, labels: list[str] | None = None, force: bool = False, limit: int | None = None, trial: str | None = None,
        settings: str | None = None) -> dict:
    """trial: write to cards/meshes-trials/<trial>/ with its own manifest (for comparing settings side by side; the
    live meshes and the Cards tab are untouched). settings: "k=v,..." overrides on Settings."""
    st = Settings.parse(settings)
    out = cfg.paths.card_meshes
    if trial:
        out = out.parent / "meshes-trials" / slug(trial)
        force = True
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out)
    work = pick_work(cfg, manifest, labels, force, st)
    limit = limit or int(cfg.cards.meshes_per_pass)
    todo = work[:limit]
    log.info("%d label(s) could use a mesh; doing %d", len(work), len(todo))
    if not todo:
        return {"done": 0, "pending": len(work)}
    gen = Generator(cfg.paths.data / "tools", st.sigma)
    done, failed = [], []
    for w in todo:
        t0 = time.time()
        try:
            img, rgba = gen.prepare(w["crop"])
            mesh = gen.mesh(img)
            if st.paint:
                mesh = paint(mesh, rgba)
            mesh = normalise(mesh, w["direction"], st, w["cls"])
            name = f"{slug(w['label'])}-{w['sighting_id'][:8]}.glb"
            tmp = out / (name + ".tmp")
            tmp.write_bytes(mesh.export(file_type="glb", include_normals=True))
            os.replace(tmp, out / name)
            old = manifest.get(w["key"], {}).get("file")
            manifest[w["key"]] = {"label": w["label"], "file": name, "sighting_id": w["sighting_id"],
                                  "box_px": w["box_px"], "score": w["score"], "faces": int(len(mesh.faces)),
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
