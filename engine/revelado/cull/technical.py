# SPDX-License-Identifier: GPL-3.0-or-later
"""Technical auto-cull (local, free, always available) — stage 0 of the culling pipeline.

Per photo, on a 2048-px oriented preview (display-transformed when the capture is S-Log, so exposure is judged the
way a person would see it):
  * faces + eyes: ``revelado.pipeline.faces`` - macOS Vision helper when available (face boxes, eye landmarks,
    people boxes); otherwise OpenCV YuNet (cross-platform, no eye contours); otherwise Haar; else none (reported).
    Eyes: eye aspect ratio (height / width of the eye contour) < 0.17 -> closed. Only measured on faces >= 90 px.
  * sharpness = variance of the Laplacian of the grey image (σ=0.6 pre-blur) inside a region, divided by
    (local grey variance + 1e-3)^0.5 * 100 so low-contrast regions aren't penalised for being flat.
    Measured on: the largest face (``sharpness_face``), the AF point from the maker notes (``sharpness_af``,
    Sony FocusLocation mapped through the orientation) and the whole frame (``sharpness_global``).
  * exposure: mean luma, % of pixels clipped high (any channel >= 250) and low (all <= 4).
Bursts: consecutive frames less than 2.5 s apart (capture time). Score 0-100 =
  55 % sharpness percentile in the session (face, else AF, else global) + 20 % exposure + 15 % eyes + 10 % subject.
Verdict: reject when sharpness is clearly worse than the burst/session (< 35 % of the session median), eyes closed on
a large face, or exposure unusable; keep when it is the best of its burst and not rejected, or score >= 70; else maybe.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

from ..imaging import ImageStore
from ..jobs import Job, run_item
from ..log import get_logger

log = get_logger("cull.technical")
BURST_GAP_S = 2.5
LONG = 2048


class FaceDetector:
    """Thin wrapper over revelado.pipeline.faces (Vision -> YuNet -> Haar)."""

    def __init__(self, legacy_dir: Path | None = None):     # legacy_dir kept for signature compatibility
        from ..pipeline import assets, faces as fm
        self.fm = fm
        order = fm.backend_order()
        if "vision" in order and assets.macos_helper("facevision") is not None:
            self.backend = "vision"
        elif "yunet" in order and assets.model_path("yunet") is not None:
            self.backend = "yunet"
        elif assets.model_path("haar_frontal_default") is not None:
            self.backend = "haar"
        else:
            self.backend = "none"

    def detect(self, rgb: np.ndarray) -> dict:
        H, W = rgb.shape[:2]
        if self.backend == "vision":
            d = self.fm.vision_people(rgb)
            if d is not None:
                faces = []
                for box, lm in zip(d["faces"], d["lm"]):
                    eyes = []
                    for k in ("leftEye", "rightEye"):
                        a = (lm or {}).get(k)
                        if a is not None and len(a) >= 4:
                            ew = float(a[:, 0].max() - a[:, 0].min())
                            eh = float(a[:, 1].max() - a[:, 1].min())
                            eyes.append(eh / ew if ew > 1 else None)
                    faces.append({"box": list(box), "conf": None, "ear": eyes})
                return {"faces": faces, "humans": [list(h) for h in d["humans"]], "backend": "vision"}
            log.warning("vision helper failed, falling back")
        if self.backend in ("vision", "yunet"):
            b = self.fm.yunet_faces(rgb)
            if b is not None:
                return {"faces": [{"box": list(x), "conf": None, "ear": []} for x in b], "humans": [], "backend": "yunet"}
        if self.backend != "none":
            from ..pipeline import assets
            c = cv2.CascadeClassifier(str(assets.model_path("haar_frontal_default")))
            g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            s = min(1.0, 1400 / max(H, W))
            gs = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else g
            rects = c.detectMultiScale(gs, 1.1, 6, minSize=(24, 24))
            return {"faces": [{"box": [x / s, y / s, w / s, h / s], "conf": None, "ear": []} for x, y, w, h in rects],
                    "humans": [], "backend": "haar"}
        return {"faces": [], "humans": [], "backend": "none"}


def _sharp(gray: np.ndarray, box: list[float] | None) -> float | None:
    H, W = gray.shape
    if box is not None:
        x, y, w, h = [int(round(v)) for v in box]
        x, y = max(0, x), max(0, y)
        w, h = min(w, W - x), min(h, H - y)
        if w < 16 or h < 16:
            return None
        g = gray[y:y + h, x:x + w]
    else:
        g = gray
    g = cv2.GaussianBlur(g, (0, 0), 0.6)
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    return float(lap.var() / np.sqrt(g.var() + 1e-3) * 100)


def _af_box(meta: dict, W: int, H: int) -> list[float] | None:
    f = meta.get("focus")
    if not f:
        return None
    fw, fh, fx, fy = f["frame_w"], f["frame_h"], f["x"], f["y"]
    bw, bh = f.get("box_w") or fw * 0.12, f.get("box_h") or fh * 0.12
    o = int(meta.get("orientation") or 1)
    # sensor (unrotated) -> oriented normalised coordinates
    u, v = fx / fw, fy / fh
    nbw, nbh = bw / fw, bh / fh
    if o == 6:
        u, v, nbw, nbh = 1 - v, u, nbh, nbw
    elif o == 8:
        u, v, nbw, nbh = v, 1 - u, nbh, nbw
    elif o == 3:
        u, v = 1 - u, 1 - v
    return [(u - nbw / 2) * W, (v - nbh / 2) * H, nbw * W, nbh * H]


def analyse_photo(store: ImageStore, det: FaceDetector, photo: dict) -> dict:
    im, mode = store.reduced(photo, LONG, "auto")
    rgb = np.asarray(im)
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    people = det.detect(rgb)
    faces = sorted(people["faces"], key=lambda f: f["box"][2] * f["box"][3], reverse=True)
    sf = _sharp(gray, faces[0]["box"]) if faces else None
    afb = _af_box(photo.get("meta") or {}, W, H)
    saf = _sharp(gray, afb) if afb else None
    sg = _sharp(gray, None)
    # eyes on large faces only
    eyes_open = None
    closed_faces = 0
    measured = 0
    for f in faces[:4]:
        if f["box"][3] < 90:
            continue
        ears = [e for e in f.get("ear") or [] if e is not None]
        if not ears:
            continue
        measured += 1
        if max(ears) < 0.17:
            closed_faces += 1
    if measured:
        eyes_open = closed_faces == 0
    luma = rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    clip_hi = float((rgb.max(axis=2) >= 250).mean() * 100)
    clip_lo = float((rgb.max(axis=2) <= 4).mean() * 100)
    mean = float(luma.mean())
    if clip_hi > 4 or mean > 200:
        ev = "sobreexpuesta"
    elif mean < 45 or clip_lo > 15:
        ev = "subexpuesta"
    else:
        ev = "correcta"
    return {
        "sharpness_face": None if sf is None else round(sf, 2),
        "sharpness_af": None if saf is None else round(saf, 2),
        "sharpness_global": round(sg, 2),
        "faces": len(faces), "people": len(people["humans"]),
        "face_boxes": [[round(v / W, 4) if i % 2 == 0 else round(v / H, 4) for i, v in enumerate(f["box"])]
                       for f in faces[:6]],
        "eyes_open": eyes_open, "eyes_measured_faces": measured, "eyes_closed_faces": closed_faces,
        "exposure": {"mean": round(mean, 1), "clip_hi_pct": round(clip_hi, 2), "clip_lo_pct": round(clip_lo, 2),
                     "verdict": ev},
        "detector": people["backend"], "display_transform": mode, "analysed_px": [W, H],
    }


def assign_bursts(rows: list[dict]) -> None:
    rows.sort(key=lambda r: (r["capture_ts"] or 0, r["name"]))
    bid, last = 0, None
    for r in rows:
        ts = r["capture_ts"]
        if last is None or ts is None or ts - last > BURST_GAP_S:
            bid += 1
        r["burst_id"] = f"b{bid:04d}"
        last = ts


def score_all(rows: list[dict]) -> None:
    """rows carry 'tech' (analyse_photo output) and 'burst_id'. Adds score/verdict/reasons/burst fields."""
    def key_sharp(t):
        return t["sharpness_face"] if t["sharpness_face"] is not None else (
            t["sharpness_af"] if t["sharpness_af"] is not None else t["sharpness_global"])
    vals = np.array([key_sharp(r["tech"]) for r in rows if r.get("tech")], np.float64)
    med = float(np.median(vals)) if len(vals) else 1.0
    for r in rows:
        t = r.get("tech")
        if not t:
            continue
        s = key_sharp(t)
        pct = float((vals < s).mean()) if len(vals) > 1 else 0.5
        expo = {"correcta": 1.0, "sobreexpuesta": 0.4, "subexpuesta": 0.5}[t["exposure"]["verdict"]]
        eyes = 1.0 if t["eyes_open"] is not False else 0.0
        subj = 1.0 if (t["faces"] or t["people"]) else 0.5
        t["sharpness_rel"] = round(s / med, 3) if med > 0 else None
        t["score"] = round(100 * (0.55 * pct + 0.20 * expo + 0.15 * eyes + 0.10 * subj), 1)
    bursts: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("tech"):
            bursts.setdefault(r["burst_id"], []).append(r)
    for bid, members in bursts.items():
        best = max(members, key=lambda r: r["tech"]["score"])
        for r in members:
            t = r["tech"]
            t["burst_id"], t["burst_size"], t["burst_best"] = bid, len(members), r is best
            reasons = []
            reject = False
            if t["sharpness_rel"] is not None and t["sharpness_rel"] < 0.35:
                reasons.append("enfoque claramente peor que el resto de la sesión")
                reject = True
            if t["eyes_open"] is False:
                reasons.append("ojos posiblemente cerrados")
                reject = True
            if t["exposure"]["verdict"] != "correcta":
                reasons.append(t["exposure"]["verdict"])
                if t["exposure"]["clip_hi_pct"] > 15 or t["exposure"]["mean"] < 25:
                    reject = True
            if len(members) > 1 and r is best:
                reasons.append(f"mejor de su ráfaga ({len(members)} fotos)")
            elif len(members) > 1:
                reasons.append(f"ráfaga de {len(members)}: hay una más nítida")
            t["reasons"] = reasons
            t["verdict"] = "reject" if reject else "keep" if (r is best or t["score"] >= 70) else "maybe"


def run(job: Job, ctx, session_id: str, photo_ids: list[str] | None) -> dict:
    cat, store = ctx.catalog, ctx.images
    det = FaceDetector(ctx.settings.legacy_dir)
    job.say(f"detector de caras: {det.backend}" + ("" if det.backend != "none" else
                                                   " (no disponible: sin métricas de caras/ojos)"))
    rows = [dict(r) | {"meta": json.loads(r["meta_json"] or "{}")} for r in cat.photos(session_id)]
    if photo_ids:
        want = set(photo_ids)
        rows = [r for r in rows if r["id"] in want]
    job.items_total = len(rows)
    t0 = time.time()
    for i, r in enumerate(rows):
        job.check_cancel()
        r["tech"] = run_item(job, r["id"], lambda r=r: analyse_photo(store, det, r))
        if r["tech"] is not None:
            job.items_done += 1
        el = time.time() - t0
        job.update((i + 1) / max(len(rows), 1) * 0.95, f"analizando {i + 1}/{len(rows)}",
                   eta_s=el / (i + 1) * (len(rows) - i - 1), photo_id=r["id"])
    assign_bursts(rows)
    score_all(rows)
    counts = {"keep": 0, "maybe": 0, "reject": 0}
    for r in rows:
        t = r.get("tech")
        if not t:
            continue
        t["analysed_at"] = time.time()
        cat.set_cull(r["id"], technical=t, burst_id=r["burst_id"])
        counts[t["verdict"]] += 1
        ctx.bus.emit({"type": "photo_updated", "photo_id": r["id"], "cull": {"technical": t}})
    job.result = {"counts": counts, "bursts": len({r["burst_id"] for r in rows}), "detector": det.backend,
                  "seconds": round(time.time() - t0, 1)}
    job.say(f"cull técnico: {counts['keep']} keep, {counts['maybe']} maybe, {counts['reject']} reject; "
            f"{job.result['bursts']} ráfagas")
    return job.result
