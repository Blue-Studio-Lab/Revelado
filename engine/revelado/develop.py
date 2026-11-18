# SPDX-License-Identifier: GPL-3.0-or-later
"""Heavy job runners: develop sample, develop batch, export. All run on the single heavy worker."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .errors import ReveladoError
from .jobs import Job, run_item
from .log import get_logger
from . import runner
from . import recipe as recipe_mod

log = get_logger("develop")


def _photo(ctx, pid: str) -> dict:
    r = ctx.catalog.photo(pid)
    if not r:
        raise ReveladoError("E_NOT_FOUND", f"foto {pid} no existe")
    return dict(r) | {"meta": json.loads(r["meta_json"] or "{}")}


def output_public(row) -> dict:
    sc = json.loads(row["sidecar_json"] or "{}")
    return {"output_id": row["output_id"], "photo_id": row["photo_id"], "job_id": row["job_id"],
            "recipe": json.loads(row["recipe_json"] or "{}"), "recipe_key": row["recipe_key"],
            "created_at": row["created_at"], "url": f"/api/outputs/{row['output_id']}/full.jpg",
            "web_url": f"/api/outputs/{row['output_id']}/web.jpg", "size": [row["width"], row["height"]],
            "has_tiff16": bool(row["tiff_path"] and Path(row["tiff_path"]).exists()),
            "pending_manual": sc.get("pending_manual", []), "times": sc.get("times"),
            "reframe": (sc.get("stages") or {}).get("reframe"), "hair": (sc.get("stages") or {}).get("hair"),
            "notes": sc.get("notes", [])}


def develop_photo(ctx, job: Job, pid: str, rcp: dict, tiff16: bool, span: tuple[float, float]) -> dict:
    photo = _photo(ctx, pid)
    job.say(f"{photo['name']}: revelando ({rcp['film']})")
    oid = uuid.uuid4().hex[:12]
    out_dir = ctx.settings.cache_dir / "develop" / pid / oid
    res = runner.develop_one(ctx.settings, job, photo, rcp, out_dir, tiff16, span)
    sidecar = json.loads((out_dir / "sidecar.json").read_text())
    size = res.get("size") or [None, None]
    row = {"output_id": oid, "photo_id": pid, "job_id": job.job_id, "recipe": rcp, "recipe_key": recipe_mod.key(rcp),
           "dir": str(out_dir), "full_path": str(out_dir / "full.jpg"), "web_path": str(out_dir / "web.jpg"),
           "tiff_path": str(out_dir / "full.tif") if (out_dir / "full.tif").exists() else None,
           "width": size[0], "height": size[1], "sidecar": sidecar}
    ctx.catalog.add_output(row)
    out = {"photo_id": pid, "output_id": oid, "url": f"/api/outputs/{oid}/full.jpg",
           "web_url": f"/api/outputs/{oid}/web.jpg", "size": size, "seconds": res.get("seconds"),
           "peak_tree_rss_mb": res.get("peak_tree_rss_mb"), "pending_manual": res.get("pending_manual", [])}
    job.outputs.append(out)
    job.items_done += 1
    pend = ", ".join(res.get("pending_manual") or []) or "-"
    job.say(f"{photo['name']}: listo en {res.get('seconds')} s, {size[0]}×{size[1]}, pico RAM "
            f"{res.get('peak_tree_rss_mb')} MB, pendientes: {pend}")
    ctx.bus.emit({"type": "photo_updated", "photo_id": pid, "output": out})
    return out


def run_sample(job: Job, ctx) -> None:
    p = job.payload
    rcp = p["recipe"]
    job.items_total = 1
    develop_photo(ctx, job, p["photo_id"], rcp, bool(p.get("tiff16")), (0.0, 1.0))


def run_batch(job: Job, ctx) -> None:
    p = job.payload
    rcp = p["recipe"]
    per = p.get("recipes") or {}
    ids = p["photo_ids"]
    job.items_total = len(ids)
    job.result = {"recipes": {pid: per.get(pid, rcp) for pid in ids}}      # resolved recipe per photo
    t0 = time.time()
    for i, pid in enumerate(ids):
        job.check_cancel()
        span = (i / len(ids), (i + 1) / len(ids))
        r = per.get(pid, rcp)
        if pid in (p.get("overrides") or {}):
            job.say(f"{pid}: receta con ajustes propios ({', '.join(sorted(p['overrides'][pid]))})")
        run_item(job, pid, lambda pid=pid, span=span, r=r: develop_photo(ctx, job, pid, r, bool(p.get("tiff16")), span))
        done = i + 1
        job.update(done / len(ids), f"{done}/{len(ids)}", eta_s=(time.time() - t0) / done * (len(ids) - done))


# ------------------------------------------------------------------------------------------------ export
def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:120] or "foto"


def _place(tmp: Path, dest: Path, base: str, ext: str) -> Path:
    """Atomically place tmp at dest/base.ext, NEVER overwriting: base_1.ext, base_2.ext... (hard link + unlink)."""
    n = 0
    while True:
        cand = dest / (f"{base}{ext}" if n == 0 else f"{base}_{n}{ext}")
        try:
            os.link(tmp, cand)
            tmp.unlink()
            return cand
        except FileExistsError:
            n += 1
            if n > 9999:
                raise ReveladoError("E_EXPORT_FAILED", "demasiados archivos con el mismo nombre")
        except OSError:
            # filesystems without hard links: fall back to exclusive create + copy
            try:
                # same permission bits the hard-link branch would give (the temp file's own, i.e. the umask's)
                fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(os.stat(tmp).st_mode))
            except FileExistsError:
                n += 1
                continue
            with os.fdopen(fd, "wb") as fh, open(tmp, "rb") as src:
                shutil.copyfileobj(src, fh)
            tmp.unlink()
            return cand


def _exif(exiftool: str | None, raw: str | None, dst: Path, w: int, h: int) -> str | None:
    if not exiftool or not raw or not Path(raw).exists():
        return "sin RAW/exiftool: EXIF no copiado"
    r = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-tagsFromFile", raw, "-all:all", "-Orientation=1",
                        "-n", f"-ExifIFD:ExifImageWidth={w}", f"-ExifIFD:ExifImageHeight={h}", "-IFD1:all=",
                        "--GPS:all", str(dst)], capture_output=True, text=True)
    r2 = subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-GPS:all=", str(dst)], capture_output=True,
                        text=True)
    return None if r.returncode == 0 and r2.returncode == 0 else (r.stderr + r2.stderr)[-200:]


def export_roots() -> list[str]:
    """Folders an export may be written into (real paths, no trailing separator). Defaults: the user's home, the
    temp folder, shared folders and the usual mount points for external disks / NAS shares of each OS (macOS
    /Volumes and /Users/Shared; Linux /media, /mnt, /run/media, /run/user/<uid> (GVFS/SMB), /srv, /data; Windows
    every drive letter and UNC shares, minus the system folders). REVELADO_EXPORT_ROOTS and the MCP server's
    REVELADO_EXPORT_DIRS (both os.pathsep-separated) add more. Exports never go to the engine cache or the
    originals (checked in validate_dest)."""
    from .config import env_value
    cands: list[str] = [str(Path.home()), tempfile.gettempdir()]
    if sys.platform == "darwin":
        cands += ["/Volumes", "/Users/Shared"]
    elif sys.platform == "win32":
        cands += [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] + ["\\\\"]   # drives + \\server\share
    else:
        cands += ["/media", "/mnt", "/run/media", "/srv", "/data"]
        if hasattr(os, "getuid"):
            cands.append(f"/run/user/{os.getuid()}")                  # GVFS/SMB mounts of this user
    for key in ("REVELADO_EXPORT_ROOTS", "REVELADO_EXPORT_DIRS"):
        cands += [x for x in env_value(key).split(os.pathsep) if x.strip()]
    out: list[str] = []
    for c in cands:
        c = c.strip()
        if c == "\\\\":                                  # UNC shares: kept as the bare "\\\\" prefix
            r = c
        else:
            try:
                r = os.path.realpath(os.path.expanduser(c))
            except (OSError, ValueError):
                continue
            r = r.rstrip("/\\") or r[:1]             # "/" stays "/" (only reachable via an env setting)
        if r and r not in out:
            out.append(r)
    return out


def _system_dirs() -> list[str]:
    """Folders inside an export root that still must never receive exports (Windows system drive)."""
    if sys.platform != "win32":
        return []
    out = []
    for var, default in (("SystemRoot", "C:\\Windows"), ("ProgramFiles", "C:\\Program Files"),
                         ("ProgramFiles(x86)", "C:\\Program Files (x86)"), ("ProgramData", "C:\\ProgramData")):
        v = os.environ.get(var) or default
        out.append(os.path.realpath(v).rstrip("\\/"))
    return out


def _fold(p: str) -> str:
    """Comparison key for a path: case-insensitive where the usual filesystem is (Windows, macOS)."""
    p = os.path.normcase(p)
    return p.lower() if sys.platform == "darwin" else p


def _prefix(root: str) -> str:
    return root if root.endswith(("/", "\\")) else root + os.sep


def _inside(path: str, root: str) -> bool:
    """path == root or below it, compared with _fold (case-insensitive on macOS/Windows)."""
    fp, fr = _fold(path), _fold(root)
    return fp == fr or fp.startswith(_fold(_prefix(root)))


def validate_dest(ctx, dest: str, photo_rows: list[dict]) -> Path:
    if not dest or not isinstance(dest, str):
        raise ReveladoError("E_BAD_INPUT", "falta 'dest_dir'")
    dest = dest.strip()
    if "\x00" in dest or any(ord(ch) < 32 for ch in dest):
        raise ReveladoError("E_BAD_INPUT", "dest_dir contiene caracteres no válidos")
    if dest.startswith("~") and dest != "~" and not dest.startswith("~/"):
        raise ReveladoError("E_BAD_INPUT", "dest_dir: solo se admite '~/' (tu carpeta personal), no '~usuario'")
    expanded = os.path.expanduser(dest)                   # "~/Entrega" -> /Users/<you>/Entrega
    if not os.path.isabs(expanded):
        raise ReveladoError("E_BAD_INPUT", "dest_dir debe ser una ruta absoluta")
    if expanded.startswith(("\\\\?", "\\\\.", "//?", "//.")):        # Windows device / verbatim paths
        raise ReveladoError("E_BAD_INPUT", "dest_dir no admite rutas de dispositivo (\\\\?\\ o \\\\.\\)")
    if ".." in Path(expanded).parts:
        raise ReveladoError("E_BAD_INPUT", "dest_dir no puede contener '..'")
    real = os.path.realpath(expanded)                     # symlinks resolved: no escape through a link
    if real.startswith(("\\\\?\\", "\\\\.\\")):                  # a link resolved to a device / verbatim path
        raise ReveladoError("E_BAD_INPUT", "dest_dir no admite rutas de dispositivo")
    # Containment: find the export root (case-insensitively where the filesystem is), adopt the root's own
    # spelling for the matching prefix, then check it again with a plain startswith on the path actually used.
    prefix = ""
    for root in export_roots():
        cand = _prefix(root)
        if _fold(real[:len(cand)]) == _fold(cand):
            prefix = cand
            real = cand + real[len(cand):]
            break
        if _fold(real) == _fold(root):
            prefix = real = cand
            break
    if not prefix:
        raise ReveladoError("E_BAD_INPUT", "dest_dir debe estar en tu carpeta personal, una carpeta compartida o "
                                           "un disco externo / de red (o en REVELADO_EXPORT_ROOTS)")
    if not real.startswith(prefix):
        raise ReveladoError("E_BAD_INPUT", "dest_dir fuera de las carpetas de exportación permitidas")
    if any(_inside(real, s) for s in _system_dirs()):
        raise ReveladoError("E_BAD_INPUT", "no se puede exportar dentro de una carpeta del sistema")
    d = Path(real)
    if _inside(real, os.path.realpath(ctx.settings.cache_dir)):
        raise ReveladoError("E_BAD_INPUT", "no se puede exportar dentro de la caché del motor")
    for r in photo_rows:
        sess = ctx.catalog.session(r["session_id"])
        if sess and _inside(real, os.path.realpath(sess["path"])):
            raise ReveladoError("E_BAD_INPUT", "no se puede exportar dentro de la carpeta de originales")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ReveladoError("E_BAD_INPUT", f"no se pudo crear la carpeta destino ({e.strerror})") from e
    if not os.access(d, os.W_OK):
        raise ReveladoError("E_BAD_INPUT", "la carpeta destino no admite escritura")
    return d


def export_one(ctx, job: Job, pid: str, opts: dict, seq: int, dest: Path, span) -> dict:
    from PIL import Image
    photo = _photo(ctx, pid)
    fmt = opts["format"]
    want_key = recipe_mod.key(opts["recipe"]) if opts.get("recipe") else None
    rows = ctx.catalog.outputs(pid)
    row = None
    for r in rows:
        if want_key and r["recipe_key"] != want_key:
            continue
        if not Path(r["full_path"] or "").exists():
            continue
        if fmt == "tiff16" and not (r["tiff_path"] and Path(r["tiff_path"]).exists()):
            continue
        row = r
        break
    if row is None:
        if not opts.get("develop_missing", True):
            raise ReveladoError("E_NOT_FOUND", "sin revelado para esta foto (develop_missing=false)")
        rcp = opts.get("recipe") or (json.loads(rows[0]["recipe_json"]) if rows else recipe_mod.normalise({}))
        job.say(f"{photo['name']}: sin revelado {'TIFF16 ' if fmt == 'tiff16' else ''}válido; revelando primero")
        out = develop_photo(ctx, job, pid, rcp, fmt == "tiff16", span)
        job.items_done -= 1                        # counted once as an export item below
        job.outputs.pop()
        row = ctx.catalog.output(out["output_id"])
    rcp = json.loads(row["recipe_json"] or "{}")
    stem = Path(photo["raw_path"] or photo["jpg_path"] or photo["name"]).stem
    naming = opts.get("naming", "original")
    base = {"original": stem, "original_film": f"{stem}_{rcp.get('film', 'film')}",
            "sequence": f"{seq:03d}_{stem}"}.get(naming, stem)
    base = _safe_name(base)
    long_edge = opts.get("long_edge")
    ext = ".jpg" if fmt == "jpg" else ".tif"
    tmp = dest / f".{base}.{uuid.uuid4().hex[:6]}.part{ext}"     # real extension: exiftool picks the writer by it
    try:
        if fmt == "jpg":
            src = Path(row["full_path"])
            # sizes up to 2560 px start from the engine's web render, whose grain was synthesised at that pitch
            # (downscaling full-resolution grain would blur it); only uncropped/cropped geometry matches either way
            if long_edge and long_edge <= 2560 and row["web_path"] and Path(row["web_path"]).exists():
                src = Path(row["web_path"])
            q = int(opts.get("quality", 97))
            if not long_edge and q >= 97:
                shutil.copyfile(src, tmp)              # engine output is already q97 4:4:4: no re-encode
                with Image.open(tmp) as im:
                    w, h = im.size
            else:
                with Image.open(src) as im:
                    icc = im.info.get("icc_profile")
                    im = im.convert("RGB")
                    if long_edge and max(im.size) > long_edge:
                        im.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
                    w, h = im.size
                    im.save(tmp, "JPEG", quality=q, subsampling=0, optimize=True, icc_profile=icc)
        else:
            import cv2
            import tifffile
            a = tifffile.imread(row["tiff_path"])
            if long_edge and max(a.shape[:2]) > long_edge:
                s = long_edge / max(a.shape[:2])
                a = cv2.resize(a, (round(a.shape[1] * s), round(a.shape[0] * s)), interpolation=cv2.INTER_AREA)
            h, w = a.shape[:2]
            from PIL import ImageCms
            icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            tifffile.imwrite(str(tmp), a, photometric="rgb", compression="zlib",
                             extratags=[(34675, 7, len(icc), icc, True)])
            del a
        note = _exif(ctx.settings.exiftool, photo.get("raw_path") or photo.get("jpg_path"), tmp, w, h)
        if note:
            job.say(f"{photo['name']}: {note}")
        final = _place(tmp, dest, base, ext)
    finally:
        if tmp.exists():
            tmp.unlink()
    if opts.get("write_recipe_json"):
        jt = dest / f".{base}.{uuid.uuid4().hex[:6]}.part.json"
        jt.write_text(json.dumps({"photo": photo["name"], "recipe": rcp, "output_id": row["output_id"]}, indent=1))
        _place(jt, dest, final.stem, ".json")
    out = {"photo_id": pid, "path": str(final), "size": [w, h], "output_id": row["output_id"],
           "url": f"/api/outputs/{row['output_id']}/full.jpg", "web_url": f"/api/outputs/{row['output_id']}/web.jpg"}
    job.outputs.append(out)
    job.items_done += 1
    job.say(f"{photo['name']}: exportada -> {final.name} ({w}×{h})")
    return out


def run_export(job: Job, ctx) -> None:
    opts = job.payload
    ids = opts["photo_ids"]
    rows = [_photo(ctx, pid) for pid in ids]
    dest = validate_dest(ctx, opts["dest_dir"], rows)
    job.items_total = len(ids)
    job.say(f"exportando {len(ids)} fotos a {dest.name}/ ({opts['format']}, calidad {opts.get('quality', 97)}, "
            f"lado largo {opts.get('long_edge') or 'completo'})")
    t0 = time.time()
    start = int(opts.get("sequence_start", 1))
    for i, pid in enumerate(ids):
        job.check_cancel()
        span = (i / len(ids), (i + 1) / len(ids))
        run_item(job, pid, lambda pid=pid, i=i, span=span: export_one(ctx, job, pid, opts, start + i, dest, span))
        done = i + 1
        job.update(done / len(ids), f"exportando {done}/{len(ids)}",
                   eta_s=(time.time() - t0) / done * (len(ids) - done))
    job.result = {"dest_dir": str(dest), "exported": job.items_done, "failed": job.items_failed}
