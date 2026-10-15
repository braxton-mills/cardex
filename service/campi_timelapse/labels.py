"""sightings_labels.txt parser (stdlib only, so the UI can read the label list without the sightings venv)."""
from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT

# Words Gemini puts where it has no make ("unknown dump truck", "N/A utility trailer", "garbage truck unknown").
NO_MAKE = {"unknown", "n/a", "na", "none", "generic", "unbranded"}


def _key(s: str) -> str:
    """Match key: case, spaces, hyphens and dots don't matter ("Mazda 3" = "Mazda3", "ID. Buzz" = "ID Buzz")."""
    return re.sub(r"[\s\-.]+", "", s).lower()


class Labels:
    """sightings_labels.txt: 'Make | Model' or a plain generic vehicle type per line; '#' comments.

    Optional aliases after '=' (comma-separated) are other spellings of that label. They are never shown to SigLIP;
    canonical() maps them (e.g. Gemini's answers) onto the label."""

    def __init__(self, path: Path):
        self.path = path
        self.names, self.make, self.model = [], [], []
        self.aliases: dict[str, int] = {}  # _key(name or alias) -> index into names
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            line, _, alias_text = line.partition("=")
            line = line.strip()
            if "|" in line:
                mk, md = (s.strip() for s in line.split("|", 1))
                self.names.append(md if md.startswith(mk) else f"{mk} {md}")  # car_id's rule: "Mazda3", "Ram 1500"
                self.make.append(mk)
                self.model.append(md)
            else:
                self.names.append(line)
                self.make.append(None)
                self.model.append(None)
            i = len(self.names) - 1
            mk = self.make[i]
            for spelling in [self.names[i], *(a.strip() for a in alias_text.split(","))]:
                if not spelling:
                    continue
                if mk and not spelling.lower().startswith(mk.lower()):
                    spelling = f"{mk} {spelling}"  # a model alias belongs to its make: "GL-Class" -> "Mercedes-Benz GL-Class"
                k = _key(spelling)
                if k in self.aliases and self.aliases[k] != i:
                    raise ValueError(f"{path.name}: '{spelling}' is listed twice "
                                     f"({self.names[self.aliases[k]]} and {self.names[i]})")
                self.aliases[k] = i
        if not self.names:
            raise ValueError(f"no labels in {path}")
        self.generic = [n for n, m in zip(self.names, self.make) if m is None]

    def entry(self, i: int) -> tuple[str, str | None, str | None]:
        return self.names[i], self.make[i], self.model[i]

    def canonical(self, make: str | None, model: str | None) -> tuple[str, str | None, str | None]:
        """(label, make, model) for an answer in someone else's naming (Gemini's). A listed name or alias -> that
        label; a make-less or branded generic vehicle ("unknown dump truck", "Komatsu excavator") -> the generic
        class; anything else is returned as given (a label that isn't in the list)."""
        make, model = (make or "").strip(), (model or "").strip()
        if make.lower() in NO_MAKE:
            make = ""
        words = model.split()
        while words and words[0].lower() in NO_MAKE:
            words.pop(0)
        while words and words[-1].lower() in NO_MAKE:
            words.pop()
        model = " ".join(words)
        label = model if not make or model.lower().startswith(make.lower()) else f"{make} {model}"
        i = self.aliases.get(_key(label)) if label else None
        if i is not None:
            return self.entry(i)
        low = f" {label.lower()}"
        for g in sorted(self.generic, key=len, reverse=True):  # "Caterpillar wheel loader" -> "wheel loader"
            if low.endswith(f" {g.lower()}"):
                return self.entry(self.names.index(g))
        return label, make or None, model or None

    def listed(self, label: str | None) -> bool:
        return bool(label) and _key(label) in self.aliases

    def resolve(self, make: str | None, model: str | None,
                siglip_label: str | None = None) -> tuple[str, str | None, str | None] | None:
        """canonical(), then: an unlisted answer from a brand with no cars in the list (or no make at all) defers to
        SigLIP when SigLIP called it a generic vehicle (Gemini named the chassis, "Mack Granite", SigLIP the job,
        "dump truck"). None when the answer has no make and isn't a listed label ("unknown sedan")."""
        label, mk, md = self.canonical(make, model)
        if self.listed(label):
            return label, mk, md
        if not label:
            return None
        car_makes = {m.lower() for m in self.make if m}
        if (not mk or mk.lower() not in car_makes) and self.listed(siglip_label):
            i = self.aliases[_key(siglip_label)]
            if self.make[i] is None:
                return self.entry(i)
        if not mk or not md:
            return None
        return label, mk, md


def labels_path(cfg) -> Path:
    """[sightings] labels_file; a relative path is relative to the project folder."""
    lp = Path(cfg.sightings.labels_file)
    return lp if lp.is_absolute() else PROJECT / lp
