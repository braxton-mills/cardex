"""The water tower intro for `campi site`: a game-style 3D replay of the real build (intro3d\\tank_scene.py in
Blender, headless) with a HUD drawn over it here, in five acts:

1. "THIS IS WHAT'S BEING BUILT": the finished tank as a cyan blueprint over the site.
2-3. The replay: the site as the camera sees it, day by day from the forms going up, driven by what `campi site`
   detected (schedule()): every pour with the boom pump unfolding over the pedestal, mixer trucks, the climbing form
   jumping a lift, the lift count from the pedestal measured each midday, plus a few facts the detector can't see
   (EXTRAS).
4. "STILL TO COME": the rest of the job as a hologram (the pedestal to full height, the bowl built at ground and
   jacked up, paint), then the finished tank.
5. An orbit with the spec cards. The facts are the ones known from the site sign and public filings (SPECS);
   nothing is invented (no height, no cost).

The Blender frames are cached in work\\intro3d\\<hash of the scene script, the acts and the schedule>\\, so a new pour
renders them again and a HUD change doesn't.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import NO_WINDOW
from .forensic import blend, ease, span

log = logging.getLogger("intro")

SCENE = Path(__file__).with_name("intro3d") / "tank_scene.py"
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
FPS = 30
GAME = r"C:\Windows\Fonts\bahnschrift.ttf"

# seconds
ACTS = {"blueprint": (0.0, 6.0), "site": (6.0, 7.5), "replay": (7.5, 26.0), "projected": (26.0, 33.0),
        "orbit": (33.0, 45.0)}
INTRO_S = ACTS["orbit"][1]
# the projected part, inside its act
PROJECTED = {"ghost": (26.3, 27.8), "bowl": (27.8, 29.0), "jack": (29.0, 31.0), "paint": (31.0, 32.0),
             "solid": (32.0, 33.0)}
LIFTS_TOTAL = 25               # the finished pedestal, illustrative (the drawings aren't public)

# the build order (the composite-tank version of it)
STAGES = [
    ("prep", "SITE PREP & EXCAVATION", None),
    ("found", "FOUNDATION · 28-DAY CURE", None),
    ("fab", "STEEL CUT & ROLLED IN THE SHOP", None),
    ("ped", "CONCRETE PEDESTAL, LIFT BY LIFT", PROJECTED["ghost"]),
    ("bowl", "BOWL BUILT AT GROUND, JACKED UP", (PROJECTED["bowl"][0], PROJECTED["jack"][1])),
    ("pipe", "PIPING, VALVES & CONTROLS", (PROJECTED["jack"][1], PROJECTED["paint"][0])),
    ("paint", "PAINT & LOGO", PROJECTED["paint"]),
    ("test", "FILL TEST & DISINFECTION", (PROJECTED["solid"][0], PROJECTED["solid"][0] + 0.5)),
    ("finish", "FENCING, PAVING, CLEANUP", (PROJECTED["solid"][0] + 0.5, PROJECTED["solid"][1])),
]
NOW_STAGE = "ped"          # where the real job is (the footage: the pedestal going up)

# what happened that the activity detector doesn't see (from the footage): day, kind, caption
FIRST_DAY = date(2026, 10, 3)
EXTRAS = [
    (date(2026, 10, 3), "forms", "CLIMBING FORMS SET UP"),
    (date(2026, 10, 10), "crew", "SATURDAY · CREW ON TOP"),
    (date(2026, 10, 14), "dig", "EXCAVATOR ON THE SPOIL PILE"),
]

SPECS = [
    # (label, value, detail)
    ("TYPE", "COMPOSITE ELEVATED TANK", "CONCRETE PEDESTAL + STEEL BOWL"),
    ("CAPACITY", "500,000 GALLONS", "0.5 MILLION GALLONS (MG)"),
    ("OWNER", "BEAR CREEK SUD", "FINANCED THROUGH GTUA BONDS"),
    ("BUILDER", "CALDWELL TANKS", "SINCE 1887 · LOUISVILLE, KY"),
    ("JOB", "STEADY PRESSURE + FIRE RESERVE", "GRAVITY-FED · PRESSURE PLANE 1"),
    ("SERVES", "ELEVON", "4,500+ HOMES · 1,000 APARTMENTS · 1,500+ ACRES"),
    ("SCHEDULE", "SUMMER 2026 TO WINTER 2027", "START AND FINISH FROM THE SITE SIGN"),
    ("STATUS", "PEDESTAL RISING", "LIFT {lifts} · STAGE 4 OF 9 · LIVE SITE FEED NEXT"),
]
CARD_T0, CARD_DT = PROJECTED["ghost"][0] + 0.8, 1.6

CYAN, GOLD, GREEN, WHITE, DIM = (80, 230, 255), (255, 206, 64), (110, 255, 150), (245, 250, 255), (150, 170, 190)
PANEL = (8, 16, 32)

# the replay's pacing (seconds before fitting it to its act)
DAY_S = 0.45                   # a day with nothing to show
FIRST_POUR_S = 3.6             # the first pour, in full: the truck backs in, the boom unfolds, the lift fills
POUR_S = 1.0
EXTRA_S = 1.0


def read_s(text: str) -> float:
    """How long a replay caption has to stay up (they're short: a few words)."""
    return max(1.5, 0.6 + 0.24 * len(text.split()))


def facts_text() -> str:
    """The facts as plain lines (for the caption prompt)."""
    return "\n".join(f"- {a.title()}: {b.title()} ({c.lower().replace('lift {lifts} · ', '')})" for a, b, c in SPECS)


# --- the schedule -------------------------------------------------------------------------------------------------

def schedule(pours: list[float], lifts: dict[date, int], last_day: date, cleared: date | None = None,
             seen: list[tuple[date, str, str]] = ()) -> dict:
    """What the replay shows and when (intro seconds), from what was detected: `pours` (start times of the concrete
    pours, every one seen), `lifts` (the pedestal's lifts at each measured day, never dropping), the day it rose
    past the trees, EXTRAS, and what Claude saw on each day (`seen`, site_ai.day_log: one event a day at most, on
    days without a hand-kept one). The lift counts are the truth: a day's new lifts are spread over its pours
    (a pour that adds none tops one up); lifts measured on a day with no pour seen (the midday reading drifts, or a
    pour was missed) wait for the next pour."""
    measured = sorted(lifts)
    lifts0 = lifts[measured[0]] if measured else 0
    extras = {}
    for d, kind, text in EXTRAS:
        if FIRST_DAY <= d <= last_day:
            extras.setdefault(d, []).append((kind, text))
    kept = set(extras)
    for d, kind, text in seen:   # in site_ai's order (the most notable first)
        if FIRST_DAY <= d <= last_day and d not in kept:
            extras[d] = [(kind, text)]
            kept.add(d)
    if cleared is not None:
        extras.setdefault(cleared, []).append(("trees", "ABOVE THE TREE LINE"))
    by_day: dict[date, list[float]] = {}
    for p in sorted(pours):
        by_day.setdefault(datetime.fromtimestamp(p).date(), []).append(p)

    # each day: its pours (with the lifts each one adds), extras, and a weight
    days, n_lift, seen_first = [], lifts0, False
    d = FIRST_DAY
    while d <= last_day:
        ps = by_day.get(d, [])
        end = max([v for k, v in lifts.items() if k <= d], default=lifts0)
        new = max(0, end - n_lift) if ps else 0
        if d == last_day and not ps and end > n_lift and days and days[-1]["pours"]:
            # measured today with no pour seen yet: the last pour's
            days[-1]["adds"][-1] += list(range(n_lift + 1, end + 1))
            days[-1]["lifts_end"] = end
            n_lift = end
        if d.weekday() == 6 and not ps and d not in extras:
            d += timedelta(days=1)
            continue
        adds = [[] for _ in ps]
        for j in range(new):     # spread the new lifts over the pours (more lifts than pours: some fill two)
            adds[min(len(ps) - 1, int(j * len(ps) / new))].append(n_lift + j + 1)
        pour_w = []
        for _ in ps:
            pour_w.append(FIRST_POUR_S if not seen_first else POUR_S)
            seen_first = True
        ex = extras.get(d, [])
        w = DAY_S + sum(pour_w) + EXTRA_S * len(ex)
        days.append({"date": d, "pours": ps, "adds": adds, "pour_w": pour_w, "extras": ex, "w": w,
                     "lifts_start": n_lift, "lifts_end": n_lift + new})
        n_lift += new
        d += timedelta(days=1)

    # captions: the pour number, then what's special about the day (or what a pour does)
    n_pour = 0
    for dd in days:
        k = len(dd["pours"])
        head, tail = None, [t for _, t in dd["extras"]]
        dd["generic"] = False
        if k:
            a = n_pour + 1
            n_pour += k
            head = f"POUR {a}" if k == 1 else f"POURS {a}-{n_pour}"
            if not tail:
                dd["generic"] = a > 1
                tail = ["THE BOOM PUMP FILLS THE FORMS" if a == 1 else
                        "THE FORMS CLIMB A LIFT" if k == 1 else f"{k} IN ONE DAY"]
        dd["caption"] = " · ".join(([head] if head else []) + tail) or None
    # fit to the act: in proportion to the weights, each captioned day at least its reading time; too many days and
    # the plain ones lose their captions (oldest first) and go by faster
    a0, a1 = ACTS["replay"]
    budget = a1 - a0 - 0.4

    def fit():
        fixed: dict[int, float] = {}
        for _ in range(len(days) + 1):
            free = [dd for i, dd in enumerate(days) if i not in fixed]
            sc = (budget - sum(fixed.values())) / max(1e-6, sum(dd["w"] for dd in free))
            more = {i: read_s(dd["caption"]) for i, dd in enumerate(days)
                    if i not in fixed and dd["caption"] and dd["w"] * sc < read_s(dd["caption"])}
            if not more:
                return fixed, sc
            fixed.update(more)
        return fixed, sc

    while sum(read_s(dd["caption"]) for dd in days if dd["caption"]) > budget * 0.9:
        plain = [dd for dd in days if dd["caption"] and dd["generic"]]
        if not plain:
            break
        plain[0]["caption"] = None
    fixed, scale = fit()
    t = a0 + 0.2
    out_days, out_pours, booms, mixers, captions, digs, crews = [], [], [], [], [], [], []
    n_pour = 0
    for i, dd in enumerate(days):
        dur = fixed.get(i, dd["w"] * scale)
        k_ = dur / dd["w"]
        t0, t1 = t, t + dur
        out_days.append({"t0": round(t0, 3), "t1": round(t1, 3), "date": dd["date"].isoformat(),
                         "label": f"{dd['date']:%a %b %d}".upper(), "lifts": dd["lifts_end"]})
        if dd["caption"]:
            captions.append({"t0": round(t0, 3), "t1": round(t1, 3), "text": dd["caption"]})
        p = t0 + DAY_S * k_ * 0.4
        day_pours = []
        for ts, adds, pw in zip(dd["pours"], dd["adds"], dd["pour_w"]):
            pd = pw * k_
            n_pour += 1
            first = n_pour == 1
            unfold = min(1.4, pd * 0.4) if first else (min(0.35, pd * 0.3) if not day_pours else 0.0)
            jump = min(0.5 if first else 0.25, pd * 0.2)
            day_pours.append({"n": n_pour, "t0": round(p, 3), "t1": round(p + pd, 3),
                              "fill": [round(p + unfold + 0.1 * pd, 3), round(p + pd - jump, 3)],
                              "jump": [round(p + pd - jump, 3), round(p + pd, 3)] if adds else None,
                              "lifts": adds, "time": datetime.fromtimestamp(ts).strftime("%H:%M")})
            p += pd
        if day_pours:
            out_pours += day_pours
            u0 = day_pours[0]["t0"]
            booms.append({"out": [u0, day_pours[0]["fill"][0] - 0.05],
                          "in": [day_pours[-1]["t1"], min(t1 + 0.3, day_pours[-1]["t1"] + 0.6)]})
            lead = 1.0 if day_pours[0]["n"] == 1 else 0.35
            mixers.append({"in": [round(u0 - lead, 3), round(u0, 3)],
                           "out": [day_pours[-1]["t1"], round(day_pours[-1]["t1"] + 0.6, 3)]})
        for kind, _ in dd["extras"]:
            if kind == "dig":
                digs.append({"t0": round(t0, 3), "t1": a1})
            elif kind == "crew":
                crews.append({"t0": round(t0, 3), "t1": round(t1, 3)})
        t = t1
    lifts_now = days[-1]["lifts_end"] if days else lifts0
    return {"acts": ACTS, "projected": PROJECTED, "lifts0": lifts0, "lifts_now": lifts_now,
            "lifts_total": max(LIFTS_TOTAL, lifts_now + 4), "days": out_days, "pours": out_pours, "booms": booms,
            "mixers": mixers, "captions": captions, "digs": digs, "crews": crews,
            "forms_up": out_days[0]["t0"] if out_days else a0}


# --- the 3D frames ------------------------------------------------------------------------------------------------

def blender_exe(cfg) -> str:
    p = getattr(getattr(cfg, "tools", None), "blender", "") or BLENDER
    if not Path(p).is_file():
        raise FileNotFoundError(f"Blender not found at {p} ([tools] blender)")
    return p


def frames_dir(cfg, sched: dict, draft: bool = False) -> Path:
    h = hashlib.sha1(SCENE.read_bytes() + json.dumps(sched, sort_keys=True).encode()).hexdigest()[:10]
    return cfg.paths.work / "intro3d" / (h + ("_draft" if draft else ""))


def n_frames() -> int:
    return int(round(INTRO_S * FPS))


def render(cfg, sched: dict, still: float | None = None, draft: bool = False) -> Path:
    """Render the 3D frames (or one still) with Blender, headless; cached. Draft: half size, fewer samples."""
    out = frames_dir(cfg, sched, draft)
    out.mkdir(parents=True, exist_ok=True)
    if still is None and len(list(out.glob("f_*.png"))) >= n_frames():
        return out
    plan = out / "plan.json"
    plan.write_text(json.dumps(sched, indent=1), encoding="utf-8")
    cmd = [blender_exe(cfg), "-b", "--factory-startup", "-P", str(SCENE), "--", str(out), "--plan", str(plan)]
    if still is not None:
        cmd += ["--still", str(still)]
    if draft:
        cmd += ["--draft"]
    t0 = time.time()
    log.info("rendering the 3D intro (%s) into %s", "still %.1f s" % still if still is not None else
             f"{n_frames()} frames", out)
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or "Traceback" in (r.stdout or ""):
        raise RuntimeError("blender failed: " + ((r.stdout or "") + (r.stderr or ""))[-3000:])
    log.info("3D intro rendered in %.0f s", time.time() - t0)
    return out


# --- the HUD ------------------------------------------------------------------------------------------------------

def panel(d, box, a: float, k: float, edge=CYAN) -> None:
    """A game panel: dark glass with two clipped corners and a bright edge (a = opacity 0-1)."""
    x0, y0, x1, y1 = box
    c = round(14 * k)
    poly = [(x0 + c, y0), (x1, y0), (x1, y1 - c), (x1 - c, y1), (x0, y1), (x0, y0 + c)]
    d.polygon(poly, fill=(*PANEL, int(205 * a)))
    d.line(poly + [poly[0]], fill=(*edge, int(255 * a)), width=max(2, round(2 * k)))


class HUD:
    def __init__(self, w: int, h: int, sched: dict):
        self.w, self.h, self.s = w, h, sched
        k = self.k = h / 1080
        self.f_tag = ImageFont.truetype(GAME, round(24 * k))
        self.f_big = ImageFont.truetype(GAME, round(76 * k))
        self.f_date = ImageFont.truetype(GAME, round(54 * k))
        self.f_sub = ImageFont.truetype(GAME, round(32 * k))
        self.f_cap = ImageFont.truetype(GAME, round(36 * k))
        self.f_num = ImageFont.truetype(GAME, round(46 * k))
        self.f_lbl = ImageFont.truetype(GAME, round(19 * k))
        self.f_val = ImageFont.truetype(GAME, round(34 * k))
        self.f_det = ImageFont.truetype(GAME, round(21 * k))

    def frame(self, t: float) -> Image.Image:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        self.title(d, t)
        self.replay(d, t)
        self.projected(d, t)
        self.cards(d, t)
        self.corner(d, t)
        return img

    def panel(self, d, box, a, edge=CYAN):
        panel(d, box, a, self.k, edge)

    def title(self, d, t):
        """The opening card: what this is."""
        a = ease(span(t, 0.3, 0.9)) * (1 - span(t, ACTS["blueprint"][1] - 0.6, ACTS["blueprint"][1]))
        if a <= 0:
            return
        k, w = self.k, self.w
        y = round(70 * k) - round(40 * k) * (1 - ease(span(t, 0.3, 0.9)))
        bw = round(1180 * k)
        self.panel(d, (w / 2 - bw / 2, y, w / 2 + bw / 2, y + round(200 * k)), a, GOLD)
        d.text((w / 2, y + round(30 * k)), "NEW STRUCTURE INCOMING", font=self.f_tag, fill=(*GOLD, int(255 * a)),
               anchor="mm")
        d.text((w / 2, y + round(95 * k)), "THIS IS WHAT'S BEING BUILT", font=self.f_big, fill=(*WHITE, int(255 * a)),
               anchor="mm")
        d.text((w / 2, y + round(160 * k)), "0.5 MG COMPOSITE ELEVATED WATER TANK · BEAR CREEK SUD", font=self.f_sub,
               fill=(*CYAN, int(255 * a)), anchor="mm")

    # the replay: the date, the counters, the day's caption
    def counts(self, t):
        pours = [p for p in self.s["pours"] if p["t0"] <= t]
        lifts = self.s["lifts0"]
        last_add = None
        for p in self.s["pours"]:
            if p["lifts"] and p["fill"][1] <= t:
                lifts = max(lifts, max(p["lifts"]))
                last_add = p["fill"][1]
        return len(pours), lifts, (pours[-1]["t0"] if pours else None), last_add

    def replay(self, d, t):
        a0, a1 = ACTS["site"][0], ACTS["replay"][1]
        a = ease(span(t, a0 + 0.3, a0 + 0.9)) * (1 - span(t, a1 - 0.2, a1 + 0.4))
        if a <= 0 or not self.s["days"]:
            return
        k, w = self.k, self.w
        days = self.s["days"]
        cur = next((dd for dd in reversed(days) if dd["t0"] <= t), days[0])
        # top left: what this is, and the date
        x, y = round(36 * k), round(30 * k)
        bw, bh = round(470 * k), round(178 * k)
        self.panel(d, (x, y, x + bw, y + bh), a, GOLD)
        d.text((x + round(22 * k), y + round(18 * k)), "REPLAY · THE BUILD SO FAR", font=self.f_tag,
               fill=(*GOLD, int(255 * a)))
        d.text((x + round(22 * k), y + round(50 * k)), cur["label"], font=self.f_date, fill=(*WHITE, int(255 * a)))
        d.text((x + round(22 * k), y + round(120 * k)), "STAGE 4 OF 9 · CONCRETE PEDESTAL", font=self.f_det,
               fill=(*DIM, int(255 * a)))
        frac = (t - days[0]["t0"]) / max(1e-6, days[-1]["t1"] - days[0]["t0"])
        bx0, bx1, by = x + round(22 * k), x + bw - round(22 * k), y + round(154 * k)
        d.rectangle([bx0, by, bx1, by + round(8 * k)], outline=(*DIM, int(200 * a)), width=1)
        d.rectangle([bx0, by, bx0 + (bx1 - bx0) * min(1.0, max(0.0, frac)), by + round(8 * k)],
                    fill=(*GOLD, int(255 * a)))
        # top right: the counters
        n_p, n_l, t_p, t_l = self.counts(t)
        bw2 = round(410 * k)
        x2 = w - round(36 * k) - bw2
        self.panel(d, (x2, y, x2 + bw2, y + round(150 * k)), a)
        for row, (lbl, val, tc) in enumerate((("CONCRETE POURS SEEN", n_p, t_p), ("PEDESTAL LIFTS", n_l, t_l))):
            yy = y + round(22 * k) + row * round(64 * k)
            pop = 1 - span(t, tc, tc + 0.5) if tc is not None else 0.0
            col = tuple(int(c1 + (c2 - c1) * pop) for c1, c2 in zip(WHITE, GREEN))
            d.text((x2 + round(22 * k), yy + round(22 * k)), lbl, font=self.f_tag, fill=(*CYAN, int(255 * a)),
                   anchor="lm")
            d.text((x2 + bw2 - round(22 * k), yy + round(22 * k)), str(val), font=self.f_num,
                   fill=(*col, int(255 * a)), anchor="rm")
        # the day's caption, bottom centre
        for c in self.s["captions"]:
            t1 = c["t1"]
            b = ease(span(t, c["t0"], c["t0"] + 0.25)) * (1 - span(t, t1 - 0.2, t1)) * a
            if b <= 0:
                continue
            tw = d.textlength(c["text"], font=self.f_cap)
            cw = tw + round(70 * k)
            cy = self.h - round(150 * k)
            self.panel(d, (w / 2 - cw / 2, cy, w / 2 + cw / 2, cy + round(70 * k)), b, GOLD)
            d.text((w / 2, cy + round(35 * k)), c["text"], font=self.f_cap, fill=(*WHITE, int(255 * b)), anchor="mm")

    def projected(self, d, t):
        """The rest of the job: a banner with the step the hologram is on."""
        p0, p1 = ACTS["projected"]
        a = ease(span(t, p0, p0 + 0.5)) * (1 - span(t, p1 - 0.4, p1 + 0.2))
        if a <= 0:
            return
        k, w = self.k, self.w
        bw = round(700 * k)
        x = w - round(36 * k) - bw
        y = round(30 * k)
        self.panel(d, (x, y, x + bw, y + round(120 * k)), a)
        d.text((x + round(24 * k), y + round(18 * k)), "STILL TO COME · PROJECTED", font=self.f_tag,
               fill=(*CYAN, int(255 * a)))
        cur = next(((i, s) for i, s in enumerate(STAGES) if s[2] and s[2][0] <= t < s[2][1]), None)
        if cur is None:
            cur = (3, STAGES[3]) if t < STAGES[3][2][0] else (len(STAGES) - 1, STAGES[-1])
        i, (_, label, _) = cur
        d.text((x + round(24 * k), y + round(52 * k)), label, font=self.f_val, fill=(*WHITE, int(255 * a)))
        d.text((x + round(24 * k), y + round(92 * k)), f"STEP {i + 1} OF 9 · NOT BUILT YET", font=self.f_det,
               fill=(*DIM, int(255 * a)))
        for j in range(len(STAGES)):
            px = x + bw - round(250 * k) + j * round(25 * k)
            py = y + round(22 * k)
            col = GREEN if j < 3 else CYAN
            lit = j < i or (j == i and int(t * 4) % 2 == 0)
            d.rectangle([px, py, px + round(16 * k), py + round(16 * k)], fill=(*col, int(255 * a)) if lit else None,
                        outline=(*DIM, int(255 * a)), width=2)

    def cards(self, d, t):
        """The specs: one card at a time, sliding in from the left and staying."""
        k = self.k
        x0, y0 = round(36 * k), round(118 * k)
        ch, gap = round(104 * k), round(10 * k)
        bw = round(640 * k)
        out = 1 - span(t, INTRO_S - 0.6, INTRO_S - 0.1)
        for i, (lbl, val, det) in enumerate(SPECS):
            t0 = CARD_T0 + i * CARD_DT
            a = ease(span(t, t0, t0 + 0.35)) * out
            if a <= 0:
                continue
            x = x0 - round(120 * k) * (1 - ease(span(t, t0, t0 + 0.35)))
            y = y0 + i * (ch + gap)
            last = i == len(SPECS) - 1
            edge = GOLD if last else CYAN
            self.panel(d, (x, y, x + bw, y + ch), a, edge)
            d.text((x + round(20 * k), y + round(14 * k)), lbl, font=self.f_lbl, fill=(*edge, int(255 * a)))
            d.text((x + round(20 * k), y + round(36 * k)), val, font=self.f_val, fill=(*WHITE, int(255 * a)))
            d.text((x + round(20 * k), y + round(76 * k)), det.format(lifts=self.s["lifts_now"]), font=self.f_det,
                   fill=(*DIM, int(255 * a)))
            if lbl == "CAPACITY":
                self.bar(d, x + bw - round(190 * k), y + round(48 * k), round(160 * k), 1.0, a, CYAN, t - t0)
            if last:
                # nine pips, three done, the pedestal blinking (in progress)
                n_done = [s[0] for s in STAGES].index(NOW_STAGE)
                for j in range(len(STAGES)):
                    px = x + bw - round(250 * k) + j * round(25 * k)
                    py = y + round(48 * k)
                    lit = j < n_done or (j == n_done and int(t * 3) % 2 == 0)
                    col = GREEN if j < n_done else GOLD
                    d.rectangle([px, py, px + round(18 * k), py + round(18 * k)],
                                fill=(*col, int(255 * a)) if lit else None, outline=(*DIM, int(255 * a)), width=2)

    def bar(self, d, x, y, bw, frac, a, col, t):
        fill = frac * ease(span(t, 0.3, 1.2))
        d.rectangle([x, y, x + bw, y + round(16 * self.k)], outline=(*col, int(255 * a)), width=2)
        d.rectangle([x + 3, y + 3, x + 3 + (bw - 6) * fill, y + round(16 * self.k) - 3], fill=(*col, int(255 * a)))

    def corner(self, d, t):
        """Always on: what this is (a rendering); in the orbit, what the finished tank is."""
        k, w, h = self.k, self.w, self.h
        a = ease(span(t, 0.3, 1.0)) * (1 - span(t, INTRO_S - 0.5, INTRO_S))
        d.text((w - round(30 * k), h - round(28 * k)), "ILLUSTRATIVE 3D RENDERING · NOT TO SCALE", font=self.f_lbl,
               fill=(*WHITE, int(200 * a)), anchor="rs")
        o0 = ACTS["orbit"][0]
        b = a * ease(span(t, o0, o0 + 0.6))
        if b > 0:
            d.text((w - round(30 * k), round(30 * k)), "WHEN FINISHED · WINTER 2027", font=self.f_tag,
                   fill=(*GOLD, int(255 * b)), anchor="ra")


def frames(cfg, w: int, h: int, sched: dict, draft: bool = False):
    """The intro, frame by frame (BGR, w x h): the 3D render with the HUD over it."""
    src = render(cfg, sched, draft=draft)
    hud = HUD(w, h, sched)
    for i in range(n_frames()):
        p = src / f"f_{i + 1:04d}.png"
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(p)
        if img.shape[1] != w or img.shape[0] != h:
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC if img.shape[1] < w else cv2.INTER_AREA)
        yield blend(img, hud.frame(i / FPS))
