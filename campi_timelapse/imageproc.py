"""Rotation, horizon leveling with an aspect-locked crop (no black corners), and deflicker gains."""
from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np

ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


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


def apply_gain(img: np.ndarray, gain: float) -> np.ndarray:
    if abs(gain - 1.0) < 0.002:
        return img
    return cv2.convertScaleAbs(img, alpha=gain)
