#!/usr/bin/env python3
"""
car_id.py - spot vehicles on the campi stream and guess their make and model.

How it works:
  1. YOLO finds and tracks every vehicle in the live stream.
  2. Each tracked vehicle's crops are scored against a list of make/model names
     with SigLIP 2 (zero-shot: no training, edit the list below to add cars).
  3. Scores are averaged over the vehicle's whole pass (bigger, closer views
     count more), then ONE result per vehicle is printed, logged to cars.csv,
     and its best crop is saved to the cars/ folder.

Setup (once, same Python env as your YOLO script):
    pip install ultralytics lap open_clip_torch "transformers[sentencepiece]"
    (first run downloads YOLO11m and SigLIP 2 weights, a few GB total)

Run:
    python car_id.py          press q in the video window (or Ctrl+C) to quit
"""

import csv
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import open_clip
import torch
from PIL import Image
from ultralytics import YOLO

# ------------------------------- settings -----------------------------------
STREAM = "http://campi.local:8000/stream.mjpg"
ROTATE = None  # None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, or cv2.ROTATE_90_COUNTERCLOCKWISE

YOLO_WEIGHTS = "yolo11m.pt"  # bigger than 'n': much better at small, distant cars
YOLO_IMGSZ = 1280            # larger input helps with far-away vehicles
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}  # COCO class ids

CLIP_MODEL = "ViT-SO400M-16-SigLIP2-384"  # strong at fine-grained recognition like car models
CLIP_PRETRAINED = "webli"

MIN_CROP_PX = 64      # skip vehicles narrower than this: too few pixels to tell models apart
MAX_SAMPLES = 25      # crops scored per vehicle; after that only closer views get scored
MIN_SAMPLES = 3       # need at least this many crops before reporting a vehicle
LOST_AFTER_S = 2.0    # report a vehicle once it has been out of view this long
UNSURE_BELOW = 0.30   # top guess below this share gets reported as "unsure"

OUT_DIR = Path(__file__).with_name("cars")       # best crop of each vehicle
CSV_PATH = Path(__file__).with_name("cars.csv")  # one row per vehicle
SHOW = True                                      # live window with labels

# What it can recognize. Add or remove freely; it's just text.
MAKES_MODELS = {
    "Toyota": ["Camry", "Corolla", "RAV4", "Highlander", "Grand Highlander", "4Runner",
               "Tacoma", "Tundra", "Sequoia", "Sienna", "Prius", "Crown"],
    "Honda": ["Civic", "Accord", "CR-V", "HR-V", "Pilot", "Passport", "Odyssey", "Ridgeline"],
    "Ford": ["F-150", "F-250 Super Duty", "Ranger", "Maverick", "Explorer", "Expedition",
             "Escape", "Bronco", "Bronco Sport", "Mustang", "Mustang Mach-E", "Transit van"],
    "Chevrolet": ["Silverado 1500", "Silverado 2500HD", "Colorado", "Tahoe", "Suburban",
                  "Equinox", "Traverse", "Blazer", "Trax", "Trailblazer", "Malibu",
                  "Camaro", "Corvette", "Express van"],
    "GMC": ["Sierra 1500", "Sierra 2500HD", "Canyon", "Yukon", "Acadia", "Terrain"],
    "Ram": ["1500", "2500", "ProMaster van"],
    "Jeep": ["Wrangler", "Gladiator", "Grand Cherokee", "Cherokee", "Compass"],
    "Dodge": ["Charger", "Challenger", "Durango"],
    "Chrysler": ["Pacifica"],
    "Nissan": ["Altima", "Sentra", "Versa", "Rogue", "Kicks", "Murano", "Pathfinder",
               "Armada", "Frontier", "Titan"],
    "Hyundai": ["Elantra", "Sonata", "Tucson", "Santa Fe", "Palisade", "Kona", "Venue",
                "Ioniq 5", "Santa Cruz"],
    "Kia": ["Forte", "K5", "Soul", "Seltos", "Sportage", "Sorento", "Telluride",
            "Carnival", "EV6"],
    "Subaru": ["Outback", "Forester", "Crosstrek", "Ascent", "Impreza", "WRX"],
    "Mazda": ["Mazda3", "CX-30", "CX-5", "CX-50", "CX-90", "MX-5 Miata"],
    "Volkswagen": ["Jetta", "Golf GTI", "Tiguan", "Atlas", "ID.4"],
    "Tesla": ["Model 3", "Model Y", "Model S", "Model X", "Cybertruck"],
    "BMW": ["3 Series", "5 Series", "X3", "X5", "X7"],
    "Mercedes-Benz": ["C-Class", "E-Class", "GLC", "GLE", "G-Class", "Sprinter van"],
    "Audi": ["A4", "A6", "Q5", "Q7"],
    "Lexus": ["ES", "IS", "RX", "NX", "GX", "TX"],
    "Acura": ["Integra", "TLX", "RDX", "MDX"],
    "Cadillac": ["Escalade", "XT5", "Lyriq"],
    "Lincoln": ["Navigator", "Aviator", "Nautilus"],
    "Buick": ["Enclave", "Envista", "Encore GX"],
    "Infiniti": ["Q50", "QX60"],
    "Volvo": ["XC60", "XC90"],
    "Porsche": ["911", "Cayenne", "Macan"],
    "Mitsubishi": ["Outlander", "Mirage"],
    "Rivian": ["R1T", "R1S"],
}
# Work and construction vehicles, so they don't get forced into a car label.
GENERIC_VEHICLES = ["dump truck", "concrete mixer truck", "semi truck", "box truck",
                    "flatbed truck", "garbage truck", "tow truck", "school bus", "city bus",
                    "excavator", "wheel loader", "skid steer loader", "motorcycle"]

PROMPTS = ["a photo of a {}.", "a {} driving on the road.", "a low resolution photo of a {}."]

LABELS = [m if m.startswith(make) else f"{make} {m}"
          for make, models in MAKES_MODELS.items() for m in models] + GENERIC_VEHICLES
# ----------------------------------------------------------------------------


class LatestFrame:
    """Reads the stream on a background thread and keeps only the newest frame,
    so detection never lags behind live. Reconnects by itself if the stream drops."""

    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.frame, self.seq = None, 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                print(f"[stream] can't open {self.url}, retrying in 5 s")
                time.sleep(5)
                continue
            print("[stream] connected")
            while True:
                ok, frame = cap.read()
                if not ok:
                    print("[stream] dropped, reconnecting")
                    break
                with self.lock:
                    self.frame, self.seq = frame, self.seq + 1
            cap.release()
            time.sleep(2)

    def get(self):
        with self.lock:
            return self.seq, self.frame


class MakeModelClassifier:
    """Zero-shot make/model scoring with SigLIP 2."""

    def __init__(self, device):
        self.device = device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL, pretrained=CLIP_PRETRAINED, device=device)
        self.model.eval()
        tokenizer = open_clip.get_tokenizer(CLIP_MODEL)
        with torch.no_grad(), self._autocast():
            per_prompt = []
            for tmpl in PROMPTS:
                tokens = tokenizer([tmpl.format(label) for label in LABELS]).to(device)
                f = self.model.encode_text(tokens).float()
                per_prompt.append(f / f.norm(dim=-1, keepdim=True))
            text = torch.stack(per_prompt).mean(0)
            self.text = text / text.norm(dim=-1, keepdim=True)
        self.scale = self.model.logit_scale.exp().item()

    def _autocast(self):
        return torch.autocast("cuda", dtype=torch.float16, enabled=self.device == "cuda")

    @torch.no_grad()
    def score(self, crops_bgr):
        """Returns one probability row over LABELS per crop."""
        imgs = [self.preprocess(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)))
                for c in crops_bgr]
        x = torch.stack(imgs).to(self.device)
        with self._autocast():
            f = self.model.encode_image(x).float()
        f = f / f.norm(dim=-1, keepdim=True)
        return (self.scale * f @ self.text.T).softmax(dim=-1).cpu().numpy()


class Track:
    """Everything we know about one vehicle while it's in view."""

    def __init__(self, yolo_class, now):
        self.yolo_class = yolo_class
        self.first_seen = self.last_seen = now
        self.box = None
        self.prob_sum = np.zeros(len(LABELS))
        self.weight = 0.0
        self.n = 0
        self.best_area, self.best_crop = 0, None

    def add(self, probs, area, crop):
        self.prob_sum += probs * area  # closer, bigger views count more
        self.weight += area
        self.n += 1
        if area > self.best_area:
            self.best_area, self.best_crop = area, crop.copy()

    def top(self, k=3):
        if self.weight == 0:
            return []
        avg = self.prob_sum / self.weight
        return [(LABELS[i], float(avg[i])) for i in np.argsort(avg)[::-1][:k]]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def report(tid, t):
    """Print, log and save one finished vehicle."""
    if t.n < MIN_SAMPLES or t.best_crop is None:
        return
    top = t.top(3)
    name, p = top[0]
    stamp = datetime.fromtimestamp(t.first_seen)
    sure = p >= UNSURE_BELOW
    crop_file = f"{stamp:%Y%m%d_%H%M%S}_{tid}_{slug(name)}.jpg"
    cv2.imwrite(str(OUT_DIR / crop_file), t.best_crop)

    verdict = f"{name} ({p:.0%})" if sure else f"unsure, best guess {name} ({p:.0%})"
    others = "  ·  ".join(f"{n} {q:.0%}" for n, q in top[1:])
    print(f"{stamp:%H:%M:%S}  #{tid:<4} {t.yolo_class:<10} {verdict}   [{others}]")

    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "track_id", "yolo_class", "guess", "confidence", "sure",
                        "second_guess", "third_guess", "samples", "seconds_in_view", "crop_file"])
        w.writerow([stamp.isoformat(timespec="seconds"), tid, t.yolo_class, name, f"{p:.3f}",
                    sure, top[1][0] if len(top) > 1 else "", top[2][0] if len(top) > 2 else "",
                    t.n, round(t.last_seen - t.first_seen, 1), crop_file])


def draw(frame, tracks, now):
    for tid, t in tracks.items():
        if t.last_seen != now or t.box is None:  # only vehicles seen in this frame
            continue
        x1, y1, x2, y2 = t.box
        guess = t.top(1)
        label = f"#{tid} {guess[0][0]} {guess[0][1]:.0%}" if guess else f"#{tid} {t.yolo_class}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty = max(y1, th + 8)
        cv2.rectangle(frame, (x1, ty - th - 8), (x1 + tw + 6, ty), (0, 200, 255), -1)
        cv2.putText(frame, label, (x1 + 3, ty - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("warning: no CUDA GPU found, this will be slow")
    OUT_DIR.mkdir(exist_ok=True)

    print("loading YOLO...")
    yolo = YOLO(YOLO_WEIGHTS)
    print(f"loading {CLIP_MODEL} ({len(LABELS)} labels)...")
    clf = MakeModelClassifier(device)
    stream = LatestFrame(STREAM)
    if SHOW:
        cv2.namedWindow("car id", cv2.WINDOW_NORMAL)

    tracks = {}
    last_seq = 0
    print("watching for vehicles, press q in the window (or Ctrl+C) to quit\n")
    try:
        while True:
            seq, frame = stream.get()
            if frame is None or seq == last_seq:
                time.sleep(0.005)
                if SHOW and cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue
            last_seq = seq
            if ROTATE is not None:
                frame = cv2.rotate(frame, ROTATE)
            now = time.time()
            h, w = frame.shape[:2]

            r = yolo.track(frame, persist=True, tracker="bytetrack.yaml",  # fixed camera: no motion compensation needed
                           device=0 if device == "cuda" else "cpu", imgsz=YOLO_IMGSZ, classes=list(VEHICLE_CLASSES), verbose=False)[0]

            batch = []  # (track, area, crop)
            boxes = r.boxes
            if boxes is not None and boxes.id is not None:
                for (x1, y1, x2, y2), tid, cls in zip(boxes.xyxy.int().tolist(),
                                                      boxes.id.int().tolist(),
                                                      boxes.cls.int().tolist()):
                    t = tracks.get(tid)
                    if t is None:
                        t = tracks[tid] = Track(VEHICLE_CLASSES.get(cls, "vehicle"), now)
                    t.last_seen, t.box = now, (x1, y1, x2, y2)

                    bw, bh = x2 - x1, y2 - y1
                    area = bw * bh
                    if bw < MIN_CROP_PX or (t.n >= MAX_SAMPLES and area <= t.best_area):
                        continue
                    px, py = int(bw * 0.06), int(bh * 0.06)  # a little context around the box
                    crop = frame[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
                    if crop.size:
                        batch.append((t, area, crop))

            if batch:
                probs = clf.score([c for _, _, c in batch])
                for (t, area, crop), p in zip(batch, probs):
                    t.add(p, area, crop)

            for tid in [tid for tid, t in tracks.items() if now - t.last_seen > LOST_AFTER_S]:
                report(tid, tracks.pop(tid))

            if SHOW:
                draw(frame, tracks, now)
                cv2.imshow("car id", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        for tid in list(tracks):
            report(tid, tracks.pop(tid))
        cv2.destroyAllWindows()
        print(f"\nlog: {CSV_PATH}\ncrops: {OUT_DIR}")


if __name__ == "__main__":
    main()