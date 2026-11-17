# SPDX-License-Identifier: GPL-3.0-or-later
"""Develop runner: runs ONE photo in a child process (memory isolation), one at a time (single heavy worker).

Default engine: the bundled pipeline (``revelado.worker``), no private files needed. Optional compare path:
``REVELADO_DEVELOP_ENGINE=legacy`` runs the private reference engine through ``revelado.legacy_worker``.
This module:
  * validates the configuration and the film (only ``kodak_ektar_100`` is implemented),
  * RAW policy: develop needs the RAW; a JPG-only photo is refused (E_NEEDS_RAW) unless the recipe explicitly sets
    ``develop_source="jpg"``,
  * admission control: free disk >= REVELADO_MIN_FREE_DISK_GB and available RAM >= REVELADO_HEAVY_MIN_AVAILABLE_GB
    (waits up to 2 minutes, then E_MEM_LOW),
  * a watchdog every 0.5 s sums the RSS of the whole child tree (Python + rawtherapee-cli) and kills it above
    REVELADO_HEAVY_RSS_CAP_GB (E_MEM_GUARD),
  * relays progress lines to the job and keeps the child's full log under the output's ``logs/``.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import subprocess
import threading
import time
import uuid
from pathlib import Path

import psutil

from .config import ENGINE_DIR, Settings
from .errors import ReveladoError
from . import legacy_adapter
from .jobs import Cancelled, Job
from .log import get_logger
from . import recipe as recipe_mod

log = get_logger("runner")


def check_config(settings: Settings) -> dict:
    """Can the configured engine develop? (native needs rawtherapee-cli for RAW sources; JPG sources work anyway)."""
    if settings.develop_engine == "legacy":
        return legacy_adapter.check_config(settings)
    if not settings.rawtherapee:
        return {"ok": True, "raw": False, "engine": "native",
                "reason": "rawtherapee-cli no encontrado: solo revelado desde JPG (develop_source='jpg')"}
    return {"ok": True, "raw": True, "engine": "native"}


def ensure_film(recipe: dict) -> None:
    legacy_adapter.ensure_film(recipe)


def source_for(photo: dict, rcp: dict) -> str:
    """'raw' | 'jpg'. Raises E_NEEDS_RAW when the photo has no RAW and the recipe did not choose the JPG mode."""
    want = rcp.get("develop_source", "raw")
    has_raw = bool(photo.get("raw_path")) and Path(photo["raw_path"]).exists()
    has_jpg = bool(photo.get("jpg_path")) and Path(photo["jpg_path"]).exists()
    if want == "jpg":
        if not has_jpg:
            raise ReveladoError("E_NOT_FOUND", "esta foto no tiene JPG", extra={"reason": "needs_jpg"})
        return "jpg"
    if not has_raw:
        raise ReveladoError("E_NEEDS_RAW", "se necesita el RAW: esta foto solo tiene JPG (elige develop_source='jpg' "
                            "para revelar desde el JPG)", message_es="Se necesita el RAW para revelar esta foto.",
                            extra={"reason": "needs_raw", "photo": photo.get("name")})
    return "raw"


def _tree_rss(p: psutil.Process) -> int:
    total = 0
    try:
        procs = [p] + p.children(recursive=True)
    except psutil.Error:
        return 0
    for c in procs:
        try:
            total += c.memory_info().rss
        except psutil.Error:
            pass
    return total


def _kill_tree(p: psutil.Process) -> None:
    try:
        procs = p.children(recursive=True) + [p]
    except psutil.Error:
        procs = [p]
    for c in procs:
        try:
            c.kill()
        except psutil.Error:
            pass


def _admit(settings: Settings, job: Job) -> None:
    free = shutil.disk_usage(settings.cache_dir).free / 1e9
    if free < settings.min_free_disk_gb:
        raise ReveladoError("E_DISK_LOW", f"{free:.1f} GB libres en el disco de caché (mínimo {settings.min_free_disk_gb:g})")
    t0 = time.time()
    warned = False
    while True:
        avail = psutil.virtual_memory().available / 1e9
        if avail >= settings.heavy_min_available_gb:
            return
        if not warned:
            job.say(f"esperando memoria libre: {avail:.1f} GB disponibles, se necesitan "
                    f"{settings.heavy_min_available_gb:g} GB (cierra aplicaciones)")
            warned = True
        if time.time() - t0 > 120:
            raise ReveladoError("E_MEM_LOW", f"{avail:.1f} GB disponibles")
        job.check_cancel()
        time.sleep(2)


def develop_one(settings: Settings, job: Job, photo: dict, rcp: dict, out_dir: Path, tiff16: bool,
                progress_span: tuple[float, float]) -> dict:
    """Develop one photo. Returns the child's result dict (+ peak_tree_rss_mb). Raises ReveladoError."""
    cfg = check_config(settings)
    if not cfg["ok"]:
        raise ReveladoError("E_LEGACY_MISSING", cfg["reason"])
    ensure_film(rcp)
    source = source_for(photo, rcp)
    legacy = settings.develop_engine == "legacy"
    if source == "raw" and not settings.rawtherapee:
        raise ReveladoError("E_TOOL_MISSING", "rawtherapee-cli no encontrado: instala RawTherapee para revelar RAW")
    if legacy and source != "raw":
        raise ReveladoError("E_BAD_INPUT", "el motor heredado solo revela RAW")
    if legacy and rcp.get("film") != "kodak_ektar_100":
        raise ReveladoError("E_BAD_INPUT", "el motor heredado solo revela con kodak_ektar_100")
    _admit(settings, job)
    run_id = uuid.uuid4().hex[:8]
    work = settings.cache_dir / "tmp" / f"work_{photo['id']}_{run_id}"
    tmp_out = settings.cache_dir / "tmp" / f"out_{photo['id']}_{run_id}"
    logs = out_dir.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    raw = photo.get("raw_path")
    if legacy:
        legacy_params, ignored = recipe_mod.to_legacy(rcp)
        params = {"legacy_dir": str(settings.legacy_dir), "raw_path": raw, "stem": Path(raw).stem,
                  "work_dir": str(work), "out_dir": str(tmp_out), "legacy_params": legacy_params, "recipe": rcp,
                  "tiff16": tiff16, "exiftool": settings.exiftool}
        module = "revelado.legacy_worker"
    else:
        engine_params, ignored = recipe_mod.to_engine(rcp)
        src = raw if source == "raw" else photo.get("jpg_path")
        params = {"source": source, "raw_path": raw, "jpg_path": photo.get("jpg_path"), "stem": Path(src).stem,
                  "work_dir": str(work), "out_dir": str(tmp_out), "engine_params": engine_params, "recipe": rcp,
                  "tiff16": tiff16}
        module = "revelado.worker"
    for note in ignored:
        job.say(f"ignorado: {note}")
    ppath = settings.cache_dir / "tmp" / f"params_{run_id}.json"
    ppath.write_text(json.dumps(params))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"               # never write .pyc files into the (read-only) legacy folder
    env.setdefault("OMP_NUM_THREADS", str(max(2, (os.cpu_count() or 4) - 2)))
    env.pop("ANTHROPIC_API_KEY", None)                 # the develop child never needs the key
    logf = logs / f"{photo['id']}_{run_id}.log"
    a, b = progress_span
    t0 = time.time()
    result: dict = {}
    tail: list[str] = []
    killed = {"why": None}
    with open(logf, "w", encoding="utf-8") as lf:
        py = settings.legacy_python if legacy else sys.executable
        proc = subprocess.Popen([py, "-u", "-m", module, str(ppath)],
                                cwd=str(ENGINE_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, encoding="utf-8", errors="replace",
                                **({"start_new_session": True} if os.name != "nt" else
                                   {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}))
        ps = psutil.Process(proc.pid)
        peak = {"rss": 0}
        stop = threading.Event()

        def watchdog():
            cap = settings.heavy_rss_cap_gb * 1e9
            while not stop.wait(0.5):
                rss = _tree_rss(ps)
                peak["rss"] = max(peak["rss"], rss)
                if rss > cap:
                    killed["why"] = "mem"
                    log.error("memory guard: %.1f GB > cap %.1f GB, killing develop", rss / 1e9, cap / 1e9)
                    _kill_tree(ps)
                    return

        def on_cancel():
            killed["why"] = "cancel"
            _kill_tree(ps)
        job.on_cancel = on_cancel
        wd = threading.Thread(target=watchdog, daemon=True)
        wd.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lf.write(line)
                s = line.rstrip("\n")
                if s.startswith("@@P "):
                    try:
                        d = json.loads(s[4:])
                        job.update(a + (b - a) * float(d.get("progress", 0)), d.get("stage"), photo_id=photo["id"])
                        if d.get("msg"):
                            job.say(f"{photo['name']}: {d['msg']}")
                    except (ValueError, TypeError):
                        pass
                elif s.startswith("@@R "):
                    try:
                        result = json.loads(s[4:])
                    except ValueError:
                        result = {"ok": False, "error": "resultado ilegible"}
                elif s.strip():
                    tail.append(s)
                    del tail[:-25]
            proc.wait()
        finally:
            stop.set()
            job.on_cancel = None
            ppath.unlink(missing_ok=True)
    if killed["why"] == "cancel":
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)
        raise Cancelled()
    if killed["why"] == "mem":
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)
        raise ReveladoError("E_MEM_GUARD", f"pico {peak['rss'] / 1e9:.1f} GB (límite {settings.heavy_rss_cap_gb:g} GB)")
    if not result.get("ok"):
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(tmp_out, ignore_errors=True)
        detail = result.get("error") or (tail[-1] if tail else f"código {proc.returncode}")
        log.error("develop failed for %s: %s\n%s", photo["id"], detail, result.get("traceback", "\n".join(tail[-10:])))
        raise ReveladoError("E_DEVELOP_FAILED", str(detail)[:400], extra={"log": str(logf)})
    # move the finished output dir into place atomically (same volume)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_out, out_dir)
    result["seconds"] = round(time.time() - t0, 1)
    result["peak_tree_rss_mb"] = round(peak["rss"] / 1e6)
    result["log"] = str(logf)
    result["source"] = source
    result["engine"] = "legacy" if legacy else "native"
    return result
