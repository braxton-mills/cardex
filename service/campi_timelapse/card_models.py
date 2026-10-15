"""Card models: a downloaded Sketchfab model per make/model label for the Cards tab (optional; [cards] models_*).

For each make/model label (generic vehicle types keep the procedural car), Sketchfab is searched for free downloadable
CC-licensed models. Every result is scored (`score`: the name must match the make/model; race, toy, game-mod and
AI-generated models, other generations and odd sizes lose points) and the best one is downloaded, unless the Cards
inspector pinned another pick or rejected the label (ui\\card_model_picks.json, written by the API).
car_models\\prepare.mjs (node + gltf-transform; install.ps1 -Models) puts it in the card frame (length 1 along +x =
the front, y up, ground at y=0, centred), drops plates and studio floors, simplifies and compresses it. An auto
pick that is untextured or not car-shaped gives way to the next candidate.

cards\\models\\ holds <slug>-<uid8>[-r<yaw>].glb, manifest.json (label lower -> file, attribution, paint materials,
...), candidates\\<slug>.json (the scored search results the inspector offers) and raw\\<uid>\\ (the downloads, kept
only for labels with an inspector choice, so a re-orientation there needs no new download; the rest are deleted).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .card_meshes import _ReadOnlyStore, load_manifest, save_manifest, slug

log = logging.getLogger("models")

API = "https://api.sketchfab.com/v3"
UA = "campi-timelapse card models"
PREPARE = Path(__file__).resolve().parent.parent / "car_models" / "prepare.mjs"
SEARCH_MAX_AGE_S = 30 * 86400   # candidates are searched again after this
MAX_ARCHIVE_BYTES = 200e6
TOP_N = 8                       # candidates kept for the inspector
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# --- scoring -----------------------------------------------------------------------------------------------------
# Words in a model's name that make it a poor card car, with the points they cost. Tags cost a third as much.
BAD_WORDS = {
    40: "race racing racecar rally drift drag nascar gt3 gt4 dtm f1 formula police cop sheriff wreck wrecked damaged "
        "crashed burnt destroyed abandoned cartoon toon chibi toy lego minecraft voxel roblox papercraft beamng "
        "assetto gta fivem forza ets2 hunyuan tripo meshy",
    30: "wheel wheels rim rims tire tyre engine seat steering dashboard chassis frame skeleton",
    20: "concept stylized stylised rusty rust monster pixel miniature diorama",
    12: "lowpoly widebody stance tuned tuning lifted custom kit bodykit taxi ambulance limo limousine scan "
        "photogrammetry print printing printable typer "
        # performance trims look unlike the everyday car the camera sees (vents, wings, wide arches)
        "quadrifoglio trx raptor amg srt hellcat demon trackhawk shelby gt500 gt350 nismo sti zl1 z06 zr1 svt",
}
BAD = {w: pts for pts, words in BAD_WORDS.items() for w in words.split()}
# Words that say nothing about which car it is.
NEUTRAL = set("""a an and the with of by for in on from free 3d model models car cars auto automobile vehicle vehicles
    game ready realistic highpoly high poly detailed pbr version v1 v2 v3 new sedan saloon suv crossover hatchback
    hatch coupe convertible wagon estate pickup truck van minivan fbx obj blend blender max c4d gltf glb rigged
    animated interior exterior uv textured texture textures""".split())
MAKE_ALIASES = {"chevrolet": ["chevy"], "volkswagen": ["vw"], "mercedes-benz": ["mercedes", "benz"],
                "mercedes": ["benz"], "mini": ["bmw mini"], "land rover": ["range rover"]}
GENERIC_MODEL_WORDS = {"van", "truck", "pickup", "suv", "car", "wagon", "sedan"}
YEAR_RE = re.compile(r"(?<!\d)(19[3-9]\d|20[0-3]\d)(?!\d)")
CC_LICENSES = re.compile(r"^(cc\b|cc0|cc attribution|creative commons)", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def years_of(year_range: str | None) -> tuple[int, int] | None:
    ys = [int(y) for y in YEAR_RE.findall(year_range or "")]
    return (min(ys), max(ys)) if ys else None


def score(result: dict, item: dict) -> tuple[float, list[str]] | None:
    """Points for one Sketchfab search result as the card model for item {make, model, year_range}, with the
    reasons; None when it can't be used at all (no name match, not CC, not downloadable, too big)."""
    if result.get("isAgeRestricted") or result.get("isDownloadable") is False:
        return None
    lic = (result.get("license") or {}).get("label") or ""
    if not CC_LICENSES.match(lic):
        return None
    archives = result.get("archives") or {}
    arch = archives.get("glb") or archives.get("gltf")
    if not arch or (arch.get("size") or 0) > MAX_ARCHIVE_BYTES:
        return None
    name = re.sub(r"(?i)\btype[ -]?r\b", "typer", result.get("name") or "")  # Civic Type R: one trim word
    n = norm(name)
    make, model = item.get("make") or "", item.get("model") or ""
    core = " ".join(w for w in words(model) if w not in GENERIC_MODEL_WORDS) or model
    makes = [make, *MAKE_ALIASES.get(make.lower(), [])]
    has_make = any(norm(m) and norm(m) in n for m in makes)
    pts, why = 0.0, []
    first = (words(core) or [""])[0]
    if any(norm(m + core) in n for m in makes if m):
        pts += 40; why.append("make+model")
    elif len(norm(core)) >= 4 and norm(core) in n:
        pts += 30 + (10 if has_make else 0); why.append("model" + (" + make" if has_make else ""))
    elif len(first) >= 4 and first in words(name) and has_make:
        pts += 15; why.append(f"'{first}' + make")
    else:
        return None

    # Every other word (a trim, a different body, someone's project name) makes it less likely to be this car.
    known = set(words(make) + words(model)) | {w for m in makes for w in words(m)} | NEUTRAL
    extra = [w for w in words(name) if w not in known and not YEAR_RE.fullmatch(w) and w not in BAD]
    if extra:
        pts -= min(4 * len(extra), 20); why.append("extra: " + " ".join(extra[:5]))
    for w in set(words(name)) | ({"lowpoly"} if "lowpoly" in n or "low poly" in name.lower() else set()):
        if w in BAD:
            pts -= BAD[w]; why.append(f"-{BAD[w]} {w}")
    tags = {t.get("name", "").lower() for t in result.get("tags") or []}
    tag_bad = sorted(t for t in tags if t in BAD)
    if tag_bad:
        pts -= sum(BAD[t] for t in tag_bad) / 3; why.append("tags: " + " ".join(tag_bad[:4]))

    ys = [int(y) for y in YEAR_RE.findall(name)]
    want = years_of(item.get("year_range"))
    if ys and want:
        lo, hi = want
        d = min(0 if lo - 1 <= y <= hi + 1 else min(abs(y - lo), abs(y - hi)) for y in ys)
        if d == 0:
            pts += 15; why.append("year fits")
        elif d <= 6:
            pts -= 5; why.append("year near")
        else:
            pts -= 25; why.append("other generation")
    elif ys and max(ys) < 1995:
        pts -= 20; why.append("classic")

    faces = arch.get("faceCount") or result.get("faceCount") or 0
    if faces < 8_000:
        pts -= 20; why.append("very low poly")
    elif faces < 30_000:
        pts -= 8; why.append("low poly")
    elif faces > 4_000_000:
        pts -= 25; why.append("huge")
    elif faces > 1_500_000:
        pts -= 10; why.append("heavy")
    elif faces > 600_000:
        pts -= 3
    likes = result.get("likeCount") or 0
    pts += min(10.0, 3 * math.log10(1 + likes))
    if result.get("staffpickedAt"):
        pts += 5; why.append("staff pick")
    return round(pts, 2), why


def thumbnail(result: dict, want: int = 400) -> str | None:
    imgs = (result.get("thumbnails") or {}).get("images") or []
    imgs = [i for i in imgs if i.get("url") and i.get("width")]
    if not imgs:
        return None
    return min(imgs, key=lambda i: (i["width"] < want, abs(i["width"] - want)))["url"]


def candidate(result: dict, pts: float, why: list[str]) -> dict:
    archives = result.get("archives") or {}
    arch = archives.get("glb") or archives.get("gltf") or {}
    user = result.get("user") or {}
    return {"uid": result["uid"], "name": result.get("name"), "author": user.get("displayName") or user.get("username"),
            "author_url": user.get("profileUrl"), "url": result.get("viewerUrl"),
            "license": (result.get("license") or {}).get("label"),
            "faces": arch.get("faceCount") or result.get("faceCount"), "bytes": arch.get("size"),
            "textures": arch.get("textureCount"), "likes": result.get("likeCount"),
            "thumbnail": thumbnail(result), "score": pts, "why": why}


# --- Sketchfab ---------------------------------------------------------------------------------------------------

def _get(url: str, token: str | None = None) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)


def search(item: dict) -> list[dict]:
    """Scored candidates for a make/model item, best first."""
    make, model = item.get("make") or "", item.get("model") or ""
    seen, out = set(), []
    for q in (f"{make} {model}", model):
        qs = urllib.parse.urlencode({"type": "models", "q": q, "downloadable": "true", "count": 24})
        try:
            results = _get(f"{API}/search?{qs}").get("results") or []
        except OSError as e:
            log.warning("search %r failed: %s", q, e)
            results = []
        for r in results:
            if r.get("uid") in seen:
                continue
            seen.add(r.get("uid"))
            s = score(r, item)
            if s:
                out.append(candidate(r, *s))
        if out:
            break
    out.sort(key=lambda c: -c["score"])
    return out


def read_token(cfg) -> str:
    p = Path(cfg.cards.sketchfab_token_file)
    try:
        tok = p.read_text(encoding="utf-8").strip()
    except OSError:
        tok = ""
    if not tok:
        raise RuntimeError(f"no Sketchfab API token in {p} (sketchfab.com > Settings > Password & API)")
    return tok


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r, \
            open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def _extract(zpath: Path, into: Path) -> None:
    """Unzip, refusing entries that would land outside `into` (zip-slip)."""
    base = into.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = (base / info.filename).resolve()
            if base not in target.parents and target != base:
                raise RuntimeError(f"unsafe path in archive: {info.filename}")
        z.extractall(base)


def fetch(uid: str, token: str, raw: Path) -> tuple[Path, dict]:
    """The downloaded model file for uid (cached in raw\\<uid>\\) and its attribution."""
    d = raw / uid
    meta_p = d / "meta.json"
    found = sorted(d.rglob("*.glb")) + sorted(d.rglob("*.gltf")) if d.is_dir() else []
    if found and meta_p.is_file():
        return found[0], json.loads(meta_p.read_text(encoding="utf-8"))
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    meta = _get(f"{API}/models/{uid}")
    links = _get(f"{API}/models/{uid}/download", token)
    lic = meta.get("license") or {}
    user = meta.get("user") or {}
    attribution = {"uid": uid, "name": meta.get("name"), "author": user.get("displayName") or user.get("username"),
                   "author_url": user.get("profileUrl"), "url": meta.get("viewerUrl"),
                   "license": lic.get("label"), "license_url": lic.get("url")}
    if links.get("glb"):
        path = d / "model.glb"
        _download(links["glb"]["url"], path)
    elif links.get("gltf"):
        z = d / "model.zip"
        _download(links["gltf"]["url"], z)
        _extract(z, d / "extracted")
        z.unlink()
        gl = sorted((d / "extracted").rglob("*.gltf")) + sorted((d / "extracted").rglob("*.glb"))
        if not gl:
            raise RuntimeError("no .gltf/.glb in the archive")
        path = gl[0]
    else:
        raise RuntimeError(f"no glb/gltf download offered (got {', '.join(links) or 'nothing'})")
    meta_p.write_text(json.dumps(attribution, indent=1), encoding="utf-8")
    return path, attribution


# --- prepare (node) ----------------------------------------------------------------------------------------------

def node_exe(cfg) -> str:
    exe = cfg.cards.node or "node"
    found = shutil.which(exe)
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    if not found and default.is_file():  # the service's session may not have the user's PATH
        found = str(default)
    if not found:
        raise RuntimeError(f"node not found ({exe}); install Node.js or set [cards] node")
    return found


def prepare(cfg, src: Path, out: Path, yaw: int) -> dict:
    if not (PREPARE.parent / "node_modules").is_dir():
        raise RuntimeError(f"{PREPARE.parent}\\node_modules is missing (install.ps1 -Models)")
    tmp = out.with_suffix(".tmp")
    cmd = [node_exe(cfg), str(PREPARE), str(src), str(tmp), "--yaw", str(yaw),
           "--faces", str(int(cfg.cards.models_faces)), "--texture", str(int(cfg.cards.models_texture))]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       creationflags=NO_WINDOW)
    if p.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"prepare.mjs exited {p.returncode}: {p.stderr.strip()[-800:]}")
    for line in p.stderr.splitlines():
        if line.strip():
            log.debug("prepare: %s", line)
    os.replace(tmp, out)
    return json.loads(p.stdout.strip().splitlines()[-1])


PAINT_NAME = re.compile(r"car ?paint|body ?paint|paint|carros|lack|^body|exterior|_body|body_", re.I)
NOT_PAINT = re.compile(r"glass|window|light|lamp|lens|chrome|tire|tyre|rubber|rim|wheel|interi|intern|inner|seat|"
                       r"black|trim|plastic|grill|mirror|brake|disc|disk|carbon|metal|logo|badge|emblem|plate|"
                       r"exhaust", re.I)


def paint_materials(materials: list[dict]) -> list[str]:
    """Names of the body-paint materials: by name, else the largest opaque, untextured, not-black/grey-trim one."""
    named = [m["name"] for m in materials if PAINT_NAME.search(m["name"]) and not NOT_PAINT.search(m["name"])]
    if named:
        return named
    total = sum(m["area"] for m in materials) or 1
    for m in materials:  # largest first
        c = m.get("color") or [1, 1, 1, 1]
        if m.get("alpha") == "BLEND" or m.get("textured") or NOT_PAINT.search(m["name"]):
            continue
        if max(c[:3]) < 0.04 or m["area"] < 0.08 * total or (m.get("y") or 0) < 0.08:
            continue
        return [m["name"]]
    return []


# --- picks (written by the API) ----------------------------------------------------------------------------------

def picks_path(cfg) -> Path:
    return cfg.paths.ui / "card_model_picks.json"


def load_picks(cfg) -> dict:
    try:
        v = json.loads(picks_path(cfg).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def set_pick(cfg, label: str, change: dict) -> dict:
    """Apply an inspector choice for a label (API side): {uid}, {rejected: true}, {yaw}, {paint: [...]} or
    {clear: true}. The next models pass downloads or re-prepares; paint applies at once."""
    picks = load_picks(cfg)
    key = label.lower()
    cur = {} if change.get("clear") else dict(picks.get(key) or {})
    if "uid" in change:
        cur.pop("rejected", None)
        if change["uid"] != cur.get("uid"):
            cur.pop("yaw", None)  # a new model gets its own front guess
        cur["uid"] = change["uid"]
    if change.get("rejected"):
        cur = {"rejected": True}
    if "yaw" in change:
        cur["yaw"] = int(change["yaw"]) % 360
    if "paint" in change:
        cur["paint"] = list(change["paint"])
    if cur:
        cur["label"], cur["updated_at"] = label, time.strftime("%Y-%m-%dT%H:%M:%S%z")
        picks[key] = cur
    else:
        picks.pop(key, None)
    p = picks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(picks, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return cur


def pending(entry: dict | None, pick: dict) -> bool:
    """Whether the next models pass will change this label's model (paint never needs one)."""
    if pick.get("rejected"):
        return entry is not None
    if pick.get("uid") and (not entry or entry.get("uid") != pick["uid"]):
        return True
    return bool(entry) and int(pick.get("yaw") or 0) != int(entry.get("yaw") or 0)


def load_candidates(d: Path, label: str) -> dict | None:
    try:
        return json.loads((d / "candidates" / f"{slug(label)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- the pass ----------------------------------------------------------------------------------------------------

def items(cfg) -> list[dict]:
    """Make/model labels, caught ones first (then the labels file order), with the card's year range."""
    from .ui.sightings_data import Sightings
    sd = Sightings(cfg, _ReadOnlyStore(cfg))
    known = sd.catalog()["known"]
    stats = sd.card_stats()
    out = []
    for i, (key, k) in enumerate(known.items()):
        if k["generic"] or not k.get("model"):
            continue
        s = stats.get(key)
        out.append({"key": key, "label": k["label"], "make": k["make"] or "", "model": k["model"],
                    "year_range": s["years"].most_common(1)[0][0] if s and s["years"] else None,
                    "caught": bool(s), "order": i})
    out.sort(key=lambda x: (not x["caught"], x["order"]))
    return out


def unfit(st: dict) -> str | None:
    """Why a prepared model is a poor auto pick: untextured, or not car-shaped in the card frame (length 1: a scene of
    several cars or loose parts comes out flat and narrow, a lone part tall or wide)."""
    if st.get("plain"):
        return "untextured"
    h, w = st.get("height") or 0, st.get("width") or 0
    if not (0.18 <= h <= 0.8 and 0.25 <= w <= 0.8):
        return f"odd proportions (height {h:.2f}, width {w:.2f} of the length)"
    return None


def accept(d: Path, manifest: dict, it: dict, attribution: dict, name: str, st: dict, yaw: int, pick: dict,
           t0: float) -> None:
    """Make a prepared model the label's model (saving the manifest) and remove the file it replaces."""
    entry = {**attribution, "label": it["label"], "file": name, "yaw": yaw, "auto_yaw": st["auto_yaw"],
             "front": st["front"], "faces": st["faces_out"], "height": st["height"], "width": st["width"],
             "paint": paint_materials(st["materials"]), "plain": bool(st.get("plain")),
             "materials": [m["name"] for m in st["materials"][:24]],
             "picked": "user" if pick.get("uid") else "auto",
             "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    old = (manifest.get(it["key"]) or {}).get("file")
    manifest[it["key"]] = entry
    save_manifest(d, manifest)
    if old and old != name:
        (d / old).unlink(missing_ok=True)
    log.info("model for %s: %s by %s (%s), %d faces, front from %s, paint %s, in %.1f s", it["label"],
             entry["name"], entry["author"], entry["license"], entry["faces"], entry["front"],
             entry["paint"] or "none", time.time() - t0)


def run(cfg, labels: list[str] | None = None, force: bool = False, limit: int | None = None,
        search_only: bool = False) -> dict:
    d = cfg.paths.card_models
    (d / "candidates").mkdir(parents=True, exist_ok=True)
    raw = d / "raw"
    manifest = load_manifest(d)
    picks = load_picks(cfg)
    only = {x.lower() for x in labels or []}
    todo = [i for i in items(cfg) if not only or i["key"] in only]
    if only and not todo:
        raise ValueError(f"no make/model label matches {', '.join(sorted(only))}")
    limit = limit or int(cfg.cards.models_per_pass)
    token = None
    done, failed, searched = [], [], 0
    rate_limited = False

    for it in todo:
        if rate_limited:
            break
        key, pick = it["key"], picks.get(it["key"]) or {}
        cands = load_candidates(d, it["label"])
        if force or not cands or time.time() - cands.get("searched_at", 0) > SEARCH_MAX_AGE_S \
                or cands.get("year_range") != it["year_range"]:
            res = search(it)
            searched += 1
            cands = {"label": it["label"], "query": f"{it['make']} {it['model']}", "year_range": it["year_range"],
                     "searched_at": time.time(), "candidates": res[:TOP_N]}
            p = d / "candidates" / f"{slug(it['label'])}.json"
            p.with_suffix(".tmp").write_text(json.dumps(cands, indent=1), encoding="utf-8")
            os.replace(p.with_suffix(".tmp"), p)
            time.sleep(0.5)  # be gentle with the search API
        if search_only:
            top = cands["candidates"][:3]
            log.info("%s: %s", it["label"], "; ".join(f"{c['score']:.0f} {c['name']}" for c in top) or "nothing")
            done.append({"label": it["label"], "candidates": [(c["score"], c["name"], c["uid"]) for c in top]})
            continue

        have = manifest.get(key)
        if pick.get("rejected") or (not pick.get("uid") and not cands["candidates"]):
            if have:
                (d / have["file"]).unlink(missing_ok=True)
                del manifest[key]
            continue
        # an auto pick that won't download or prepare falls back to the next candidates
        uids = [pick["uid"]] if pick.get("uid") else [c["uid"] for c in cands["candidates"][:3]]
        yaw = int(pick.get("yaw") or 0) % 360
        # an auto model stays while it is still among the top candidates (it may be a fallback)
        current = uids if not pick.get("uid") and (have or {}).get("picked") == "auto" else uids[:1]
        if have and have.get("uid") in current and int(have.get("yaw") or 0) == yaw and not (force and only):
            continue
        if len(done) >= limit:
            break
        # an unfit auto pick (untextured, odd shape) waits: a later fit candidate wins, else the first unfit one
        fallback = None
        for uid in uids:
            t0 = time.time()
            try:
                token = token or read_token(cfg)
                src, attribution = fetch(uid, token, raw)
                # the yaw is in the name: the UI caches model files by name
                name = f"{slug(it['label'])}-{uid[:8]}{f'-r{yaw}' if yaw else ''}.glb"
                st = prepare(cfg, src, d / name, yaw)
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    log.exception("model %s for %s failed: %s", uid, it["label"], e)
                    continue
                log.warning("Sketchfab is rate limiting downloads; the rest wait for the next pass")
                rate_limited = True
                break
            except Exception as e:
                log.exception("model %s for %s failed: %s", uid, it["label"], e)
                continue
            why = unfit(st)
            if why and not pick.get("uid"):
                log.info("%s for %s: %s", attribution["name"], it["label"], why)
                if fallback:
                    (d / name).unlink(missing_ok=True)
                else:
                    fallback = (attribution, name, st, t0)
                continue
            if fallback:
                (d / fallback[1]).unlink(missing_ok=True)
                fallback = None
            accept(d, manifest, it, attribution, name, st, yaw, pick, t0)
            done.append(it["label"])
            break
        if fallback and not rate_limited:  # a rate-limited label tries its other candidates next pass
            accept(d, manifest, it, *fallback[:3], yaw, pick, fallback[3])
            done.append(it["label"])
        elif it["label"] not in done:
            failed.append(it["label"])
        # downloads are kept only for labels the inspector has touched (where a rotate needs no new download)
        keep = (manifest.get(key) or {}).get("uid") if pick else None
        for uid in uids:
            if uid != keep:
                shutil.rmtree(raw / uid, ignore_errors=True)

    if not search_only:
        save_manifest(d, manifest)  # also marks the picks as handled for the supervisor
    if not search_only and not only:
        used = {manifest[k]["uid"] for k in picks if k in manifest} | {p["uid"] for p in picks.values() if p.get("uid")}
        for sub in raw.iterdir() if raw.is_dir() else []:
            if sub.is_dir() and sub.name not in used:
                shutil.rmtree(sub, ignore_errors=True)
    return {"done": done if search_only else len(done), "failed": failed, "searched": searched}
