# SPDX-License-Identifier: GPL-3.0-or-later
"""Child process: develops ONE photo through the legacy engine chain (the same chain as the legacy ``lote.py``).

Run as ``python -m revelado.legacy_worker <params.json>``; it is always started by ``legacy_adapter`` in its own
process (memory isolation: when it exits, every full-resolution buffer is gone).

Chain: exposure analysis (develop.analyze_exposure) -> RAW develop + clean (develop.develop, RawTherapee) ->
optional hair clean-up with the legacy safety gate (cabello.clean + lote.hair_gate) -> film render
(engine_A-fisico.render, with the recipe multipliers) -> EXIF from the RAW (no GPS) -> optional reframing
(encuadre.analyze / apply) -> atomic move of the outputs into the output directory.

Protocol on stdout: lines ``@@P {json}`` (progress: stage, progress 0-1) and one final ``@@R {json}`` (result).
Everything else is free-form log text.
"""
from __future__ import annotations

import importlib.util
import json
import os
import resource
import shutil
import subprocess
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
    s = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    c = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    k = 1e6 if sys.platform == "darwin" else 1e3       # macOS reports bytes, Linux KiB
    return {"python": round(s / k), "children": round(c / k)}


def exif_copy(exiftool: str, src: Path, dst: Path, extra: list[str] | None = None) -> str | None:
    r = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-tagsFromFile", str(src), "-all:all",
                        "-Orientation=1", "-n", "-IFD1:all=", *(extra or []), str(dst)], capture_output=True, text=True)
    r2 = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-GPS:all=", str(dst)], capture_output=True,
                        text=True)
    if r.returncode != 0 or r2.returncode != 0:
        return (r.stderr + r2.stderr)[-300:]
    return None


def main(params_path: str) -> int:
    prm = json.loads(Path(params_path).read_text())
    t_all = time.time()
    legacy = Path(prm["legacy_dir"])
    snap = legacy / "_snap_v0"
    arw = Path(prm["raw_path"])
    stem = prm["stem"]
    work = Path(prm["work_dir"])
    out_dir = Path(prm["out_dir"])
    lp = prm["legacy_params"]
    exiftool = prm.get("exiftool") or "exiftool"
    side: dict = {"stem": stem, "engine": "legacy:_snap_v0/engine_A-fisico.py", "recipe": prm.get("recipe"),
                  "legacy_params": lp, "stages": {}, "times": {}, "pending_manual": [], "notes": []}
    if work.exists():
        shutil.rmtree(work)
    (work / "render").mkdir(parents=True)
    sys.path.insert(0, str(snap))
    sys.path.insert(1, str(legacy))
    os.chdir(work)                                    # any relative scratch file of the legacy code lands here
    import develop as dev                             # frozen snapshot

    # 1. exposure
    P("exposure", 0.0, "análisis de exposición")
    t = time.time()
    a = dev.analyze_exposure(arw)
    ev = float(a["ev"])
    side["ev_auto"] = ev
    side["stages"]["exposure"] = {k: v for k, v in a.items() if isinstance(v, (int, float, str, bool, type(None)))}
    side["times"]["exposure"] = round(time.time() - t, 1)
    P("exposure", 1.0, f"EV automático {ev:+.2f}")

    # 2. develop + clean
    P("develop", 0.02, "revelado RAW + limpieza (RawTherapee)")
    t = time.time()
    base0 = work / "base" / f"{stem}.tif"
    r = dev.develop(arw, base0, ev=ev, exposure_json=None, tmp_dir=work / "_tmp_rt")
    side["stages"]["develop"] = {k: r[k] for k in ("seconds_total", "seconds_rt", "nr", "clean", "lens", "stats") if k in r}
    side["times"]["develop"] = round(time.time() - t, 1)
    P("develop", 1.0)

    # 3. hair (optional, gated)
    base_render = base0
    hair = lp.get("hair", "off")
    t = time.time()
    if hair != "off":
        P("hair", 0.05, f"cabello ({hair}) con puerta de seguridad")
        try:
            import cabello
            spec = importlib.util.spec_from_file_location("legacy_lote", str(legacy / "lote.py"))
            lote = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(lote)
            img = dev.load_base(base0)
            out, rep = cabello.clean(img, strength=hair, ev=ev, stem=stem, overrides={"skin": False})
            met = lote.hair_metrics(img, out, ev) if rep.get("status") == "applied" else {}
            ok, why = lote.hair_gate(rep, met)
            cab = {"version": getattr(cabello, "VERSION", "?"), "strength": hair, "status": rep.get("status"),
                   "applied": ok, "reason": why, "metrics": met}
            if ok:
                import editorial as ed
                meta = dev.load_base_meta(base0)
                meta["cabello"] = {"version": cab["version"], "strength": hair, "status": "applied"}
                base_render = work / "cabello" / f"{stem}.tif"
                ed.save_base(base_render, out, meta)
            elif rep.get("status") == "applied" or (rep.get("status") == "skipped"
                                                     and "sin personas" not in str(rep.get("reason", ""))):
                side["pending_manual"].append("cabello_pendiente_manual")
            del img, out
        except Exception as e:  # noqa: BLE001 - hair is optional, never blocks delivery
            cab = {"applied": False, "error": repr(e), "traceback": traceback.format_exc()[-1200:]}
            side["pending_manual"].append("cabello_pendiente_manual")
        side["stages"]["hair"] = cab
    else:
        side["stages"]["hair"] = {"applied": False, "reason": "desactivado en la receta"}
    side["times"]["hair"] = round(time.time() - t, 1)
    P("hair", 1.0)

    # 4. film render
    P("render", 0.02, "película Kodak Ektar 100 (motor físico)")
    t = time.time()
    spec = importlib.util.spec_from_file_location("engA", str(snap / "engine_A-fisico.py"))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    full = work / "render" / f"{stem}.jpg"
    want_tiff = bool(prm.get("tiff16"))
    rr = eng.render(str(base_render), str(full), ev=ev, ev_offset=lp["ev_offset"], strength=lp["strength"],
                    grain=lp["grain"], skin=lp["skin"], sharp=lp["sharp"], subject=lp["subject"], blem=lp["blem"],
                    web=True, save_tiff16=want_tiff)
    web = work / "render" / "web2560" / f"{stem}.jpg"
    tif = full.with_suffix(".tif") if want_tiff else None
    side["stages"]["render"] = {k: rr.get(k) for k in ("ev", "ev_guard", "p998_scene", "clip_any_pct", "mean_okC",
                                                       "luma_p0_5", "luma_p99_9", "size", "seconds", "timings")}
    side["times"]["render"] = round(time.time() - t, 1)
    del eng
    shutil.rmtree(work / "base", ignore_errors=True)
    shutil.rmtree(work / "cabello", ignore_errors=True)
    for p in (full, web, tif):
        if p is not None and p.exists():
            err = exif_copy(exiftool, arw, p)
            if err:
                side["notes"].append(f"EXIF no copiado en {p.name}: {err}")
    P("render", 1.0)

    # 5. reframing
    t = time.time()
    mode = lp.get("reframe", "off")
    final_full, final_web = full, web
    if mode != "off":
        P("reframe", 0.1, "encuadre")
        try:
            import cv2
            from PIL import Image
            import encuadre as enc
            wimg = cv2.imread(str(web))
            fs = Image.open(full).size
            p = enc.analyze(wimg, stem=stem, web_path=web, full_size=fs)
            p.pop("_dbg", None)
            del wimg
            info = {k: v for k, v in p.items() if isinstance(v, (int, float, str, bool, list, type(None)))}
            if mode == "apply":
                cf, cw = work / "render" / f"{stem}_crop.jpg", work / "render" / f"{stem}_crop_web.jpg"
                enc.apply(full, p, cf, web_src=web, web_out=cw, raw_path=arw)
                final_full, final_web = cf, cw
                info["applied"] = True
                if tif is not None:
                    if not p.get("angle_deg"):
                        import tifffile
                        a16 = tifffile.imread(str(tif))
                        x, y, w, h = int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"])
                        tifffile.imwrite(str(tif), a16[y:y + h, x:x + w], photometric="rgb")
                        del a16
                        exif_copy(exiftool, arw, tif)
                    else:
                        side["notes"].append("TIFF16 sin enderezar: el giro solo se aplica al JPG (próximamente)")
            else:
                info["applied"] = False
                info["note"] = "sugerencia: no aplicada (reframe='suggest')"
            side["stages"]["reframe"] = info
        except Exception as e:  # noqa: BLE001 - framing is optional
            side["stages"]["reframe"] = {"applied": False, "error": repr(e), "traceback": traceback.format_exc()[-1200:]}
            side["pending_manual"].append("encuadre_pendiente_manual")
    else:
        side["stages"]["reframe"] = {"applied": False, "reason": "desactivado en la receta"}
    side["times"]["reframe"] = round(time.time() - t, 1)
    P("reframe", 1.0)

    # 6. verify + atomic delivery into out_dir
    P("finalize", 0.1)
    from PIL import Image
    with Image.open(final_full) as im:
        im.verify()
    with Image.open(final_full) as im:
        size = list(im.size)
    if min(size) < 1000:
        raise RuntimeError(f"resultado sospechoso {size}")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.replace(final_full, out_dir / "full.jpg")
    os.replace(final_web, out_dir / "web.jpg")
    if tif is not None and tif.exists():
        os.replace(tif, out_dir / "full.tif")
    side["size"] = size
    side["times"]["total"] = round(time.time() - t_all, 1)
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
