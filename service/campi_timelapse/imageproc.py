"""Rotation, horizon leveling with an aspect-locked crop (no black corners), deflicker gains, and night fixes."""
from __future__ import annotations

import math
from collections import deque
from functools import lru_cache

import cv2
import numpy as np

ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
NIGHT_LUMA, DAY_LUMA = 60.0, 90.0  # night fixes are full at frame luma <= NIGHT_LUMA and gone by DAY_LUMA


REF_EXPOSURE = 16.0  # seconds x gain: the Pi's longest night exposure (1 s at gain 16) when those were tuned


def night_weight(frame_luma: float) -> float:
    return min(1.0, max(0.0, (DAY_LUMA - frame_luma) / (DAY_LUMA - NIGHT_LUMA)))


def scene_luma(luma: float, exposure: float | None = None) -> float:
    """Frame brightness as it would be at REF_EXPOSURE, for night_weight(). Auto-exposure keeps frames bright as long
    as it can, so luma alone only says "night" once the exposure is maxed out; with 4 s exposures an overcast night
    would look like day. Frames without a recorded exposure (older ones) pass through as they were tuned."""
    return luma * REF_EXPOSURE / exposure if exposure else luma


def crop_size(w: int, h: int, level_deg: float, aw: int, ah: int) -> tuple[int, int]:
    """Largest centered aw:ah rectangle inside a w x h image rotated by level_deg (no black corners), even sides."""
    t = math.radians(abs(level_deg))
    c, s = math.cos(t), math.sin(t)
    g = math.gcd(aw, ah)
    aw, ah = aw // g, ah // g                     # e.g. 4:3
    a = aw / ah
    # Corners of the crop, rotated back into the source, must stay inside it.
    ch_max = min(w / (a * c + s), h / (a * s + c))
    m = int(ch_max / ah)
    if (aw % 2 or ah % 2) and m % 2:              # keep both sides even
        m -= 1
    return aw * m, ah * m


@lru_cache(maxsize=8)
def _plan(w: int, h: int, rotation: int, level_deg: float, aw: int, ah: int):
    if rotation in (90, 270):
        w, h = h, w
    cw, ch = crop_size(w, h, level_deg, aw, ah)
    # Rotate about the image centre, then shift so the crop window lands at (0, 0).
    m = cv2.getRotationMatrix2D((w / 2, h / 2), level_deg, 1.0)
    m[0, 2] -= (w - cw) / 2
    m[1, 2] -= (h - ch) / 2
    return m, cw, ch


def crop_dims(src_w: int, src_h: int, rotation: int, level_deg: float, out_w: int, out_h: int) -> tuple[int, int]:
    _, cw, ch = _plan(src_w, src_h, rotation, level_deg, out_w, out_h)
    return cw, ch


def level(img: np.ndarray, rotation: int, level_deg: float, out_w: int, out_h: int) -> np.ndarray:
    """Rotate the actual pixels (EXIF orientation is never used), level, and crop to the output's aspect ratio."""
    h, w = img.shape[:2]
    m, cw, ch = _plan(w, h, rotation, level_deg, out_w, out_h)
    if rotation:
        img = cv2.rotate(img, ROTATE[rotation])
    return cv2.warpAffine(img, m, (cw, ch), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)


def deflicker_gains(lumas: list[float], window: int, lo: float, hi: float) -> np.ndarray:
    """Gain per frame so its brightness follows a centered rolling average of its neighbours."""
    y = np.asarray(lumas, dtype=np.float64)
    if len(y) < 3 or window < 2:
        return np.ones(len(y))
    pad = window // 2
    yp = np.pad(y, pad, mode="edge")
    target = np.convolve(yp, np.ones(window) / window, mode="same")[pad:pad + len(y)]
    return np.clip(target / np.maximum(y, 1.0), lo, hi)


class LightDimmer:
    """Tame floodlights that blow out at night, in and around a polygon (0-1 of the camera frame, before leveling, so
    it matches the live view).

    The gain follows the local brightness (luminance blurred over about SIGMA of the frame width), not each pixel:
    big blown-out areas and their glare are pulled down as a whole, the texture inside them survives, and small lamps
    on a dark background (which barely lift the local average) stay bright points. Local brightness above KNEE is
    compressed in log space with a soft knee, so there is no visible threshold; `strength` is how much of the excess
    (in stops) is removed. Pixels that clipped to white have no colour of their own, so as they are dimmed they take
    the tint of the unclipped glow around them instead of going grey. The polygon only says where to look: its edge
    is feathered widely, and as the gain is 1 wherever the scene is dark, the edge never shows. Follows night_weight().
    """
    KNEE = 60.0        # local brightness (0-255) where compression starts
    SOFT = 0.6         # knee width, stops
    SIGMA = 0.008      # local-brightness blur, fraction of the frame width (lamps are smaller, buildings larger)
    TINT_SIGMA = 0.02  # how far clipped pixels look for the colour of their glow
    RECON_DEPTH = 0.01  # clipped blobs: +1 stop of estimated brightness per this much depth (fraction of width)
    RECON_STOPS = 3.0   # ... up to this many stops
    RECON_LOCAL = 0.5   # share of those stops that counts toward the local brightness (how hard glare is pulled down)
    SCALE = 4          # the blurs run at 1/SCALE resolution

    def __init__(self, polygon, strength: float, feather: float = 0.04):
        self.poly = np.asarray(polygon, np.float32) if len(polygon) >= 3 else None
        self.strength, self.feather = float(strength), feather
        self._shape = None

    def _build(self, h: int, w: int):
        r = max(1, round(self.feather * w))
        pts = self.poly * [w - 1, h - 1]
        x0, y0 = np.maximum(np.floor(pts.min(0)).astype(int) - 2 * r, 0)
        x1, y1 = np.minimum(np.ceil(pts.max(0)).astype(int) + 2 * r + 1, [w, h])
        self.box, self._shape = (slice(y0, y1), slice(x0, x1)), (h, w)
        s = self.SCALE
        self.small = (max(1, (x1 - x0) // s), max(1, (y1 - y0) // s))  # cv2 (width, height)
        mask = np.zeros(self.small[::-1], np.float32)
        cv2.fillPoly(mask, [np.round((pts - [x0, y0]) / s).astype(np.int32)], 1.0)
        self.mask = cv2.GaussianBlur(mask, (0, 0), r / s)
        self.sigma, self.tint_sigma = max(0.5, self.SIGMA * w / s), max(0.5, self.TINT_SIGMA * w / s)
        self.recon = max(0.5, self.RECON_DEPTH * w / s)

    def __call__(self, img: np.ndarray, frame_luma: float) -> np.ndarray:
        night = night_weight(frame_luma)
        if self.poly is None or self.strength <= 0 or night <= 0:
            return img
        if self._shape != img.shape[:2]:
            self._build(*img.shape[:2])
        roi8 = img[self.box]
        rh, rw = roi8.shape[:2]
        small = cv2.resize(roi8, self.small, interpolation=cv2.INTER_AREA).astype(np.float32)
        ys = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        # clipped blobs lost their falloff: estimate it, one stop brighter per RECON_DEPTH of depth into the blob
        # (capped), so big glare fades toward its source instead of becoming a flat wall; small lamps barely change
        clip_s = (np.max(small, axis=2) >= 250).astype(np.uint8)
        depth = cv2.distanceTransform(clip_s, cv2.DIST_L2, 3) if clip_s.any() else np.zeros_like(ys)
        boost = np.exp2(np.minimum(cv2.GaussianBlur(depth, (0, 0), 1.5) / self.recon, self.RECON_STOPS))
        ys = ys * boost ** self.RECON_LOCAL

        # gain from the local brightness: log2(local) - strength * softplus(log2(local) - log2(knee))
        excess = np.log2(cv2.GaussianBlur(ys, (0, 0), self.sigma) + 1.0) - math.log2(self.KNEE + 1.0)
        soft = self.SOFT * np.logaddexp2(0.0, excess / self.SOFT)
        gain = 1.0 - night * self.mask * (1.0 - np.exp2(-self.strength * soft))

        # colour of the unclipped glow nearby, per channel relative to luminance (1 = neutral where there is none)
        wgt = ((ys > 40) & (ys < 230)).astype(np.float32)
        num = cv2.GaussianBlur(small * wgt[..., None], (0, 0), self.tint_sigma)
        den = cv2.GaussianBlur(ys * wgt, (0, 0), self.tint_sigma)
        tint = (num + 2.0) / (den[..., None] + 2.0)
        tint /= np.maximum(tint @ np.float32([0.114, 0.587, 0.299]), 1e-3)[..., None]
        tint = np.clip(tint, 0.35, 1.8)

        gain = cv2.resize(gain, (rw, rh), interpolation=cv2.INTER_LINEAR)
        out = cv2.multiply(roi8.astype(np.float32), cv2.merge([gain, gain, gain]))
        # clipped pixels (few): as they are dimmed, use the reconstructed brightness and the glow's colour (theirs
        # is lost). The tint is smooth, so it is sampled nearest from the small grid; the boost is interpolated.
        mx = cv2.max(cv2.max(roi8[..., 0], roi8[..., 1]), roi8[..., 2])
        yy, xx = np.nonzero(mx > 235)
        if yy.size:
            g = gain[yy, xx]
            amount = (np.clip((mx[yy, xx] - 235.0) / 20.0, 0.0, 1.0) * np.clip((1.0 - g) * 2.5, 0.0, 1.0))[:, None]
            sh, sw = boost.shape
            si, sj = np.minimum(yy * sh // rh, sh - 1), np.minimum(xx * sw // rw, sw - 1)
            b = cv2.resize(boost, (rw, rh), interpolation=cv2.INTER_LINEAR)[yy, xx]
            o = out[yy, xx]
            y = o @ np.float32([0.114, 0.587, 0.299])
            out[yy, xx] = o + amount * ((y * b)[:, None] * tint[si, sj] - o)
        img = img.copy()
        img[self.box] = cv2.convertScaleAbs(out)  # rounds and saturates to uint8
        return img


class NightSky:
    """Night fixes for the sky above the treeline, a polygon (0-1 of the camera frame, before leveling, like
    LightDimmer's), so faint stars survive:

    - hot pixels: sensor points lit in every night frame (stars drift past, these stay) are replaced by the median
      of their surroundings, before anything else touches the frame. The map comes from find_hot_pixels().
    - stacking: the sky is averaged over more frames than the rest of the scene (temporal_denoise's wide_radius);
      nothing up there moves fast enough to smear in ~20 s, and the noise falls ~sqrt(n).
    - flattening: the smooth glow (town, plant floodlights, haze) is estimated with the stars median-filtered out
      and pulled down toward the darkest part of the sky, so stars over the glow get the same contrast.
    All three follow night_weight()."""
    FEATHER = 0.004       # mask edge blur, fraction of the frame width
    HOT_WINDOW = 21       # the Pi's ISP smears each hot pixel into a ~10 px blob: compare with a median this wide
    HOT_MIN = 4           # a blob is every pixel this far (0-255) above that median in the median night frame ...
    HOT_PEAK = 9          # ... that peaks at least this high (fainter ones are mostly noise that never repeats) ...
    HOT_LAMP = 80         # ... but not this high (a tower beacon or a lamp is a real light)
    HOT_MAX_AREA = 200    # ... and is a speck, not a building
    HOT_MIN_SPAN_S = 300  # the sampled frames must span this long, so every star has moved on
    HOT_FILL_SIGMA = 3.0  # hot pixels take the smooth sky around them
    STACK_MAX_S = 24      # the sky stack never spans more than this (thinned frames are a minute apart)
    BG_SCALE = 8          # the glow is estimated at 1/BG_SCALE resolution ...
    BG_SIGMA = 0.015      # ... blurred over this fraction of the frame width
    FLOOR_PCT = 5         # glow is pulled down to this percentile of the sky's glow

    def __init__(self, polygon, stack_frames: int, flatten: float, hot_pixels: bool):
        self.poly = np.asarray(polygon, np.float32) if len(polygon) >= 3 else None
        self.stack_radius = max(0, int(stack_frames) - 1) // 2 if self.poly is not None else 0
        self.flatten_strength = float(flatten) if self.poly is not None else 0.0
        self.hot_pixels = bool(hot_pixels) and self.poly is not None
        self.hot = None   # bool map (camera frame) from find_hot_pixels or a saved one
        self._hot_boxes = None
        self._raw = None

    @property
    def enabled(self) -> bool:
        return self.poly is not None

    def raw_mask(self, h: int, w: int) -> np.ndarray:
        """Sky weight 0-1 in the camera frame."""
        if self._raw is None or self._raw.shape != (h, w):
            mask = np.zeros((h, w), np.float32)
            cv2.fillPoly(mask, [np.round(self.poly * [w - 1, h - 1]).astype(np.int32)], 1.0)
            self._raw = cv2.GaussianBlur(mask, (0, 0), max(0.5, self.FEATHER * w))
        return self._raw

    def find_hot_pixels(self, imgs) -> np.ndarray | None:
        """Hot-pixel map from night frames spread over several minutes (a median, so stars and planes drop out)."""
        if not imgs:
            return None
        h, w = imgs[0].shape[:2]
        sky = self.raw_mask(h, w) > 0.5
        rows = sky_rows(sky.astype(np.float32))
        res = [cv2.subtract(img[rows], cv2.medianBlur(img[rows], self.HOT_WINDOW))
               for img in imgs if img.shape[:2] == (h, w)]
        if len(res) < 5:
            return None
        med = np.zeros((h, w), np.float32)
        med[rows] = np.median(np.stack(res), axis=0).max(axis=2)  # per channel first: noise in any one is common
        cand = ((med >= self.HOT_MIN) & sky).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cand)
        peak = np.zeros(n, np.float32)
        np.maximum.at(peak, lab.ravel(), med.ravel())
        keep = (stats[:, cv2.CC_STAT_AREA] <= self.HOT_MAX_AREA) & (peak >= self.HOT_PEAK) & (peak < self.HOT_LAMP)
        keep[0] = False
        hot = keep[lab].astype(np.uint8)
        return cv2.dilate(hot, np.ones((5, 5), np.uint8)) > 0  # the blob's faint rim

    def fix_hot(self, img: np.ndarray, frame_luma: float) -> np.ndarray:
        night = night_weight(frame_luma)
        if not self.hot_pixels or self.hot is None or night <= 0 or self.hot.shape != img.shape[:2]:
            return img
        if self._hot_boxes is None or self._hot_boxes[0] is not self.hot:
            self._hot_boxes = (self.hot, self._boxes(self.hot))
        img = img.copy()
        for box, hot, inv_den in self._hot_boxes[1]:
            # normalized blur of the healthy pixels: the smooth sky under each blob, however big the blob is
            part = img[box].astype(np.float32)
            fill = cv2.GaussianBlur(part * (~hot)[..., None], (0, 0), self.HOT_FILL_SIGMA) * inv_den
            part[hot] += night * (fill[hot] - part[hot])
            img[box] = cv2.convertScaleAbs(part)
        return img

    def _boxes(self, hot: np.ndarray):
        """(slices, hot mask, 1 / blurred healthy weight) per blob, padded so each sees healthy sky around it."""
        n, _, stats, _ = cv2.connectedComponentsWithStats(hot.astype(np.uint8))
        pad, h, w = int(3 * self.HOT_FILL_SIGMA) + 2, *hot.shape
        out = []
        for x, y, bw, bh, _ in stats[1:]:
            box = (slice(max(0, y - pad), min(h, y + bh + pad)), slice(max(0, x - pad), min(w, x + bw + pad)))
            m = hot[box]
            den = cv2.GaussianBlur((~m).astype(np.float32), (0, 0), self.HOT_FILL_SIGMA)
            out.append((box, m, (1.0 / np.maximum(den, 1e-3))[..., None]))
        return out

    def flatten(self, img: np.ndarray, frame_luma: float, mask: np.ndarray) -> np.ndarray:
        """Subtract the sky's smooth glow above its darkest part; `mask` is the sky weight at img's size."""
        night = night_weight(frame_luma)
        if self.flatten_strength <= 0 or night <= 0:
            return img
        h, w = img.shape[:2]
        s = self.BG_SCALE
        size = (max(1, w // s), max(1, h // s))
        small = cv2.medianBlur(cv2.resize(img, size, interpolation=cv2.INTER_AREA), 5).astype(np.float32)
        m = (cv2.resize(mask, size, interpolation=cv2.INTER_AREA) > 0.5).astype(np.float32)
        if not m.any():
            return img
        sigma = max(0.5, self.BG_SIGMA * w / s)
        # normalized blur: only sky pixels count, so the dark treeline doesn't drag the glow down at the horizon
        bg = cv2.GaussianBlur(small * m[..., None], (0, 0), sigma) / np.maximum(
            cv2.GaussianBlur(m, (0, 0), sigma), 1e-3)[..., None]
        floor = np.percentile(bg[m > 0], self.FLOOR_PCT, axis=0)
        excess = np.maximum(bg - floor, 0.0) * (self.flatten_strength * night)
        rows = sky_rows(mask)
        excess = cv2.resize(excess, (w, h), interpolation=cv2.INTER_LINEAR)[rows] * mask[rows][..., None]
        img = img.copy()
        img[rows] = cv2.subtract(img[rows], excess, dtype=cv2.CV_8U)
        return img


def sky_rows(mask: np.ndarray) -> slice:
    """The rows where a 0-1 mask is set (the sky is the top part of the frame: work only there)."""
    ys = np.flatnonzero(mask.reshape(mask.shape[0], -1).max(axis=1) > 1e-3)
    return slice(int(ys[0]), int(ys[-1]) + 1) if ys.size else slice(0, 0)


def temporal_denoise(items, radius: int, weight, wide_radius: int = 0, wide_mask: np.ndarray | None = None):
    """Yield each (key, img) blended toward the mean of its neighbours within `radius` frames (clipped at the ends).

    Night frames carry per-frame sensor noise that shimmers in a timelapse; averaging 2r+1 frames cuts it by about
    sqrt(2r+1) while static scenery stays sharp (moving things smear across the window). `weight(key)` (0-1) sets
    how far each frame moves toward the mean, so daylight frames pass through untouched. Where `wide_mask` (0-1,
    frame-sized) is set, the mean spans `wide_radius` frames instead (the night sky)."""
    rows = slice(0, 0)
    if wide_mask is None or wide_radius <= radius:
        wide_radius, wide_mask = 0, None
    else:
        rows = sky_rows(wide_mask)
        wide_mask = (wide_mask[..., None] if wide_mask.ndim == 2 else wide_mask)[rows]
    reach = max(radius, wide_radius)
    if reach < 1:
        yield from items
        return
    it, buf, start, i, done = iter(items), deque(), 0, 0, False

    def mean(r, part=slice(None)):
        lo, hi = max(i - r, start), min(i + r, start + len(buf) - 1)
        acc = buf[lo - start][1][part].astype(np.float32)
        for j in range(lo + 1, hi + 1):
            cv2.add(acc, buf[j - start][1][part], acc, dtype=cv2.CV_32F)
        return acc * np.float32(1.0 / (hi - lo + 1))

    while True:
        while not done and len(buf) - (i - start) <= reach:  # load up to item i + reach
            try:
                buf.append(next(it))
            except StopIteration:
                done = True
        if i - start >= len(buf):
            return
        key, img = buf[i - start]
        w = weight(key)
        if w > 0 and len(buf) > 1:
            target = mean(radius)
            if wide_mask is not None:
                sky = target[rows]
                sky += wide_mask * (mean(wide_radius, rows) - sky)
            if w < 1:
                target = cv2.addWeighted(img.astype(np.float32), 1.0 - w, target, w, 0.0)
            img = cv2.convertScaleAbs(target)  # rounds and saturates to uint8
        yield key, img
        i += 1
        while i - reach > start:
            buf.popleft()
            start += 1


def apply_gain(img: np.ndarray, gain: float) -> np.ndarray:
    if abs(gain - 1.0) < 0.002:
        return img
    return cv2.convertScaleAbs(img, alpha=gain)
