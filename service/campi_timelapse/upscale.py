"""Real-ESRGAN upscaling on the GPU (venv-sightings: torch), for `campi site`'s footage: the subject box is a quarter
of the camera frame, so it is shown 2x larger; this draws in the detail instead of stretching the pixels.

The network (RRDBNet, as in basicsr) is written out here so nothing new needs installing; the weights are downloaded
once into the data folder's tools\\upscale\\ and loaded as plain tensors (weights_only).
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger("upscale")

MODELS = {
    2: ("RealESRGAN_x2plus.pth", "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth"),
    4: ("RealESRGAN_x4plus.pth", "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"),
}


def _net(scale: int):
    import torch
    from torch import nn
    from torch.nn import functional as F

    class RDB(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
            self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
            self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
            self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
            self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            return self.conv5(torch.cat((x, x1, x2, x3, x4), 1)) * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, nf):
            super().__init__()
            self.rdb1, self.rdb2, self.rdb3 = RDB(nf), RDB(nf), RDB(nf)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, scale, nf=64, nb=23):
            super().__init__()
            self.scale = scale
            self.conv_first = nn.Conv2d(3 * (4 if scale == 2 else 1), nf, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(nf) for _ in range(nb)])
            self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            feat = F.pixel_unshuffle(x, 2) if self.scale == 2 else x
            feat = self.conv_first(feat)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet(scale)


class Upscaler:
    """`up(bgr)` -> the image `scale` times larger (x2: natural; x4 paints over distant trees and gravel)."""

    def __init__(self, cfg, scale: int = 2):
        import torch

        name, url = MODELS[scale]
        p = Path(cfg.paths.data) / "tools" / "upscale" / name
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            log.info("downloading %s", name)
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(p)
        sd = torch.load(p, map_location="cpu", weights_only=True)
        sd = sd.get("params_ema", sd.get("params", sd))
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        torch.backends.cudnn.benchmark = True       # every frame is the same size
        self.half = self.dev == "cuda"
        net = _net(scale)
        net.load_state_dict(sd, strict=True)
        net.eval().to(self.dev)
        self.net = net.half() if self.half else net
        self.scale = scale
        self.torch = torch

    def __call__(self, img: np.ndarray) -> np.ndarray:
        torch = self.torch
        with torch.inference_mode():
            x = torch.from_numpy(np.ascontiguousarray(img[..., ::-1])).to(self.dev)
            x = x.permute(2, 0, 1)[None].float().div_(255)
            if self.half:
                x = x.half()
            y = self.net(x).clamp_(0, 1)[0].permute(1, 2, 0).float().mul_(255).round_().byte().cpu().numpy()
        return np.ascontiguousarray(y[..., ::-1])
