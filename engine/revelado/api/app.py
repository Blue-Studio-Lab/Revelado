# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI application: the local engine API (contract in engine/README.md).

Binds to 127.0.0.1 only. Every route except the static web app requires the token (header ``X-Revelado-Token``
or ``?token=``). The token is random per start and written to ``engine/.runtime_token`` (mode 0600); in dev mode
(REVELADO_DEV=1) the literal ``dev`` is accepted too.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import __version__, diagnostics, films, library, metadata, profiles
from .. import recipe as recipe_mod
from ..catalog import Catalog
from ..config import REPO_DIR, Settings, get_settings
from ..cull import claude as claude_cull
from ..cull import technical
from ..develop import output_public, run_batch, run_export, run_sample
from ..errors import ReveladoError
from ..imaging import ImageStore
from ..jobs import EventBus, Job, JobManager
from ..runner import check_config
from ..log import get_logger, setup_logging

log = get_logger("api")


@dataclass
class Ctx:
    settings: Settings
    catalog: Catalog
    images: ImageStore
    bus: EventBus
    jobs: JobManager
    token: str


CTX: Ctx | None = None


def ctx() -> Ctx:
    assert CTX is not None
    return CTX


def build_ctx(settings: Settings | None = None) -> Ctx:
    s = settings or get_settings()
    setup_logging(s.log_dir)
    cat = Catalog(s.catalog_path)
    bus = EventBus()

    def persist(j: Job, finished: bool) -> None:
        cat.save_job(j.job_id, j.kind, j.state, {k: v for k, v in j.payload.items() if k != "recipe"} |
                     {"recipe": j.payload.get("recipe")}, j.public() if finished else None, finished)
    jm = JobManager(bus, persist)
    token = secrets.token_urlsafe(24)
    for tp in (s.runtime_token_path, s.user_token_path):
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(token)
            os.chmod(tp, 0o600)
        except OSError:
            log.warning("could not write the runtime token file %s", tp)
    c = Ctx(s, cat, ImageStore(s.cache_dir), bus, jm, token)
    jm.register("sample", lambda j: run_sample(j, c))
    jm.register("batch", lambda j: run_batch(j, c))
    jm.register("export", lambda j: run_export(j, c))
    jm.register("cull_technical", lambda j: technical.run(j, c, j.payload["session_id"], j.payload.get("photo_ids")))
    jm.register("cull_claude", lambda j: claude_cull.run(j, c, j.payload["session_id"], j.payload.get("photo_ids")))
    return c


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CTX
    if CTX is None:
        CTX = build_ctx()
    CTX.bus.bind(asyncio.get_running_loop())
    s = CTX.settings
    log.info("Revelado engine %s on %s:%s (dev=%s) cache=%s develop=%s films=%s", __version__, s.host, s.port,
             s.dev, s.cache_dir, check_config(s), "ok" if s.films_dir else "missing")
    yield
    CTX.jobs.shutdown()


app = FastAPI(title="Revelado engine", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|tauri://localhost)$",
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Mask-Stats", "X-Display-Transform", "X-Preview-Source", "X-Crop-Box"])


def _authorised(token: str | None) -> bool:
    c = ctx()
    if not token:
        return False
    if secrets.compare_digest(token, c.token):
        return True
    return c.settings.dev and token == "dev"


@app.middleware("http")
async def auth_and_errors(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and request.method != "OPTIONS":
        tok = request.headers.get("x-revelado-token") or request.query_params.get("token")
        if not _authorised(tok):
            return JSONResponse({"detail": "Token inválido o ausente.", "error": {
                "code": "E_AUTH", "message_es": "Token inválido o ausente.", "message_en": "Missing or invalid token.",
                "detail": "", "retryable": False}}, status_code=401)
    t0 = time.time()
    try:
        resp = await call_next(request)
    except ReveladoError as e:
        resp = JSONResponse(e.body(), status_code=e.status)
    except ValidationError as e:
        resp = JSONResponse(ReveladoError("E_BAD_INPUT", _val_msg(e)).body(), status_code=422)
    except Exception:  # noqa: BLE001
        log.exception("unhandled error on %s %s", request.method, path)
        e = ReveladoError("E_INTERNAL")
        resp = JSONResponse(e.body(), status_code=500)
    dt = (time.time() - t0) * 1000
    if dt > 1500:
        log.info("slow request %s %s %.0f ms", request.method, path, dt)
    return resp


def _val_msg(e) -> str:
    try:
        return "; ".join(f"{'.'.join(str(x) for x in d.get('loc', ()) if x != 'body')}: {d.get('msg')}"
                         for d in e.errors())[:400]
    except Exception:  # noqa: BLE001
        return str(e)[:400]


@app.exception_handler(RequestValidationError)
async def _reqval(request: Request, e: RequestValidationError):
    return JSONResponse(ReveladoError("E_BAD_INPUT", _val_msg(e)).body(), status_code=422)


@app.exception_handler(ReveladoError)
async def _derr(request: Request, e: ReveladoError):
    return JSONResponse(e.body(), status_code=e.status)


@app.exception_handler(Exception)
async def _anyerr(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(ReveladoError("E_INTERNAL").body(), status_code=500)


# ------------------------------------------------------------------------------------------------ helpers
def _photo(pid: str) -> dict:
    r = ctx().catalog.photo(pid)
    if not r:
        raise ReveladoError("E_NOT_FOUND", "foto desconocida")
    return dict(r) | {"meta": json.loads(r["meta_json"] or "{}")}


def _session(sid: str):
    s = ctx().catalog.session(sid)
    if not s:
        raise ReveladoError("E_SESSION_UNKNOWN")
    return s


def _jpeg_file(p: Path, headers: dict | None = None) -> FileResponse:
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", **(headers or {})})


def _public_photo(r, out_counts: dict[str, int]) -> dict:
    m = json.loads(r["meta_json"] or "{}")
    rot = int(m.get("orientation") or 1) in (5, 6, 7, 8)
    cull = None
    if r["cull_json"] or r["claude_json"]:
        cull = {"technical": json.loads(r["cull_json"]) if r["cull_json"] else None,
                "claude": json.loads(r["claude_json"]) if r["claude_json"] else None}
    return {"id": r["id"], "name": r["name"], "raw_path": r["raw_path"], "jpg_path": r["jpg_path"],
            "has_raw": bool(r["raw_path"]), "has_jpg": bool(r["jpg_path"]),
            # develop from the RAW; a JPG-only photo can only be developed with recipe develop_source="jpg"
            "can_develop": bool(r["raw_path"]), "develop_block_reason": None if r["raw_path"] else "needs_raw",
            "camera": m.get("camera"),
            "lens": m.get("lens"), "iso": m.get("iso"), "shutter": m.get("shutter"), "aperture": m.get("aperture"),
            "focal": m.get("focal"), "datetime": m.get("datetime"),
            # width/height are as DISPLAYED (orientation applied): the coordinate space of /crop
            "width": (m.get("height") if rot else m.get("width")), "height": (m.get("width") if rot else m.get("height")),
            "sensor_width": m.get("width"), "sensor_height": m.get("height"),
            "orientation": m.get("orientation"), "picture_profile": m.get("picture_profile"),
            "is_log": bool(m.get("is_log")), "rating": r["rating"] or 0, "flag": r["flag"], "burst_id": r["burst_id"],
            "cull": cull, "outputs": out_counts.get(r["id"], 0), "metadata_error": m.get("error")}


# ------------------------------------------------------------------------------------------------ health / sessions
@app.get("/api/health")
def health():
    s = ctx().settings
    lc = check_config(s)
    return {"ok": True, "version": __version__, "rawtherapee": bool(s.rawtherapee), "exiftool": bool(s.exiftool),
            "ai": {"provider": "anthropic" if s.anthropic_key() else None, "key_configured": bool(s.anthropic_key())},
            "develop_engine": {"available": lc["ok"], "engine": lc.get("engine"), "raw": lc.get("raw", False),
                               "reason": lc.get("reason"), "films": sorted(films.SUPPORTED_BY_ENGINE)},
            "films_dir": bool(s.films_dir and s.films_dir.is_dir()), "dev": s.dev,
            "capabilities": {"develop_jpg": True, "tiff16_rotation": True, "raw_only_ok": True, "jpg_only_ok": True},
            "jobs_active": len(ctx().jobs.active()),
            # the user's home folder, so the web UI can expand "~/..." before sending a path (local engine only)
            "home": str(Path.home()),
            "disk_free_gb": round(shutil.disk_usage(s.cache_dir).free / 1e9, 1)}


class OpenBody(BaseModel):
    path: str
    include_hidden: bool = False
    rescan: bool = True


@app.post("/api/library/open")
async def library_open(body: OpenBody):
    return await run_in_threadpool(library.open_library, ctx().catalog, ctx().settings, body.path, body.include_hidden)


@app.get("/api/sessions")
def sessions():
    return [{"session_id": r["session_id"], "path": r["path"], "name": r["name"], "count": r["count"],
             "opened_at": r["opened_at"]} for r in ctx().catalog.sessions()]


@app.get("/api/photos")
def photos(session_id: str):
    _session(session_id)
    c = ctx().catalog
    counts = c.output_counts(session_id)
    return [_public_photo(r, counts) for r in c.photos(session_id)]


# ------------------------------------------------------------------------------------------------ images
def _display(v: str) -> str:
    if v not in ("auto", "camera"):
        raise ReveladoError("E_BAD_INPUT", "display debe ser auto|camera")
    return v


@app.get("/api/photos/{pid}/thumb")
async def thumb(pid: str, size: int = 400, display: str = "auto"):
    p = _photo(pid)
    size = int(min(max(size, 64), 1024))
    path, mode = await run_in_threadpool(ctx().images.cached_jpeg, p, "thumb", size, _display(display))
    return _jpeg_file(path, {"X-Display-Transform": mode})


@app.get("/api/photos/{pid}/preview")
async def preview(pid: str, size: int = 2048, display: str = "auto"):
    p = _photo(pid)
    size = int(min(max(size, 256), 2048))
    path, mode = await run_in_threadpool(ctx().images.cached_jpeg, p, "preview", size, _display(display))
    return _jpeg_file(path, {"X-Display-Transform": mode, "X-Preview-Source": ctx().images.preview_source(p)})


@app.get("/api/photos/{pid}/crop")
async def crop(pid: str, x: int, y: int, w: int, h: int, display: str = "auto"):
    p = _photo(pid)
    data, mode, box = await run_in_threadpool(ctx().images.crop, p, x, y, w, h, _display(display))
    # X-Preview-Source: where the pixels come from (camera_jpg | embedded | quick_render | raw_full)
    return Response(data, media_type="image/jpeg", headers={"X-Display-Transform": mode, "X-Crop-Box": json.dumps(box),
                                                            "X-Preview-Source": box["source"],
                                                            "Cache-Control": "private, max-age=3600"})


@app.get("/api/photos/{pid}/exif")
async def exif(pid: str):
    p = _photo(pid)
    src = p["raw_path"] or p["jpg_path"]
    full = await run_in_threadpool(metadata.full_exif, ctx().settings.exiftool, Path(src))
    return {"normalised": {k: v for k, v in p["meta"].items() if k not in ("body_hash", "lens_hash")},
            "source": Path(src).name, "tags": full}


@app.get("/api/photos/{pid}/profile")
def profile(pid: str):
    p = _photo(pid)
    if p["meta"].get("error"):
        raise ReveladoError("E_RAW_DECODE", "sin metadatos para esta foto")
    return profiles.profile_for(p["meta"])


@app.get("/api/photos/{pid}/mask/{kind}")
async def mask(pid: str, kind: str, size: int = 1600):
    p = _photo(pid)
    jpeg = None
    if not p.get("raw_path"):
        jpeg = ctx().images.source_jpeg(p)
    path, stats = await run_in_threadpool(diagnostics.compute, p, kind, size, ctx().settings.cache_dir, jpeg)
    return FileResponse(path, media_type="image/jpeg",
                        headers={"X-Mask-Stats": json.dumps(stats, ensure_ascii=True, separators=(",", ":")),
                                 "Cache-Control": "private, max-age=3600"})


@app.get("/api/photos/{pid}/mask/{kind}/stats")
async def mask_stats(pid: str, kind: str, size: int = 1600):
    p = _photo(pid)
    jpeg = None if p.get("raw_path") else ctx().images.source_jpeg(p)
    _, stats = await run_in_threadpool(diagnostics.compute, p, kind, size, ctx().settings.cache_dir, jpeg)
    return stats


class RatingBody(BaseModel):
    rating: int | None = Field(default=None, ge=0, le=5)
    flag: Literal["pick", "reject"] | None = None


@app.post("/api/photos/{pid}/rating")
async def rating(pid: str, request: Request):
    _photo(pid)
    try:
        raw = await request.json()
    except ValueError:
        raise ReveladoError("E_BAD_INPUT", "JSON inválido") from None
    if not isinstance(raw, dict):
        raise ReveladoError("E_BAD_INPUT", "se esperaba un objeto")
    body = RatingBody.model_validate(raw)
    ctx().catalog.set_rating(pid, body.rating, body.flag, "flag" in raw)
    r = ctx().catalog.photo(pid)
    ev = {"type": "photo_updated", "photo_id": pid, "rating": r["rating"], "flag": r["flag"]}
    ctx().bus.emit(ev)
    return {"ok": True, "id": pid, "rating": r["rating"], "flag": r["flag"]}


@app.get("/api/photos/{pid}/cull")
def photo_cull(pid: str):
    r = ctx().catalog.photo(pid)
    if not r:
        raise ReveladoError("E_NOT_FOUND", "foto desconocida")
    return {"technical": json.loads(r["cull_json"]) if r["cull_json"] else None,
            "claude": json.loads(r["claude_json"]) if r["claude_json"] else None}


@app.get("/api/photos/{pid}/outputs")
def photo_outputs(pid: str):
    _photo(pid)
    return [output_public(r) for r in ctx().catalog.outputs(pid)]


@app.get("/api/outputs/{oid}/{variant}")
def output_file(oid: str, variant: str):
    r = ctx().catalog.output(oid)
    if not r:
        raise ReveladoError("E_NOT_FOUND", "resultado desconocido")
    col = {"full.jpg": "full_path", "web.jpg": "web_path"}.get(variant)
    if not col:
        raise ReveladoError("E_BAD_INPUT", "variante: full.jpg | web.jpg")
    p = Path(r[col] or "")
    if not p.exists():
        raise ReveladoError("E_NOT_FOUND", "el archivo del resultado ya no existe en la caché")
    return _jpeg_file(p)


# ------------------------------------------------------------------------------------------------ cull
class CullBody(BaseModel):
    session_id: str
    mode: Literal["technical", "claude"] = "technical"
    photo_ids: list[str] | None = None
    allow_cloud: bool = False


@app.post("/api/cull/auto")
def cull_auto(body: CullBody):
    _session(body.session_id)
    if body.mode == "claude":
        if not ctx().settings.anthropic_key():
            raise ReveladoError("E_AI_NO_KEY")
        if not body.allow_cloud:
            raise ReveladoError("E_AI_CONSENT")
        j = ctx().jobs.submit("cull_claude", body.model_dump(), priority=3)
    else:
        j = ctx().jobs.submit("cull_technical", body.model_dump(), priority=3)
    return {"job_id": j.job_id}


@app.get("/api/cull/estimate")
def cull_estimate(session_id: str, photo_ids: str | None = None):
    _session(session_id)
    n = len(photo_ids.split(",")) if photo_ids else len(ctx().catalog.photos(session_id))
    return claude_cull.estimate(n) | {"key_configured": bool(ctx().settings.anthropic_key())}


# ------------------------------------------------------------------------------------------------ films
@app.get("/api/films")
def films_list():
    return films.list_films(ctx().settings.films_dir)


@app.get("/api/films/{fid}/swatch")
async def film_swatch(fid: str, size: int = 600):
    size = int(min(max(size, 128), 1200))
    p = await run_in_threadpool(films.swatch, ctx().settings.films_dir, fid, ctx().settings.cache_dir, size)
    return _jpeg_file(p)


# ------------------------------------------------------------------------------------------------ develop
@app.get("/api/recipe/default")
def recipe_default():
    return dict(recipe_mod.DEFAULT)


@app.get("/api/recipe/schema")
def recipe_schema():
    return recipe_mod.SCHEMA


@app.get("/api/recipe/presets")
def recipe_presets():
    return [{"id": k, "label_es": recipe_mod.PRESET_LABELS[k]["es"], "label_en": recipe_mod.PRESET_LABELS[k]["en"],
             "recipe": dict(v), "default": k == "neutral"} for k, v in recipe_mod.PRESETS.items()]


class SampleBody(BaseModel):
    photo_id: str
    recipe: dict[str, Any] | None = None
    tiff16: bool = False


class BatchBody(BaseModel):
    photo_ids: list[str]
    recipe: dict[str, Any] | None = None
    tiff16: bool = False
    # per-photo partial recipes merged over the batch recipe, e.g. {"<photo_id>": {"skin_retouch": 60}}
    overrides: dict[str, dict[str, Any]] | None = None


def _precheck_develop(rcp: dict, ids: list[str]) -> None:
    from ..runner import ensure_film, source_for
    lc = check_config(ctx().settings)
    if not lc["ok"]:
        raise ReveladoError("E_LEGACY_MISSING", lc["reason"])
    ensure_film(rcp)
    for pid in ids:
        p = _photo(pid)
        try:
            src = source_for(p, rcp)
        except ReveladoError as e:
            e.extra.setdefault("photo_id", pid)
            raise
        if src == "raw" and not lc.get("raw", True):
            raise ReveladoError("E_TOOL_MISSING", lc.get("reason", "rawtherapee-cli no encontrado"),
                                extra={"photo_id": pid})


@app.post("/api/develop/sample")
def develop_sample(body: SampleBody):
    rcp = recipe_mod.normalise(body.recipe)
    _precheck_develop(rcp, [body.photo_id])
    j = ctx().jobs.submit("sample", {"photo_id": body.photo_id, "recipe": rcp, "tiff16": body.tiff16}, priority=1)
    return {"job_id": j.job_id, "recipe": rcp}


@app.post("/api/develop/batch")
def develop_batch(body: BatchBody):
    if not body.photo_ids:
        raise ReveladoError("E_BAD_INPUT", "photo_ids vacío")
    rcp = recipe_mod.normalise(body.recipe)
    ids = list(dict.fromkeys(body.photo_ids))
    unknown = sorted(set(body.overrides or {}) - set(ids))
    if unknown:
        raise ReveladoError("E_BAD_INPUT", "overrides para fotos que no están en photo_ids",
                            extra={"photo_ids": unknown})
    recipes = {}
    for pid in ids:
        ov = (body.overrides or {}).get(pid)
        r = recipe_mod.normalise({**(body.recipe or {}), **ov}) if ov else rcp
        _precheck_develop(r, [pid])
        recipes[pid] = r
    j = ctx().jobs.submit("batch", {"photo_ids": ids, "recipe": rcp, "recipes": recipes,
                                    "overrides": body.overrides or {}, "tiff16": body.tiff16}, priority=5)
    return {"job_id": j.job_id, "recipe": rcp,
            "recipes": {pid: r for pid, r in recipes.items() if pid in (body.overrides or {})}}


class ExportBody(BaseModel):
    photo_ids: list[str]
    dest_dir: str | None = None
    dest: str | None = None
    format: Literal["jpg", "tiff16"] = "jpg"
    quality: int = Field(default=97, ge=1, le=100)
    long_edge: int | None = Field(default=None, ge=256, le=20000)
    naming: Literal["original", "original_film", "sequence"] = "original"
    sequence_start: int = Field(default=1, ge=0)
    recipe: dict[str, Any] | None = None
    develop_missing: bool = True
    write_recipe_json: bool = False


@app.post("/api/export")
def export(body: ExportBody):
    if not body.photo_ids:
        raise ReveladoError("E_BAD_INPUT", "photo_ids vacío")
    dest = body.dest_dir or body.dest
    if not dest:
        raise ReveladoError("E_BAD_INPUT", "falta dest_dir")
    rows = [_photo(pid) for pid in body.photo_ids]
    from ..develop import validate_dest
    dest = str(validate_dest(ctx(), dest, rows))          # fail fast, before queueing; "~/..." is expanded
    payload = body.model_dump() | {"dest_dir": dest, "photo_ids": list(dict.fromkeys(body.photo_ids))}
    payload["recipe"] = recipe_mod.normalise(body.recipe) if body.recipe else None
    if payload["recipe"]:
        from ..runner import ensure_film
        ensure_film(payload["recipe"])
    j = ctx().jobs.submit("export", payload, priority=5)
    return {"job_id": j.job_id}


# ------------------------------------------------------------------------------------------------ jobs + events
@app.get("/api/jobs/{job_id}")
def job_get(job_id: str):
    j = ctx().jobs.get(job_id)
    if j:
        return j.public()
    old = ctx().catalog.job(job_id)
    if old and old.get("result_json"):
        d = json.loads(old["result_json"])
        if d:
            return d
    if old:
        return {"job_id": job_id, "kind": old["kind"], "state": "error" if old["state"] in ("queued", "running")
                else old["state"], "progress": 0, "stage": None, "eta_s": None, "outputs": [],
                "log": ["trabajo interrumpido: el motor se reinició"], "errors": [], "items_done": 0, "items_failed": 0}
    raise ReveladoError("E_NOT_FOUND", "trabajo desconocido")


@app.get("/api/jobs")
def jobs_list(active: int = 0):
    js = ctx().jobs.active() if active else list(ctx().jobs.jobs.values())
    return [j.public() | {"log": j.log[-5:]} for j in sorted(js, key=lambda j: j.created_at, reverse=True)]


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    if not ctx().jobs.get(job_id):
        raise ReveladoError("E_NOT_FOUND", "trabajo desconocido")
    return {"ok": ctx().jobs.cancel(job_id)}


@app.websocket("/api/events")
async def events(ws: WebSocket):
    tok = ws.headers.get("x-revelado-token") or ws.query_params.get("token")
    if not _authorised(tok):
        await ws.close(code=4401)
        return
    await ws.accept()
    q = ctx().bus.subscribe()
    try:
        await ws.send_json({"type": "hello", "version": __version__})
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=20)
                await ws.send_json(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "ts": time.time()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        ctx().bus.unsubscribe(q)


# ------------------------------------------------------------------------------------------------ built web app (optional)
_DIST = REPO_DIR / "apps" / "web" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def web_app(path: str):
    if path.startswith("api/"):
        raise ReveladoError("E_NOT_FOUND", "ruta desconocida")
    if not _DIST.is_dir():
        return JSONResponse({"name": "Revelado engine", "version": __version__,
                             "note": "La interfaz web se sirve con 'npm run dev' en apps/web (o compílala en apps/web/dist)."})
    f = (_DIST / path).resolve()
    if path and f.is_file() and _DIST.resolve() in f.parents:
        return FileResponse(f)
    return FileResponse(_DIST / "index.html")
