"""Rotation, horizon leveling with an aspect-locked crop (no black corners), deflicker gains, and night fixes."""
from __future__ import annotations

import math
from collections import deque
from functools import lru_cache

import cv2
import numpy as np

ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
NIGHT_LUMA, DAY_LUMA = 60.0, 90.0  # night fixes are full at frame luma <= NIGHT_LUMA and gone by DAY_LUMA


def night_weight(frame_luma: float) -> float:
    return min(1.0, max(0.0, (DAY_LUMA - frame_luma) / (DAY_LUMA - NIGHT_LUMA)))


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
    """Pull bright pixels inside a polygon down on dark frames (e.g. floodlights that blow out at night).

    Works on the camera frame before leveling, so the polygon (0-1 of the frame) matches what the live view shows.
    Brightness above KNEE is compressed by (1 - strength), keeping brighter pixels brighter (a flat multiplier would
    turn the lamps into dark holes inside their glow); below KNEE nothing changes. Colour is scaled with the
    brightness, the polygon edge is feathered, and the effect follows night_weight()."""
    KNEE = 60.0

    def __init__(self, polygon, strength: float, feather: float = 0.01):
        self.poly = np.asarray(polygon, np.float32) if len(polygon) >= 3 else None
        self.strength, self.feather = float(strength), feather
        self._shape = None

    def _build(self, h: int, w: int):
        r = max(1, round(self.feather * w))
        pts = np.round(self.poly * [w - 1, h - 1]).astype(np.int32)
        x0, y0 = np.maximum(pts.min(0) - 3 * r, 0)
        x1, y1 = np.minimum(pts.max(0) + 3 * r + 1, [w, h])
        mask = np.zeros((y1 - y0, x1 - x0), np.float32)
        cv2.fillPoly(mask, [pts - [x0, y0]], 1.0)
        self.mask = cv2.GaussianBlur(mask, (0, 0), r)
        self.box, self._shape = (slice(y0, y1), slice(x0, x1)), (h, w)

    def __call__(self, img: np.ndarray, frame_luma: float) -> np.ndarray:
        night = night_weight(frame_luma)
        if self.poly is None or self.strength <= 0 or night <= 0:
            return img
        if self._shape != img.shape[:2]:
            self._build(*img.shape[:2])
        roi = img[self.box].astype(np.float32)
        y = np.maximum(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 1.0)
        compressed = np.where(y > self.KNEE, self.KNEE + (y - self.KNEE) * (1.0 - self.strength), y)
        f = 1.0 - night * self.mask * (1.0 - compressed / y)
        img = img.copy()
        img[self.box] = np.clip(roi * f[..., None], 0, 255).astype(np.uint8)
        return img


def temporal_denoise(items, radius: int, weight):
    """Yield each (key, img) blended toward the mean of its neighbours within `radius` frames (clipped at the ends).

    Night frames carry per-frame sensor noise that shimmers in a timelapse; averaging 2r+1 frames cuts it by about
    sqrt(2r+1) while static scenery stays sharp (moving things smear across the window). `weight(key)` (0-1) sets
    how far each frame moves toward the mean, so daylight frames pass through untouched."""
    if radius < 1:
        yield from items
        return
    it, buf, start, i, done = iter(items), deque(), 0, 0, False
    while True:
        while not done and len(buf) - (i - start) <= radius:  # load up to item i + radius
            try:
                buf.append(next(it))
            except StopIteration:
                done = True
        if i - start >= len(buf):
            return
        key, img = buf[i - start]
        w = weight(key)
        lo, hi = max(i - radius, start), min(i + radius, start + len(buf) - 1)
        if w > 0 and hi > lo:
            mean = np.mean([buf[j - start][1] for j in range(lo, hi + 1)], axis=0, dtype=np.float32)
            img = cv2.addWeighted(img.astype(np.float32), 1.0 - w, mean, w, 0.0)
            img = np.clip(img + 0.5, 0, 255).astype(np.uint8)
        yield key, img
        i += 1
        while i - radius > start:
            buf.popleft()
            start += 1


def apply_gain(img: np.ndarray, gain: float) -> np.ndarray:
    if abs(gain - 1.0) < 0.002:
        return img
    return cv2.convertScaleAbs(img, alpha=gain)
