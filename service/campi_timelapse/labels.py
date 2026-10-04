"""sightings_labels.txt parser (stdlib only, so the UI can read the label list without the sightings venv)."""
from __future__ import annotations

from pathlib import Path

from .config import PROJECT


class Labels:
    """sightings_labels.txt: 'Make | Model' or a plain generic vehicle type per line; '#' comments."""

    def __init__(self, path: Path):
        self.path = path
        self.names, self.make, self.model = [], [], []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "|" in line:
                mk, md = (s.strip() for s in line.split("|", 1))
                self.names.append(md if md.startswith(mk) else f"{mk} {md}")  # car_id's rule: "Mazda3", "Ram 1500"
                self.make.append(mk)
                self.model.append(md)
            else:
                self.names.append(line)
                self.make.append(None)
                self.model.append(None)
        if not self.names:
            raise ValueError(f"no labels in {path}")


def labels_path(cfg) -> Path:
    """[sightings] labels_file; a relative path is relative to the project folder."""
    lp = Path(cfg.sightings.labels_file)
    return lp if lp.is_absolute() else PROJECT / lp
