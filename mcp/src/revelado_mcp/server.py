# SPDX-License-Identifier: GPL-3.0-or-later
"""The Revelado MCP server: tools that drive a running Revelado engine over its local HTTP API.

Design rules:

* Only allow-listed folders are opened; exports only go to allow-listed export folders and never into a photo
  source folder. No tool takes an arbitrary file path for reading, and no tool deletes anything.
* Photos are addressed by the opaque ids the engine returns. A photo id is accepted only if it belongs to a
  session (folder) that is inside the allow-list.
* Heavy work (culling, develop, export) is queued in the engine's single heavy-job queue. On top of that, one
  connection may have at most ONE heavy job in flight, so an agent cannot flood the queue.
* Images given to the model are <= 1024 px and carry no metadata (no EXIF / GPS).
* ``export`` writes files, so it requires ``confirm=true`` and is annotated as destructive.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import httpx
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .config import Config, ConfigError, canonical, is_within
from .engine import EngineClient, EngineError
from .images import MAX_PREVIEW_PX, prepare_jpeg
from .naming import APP_NAME, SERVER_NAME
from .recipes import unsupported_fields, validate_patch

TERMINAL_STATES = {"done", "error", "cancelled"}
HEAVY_KINDS = {"sample", "batch", "export", "cull_technical"}
DROP_TAG_PREFIXES = ("GPS", "Composite:GPS", "XMP:GPS", "EXIF:GPS")
DROP_TAG_WORDS = ("serialnumber", "ownername", "artist", "copyright", "usercomment", "thumbnailimage",
                  "previewimage", "jpgfromraw")

INSTRUCTIONS = f"""\
{APP_NAME} is a local RAW photo developer. These tools drive the {APP_NAME} engine running on this computer.
Workflow rules:
1. Call engine_status first; open a folder with open_folder (only allow-listed folders work).
2. Look before you judge: list_photos, then get_preview (small, <=1024 px) and diagnose for the frames you need.
   Use auto_cull_technical (local, free) before looking at many frames yourself.
3. Develop ONE sample first (develop_sample shows before/after). Adjust with patch_recipe, re-sample, and only
   then run develop_batch. One heavy job at a time: wait with job_status.
4. Originals are never modified. export writes new files into an allow-listed export folder and needs
   confirm=true - ask the user before exporting.
5. Default recipe values may include a film look; if the user asked for an untouched/neutral result, set
   film_strength 0, grain 0, skin_retouch 0 and blemishes 0 with patch_recipe before developing.
"""


@dataclass
class SessionInfo:
    session_id: str
    path: Path
    name: str


@dataclass
class State:
    sessions: dict[str, SessionInfo] = field(default_factory=dict)
    photo_session: dict[str, str] = field(default_factory=dict)          # photo_id -> session_id
    photo_rows: dict[str, list[dict]] = field(default_factory=dict)      # session_id -> rows (last listing)
    recipes: dict[str, dict] = field(default_factory=dict)               # session_id | "_default" -> recipe
    own_jobs: dict[str, str] = field(default_factory=dict)               # job_id -> kind
    heavy_job: str | None = None
    schema: dict | None = None


def _ro(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                           open_world_hint=False)


def _rw(title: str, *, idempotent: bool = False, destructive: bool = False) -> ToolAnnotations:
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=destructive,
                           idempotent_hint=idempotent, open_world_hint=False)


def _j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def _compact_cull(c: Any) -> dict | None:
    if not isinstance(c, dict):
        return None
    t = c.get("technical") or {}
    cl = c.get("claude") or {}
    out = {}
    if t:
        out["technical"] = {k: t.get(k) for k in ("score", "verdict", "reasons", "burst_id", "burst_best",
                                                  "sharpness_af", "faces", "eyes_open") if k in t}
        exp = t.get("exposure")
        if isinstance(exp, dict):
            out["technical"]["exposure"] = exp.get("verdict")
    if cl:
        out["ai"] = {k: cl.get(k) for k in ("score", "verdict", "reasons", "pick") if k in cl}
    return out or None


def _public_row(r: dict) -> dict:
    """Photo row without filesystem paths."""
    return {"id": r.get("id"), "name": r.get("name"), "camera": r.get("camera"), "lens": r.get("lens"),
            "iso": r.get("iso"), "shutter": r.get("shutter"), "aperture": r.get("aperture"), "focal": r.get("focal"),
            "datetime": r.get("datetime"), "width": r.get("width"), "height": r.get("height"),
            "rating": r.get("rating") or 0, "flag": r.get("flag"), "has_raw": bool(r.get("has_raw")),
            "has_jpg": bool(r.get("has_jpg", r.get("jpg_path"))), "is_log": bool(r.get("is_log")),
            "burst_id": r.get("burst_id"), "developed": r.get("outputs") or 0, "cull": _compact_cull(r.get("cull"))}


def _clean_tags(tags: Any, limit: int) -> dict:
    if not isinstance(tags, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in tags.items():
        ks = str(k)
        bare = ks.split(":")[-1]
        if bare.startswith("GPS") or ks.startswith(DROP_TAG_PREFIXES) or any(w in ks.lower() for w in DROP_TAG_WORDS):
            continue
        if "path" in ks.lower() or "directory" in ks.lower() or ks.lower().endswith("filename"):
            continue
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "..."
        out[ks] = v
        if len(out) >= limit:
            break
    return out


def _job_summary(j: dict, log_lines: int = 15) -> dict:
    outs = []
    for o in j.get("outputs") or []:
        if isinstance(o, dict):
            outs.append({k: o[k] for k in ("photo_id", "output_id", "path") if k in o})
    s = {k: j.get(k) for k in ("job_id", "kind", "state", "progress", "stage", "eta_s", "items_total",
                               "items_done", "items_failed") if k in j}
    s["errors"] = j.get("errors") or []
    s["outputs"] = outs
    s["log_tail"] = (j.get("log") or [])[-log_lines:]
    return s


def build_server(config: Config, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> MCPServer:
    """Build the MCP server. ``transport`` lets tests swap in a fake engine."""
    logging.getLogger("httpx").setLevel(logging.WARNING)      # don't log every engine request URL
    engine = EngineClient(config, transport)
    st = State()
    mcp = MCPServer(SERVER_NAME, title=f"{APP_NAME} MCP server", version=__version__, instructions=INSTRUCTIONS,
                    description=f"Drive the local {APP_NAME} RAW developer: cull, diagnose, develop, export.")

    # ------------------------------------------------------------------------------------------ helpers
    def wrap_cfg(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ConfigError as e:
            raise ToolError(f"[E_NOT_ALLOWED] {e}") from None

    async def admit_session(sid: str) -> SessionInfo:
        if sid in st.sessions:
            return st.sessions[sid]
        for s in await engine.get_json("/api/sessions"):
            if s.get("session_id") == sid:
                try:
                    p = canonical(s.get("path") or "")
                except ConfigError:
                    break
                if config.allowed_dirs and any(is_within(p, r) for r in config.allowed_dirs):
                    info = SessionInfo(sid, p, s.get("name") or p.name)
                    st.sessions[sid] = info
                    return info
                raise ToolError("[E_NOT_ALLOWED] that session's folder is outside the allow-listed photo folders.")
        raise ToolError(f"[E_SESSION_UNKNOWN] unknown session '{sid}'. Call open_folder or list_sessions first.")

    async def refresh_photos(sid: str) -> list[dict]:
        rows = await engine.get_json("/api/photos", session_id=sid)
        st.photo_rows[sid] = rows
        for r in rows:
            st.photo_session[r["id"]] = sid
        return rows

    async def photo_session(pid: str) -> str:
        if pid in st.photo_session:
            return st.photo_session[pid]
        for sid in list(st.sessions):
            await refresh_photos(sid)
            if pid in st.photo_session:
                return st.photo_session[pid]
        raise ToolError(f"[E_NOT_FOUND] photo '{pid}' is not in any folder opened through this server. "
                        "Use open_folder and list_photos to get photo ids.")

    async def photo_row(pid: str) -> dict:
        sid = await photo_session(pid)
        for r in st.photo_rows.get(sid) or await refresh_photos(sid):
            if r["id"] == pid:
                return r
        raise ToolError(f"[E_NOT_FOUND] photo '{pid}' no longer exists in its folder.")

    async def schema() -> dict:
        if st.schema is None:
            st.schema = await engine.get_json("/api/recipe/schema")
        return st.schema

    async def working_recipe(key: str) -> dict:
        if key not in st.recipes:
            st.recipes[key] = dict(await engine.get_json("/api/recipe/default"))
        return st.recipes[key]

    async def guard_heavy() -> None:
        jid = st.heavy_job
        if not jid:
            return
        try:
            j = await engine.get_json(f"/api/jobs/{jid}")
        except EngineError as e:
            if e.code == "E_NOT_FOUND":
                st.heavy_job = None
                return
            raise
        if j.get("state") in TERMINAL_STATES:
            st.heavy_job = None
            return
        raise ToolError(f"[E_BUSY] heavy job {jid} ({j.get('kind')}) from this connection is still {j.get('state')} "
                        f"({round(100 * (j.get('progress') or 0))}%). Wait with job_status or stop it with "
                        "cancel_job; only one heavy job at a time.")

    def register(job_id: str, kind: str) -> None:
        st.own_jobs[job_id] = kind
        if kind in HEAVY_KINDS:
            st.heavy_job = job_id

    async def wait_job(job_id: str, wait_s: float) -> dict:
        deadline = time.monotonic() + min(max(wait_s, 0.0), config.max_wait_s)
        while True:
            j = await engine.get_json(f"/api/jobs/{job_id}")
            if j.get("state") in TERMINAL_STATES:
                if st.heavy_job == job_id:
                    st.heavy_job = None
                return j
            if time.monotonic() >= deadline:
                return j
            await sleep(config.poll_interval_s)

    async def image_of(path: str, max_px: int, **params: Any) -> tuple[Image, tuple[int, int]]:
        data, _ = await engine.get_bytes(path, **params)
        jpeg, size = prepare_jpeg(data, max_px)
        return Image(data=jpeg, format="jpeg"), size

    async def photo_ids_check(ids: list[str]) -> tuple[list[str], set[str]]:
        if not ids:
            raise ToolError("[E_BAD_INPUT] photo_ids is empty")
        ids = list(dict.fromkeys(ids))
        return ids, {await photo_session(pid) for pid in ids}

    # ------------------------------------------------------------------------------------------ tools
    @mcp.tool(annotations=_ro("Engine status"), structured_output=False)
    async def engine_status() -> dict:
        """Check that the Revelado engine is running and what it can do (RAW decoder, develop engine, trained
        films, active jobs), plus which folders this server may open and export to."""
        h = await engine.get_json("/api/health")
        dev = h.get("develop_engine") or {}
        return {"engine": {"ok": h.get("ok"), "version": h.get("version"), "rawtherapee": h.get("rawtherapee"),
                           "exiftool": h.get("exiftool"), "develop_available": dev.get("available"),
                           "develop_unavailable_reason": dev.get("reason"), "trained_films": dev.get("films"),
                           "jobs_active": h.get("jobs_active"), "disk_free_gb": h.get("disk_free_gb")},
                "mcp": {"version": __version__, **config.public(), "heavy_job_in_flight": st.heavy_job}}

    @mcp.tool(annotations=_ro("List opened folders"), structured_output=False)
    async def list_sessions() -> dict:
        """Folders the engine has opened before (most recent first) that are inside the allow-list."""
        out = []
        for s in await engine.get_json("/api/sessions"):
            try:
                p = canonical(s.get("path") or "")
            except ConfigError:
                continue
            if any(is_within(p, r) for r in config.allowed_dirs):
                st.sessions.setdefault(s["session_id"], SessionInfo(s["session_id"], p, s.get("name") or p.name))
                out.append({k: s.get(k) for k in ("session_id", "name", "path", "count", "opened_at")})
        return {"sessions": out}

    @mcp.tool(annotations=_rw("Open folder", idempotent=True), structured_output=False)
    async def open_folder(
        path: Annotated[str, Field(description="Absolute path of a photo folder inside an allow-listed folder.")],
        rescan: Annotated[bool, Field(description="Re-scan the folder for new files.")] = True,
    ) -> dict:
        """Open (or re-open) a folder of RAW/JPEG photos. Returns a session_id. Re-opening the same folder returns
        the same session with ratings, culling and develops restored. Originals are never modified."""
        p = wrap_cfg(lambda: config.check_source(path))
        if not p.is_dir():
            raise ToolError(f"[E_NOT_FOUND] not a folder: {p}")
        r = await engine.post_json("/api/library/open", {"path": str(p), "rescan": rescan})
        sid = r["session_id"]
        st.sessions[sid] = SessionInfo(sid, p, r.get("name") or p.name)
        await refresh_photos(sid)
        return {k: r.get(k) for k in ("session_id", "name", "count", "pairs", "raw_only", "jpg_only", "restored")} | {
            "next": "list_photos(session_id=...) to see the frames; auto_cull_technical for a first pass."}

    @mcp.tool(annotations=_ro("List photos"), structured_output=False)
    async def list_photos(
        session_id: str,
        min_rating: Annotated[int, Field(ge=0, le=5, description="Only photos rated at least this.")] = 0,
        flag: Annotated[Literal["any", "pick", "reject", "unflagged"], Field(description="Filter by flag.")] = "any",
        verdict: Annotated[Literal["any", "keep", "maybe", "reject", "unculled"],
                           Field(description="Filter by technical cull verdict.")] = "any",
        has_raw: Annotated[bool | None, Field(description="true = only RAW(+JPG) photos; false = JPEG-only.")] = None,
        camera: Annotated[str | None, Field(description="Case-insensitive substring of the camera model.")] = None,
        burst_best_only: Annotated[bool, Field(description="Keep only the best frame of each burst.")] = False,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> dict:
        """List the photos of an opened folder with EXIF summary, rating, flag and cull result. Filters combine
        with AND. Paths are not returned; use the photo ids with the other tools."""
        await admit_session(session_id)
        rows = await refresh_photos(session_id)
        sel = []
        for r in rows:
            tech = ((r.get("cull") or {}).get("technical") or {}) if isinstance(r.get("cull"), dict) else {}
            if (r.get("rating") or 0) < min_rating:
                continue
            if flag == "unflagged" and r.get("flag"):
                continue
            if flag in ("pick", "reject") and r.get("flag") != flag:
                continue
            if verdict == "unculled" and tech:
                continue
            if verdict in ("keep", "maybe", "reject") and tech.get("verdict") != verdict:
                continue
            if has_raw is not None and bool(r.get("has_raw")) != has_raw:
                continue
            if camera and camera.lower() not in str(r.get("camera") or "").lower():
                continue
            if burst_best_only and r.get("burst_id") and tech and not tech.get("burst_best"):
                continue
            sel.append(r)
        page = sel[offset:offset + limit]
        return {"session_id": session_id, "total": len(rows), "matched": len(sel), "offset": offset,
                "returned": len(page), "photos": [_public_row(r) for r in page]}

    @mcp.tool(annotations=_ro("Photo info"), structured_output=False)
    async def get_photo_info(
        photo_id: str,
        max_tags: Annotated[int, Field(ge=0, le=400, description="How many raw EXIF tags to include.")] = 60,
    ) -> dict:
        """EXIF summary, camera/lens profile with recommended corrections (noise, CA, sharpening, exposure hint,
        log profile) and the cull result for one photo. Location (GPS), serial numbers and owner tags are removed."""
        row = await photo_row(photo_id)
        out: dict[str, Any] = {"photo": _public_row(row)}
        ex = await engine.get_json(f"/api/photos/{photo_id}/exif")
        norm = {k: v for k, v in (ex.get("normalised") or {}).items()
                if "path" not in k.lower() and "gps" not in k.lower() and "serial" not in k.lower()}
        out["exif"] = norm
        if max_tags:
            out["exif_tags"] = _clean_tags(ex.get("tags"), max_tags)
        try:
            out["profile"] = await engine.get_json(f"/api/photos/{photo_id}/profile")
        except EngineError as e:
            out["profile"] = {"error": str(e)}
        out["cull"] = await engine.get_json(f"/api/photos/{photo_id}/cull")
        return out

    @mcp.tool(annotations=_ro("Preview image"), structured_output=False)
    async def get_preview(
        photo_id: str,
        size: Annotated[int, Field(ge=128, le=MAX_PREVIEW_PX, description="Long edge in px (max 1024).")] = 1024,
        source: Annotated[Literal["camera", "developed"],
                          Field(description="camera = embedded/camera JPEG; developed = latest develop result.")] = "camera",
        display: Annotated[Literal["auto", "camera"],
                           Field(description="auto makes flat log (S-Log3) JPEGs viewable; camera = untouched.")] = "auto",
    ) -> list:
        """A small preview image (<= 1024 px, metadata stripped) of one photo, to look at framing, expression,
        focus and exposure. Use sparingly: images cost context."""
        row = await photo_row(photo_id)
        if source == "developed":
            outs = await engine.get_json(f"/api/photos/{photo_id}/outputs")
            if not outs:
                raise ToolError(f"[E_NOT_FOUND] '{photo_id}' has not been developed yet (use develop_sample).")
            last = outs[-1]
            img, wh = await image_of(f"/api/outputs/{last['output_id']}/web.jpg", size)
            meta = {"photo_id": photo_id, "name": row.get("name"), "source": "developed",
                    "output_id": last["output_id"], "recipe": last.get("recipe"), "size": wh}
        else:
            img, wh = await image_of(f"/api/photos/{photo_id}/thumb", size, size=size, display=display)
            meta = {"photo_id": photo_id, "name": row.get("name"), "source": "camera", "size": wh}
        return [_j(meta), img]

    @mcp.tool(annotations=_ro("Diagnose noise / CA / shading"), structured_output=False)
    async def diagnose(
        photo_id: str,
        kinds: Annotated[list[Literal["noise", "ca", "shading"]],
                         Field(description="Which diagnostics to compute.")] = ["noise", "ca", "shading"],  # noqa: B006
        include_masks: Annotated[bool, Field(description="Also return the colour-coded mask images (<=1024 px).")] = False,
    ) -> list:
        """Technical diagnostics for one photo: noise level, chromatic aberration / fringing and colour shading
        statistics (computed by the engine on a reduced image), optionally with mask images."""
        await photo_row(photo_id)
        kinds = list(dict.fromkeys(kinds)) or ["noise"]
        stats: dict[str, Any] = {}
        images: list[Any] = []
        for k in kinds:
            stats[k] = await engine.get_json(f"/api/photos/{photo_id}/mask/{k}/stats")
            if include_masks:
                img, _ = await image_of(f"/api/photos/{photo_id}/mask/{k}", MAX_PREVIEW_PX, size=MAX_PREVIEW_PX)
                images += [f"mask: {k}", img]
        return [_j({"photo_id": photo_id, "stats": stats}), *images]

    @mcp.tool(annotations=_rw("Rate / flag", idempotent=True), structured_output=False)
    async def set_rating(
        photo_id: str,
        rating: Annotated[int | None, Field(ge=0, le=5, description="Stars 0-5 (omit to keep).")] = None,
        flag: Annotated[Literal["pick", "reject", "none"] | None,
                        Field(description="pick / reject / none (clear). Omit to keep.")] = None,
    ) -> dict:
        """Set the star rating and/or pick/reject flag of a photo (catalog only; files are not touched)."""
        await photo_row(photo_id)
        if rating is None and flag is None:
            raise ToolError("[E_BAD_INPUT] give rating and/or flag")
        body: dict[str, Any] = {}
        if rating is not None:
            body["rating"] = rating
        if flag is not None:
            body["flag"] = None if flag == "none" else flag
        r = await engine.post_json(f"/api/photos/{photo_id}/rating", body)
        sid = st.photo_session[photo_id]
        for row in st.photo_rows.get(sid, []):
            if row["id"] == photo_id:
                row["rating"], row["flag"] = r.get("rating"), r.get("flag")
        return {"photo_id": photo_id, "rating": r.get("rating"), "flag": r.get("flag")}

    @mcp.tool(annotations=_rw("Technical auto-cull", idempotent=True), structured_output=False)
    async def auto_cull_technical(
        session_id: str,
        photo_ids: Annotated[list[str] | None, Field(description="Subset; omit for the whole folder.")] = None,
        wait_seconds: Annotated[float, Field(ge=0, le=600, description="Wait up to this long for the result.")] = 0,
    ) -> dict:
        """Run the local technical cull (sharpness, faces/eyes, exposure, bursts) - free, no cloud. Writes a
        keep/maybe/reject verdict with reasons per photo; does not change ratings. Queued as a heavy job."""
        await admit_session(session_id)
        if photo_ids:
            for pid in photo_ids:
                if await photo_session(pid) != session_id:
                    raise ToolError(f"[E_BAD_INPUT] photo '{pid}' is not in session '{session_id}'")
        await guard_heavy()
        r = await engine.post_json("/api/cull/auto", {"session_id": session_id, "mode": "technical",
                                                      "photo_ids": photo_ids or None})
        register(r["job_id"], "cull_technical")
        j = await wait_job(r["job_id"], wait_seconds) if wait_seconds else {"job_id": r["job_id"], "state": "queued"}
        out = _job_summary(j) if wait_seconds else dict(j)
        if j.get("state") == "done":
            counts: dict[str, int] = {}
            for row in await refresh_photos(session_id):
                v = (((row.get("cull") or {}).get("technical")) or {}).get("verdict") or "unculled"
                counts[v] = counts.get(v, 0) + 1
            out["verdicts"] = counts
            out["next"] = "list_photos(verdict='keep') then get_preview to judge expression and moment."
        else:
            out["next"] = "job_status(job_id, wait_seconds=60) until state is done."
        return out

    @mcp.tool(annotations=_ro("List film looks"), structured_output=False)
    async def list_films() -> dict:
        """Film looks known to the engine and whether each one can be developed now ('trained')."""
        films = await engine.get_json("/api/films")
        return {"films": [{k: f.get(k) for k in ("id", "name", "type", "status", "tier", "scans") if k in f}
                          for f in films],
                "note": "Only films with status 'trained' can be used in a recipe today; others return E_FILM_UNTRAINED."}

    @mcp.tool(annotations=_ro("Get working recipe"), structured_output=False)
    async def get_recipe(
        session_id: Annotated[str | None, Field(description="Session whose working recipe to show; omit for the "
                                                            "default one.")] = None,
        include_schema: Annotated[bool, Field(description="Include field ranges/options.")] = True,
    ) -> dict:
        """The working develop recipe (what develop_sample / develop_batch will use) and its parameter schema."""
        if session_id:
            await admit_session(session_id)
        rcp = await working_recipe(session_id or "_default")
        out: dict[str, Any] = {"session_id": session_id, "recipe": rcp}
        if include_schema:
            sch = await schema()
            out["schema"] = {k: {kk: v.get(kk) for kk in ("type", "min", "max", "step", "options", "default",
                                                         "label_en", "supported", "note") if v.get(kk) is not None}
                             for k, v in sch.items()}
            out["ignored_by_engine"] = unsupported_fields(rcp, sch)
        return out

    @mcp.tool(annotations=_rw("Patch working recipe", idempotent=True), structured_output=False)
    async def patch_recipe(
        patch: Annotated[dict[str, Any], Field(description="Fields to change, e.g. {\"exposure_ev\": 0.3}.")],
        session_id: Annotated[str | None, Field(description="Session to edit; omit for the default recipe.")] = None,
        reset: Annotated[bool, Field(description="Start from the engine default before applying the patch.")] = False,
    ) -> dict:
        """Change the working recipe (validated against the schema; out-of-range values are rejected, not
        clamped). Nothing is rendered until develop_sample / develop_batch."""
        if session_id:
            await admit_session(session_id)
        key = session_id or "_default"
        sch = await schema()
        clean = validate_patch(patch, sch)
        if reset:
            st.recipes.pop(key, None)
        rcp = await working_recipe(key)
        changes = {k: {"from": rcp.get(k), "to": v} for k, v in clean.items() if rcp.get(k) != v}
        rcp.update(clean)
        return {"session_id": session_id, "recipe": rcp, "changed": changes,
                "ignored_by_engine": unsupported_fields(rcp, sch)}

    @mcp.tool(annotations=_rw("Develop one sample"), structured_output=False)
    async def develop_sample(
        photo_id: str,
        recipe_patch: Annotated[dict[str, Any] | None,
                                Field(description="One-off changes on top of the working recipe (not saved).")] = None,
        wait_seconds: Annotated[float, Field(ge=0, le=600, description="How long to wait for the render.")] = 240,
        preview_size: Annotated[int, Field(ge=256, le=MAX_PREVIEW_PX)] = 1024,
    ) -> list:
        """Develop ONE photo with the working recipe (+ optional one-off patch) and return before/after
        previews. Always do this before develop_batch. Heavy: queued in the engine, one at a time."""
        sid = await photo_session(photo_id)
        row = await photo_row(photo_id)
        if not row.get("has_raw"):
            raise ToolError(f"[E_BAD_INPUT] '{photo_id}' has no RAW file; developing needs the RAW.")
        rcp = dict(await working_recipe(sid))
        if recipe_patch:
            rcp.update(validate_patch(recipe_patch, await schema()))
        await guard_heavy()
        r = await engine.post_json("/api/develop/sample", {"photo_id": photo_id, "recipe": rcp})
        register(r["job_id"], "sample")
        j = await wait_job(r["job_id"], wait_seconds)
        summary = _job_summary(j, log_lines=8) | {"recipe": r.get("recipe", rcp)}
        if j.get("state") != "done":
            summary["next"] = ("still rendering: call job_status(job_id, wait_seconds=60, include_preview=true)"
                               if j.get("state") not in TERMINAL_STATES else "the sample failed; see errors/log_tail")
            return [_j(summary)]
        outs = [o for o in j.get("outputs") or [] if o.get("output_id")]
        if not outs:
            return [_j(summary | {"note": "job finished without an output"})]
        before, _ = await image_of(f"/api/photos/{photo_id}/thumb", preview_size, size=preview_size, display="auto")
        after, _ = await image_of(f"/api/outputs/{outs[-1]['output_id']}/web.jpg", preview_size)
        summary["next"] = "Compare; adjust with patch_recipe and re-sample, or develop_batch when it looks right."
        return [_j(summary), "BEFORE (camera JPEG):", before, "AFTER (developed with the recipe):", after]

    @mcp.tool(annotations=_rw("Develop batch"), structured_output=False)
    async def develop_batch(
        photo_ids: Annotated[list[str], Field(description="Photos to develop (all from opened folders).")],
        recipe_patch: Annotated[dict[str, Any] | None,
                                Field(description="One-off changes on top of the working recipe.")] = None,
    ) -> dict:
        """Develop many photos with the working recipe of their folder. Heavy and long: returns a job_id at once;
        follow it with job_status. Develop and approve a sample first. Results go to the engine cache; use export
        to write files."""
        ids, sids = await photo_ids_check(photo_ids)
        if len(ids) > config.max_batch:
            raise ToolError(f"[E_BAD_INPUT] {len(ids)} photos exceeds the batch limit of {config.max_batch}; split it.")
        if len(sids) > 1:
            raise ToolError("[E_BAD_INPUT] all photos of a batch must come from the same folder (session).")
        rcp = dict(await working_recipe(next(iter(sids))))
        if recipe_patch:
            rcp.update(validate_patch(recipe_patch, await schema()))
        await guard_heavy()
        r = await engine.post_json("/api/develop/batch", {"photo_ids": ids, "recipe": rcp})
        register(r["job_id"], "batch")
        return {"job_id": r["job_id"], "photos": len(ids), "recipe": r.get("recipe", rcp),
                "next": "job_status(job_id, wait_seconds=120) until done; cancel_job to stop."}

    @mcp.tool(annotations=_ro("Job status"), structured_output=False)
    async def job_status(
        job_id: str,
        wait_seconds: Annotated[float, Field(ge=0, le=600, description="Wait up to this long for it to finish.")] = 0,
        include_preview: Annotated[bool, Field(description="If a finished develop job, show the last result.")] = False,
    ) -> list:
        """Progress, errors and outputs of an engine job (cull, sample, batch, export)."""
        j = await wait_job(job_id, wait_seconds) if wait_seconds else await engine.get_json(f"/api/jobs/{job_id}")
        if st.heavy_job == job_id and j.get("state") in TERMINAL_STATES:
            st.heavy_job = None
        out: list[Any] = [_j(_job_summary(j))]
        if include_preview and j.get("state") == "done":
            ids = [o["output_id"] for o in j.get("outputs") or [] if isinstance(o, dict) and o.get("output_id")]
            if ids:
                img, _ = await image_of(f"/api/outputs/{ids[-1]}/web.jpg", MAX_PREVIEW_PX)
                out += ["last result:", img]
        return out

    @mcp.tool(annotations=_rw("Cancel job", idempotent=True), structured_output=False)
    async def cancel_job(job_id: str) -> dict:
        """Stop a job started through this server (it stops between photos)."""
        if job_id not in st.own_jobs:
            raise ToolError("[E_NOT_ALLOWED] only jobs started through this MCP server can be cancelled here; "
                            "use the Revelado app for others.")
        r = await engine.post_json(f"/api/jobs/{job_id}/cancel", {})
        return {"job_id": job_id, "cancel_requested": bool(r.get("ok"))}

    @mcp.tool(annotations=_rw("Export files", destructive=True), structured_output=False)
    async def export(
        photo_ids: Annotated[list[str], Field(description="Photos to export.")],
        dest_dir: Annotated[str, Field(description="Absolute folder inside an allow-listed export folder.")],
        confirm: Annotated[bool, Field(description="Must be true. Ask the user first: this writes files.")] = False,
        format: Annotated[Literal["jpg", "tiff16"], Field(description="jpg or 16-bit TIFF.")] = "jpg",
        quality: Annotated[int, Field(ge=1, le=100)] = 97,
        long_edge: Annotated[int | None, Field(ge=256, le=20000, description="Resize the long edge; omit = full.")] = None,
        naming: Annotated[Literal["original", "original_film", "sequence"], Field()] = "original",
        develop_missing: Annotated[bool, Field(description="Develop photos that have no develop yet (heavy). "
                                                           "If false they fail with E_NOT_FOUND.")] = False,
    ) -> dict:
        """Write finished files (latest develop of each photo) into an allow-listed export folder. Never
        overwrites (adds _1, _2...), never writes into a photo source folder, strips GPS. Requires confirm=true."""
        if confirm is not True:
            raise ToolError("[E_CONFIRM_REQUIRED] export writes files to disk. Ask the user to confirm the photos and "
                            "destination, then call export again with confirm=true.")
        ids, sids = await photo_ids_check(photo_ids)
        sources = [st.sessions[s].path for s in sids if s in st.sessions]
        dest = wrap_cfg(lambda: config.check_export(dest_dir, sources))
        await guard_heavy()
        r = await engine.post_json("/api/export", {"photo_ids": ids, "dest_dir": str(dest), "format": format,
                                                   "quality": quality, "long_edge": long_edge, "naming": naming,
                                                   "develop_missing": develop_missing})
        register(r["job_id"], "export")
        return {"job_id": r["job_id"], "photos": len(ids), "dest_dir": str(dest),
                "next": "job_status(job_id, wait_seconds=120); outputs list the written paths."}

    mcp._revelado_state = st          # type: ignore[attr-defined]  # for tests / introspection
    mcp._revelado_engine = engine     # type: ignore[attr-defined]
    return mcp


TOOL_NAMES = ("engine_status", "list_sessions", "open_folder", "list_photos", "get_photo_info", "get_preview",
              "diagnose", "set_rating", "auto_cull_technical", "list_films", "get_recipe", "patch_recipe",
              "develop_sample", "develop_batch", "job_status", "cancel_job", "export")
