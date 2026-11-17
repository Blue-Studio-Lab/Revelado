# SPDX-License-Identifier: GPL-3.0-or-later
"""Child process: develops ONE photo with the bundled pipeline (no private files needed).

Run as ``python -m revelado.worker <params.json>``; it is always started by ``revelado.runner`` in its own process
(memory isolation: when it exits, every full-resolution buffer is gone).

Chain (RAW source): exposure analysis -> RAW develop + clean (RawTherapee + Python NR / chroma NR / defringe /
distortion) -> optional hair clean-up (EXPERIMENTAL, off by default, safety gate) -> film render (Ektar) -> EXIF
from the RAW (no GPS) -> optional reframing (JPG, web AND TIFF16 get the same rotation + crop) -> atomic delivery.

JPG source (only when the recipe explicitly sets ``develop_source="jpg"``): the camera JPEG is linearised
(sRGB -> linear Rec.2020) and used as the base; no RAW develop / NR stage. Lower quality by nature: it is a
convenience mode, never chosen silently.

Protocol on stdout: lines ``@@P {json}`` (progress: stage, progress 0-1) and one final ``@@R {json}`` (result).
Everything else is free-form log text.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

STAGES = [("exposure", 0.0, 0.06), ("develop", 0.06, 0.50), ("hair", 0.50, 0.60), ("render", 0.60, 0.90),
          ("reframe", 0.90, 0.96), ("finalize", 0.96, 1.0)]
_SPAN = {n: (a, b) for n, a, b in STAGES}


def P(stage: str, frac: float = 0.0, msg: str | None = None) -> None:
    a, b = _SPAN[stage]
    d = {"stage": stage, "progress": round(a + (b - a) * frac, 4)}
    if msg:
        d["msg"] = msg
    print("@@P " + json.dumps(d, ensure_ascii=False), flush=True)


def peak_mb() -> dict:
    try:
        import resource
    except ImportError:                                # Windows
        import psutil
        return {"python": round(psutil.Process().memory_info().peak_wset / 1e6), "children": None}
    s = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    c = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    k = 1e6 if sys.platform == "darwin" else 1e3       # macOS reports bytes, Linux KiB
    return {"python": round(s / k), "children": round(c / k)}


def exif_copy(src: Path | None, dst: Path, extra: list[str] | None = None) -> str | None:
    import subprocess
    from .tools import find_tool
    exiftool = find_tool("exiftool")
    if not exiftool or not src:
        return "exiftool no disponible: EXIF no copiado"
    r = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-tagsFromFile", str(src), "-all:all",
                        "-Orientation=1", "-n", "-IFD1:all=", *(extra or []), str(dst)], capture_output=True, text=True)
    r2 = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-GPS:all=", str(dst)], capture_output=True,
                        text=True)
    if r.returncode != 0 or r2.returncode != 0:
        return (r.stderr + r2.stderr)[-300:]
    return None


def jpg_to_base(jpg: Path, out_tif: Path) -> dict:
    """Camera JPEG -> linear Rec.2020 half-float base (orientation applied). Returns info."""
    import numpy as np
    import tifffile
    from PIL import Image, ImageOps
    from .pipeline.colorspaces import SRGB_TO_XYZ, XYZ_TO_REC2020, srgb_decode
    with Image.open(jpg) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        a = np.asarray(im, dtype=np.float32) / 255.0
    lin = srgb_decode(a)
    M = (XYZ_TO_REC2020 @ SRGB_TO_XYZ).astype(np.float32)
    for y in range(0, lin.shape[0], 512):
        lin[y:y + 512] = lin[y:y + 512] @ M.T
    np.maximum(lin, 0, out=lin)
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(out_tif), lin.astype(np.float16), photometric="rgb",
                     description=json.dumps({"source": "camera-jpeg", "encoding": "linear Rec.2020"}))
    return {"shape": list(lin.shape), "source": "camera-jpeg"}


def main(params_path: str) -> int:
    prm = json.loads(Path(params_path).read_text())
    t_all = time.time()
    src_kind = prm.get("source", "raw")
    raw = Path(prm["raw_path"]) if prm.get("raw_path") else None
    jpg = Path(prm["jpg_path"]) if prm.get("jpg_path") else None
    src = raw if src_kind == "raw" else jpg
    stem = prm.get("stem") or src.stem
    work = Path(prm["work_dir"])
    out_dir = Path(prm["out_dir"])
    ep = prm["engine_params"]
    side: dict = {"engine": "revelado.pipeline (native)", "source": src_kind, "recipe": prm.get("recipe"),
                  "engine_params": ep, "stages": {}, "times": {}, "pending_manual": [], "notes": []}
    if work.exists():
        shutil.rmtree(work)
    (work / "render").mkdir(parents=True)

    from .pipeline import raw as rawdev
    from .pipeline.film import ektar

    # grain seed: from the SOURCE file content (deterministic: same file + same recipe -> same pixels)
    seed = int(prm["seed"]) if prm.get("seed") is not None else ektar.seed_from_file(src)
    side["seed"] = seed

    # 1. exposure
    t = time.time()
    if src_kind == "raw":
        P("exposure", 0.0, "análisis de exposición")
        a = rawdev.analyze_exposure(raw)
        ev = float(a["ev"])
        side["stages"]["exposure"] = {k: v for k, v in a.items() if isinstance(v, (int, float, str, bool, type(None)))}
    else:
        ev = None
        side["stages"]["exposure"] = {"note": "JPG: exposición automática del render (sin RAW)"}
    side["ev_auto"] = ev
    side["times"]["exposure"] = round(time.time() - t, 2)
    P("exposure", 1.0, f"EV automático {ev:+.2f}" if ev is not None else None)

    # 2. develop + clean
    t = time.time()
    base0 = work / "base" / f"{stem}.tif"
    if src_kind == "raw":
        P("develop", 0.02, "revelado RAW + limpieza (RawTherapee)")
        r = rawdev.develop(raw, base0, ev=ev, exposure_json=None, tmp_dir=work / "_tmp_rt",
                           clean=ep.get("nr", "auto") != "off", nr_mode=ep.get("nr", "auto"),
                           defringe=ep.get("ca", "auto") != "off", raw_ca=ep.get("ca", "auto") != "off")
        side["stages"]["develop"] = {k: r[k] for k in ("seconds_total", "seconds_rt", "nr", "clean", "lens", "stats")
                                     if k in r}
    else:
        P("develop", 0.02, "base lineal desde el JPG de cámara")
        side["stages"]["develop"] = jpg_to_base(jpg, base0)
        side["notes"].append("revelado desde JPG: sin RAW no hay reducción de ruido, CA ni rango extendido")
    side["times"]["develop"] = round(time.time() - t, 2)
    P("develop", 1.0)
    if prm.get("stop_after") == "develop":           # benchmarks: keep the neutral linear base, stop here
        out_dir.mkdir(parents=True, exist_ok=True)
        os.replace(base0, out_dir / "base.tif")
        side["times"]["total"] = round(time.time() - t_all, 2)
        side["peak_rss_mb"] = peak_mb()
        (out_dir / "sidecar.json").write_text(json.dumps(side, indent=1, ensure_ascii=False, default=str))
        shutil.rmtree(work, ignore_errors=True)
        print("@@R " + json.dumps({"ok": True, "times": side["times"], "peak_rss_mb": side["peak_rss_mb"],
                                   "ev": ev}), flush=True)
        return 0

    # 3. hair (experimental, optional, gated)
    base_render = base0
    hair = ep.get("hair", "off")
    t = time.time()
    if hair != "off":
        P("hair", 0.05, f"cabello ({hair}, experimental) con puerta de seguridad")
        try:
            import numpy as np
            import tifffile
            from .pipeline import hair as hairmod
            img = rawdev.load_base(base0)
            out, info = hairmod.clean_gated(img, strength=hair, ev=ev)
            if out is not None:
                meta = rawdev.load_base_meta(base0)
                meta["hair"] = {"version": info["version"], "strength": hair, "status": "applied"}
                base_render = work / "hair" / f"{stem}.tif"
                base_render.parent.mkdir(parents=True, exist_ok=True)
                tifffile.imwrite(str(base_render), out.astype(np.float16), photometric="rgb",
                                 description=json.dumps(meta))
            if info.get("pending_manual"):
                side["pending_manual"].append("cabello_pendiente_manual")
            del img, out
        except Exception as e:  # noqa: BLE001 - hair is optional, never blocks delivery
            info = {"applied": False, "error": repr(e), "traceback": traceback.format_exc()[-1200:]}
            side["pending_manual"].append("cabello_pendiente_manual")
        side["stages"]["hair"] = info
    else:
        side["stages"]["hair"] = {"applied": False, "reason": "desactivado en la receta"}
    side["times"]["hair"] = round(time.time() - t, 2)
    P("hair", 1.0)

    # 4. film render
    film = ep.get("film", "none")
    t = time.time()
    full = work / "render" / f"{stem}.jpg"
    want_tiff = bool(prm.get("tiff16"))
    if film == "none":
        from .pipeline.film import neutral
        P("render", 0.02, "render neutro (sin película)")
        renderer = neutral.render
    else:
        P("render", 0.02, "película Kodak Ektar 100 (motor físico)")
        renderer = ektar.render
    rr = renderer(str(base_render), str(full), ev=ev, ev_offset=ep["ev_offset"], strength=ep["strength"],
                      grain=ep["grain"], skin=ep["skin"], sharp=ep["sharp"], subject=ep["subject"], blem=ep["blem"],
                      web=True, save_tiff16=want_tiff, seed=seed, web_long_side=int(prm.get("web_long_side", 2560)))
    web = work / "render" / "web2560" / f"{stem}.jpg"
    tif = full.with_suffix(".tif") if want_tiff else None
    side["stages"]["render"] = {k: rr.get(k) for k in ("ev", "ev_guard", "p998_scene", "clip_any_pct", "mean_okC",
                                                       "luma_p0_5", "luma_p99_9", "size", "seconds", "timings",
                                                       "seed")}
    side["stages"]["render"]["faces"] = (rr.get("finish") or {}).get("faces")
    side["stages"]["render"]["face_detector"] = (rr.get("finish") or {}).get("det")
    side["times"]["render"] = round(time.time() - t, 2)
    shutil.rmtree(work / "base", ignore_errors=True)
    shutil.rmtree(work / "hair", ignore_errors=True)
    exif_src = raw if src_kind == "raw" else jpg
    for p in (full, web, tif):
        if p is not None and p.exists():
            err = exif_copy(exif_src, p)
            if err:
                side["notes"].append(f"EXIF no copiado en {p.name}: {err}")
    P("render", 1.0)

    # 5. reframing (JPG, web and TIFF16 receive the SAME geometry)
    t = time.time()
    mode = ep.get("reframe", "off")
    final_full, final_web, final_tif = full, web, tif
    if mode != "off":
        P("reframe", 0.1, "encuadre")
        try:
            import cv2
            from PIL import Image
            from .pipeline import reframe as enc
            wimg = cv2.imread(str(web))
            fs = Image.open(full).size
            p = enc.analyze(wimg, web_path=web, full_size=fs, overrides=prm.get("reframe_overrides"))
            p.pop("_dbg", None)
            del wimg
            info = {k: v for k, v in p.items() if isinstance(v, (int, float, str, bool, list, type(None)))}
            if mode == "apply":
                cf, cw = work / "render" / f"{stem}_crop.jpg", work / "render" / f"{stem}_crop_web.jpg"
                enc.apply(full, p, cf, web_src=web, web_out=cw, raw_path=exif_src)
                final_full, final_web = cf, cw
                info["applied"] = True
                info["method"] = p.get("method")
                if tif is not None:
                    ct = work / "render" / f"{stem}_crop.tif"
                    enc.apply_tiff16(tif, p, ct, raw_path=exif_src)
                    final_tif = ct
                    info["tiff16"] = "mismo giro + recorte (Lanczos 16 bit)" if p.get("angle_deg") else "mismo recorte"
            else:
                info["applied"] = False
                info["note"] = "sugerencia: no aplicada (reframe='suggest')"
            side["stages"]["reframe"] = info
        except Exception as e:  # noqa: BLE001 - framing is optional
            side["stages"]["reframe"] = {"applied": False, "error": repr(e), "traceback": traceback.format_exc()[-1200:]}
            side["pending_manual"].append("encuadre_pendiente_manual")
    else:
        side["stages"]["reframe"] = {"applied": False, "reason": "desactivado en la receta"}
    side["times"]["reframe"] = round(time.time() - t, 2)
    P("reframe", 1.0)

    # 6. verify + atomic delivery into out_dir
    P("finalize", 0.1)
    from PIL import Image
    with Image.open(final_full) as im:
        im.verify()
    with Image.open(final_full) as im:
        size = list(im.size)
    if min(size) < int(prm.get("min_side", 256)):
        raise RuntimeError(f"resultado sospechoso {size}")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.replace(final_full, out_dir / "full.jpg")
    os.replace(final_web, out_dir / "web.jpg")
    if final_tif is not None and final_tif.exists():
        os.replace(final_tif, out_dir / "full.tif")
    side["size"] = size
    side["times"]["total"] = round(time.time() - t_all, 2)
    side["peak_rss_mb"] = peak_mb()
    side["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "sidecar.json").write_text(json.dumps(side, indent=1, ensure_ascii=False, default=str))
    shutil.rmtree(work, ignore_errors=True)
    P("finalize", 1.0)
    print("@@R " + json.dumps({"ok": True, "size": size, "tiff": (out_dir / "full.tif").exists(),
                               "times": side["times"], "peak_rss_mb": side["peak_rss_mb"],
                               "pending_manual": side["pending_manual"], "notes": side["notes"]}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as e:  # noqa: BLE001
        print("@@R " + json.dumps({"ok": False, "error": repr(e), "traceback": traceback.format_exc()[-3000:]}),
              flush=True)
        sys.exit(2)
