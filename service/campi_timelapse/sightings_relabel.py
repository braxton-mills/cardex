"""One-off relabelling after sightings_labels.txt changes (dry run unless apply=True; both back up the DBs first).

- normalize (stdlib): Gemini-decided rows, discovered_labels and your label corrections (ui.db) are mapped onto the
  list's naming through Labels.resolve()/canonical() ("Mazda 3" -> "Mazda3", "unknown dump truck" -> "dump truck").
- rescore (sightings venv, CUDA): SigLIP-only rows from the last N days are scored again on their saved crop against
  the current list. Only one crop is saved (the live pass averages up to 5), so a row changes only when the new
  top-1 is a label the old list didn't have; unsure or close ones are queued for Gemini (up to cloud_budget).
"""
from __future__ import annotations

import csv
import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import sightings_db as db
from .config import read_json
from .labels import Labels, labels_path

log = logging.getLogger("relabel")


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dst = path.with_name(f"{path.stem}.before-relabel-{time.strftime('%Y-%m-%d-%H%M%S')}{path.suffix}")
    src, out = sqlite3.connect(path), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def _ui_db(cfg) -> Path:
    return cfg.paths.data / "ui" / "ui.db"


def _report(title: str, changes: Counter, limit: int = 60) -> None:
    print(f"{title}: {sum(changes.values())} rows, {len(changes)} mappings")
    for (old, new), n in changes.most_common(limit):
        print(f"  {n:5d}  {old}  ->  {new}")


def normalize(cfg, apply: bool = False) -> dict:
    labels = Labels(labels_path(cfg))
    if apply:
        for p in (db.db_path(cfg.paths.sightings), _ui_db(cfg)):
            print(f"backup: {_backup(p)}")
    con = db.connect(cfg.paths.sightings)
    rows, disc, over = Counter(), Counter(), Counter()
    now = db.utc_iso(time.time())
    try:
        updates = []
        for r in con.execute("SELECT id, label, make, model, siglip_label FROM sightings WHERE source = 'cloud'"):
            # a generic row has no make/model, only its label
            res = labels.resolve(r["make"], r["model"] or r["label"], r["siglip_label"])
            if res and (res[0], res[1], res[2]) != (r["label"], r["make"], r["model"]):
                rows[(r["label"], res[0])] += 1
                updates.append((*res, now, r["id"]))
        merged: dict[str, list] = {}
        for r in con.execute("SELECT label, make, model, first_seen, count FROM discovered_labels"):
            new, mk, md = labels.canonical(r["make"], r["model"] or r["label"])
            if new != r["label"]:
                disc[(r["label"], "(in the list)" if labels.listed(new) else new)] += 1
            if new and not labels.listed(new):  # still undiscovered: keep it under one spelling
                m = merged.setdefault(new.lower(), [new, mk, md, r["first_seen"], 0])
                m[3], m[4] = min(m[3], r["first_seen"]), m[4] + r["count"]
        # A discovered label no row carries any more (a chassis brand now filed under SigLIP's "dump truck") goes.
        final = {r["id"]: r["label"] for r in con.execute("SELECT id, label FROM sightings WHERE source = 'cloud'")}
        final.update({u[-1]: u[0] for u in updates})
        carried = {(lb or "").lower() for lb in final.values()}
        for k in [k for k in merged if k not in carried]:
            disc[(merged[k][0], "(no sightings left)")] += 1
            del merged[k]
        if apply:
            con.execute("BEGIN IMMEDIATE")
            con.executemany("UPDATE sightings SET label = ?, make = ?, model = ?, updated_at = ? WHERE id = ?", updates)
            con.execute("DELETE FROM discovered_labels")
            con.executemany("INSERT INTO discovered_labels (label, make, model, first_seen, count) VALUES (?, ?, ?, ?, ?)",
                            list(merged.values()))
            con.commit()
    finally:
        con.close()
    ui = _ui_db(cfg)
    if ui.exists():
        ucon = sqlite3.connect(ui, timeout=10)
        try:
            fixes = []
            for sid, label, mk, md in ucon.execute("SELECT sighting_id, label, make, model FROM label_overrides"):
                new = labels.canonical(mk, md or label)
                if labels.listed(new[0]) and new[0] != label:
                    over[(label, new[0])] += 1
                    fixes.append((*new, sid))
            if apply and fixes:
                ucon.executemany("UPDATE label_overrides SET label = ?, make = ?, model = ? WHERE sighting_id = ?", fixes)
                ucon.commit()
        finally:
            ucon.close()
    _report("Gemini rows", rows)
    _report("discovered labels", disc)
    _report("your corrections", over)
    print("applied" if apply else "dry run (--apply writes)")
    return {"rows": sum(rows.values()), "discovered": sum(disc.values()), "overrides": sum(over.values())}


def _gaming(cfg) -> bool:
    st = read_json(cfg.paths.state / "status.json", {}) or {}
    return bool(st.get("sightings_paused")) and time.time() - st.get("updated", 0) < 60


def rescore(cfg, days: float, old_labels: Path, apply: bool = False, cloud_budget: int = 300,
            until: float | None = None) -> dict:
    import cv2

    from .sightings import CPU_THREADS, MakeModelClassifier
    import torch
    torch.set_num_threads(CPU_THREADS)
    s = cfg.sightings
    labels = Labels(labels_path(cfg))
    old = {n.lower() for n in Labels(old_labels).names}
    added = sum(n.lower() not in old for n in labels.names)
    print(f"{len(labels.names)} labels, {added} not in {old_labels.name}")
    if apply:
        print(f"backup: {_backup(db.db_path(cfg.paths.sightings))}")
    overridden = set()
    if _ui_db(cfg).exists():
        u = sqlite3.connect(f"file:{_ui_db(cfg).as_posix()}?mode=ro", uri=True)
        overridden = {r[0] for r in u.execute("SELECT sighting_id FROM label_overrides")}
        u.close()
    con = db.connect(cfg.paths.sightings)
    lo = db.utc_iso(time.time() - days * 86400)
    # Rows after `until` (default: when the list changed) were scored live against this list already.
    hi = db.utc_iso(until or labels_path(cfg).stat().st_mtime)
    todo = [r for r in con.execute(
        "SELECT id, label, crop_path FROM sightings WHERE source = 'siglip' AND started_at >= ? AND started_at < ? "
        "AND crop_path IS NOT NULL AND COALESCE(cloud_status, '') != 'pending' ORDER BY started_at", (lo, hi))
        if r["id"] not in overridden]
    print(f"{len(todo)} SigLIP-only rows since {db.local_str(lo)}")
    clf = MakeModelClassifier(s.clip_model, labels, "cuda")
    changes, queued, t0 = Counter(), 0, time.perf_counter()
    report = cfg.paths.sightings / f"rescore-{time.strftime('%Y-%m-%d-%H%M%S')}{'' if apply else '-dry'}.csv"
    rep = report.open("w", encoding="utf-8", newline="")
    out = csv.writer(rep)
    out.writerow(["id", "crop_path", "old", "new", "p", "p2", "queued"])
    for i in range(0, len(todo), 32):
        while _gaming(cfg):
            log.info("gaming: rescore paused")
            time.sleep(30)
        batch = [(r, cv2.imread(str(cfg.paths.sightings / r["crop_path"]), cv2.IMREAD_COLOR)) for r in todo[i:i + 32]]
        batch = [(r, im) for r, im in batch if im is not None]
        if not batch:
            continue
        probs = clf.score([im for _, im in batch])
        now = db.utc_iso(time.time())
        for (r, _), pr in zip(batch, probs):
            top = pr.argsort()[::-1][:3]
            best, p, p2 = int(top[0]), float(pr[top[0]]), float(pr[top[1]])
            new = labels.names[best]
            if new.lower() in old or new == r["label"]:
                continue
            changes[(r["label"], new)] += 1
            ask = (p < s.unsure_below or p - p2 < s.cloud.cloud_margin) and queued < cloud_budget
            out.writerow([r["id"], r["crop_path"], r["label"], new, f"{p:.4f}", f"{p2:.4f}", int(ask)])
            if apply:
                con.execute(
                    "UPDATE sightings SET label = ?, make = ?, model = ?, confidence = ?, runner_ups = ?, unsure = ?, "
                    "siglip_label = ?, siglip_confidence = ?, updated_at = ?"
                    + (", cloud_status = 'pending'" if ask else "") + " WHERE id = ?",
                    (new, labels.make[best], labels.model[best], round(p, 4),
                     json.dumps([{"label": labels.names[int(j)], "p": round(float(pr[j]), 4)} for j in top[1:]]),
                     int(p < s.unsure_below), new, round(p, 4), now, r["id"]))
                con.commit()
                if ask:  # re-open an old (e.g. capped) job too; enqueue_cloud would ignore it
                    con.execute("INSERT INTO cloud_queue (sighting_id, created_at, next_try_at) VALUES (?, ?, ?) "
                                "ON CONFLICT(sighting_id) DO UPDATE SET done_at = NULL, last_error = NULL, attempts = 0, "
                                "next_try_at = excluded.next_try_at", (r["id"], now, now))
                    con.commit()
            queued += ask
        if (i // 32) % 20 == 0:
            print(f"  {min(i + 32, len(todo))}/{len(todo)}  changed {sum(changes.values())}", flush=True)
    con.close()
    rep.close()
    print(f"{time.perf_counter() - t0:.0f} s; every change: {report}")
    _report("rescored", changes)
    print(f"{queued} queued for Gemini; " + ("applied" if apply else "dry run (--apply writes)"))
    return {"rows": len(todo), "changed": sum(changes.values()), "queued": queued}
