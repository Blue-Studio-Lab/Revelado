# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude culling ("Deja que Claude elija"), stages 1 and 2 of the culling pipeline.

Opt-in per request: requires ANTHROPIC_API_KEY AND ``allow_cloud: true``. What leaves the machine:
  stage 1 (prefilter, claude-haiku-5-5, effort low): every frame at <= 512 px;
  stage 2 (finalists, claude-opus-5-5, effort medium): hero/keep/maybe + safety net, at <= 1024 px plus one 1:1 crop
  (<= 448 px) of the largest face found by the local detector.
Images: JPEG q85 re-encoded WITHOUT EXIF (no GPS, no serials, no names), orientation applied, S-Log display transform
applied. Frames are identified only by opaque ids (p0001…); file names and paths never leave the machine.
Request layout (cache-friendly): frozen system prompt (cache_control) -> session context block (cache_control) ->
per-frame label + metrics + image blocks -> final instruction. Structured output via ``output_config.format``
(JSON schema with numeric constraints stripped; ranges clamped client-side).

Stage 4 (story assembly) and Message Batches are not implemented in v0.1 ("próximamente").
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import ReveladoError
from ..log import get_logger

log = get_logger("cull.claude")
PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

MODELS = {"prefilter": os.environ.get("REVELADO_CLAUDE_PREFILTER_MODEL") or "claude-haiku-5-5",
          "finalist": os.environ.get("REVELADO_CLAUDE_FINALIST_MODEL") or "claude-opus-5-5"}
EFFORT = {"prefilter": "low", "finalist": "medium"}
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20), "claude-sonnet-5-5": (2.00, 10.00, 0.10),
          "claude-haiku-5-5": (0.10, 0.50, 0.01)}           # USD per MTok: input, output, cache read
SIZES = {"prefilter": 512, "finalist": 1024, "face": 448}
GROUP = {"prefilter": 24, "finalist": 12}

# ------------------------------------------------------------------------------------------------ schemas
STRIP = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
         "minItems", "maxItems", "uniqueItems", "pattern", "default", "title", "examples"}


def to_api_schema(model: type[BaseModel]) -> dict:
    s = model.model_json_schema()

    def walk(n):
        if isinstance(n, dict):
            for k in [k for k in n if k in STRIP]:
                n.pop(k)
            if n.get("type") == "object" or "properties" in n:
                props = n.get("properties", {})
                n["additionalProperties"] = False
                n["required"] = list(props)
                for v in props.values():
                    walk(v)
            for k, v in n.items():
                if k != "properties":
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(s)
    return s


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Prefilter(Strict):
    id: str
    verdict: Literal["hero", "keep", "maybe", "reject"]
    score: int = Field(ge=0, le=100)
    moment: int = Field(ge=1, le=5)
    expression: int = Field(ge=1, le=5)
    composition: int = Field(ge=1, le=5)
    light: int = Field(ge=1, le=5)
    eyes: Literal["open", "closed", "mixed", "no_faces", "unclear"]
    reject_reason: Literal["focus_missed", "motion_blur", "eyes_closed", "no_subject", "obstruction",
                           "bad_expression", "exposure", "duplicate_weaker", "test_shot", "other"] | None
    note: str = Field(max_length=90)


class PrefilterGroup(Strict):
    group_id: str
    frames: list[Prefilter]
    burst_best: list[str]


class FaceCheck(Strict):
    crop_id: str
    sharpness: Literal["sharp", "acceptable_web", "soft", "missed"]
    eyes: Literal["open", "closed", "half", "not_visible"]


class Finalist(Strict):
    id: str
    pick: Literal["select", "alternate", "drop"]
    verdict: Literal["hero", "keep", "maybe", "reject"]
    score: int = Field(ge=0, le=100)
    faces: list[FaceCheck]
    burst_rank: int = Field(ge=1)
    duplicate_of: str | None
    story_moments: list[Literal["arrival", "ceremony", "proposal", "ring", "reaction", "kiss", "portrait",
                                "laughter", "detail", "landscape", "family_group", "friends", "transition",
                                "closing", "other"]]
    retouch_hint: Literal["none", "stray_hair", "eyes_closed_fixable", "object_in_frame", "spots"] | None
    reason: str = Field(max_length=200)


class FinalGroup(Strict):
    group_id: str
    frames: list[Finalist]
    group_note: str = Field(max_length=300)


def clamp_to_model(model: type[BaseModel], data: Any) -> Any:
    """Clamp numeric values / truncate strings to the model's declared constraints (structure is API-guaranteed)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, f in model.model_fields.items():
        if name not in out:
            continue
        v = out[name]
        ann = f.annotation
        sub = getattr(ann, "__args__", None)
        if isinstance(v, list) and sub and isinstance(sub[0], type) and issubclass(sub[0], BaseModel):
            out[name] = [clamp_to_model(sub[0], x) for x in v]
            continue
        for m in f.metadata:
            if isinstance(v, (int, float)):
                if getattr(m, "ge", None) is not None:
                    v = max(v, m.ge)
                if getattr(m, "le", None) is not None:
                    v = min(v, m.le)
            if isinstance(v, str) and getattr(m, "max_length", None) is not None:
                v = v[:m.max_length]
        out[name] = v
    return out


# ------------------------------------------------------------------------------------------------ images
@dataclass(frozen=True)
class Sent:
    id: str
    b64: str
    w: int
    h: int
    sha256: str


def encode_jpeg(im) -> tuple[str, str]:
    """Re-encode WITHOUT EXIF (PIL save without exif=) -> base64, sha256."""
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    raw = buf.getvalue()
    return base64.standard_b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()


def frame(store, photo: dict, oid: str, long_edge: int) -> Sent:
    im, _ = store.reduced(photo, long_edge, "auto")
    b64, h = encode_jpeg(im)
    return Sent(oid, b64, im.width, im.height, h)


def face_crop(store, photo: dict, oid: str, box_norm: list[float], side: int = 448) -> Sent | None:
    from PIL import Image
    meta = photo.get("meta") or {}
    W, H = meta.get("width"), meta.get("height")
    if not W or not H:
        return None
    if int(meta.get("orientation") or 1) in (5, 6, 7, 8):
        W, H = H, W
    x, y, w, h = box_norm
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    s = max(w * W, h * H) * 1.6
    s = max(min(s, 1400), 160)
    data, _, _ = store.crop(photo, int(cx - s / 2), int(cy - s / 2), int(s), int(s), "auto")
    im = Image.open(io.BytesIO(data))
    im.thumbnail((side, side), Image.Resampling.LANCZOS)
    b64, hh = encode_jpeg(im)
    return Sent(oid, b64, im.width, im.height, hh)


def image_block(s: Sent) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": s.b64}}


def est_tokens(w: int, h: int) -> int:
    return math.ceil(w * h / 750)


# ------------------------------------------------------------------------------------------------ request building
@dataclass
class Route:
    name: str
    model: str
    effort: str
    system_text: str
    max_tokens: int = 16000

    def system(self) -> list[dict]:
        return [{"type": "text", "text": self.system_text, "cache_control": {"type": "ephemeral"}}]


def route(name: str) -> Route:
    file = {"prefilter": "cull_prefilter.v1.md", "finalist": "cull_final.v1.md"}[name]
    return Route(name, MODELS[name], EFFORT[name], (PROMPTS / file).read_text(encoding="utf-8"))


def build_params(rt: Route, content: list[dict], out: type[BaseModel]) -> dict:
    return dict(model=rt.model, max_tokens=rt.max_tokens, system=rt.system(),
                messages=[{"role": "user", "content": content}],
                output_config={"effort": rt.effort, "format": {"type": "json_schema", "schema": to_api_schema(out)}})


def session_context(lang: str = "es", genre: str = "boda / preboda", n_frames: int = 0) -> str:
    return (f"Session context\n- genre: {genre}\n- UI language for notes and reasons: {lang}\n"
            f"- frames in session: {n_frames}\n- priorities: focus on eyes, genuine expression, the moment, "
            f"no closed eyes, avoid redundancy, cover the story\n- colour is ungraded (log capture with a technical "
            f"display transform): do not judge colour or contrast")


def _metrics_small(t: dict | None) -> dict:
    if not t:
        return {}
    return {"sharp_face": t.get("sharpness_face"), "sharp_af": t.get("sharpness_af"),
            "sharp_rel": t.get("sharpness_rel"), "faces": t.get("faces"), "eyes_open": t.get("eyes_open"),
            "exposure": (t.get("exposure") or {}).get("verdict"), "local_verdict": t.get("verdict")}


def prefilter_content(ctx_text: str, gid: str, frames: list[dict]) -> list[dict]:
    """frames: [{oid, burst, burst_pos, burst_size, sent: Sent, tech}]"""
    blocks: list[dict] = [{"type": "text", "text": ctx_text, "cache_control": {"type": "ephemeral"}}]
    for f in frames:
        m = json.dumps(_metrics_small(f.get("tech")), sort_keys=True, separators=(",", ":"))
        blocks.append({"type": "text", "text": f"[{f['oid']} · burst {f['burst']} ({f['burst_pos']}/{f['burst_size']}) · "
                                               f"{f['sent'].w}×{f['sent'].h}] metrics={m}"})
        blocks.append(image_block(f["sent"]))
    blocks.append({"type": "text", "text": f"Group {gid}: return one entry per frame id above, in the same order."})
    return blocks


def finalist_content(ctx_text: str, gid: str, frames: list[dict]) -> list[dict]:
    blocks: list[dict] = [{"type": "text", "text": ctx_text, "cache_control": {"type": "ephemeral"}}]
    for f in frames:
        m = json.dumps(_metrics_small(f.get("tech")), sort_keys=True, separators=(",", ":"))
        s1 = json.dumps({k: (f.get("stage1") or {}).get(k) for k in ("verdict", "score", "note")},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        blocks.append({"type": "text", "text": f"[{f['oid']} · burst {f['burst']} · {f['sent'].w}×{f['sent'].h}] "
                                               f"metrics={m} first_pass={s1}"})
        blocks.append(image_block(f["sent"]))
        if f.get("face"):
            fc: Sent = f["face"]
            blocks.append({"type": "text", "text": f"[{fc.id} · {fc.w}×{fc.h} · 1:1 crop of the largest face of {f['oid']}]"})
            blocks.append(image_block(fc))
    blocks.append({"type": "text", "text": f"Group {gid}: return one entry per frame id above, in the same order."})
    return blocks


# ------------------------------------------------------------------------------------------------ calling
class Ledger:
    def __init__(self):
        self.calls: list[dict] = []

    def record(self, route_name: str, model: str, usage: Any) -> None:
        def g(k):
            return int(getattr(usage, k, 0) or 0) if usage is not None else 0
        inp, out, cr, cw = g("input_tokens"), g("output_tokens"), g("cache_read_input_tokens"), \
            g("cache_creation_input_tokens")
        pi, po, pc = PRICES.get(model, PRICES["claude-opus-5-5"])
        usd = (inp * pi + out * po + cr * pc + cw * pi * 1.25) / 1e6
        self.calls.append({"route": route_name, "model": model, "input": inp, "output": out, "cache_read": cr,
                           "cache_write": cw, "usd": round(usd, 4)})

    def total(self) -> dict:
        return {"calls": len(self.calls), "usd": round(sum(c["usd"] for c in self.calls), 4),
                "input_tokens": sum(c["input"] for c in self.calls),
                "output_tokens": sum(c["output"] for c in self.calls)}


def call_structured(client, rt: Route, content: list[dict], out: type[BaseModel], ledger: Ledger) -> BaseModel:
    import anthropic
    params = build_params(rt, content, out)
    try:
        resp = client.messages.create(**params)
    except anthropic.AuthenticationError as e:
        raise ReveladoError("E_AI_AUTH", "clave rechazada") from e
    except anthropic.RateLimitError as e:
        raise ReveladoError("E_AI_RATE") from e
    except anthropic.BadRequestError as e:
        raise ReveladoError("E_AI_ERROR", f"petición rechazada: {str(e)[:200]}") from e
    except anthropic.APIError as e:
        raise ReveladoError("E_AI_ERROR", str(e)[:200]) from e
    ledger.record(rt.name, getattr(resp, "model", rt.model), getattr(resp, "usage", None))
    if resp.stop_reason == "refusal":
        raise ReveladoError("E_AI_REFUSAL")
    if resp.stop_reason == "max_tokens":
        raise ReveladoError("E_AI_ERROR", "respuesta truncada (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    try:
        return out.model_validate_json(text)
    except ValidationError:
        try:
            return out.model_validate(clamp_to_model(out, json.loads(text)))
        except (ValidationError, json.JSONDecodeError) as e:
            raise ReveladoError("E_AI_ERROR", "respuesta con formato inválido") from e


def make_client(api_key: str):
    try:
        import anthropic
    except ImportError as e:
        raise ReveladoError("E_TOOL_MISSING", "falta el paquete 'anthropic' (pip install anthropic)") from e
    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=300)


# ------------------------------------------------------------------------------------------------ estimate + run
def estimate(n_frames: int, finalist_share: float = 0.45) -> dict:
    t1_img = est_tokens(512, 341)
    t2_img = est_tokens(1024, 683) + est_tokens(448, 448)
    groups1 = max(1, math.ceil(n_frames / GROUP["prefilter"]))
    n2 = math.ceil(n_frames * finalist_share)
    groups2 = max(1, math.ceil(n2 / GROUP["finalist"])) if n2 else 0
    in1 = n_frames * (t1_img + 60) + groups1 * 3200
    in2 = n2 * (t2_img + 90) + groups2 * 3200
    out1, out2 = n_frames * 120 + groups1 * 1500, n2 * 220 + groups2 * 3000
    p1, p2 = PRICES[MODELS["prefilter"]], PRICES[MODELS["finalist"]]
    usd = (in1 * p1[0] + out1 * p1[1] + in2 * p2[0] + out2 * p2[1]) / 1e6
    return {"frames": n_frames, "finalists_est": n2, "requests": groups1 + groups2,
            "est_input_tokens": in1 + in2, "est_output_tokens": out1 + out2, "est_usd": round(usd, 2),
            "models": {"prefilter": MODELS["prefilter"], "finalist": MODELS["finalist"]},
            "effort": EFFORT, "note": "estimación; el coste real se calcula con el uso devuelto por la API"}


def run(job, ctx, session_id: str, photo_ids: list[str] | None, client=None) -> dict:
    from ..jobs import run_item
    from . import technical
    key = ctx.settings.anthropic_key()
    if client is None:
        if not key:
            raise ReveladoError("E_AI_NO_KEY")
        client = make_client(key)
    cat = ctx.catalog
    rows = [dict(r) | {"meta": json.loads(r["meta_json"] or "{}")} for r in cat.photos(session_id)]
    if photo_ids:
        want = set(photo_ids)
        rows = [r for r in rows if r["id"] in want]
    if not rows:
        raise ReveladoError("E_BAD_INPUT", "no hay fotos que analizar")
    if any(not r["cull_json"] for r in rows):
        job.say("primero el cull técnico local (métricas, ráfagas, caras)")
        technical.run(job, ctx, session_id, photo_ids)
        job.items_done = job.items_failed = 0
        rows = [dict(r) | {"meta": json.loads(r["meta_json"] or "{}")} for r in cat.photos(session_id)
                if not photo_ids or r["id"] in set(photo_ids)]
    rows.sort(key=lambda r: (r["capture_ts"] or 0, r["name"]))
    for i, r in enumerate(rows, 1):
        r["oid"] = f"p{i:04d}"
        r["tech"] = json.loads(r["cull_json"] or "null")
    by_oid = {r["oid"]: r for r in rows}
    ctx_text = session_context(n_frames=len(rows))
    ledger = Ledger()
    job.items_total = len(rows)
    job.say(f"Claude fase 1 ({MODELS['prefilter']}, {EFFORT['prefilter']}): {len(rows)} fotos a <= {SIZES['prefilter']} px, sin EXIF")
    # ---------------- stage 1
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for r in rows:                                   # keep bursts together when possible
        if len(cur) >= GROUP["prefilter"]:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
    rt1 = route("prefilter")
    stage1: dict[str, dict] = {}
    for gi, g in enumerate(groups, 1):
        job.check_cancel()
        frames = []
        bursts: dict[str, list] = {}
        for r in g:
            bursts.setdefault((r["tech"] or {}).get("burst_id", r["oid"]), []).append(r["oid"])
        for r in g:
            b = (r["tech"] or {}).get("burst_id", r["oid"])
            sent = run_item(job, r["id"], lambda r=r: frame(ctx.images, r, r["oid"], SIZES["prefilter"]))
            if sent is None:
                continue
            frames.append({"oid": r["oid"], "burst": b, "burst_pos": bursts[b].index(r["oid"]) + 1,
                           "burst_size": len(bursts[b]), "sent": sent, "tech": r["tech"]})
        if not frames:
            continue
        gid = f"g{gi:02d}"
        res = call_structured(client, rt1, prefilter_content(ctx_text, gid, frames), PrefilterGroup, ledger)
        for f in res.frames:
            if f.id in by_oid:
                stage1[f.id] = f.model_dump()
        job.update(0.05 + 0.45 * gi / len(groups), f"Claude fase 1: grupo {gi}/{len(groups)}")
    # ---------------- finalists + safety net
    fin = [r for r in rows if (stage1.get(r["oid"]) or {}).get("verdict") in ("hero", "keep", "maybe")]
    net = [r for r in rows if (stage1.get(r["oid"]) or {}).get("verdict") == "reject"
           and (r["tech"] or {}).get("verdict") == "keep"]
    fin_ids = {r["oid"] for r in fin + net}
    fin_rows = [r for r in rows if r["oid"] in fin_ids]
    job.say(f"Claude fase 2 ({MODELS['finalist']}, {EFFORT['finalist']}): {len(fin_rows)} finalistas "
            f"({len(net)} por red de seguridad) a <= {SIZES['finalist']} px + recorte 1:1 de cara")
    rt2 = route("finalist")
    stage2: dict[str, dict] = {}
    groups2 = [fin_rows[i:i + GROUP["finalist"]] for i in range(0, len(fin_rows), GROUP["finalist"])]
    for gi, g in enumerate(groups2, 1):
        job.check_cancel()
        frames = []
        for r in g:
            sent = run_item(job, r["id"], lambda r=r: frame(ctx.images, r, r["oid"], SIZES["finalist"]))
            if sent is None:
                continue
            face = None
            boxes = (r["tech"] or {}).get("face_boxes") or []
            if boxes:
                try:
                    face = face_crop(ctx.images, r, f"{r['oid']}_h1", boxes[0])
                except ReveladoError:
                    face = None
            frames.append({"oid": r["oid"], "burst": (r["tech"] or {}).get("burst_id", r["oid"]), "sent": sent,
                           "face": face, "tech": r["tech"], "stage1": stage1.get(r["oid"])})
        if not frames:
            continue
        res = call_structured(client, rt2, finalist_content(ctx_text, f"f{gi:02d}", frames), FinalGroup, ledger)
        for f in res.frames:
            if f.id in by_oid:
                stage2[f.id] = f.model_dump()
        job.update(0.5 + 0.45 * gi / max(len(groups2), 1), f"Claude fase 2: grupo {gi}/{len(groups2)}")
    # ---------------- persist
    now = time.time()
    for r in rows:
        s1, s2 = stage1.get(r["oid"]), stage2.get(r["oid"])
        if not s1 and not s2:
            continue
        final = s2 or s1
        data = {"verdict": final["verdict"], "score": final["score"], "pick": (s2 or {}).get("pick"),
                "reason": (s2 or {}).get("reason") or (s1 or {}).get("note"), "stage1": s1, "stage2": s2,
                "models": {"prefilter": MODELS["prefilter"], "finalist": MODELS["finalist"] if s2 else None},
                "analysed_at": now}
        cat.set_cull(r["id"], claude=data)
        job.items_done += 1
        ctx.bus.emit({"type": "photo_updated", "photo_id": r["id"], "cull": {"claude": data}})
    job.result = {"stage1": len(stage1), "stage2": len(stage2), "cost": ledger.total(), "calls": ledger.calls}
    job.say(f"Claude: {len(stage1)} evaluadas, {len(stage2)} finalistas; coste real ${ledger.total()['usd']:.2f}")
    return job.result
