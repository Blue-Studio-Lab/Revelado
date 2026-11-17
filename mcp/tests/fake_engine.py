# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory stand-in for the Revelado engine HTTP API, served through httpx.MockTransport.

It implements just enough of engine/README.md for the MCP tools: no image processing, no files.
Photo names are synthetic (frame_0001...).
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

SCHEMA = {
    "film": {"type": "string", "default": "kodak_ektar_100", "supported": True},
    "film_strength": {"type": "number", "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0, "supported": True},
    "exposure_ev": {"type": "number", "min": -2.0, "max": 2.0, "step": 0.1, "default": 0.0, "supported": True},
    "grain": {"type": "number", "min": 0.0, "max": 1.5, "step": 0.05, "default": 1.0, "supported": True},
    "skin_retouch": {"type": "number", "min": 0, "max": 100, "step": 1, "default": 60, "supported": True},
    "hair": {"type": "enum", "options": ["off", "suave", "medio", "fuerte"], "default": "off", "supported": True},
    "nr": {"type": "enum", "options": ["auto", "soft", "strong", "off"], "default": "auto", "supported": False},
}
TRAINED = {"kodak_ektar_100"}


def jpeg(w: int, h: int, color=(120, 90, 60), gps: bool = False) -> bytes:
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x010F] = "FakeCam"                       # Make
        exif[0x8825] = {1: "N", 2: (40.0, 26.0, 46.0)}  # GPSInfo IFD
        im.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()


def err(status: int, code: str, en: str, es: str = "") -> httpx.Response:
    return httpx.Response(status, json={"detail": es or en, "error": {"code": code, "message_es": es or en,
                                                                     "message_en": en, "detail": "",
                                                                     "retryable": False}})


class FakeEngine:
    def __init__(self, token: str = "tok-1") -> None:
        self.token = token
        self.sessions: dict[str, dict] = {}
        self.photos: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.outputs: dict[str, dict] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.job_steps_to_finish = 1          # GET /jobs/{id} calls before a job is "done"
        self.down = False
        self._n = 0

    # ---- helpers for tests
    def add_folder(self, path: Path, n: int = 4, raw: bool = True) -> str:
        sid = "s" + hashlib.sha1(str(path).encode()).hexdigest()[:8]
        self.sessions[sid] = {"session_id": sid, "path": str(path), "name": path.name, "count": n,
                              "opened_at": "2026-01-01T00:00:00"}
        for i in range(n):
            pid = f"{sid}-p{i:04d}"
            self.photos[pid] = {
                "id": pid, "session": sid, "name": f"frame_{i:04d}", "raw_path": str(path / f"frame_{i:04d}.raw"),
                "jpg_path": str(path / f"frame_{i:04d}.jpg"), "has_raw": raw and i != 3, "has_jpg": True,
                "camera": "FakeCam A1" if i % 2 == 0 else "OtherCam", "lens": "50mm", "iso": 100 * (i + 1),
                "shutter": "1/200", "aperture": 2.8, "focal": 50, "datetime": "2026:01:01 10:00:00",
                "width": 6000, "height": 4000, "rating": 0, "flag": None, "is_log": False, "burst_id": None,
                "outputs": 0, "cull": None}
        return sid

    def _job(self, kind: str, payload: dict) -> str:
        self._n += 1
        jid = f"j{self._n}"
        self.jobs[jid] = {"job_id": jid, "kind": kind, "state": "queued", "progress": 0.0, "stage": None, "eta_s": None,
                          "outputs": [], "log": ["queued"], "errors": [], "items_done": 0, "items_failed": 0,
                          "_polls": 0, "_payload": payload}
        return jid

    def _advance(self, j: dict) -> None:
        if j["state"] in ("done", "error", "cancelled"):
            return
        j["_polls"] += 1
        if j["_polls"] < self.job_steps_to_finish:
            j["state"], j["progress"] = "running", 0.5
            return
        j["state"], j["progress"] = "done", 1.0
        p = j["_payload"]
        if j["kind"] in ("sample", "batch"):
            for pid in p.get("photo_ids") or [p["photo_id"]]:
                self._n += 1
                oid = f"o{self._n}"
                self.outputs[oid] = {"output_id": oid, "photo_id": pid, "recipe": p["recipe"]}
                self.photos[pid]["outputs"] += 1
                j["outputs"].append({"photo_id": pid, "output_id": oid, "url": f"/api/outputs/{oid}/full.jpg",
                                     "web_url": f"/api/outputs/{oid}/web.jpg"})
        elif j["kind"] == "cull_technical":
            for pid, ph in self.photos.items():
                if ph["session"] == p["session_id"]:
                    ph["cull"] = {"technical": {"score": 80, "verdict": "keep" if ph["iso"] < 300 else "reject",
                                                "reasons": ["ok"], "burst_best": True}, "claude": None}
        elif j["kind"] == "export":
            j["outputs"] = [{"photo_id": pid, "path": f"{p['dest_dir']}/x.jpg"} for pid in p["photo_ids"]]

    def public_photo(self, ph: dict) -> dict:
        return {k: v for k, v in ph.items() if k != "session"}

    # ---- transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))
        tok = request.headers.get("x-revelado-token")
        if tok != self.token:
            return err(401, "E_AUTH", "Missing or invalid token.")
        q = request.url.params
        parts = path.strip("/").split("/")[1:]       # drop 'api'
        m = request.method

        if parts == ["health"]:
            return httpx.Response(200, json={"ok": True, "version": "0.1.0", "rawtherapee": True, "exiftool": True,
                                             "develop_engine": {"available": True, "reason": None, "films": sorted(TRAINED)},
                                             "jobs_active": 0, "disk_free_gb": 100.0})
        if parts == ["sessions"]:
            return httpx.Response(200, json=list(self.sessions.values()))
        if parts == ["library", "open"] and m == "POST":
            p = Path(body["path"])
            sid = next((s for s, v in self.sessions.items() if v["path"] == str(p)), None) or self.add_folder(p)
            s = self.sessions[sid]
            return httpx.Response(200, json={"session_id": sid, "count": s["count"], "pairs": s["count"], "raw_only": 0,
                                             "jpg_only": 0, "restored": False, "path": s["path"], "name": s["name"]})
        if parts == ["photos"]:
            sid = q.get("session_id")
            if sid not in self.sessions:
                return err(404, "E_SESSION_UNKNOWN", "Unknown session.")
            return httpx.Response(200, json=[self.public_photo(p) for p in self.photos.values() if p["session"] == sid])
        if parts[:1] == ["photos"] and len(parts) >= 3:
            pid = parts[1]
            ph = self.photos.get(pid)
            if not ph:
                return err(404, "E_NOT_FOUND", "Unknown photo.")
            sub = parts[2:]
            if sub == ["thumb"]:
                size = int(q.get("size", 400))
                return httpx.Response(200, content=jpeg(size, size * 2 // 3, gps=True),
                                      headers={"content-type": "image/jpeg"})
            if sub == ["exif"]:
                return httpx.Response(200, json={"normalised": {"camera": ph["camera"], "iso": ph["iso"],
                                                                "gps_lat": 40.4, "serial": "123"},
                                                 "source": ph["name"] + ".raw",
                                                 "tags": {"EXIF:Model": ph["camera"], "EXIF:GPSLatitude": "40 deg",
                                                          "EXIF:SerialNumber": "999", "File:Directory": "/x/y",
                                                          "EXIF:ISO": ph["iso"]}})
            if sub == ["profile"]:
                return httpx.Response(200, json={"camera_profile": "fake", "recommended": {"nr_level": 1}})
            if sub == ["cull"]:
                return httpx.Response(200, json=ph["cull"] or {"technical": None, "claude": None})
            if sub == ["outputs"]:
                return httpx.Response(200, json=[{"output_id": o["output_id"], "recipe": o["recipe"]}
                                                 for o in self.outputs.values() if o["photo_id"] == pid])
            if len(sub) == 3 and sub[0] == "mask" and sub[2] == "stats":
                return httpx.Response(200, json={"kind": sub[1], "level": 0.2})
            if len(sub) == 2 and sub[0] == "mask":
                return httpx.Response(200, content=jpeg(1600, 1067), headers={"content-type": "image/jpeg"})
            if sub == ["rating"] and m == "POST":
                if "rating" in body and body["rating"] is not None:
                    ph["rating"] = body["rating"]
                if "flag" in body:
                    ph["flag"] = body["flag"]
                return httpx.Response(200, json={"ok": True, "id": pid, "rating": ph["rating"], "flag": ph["flag"]})
        if parts[:1] == ["outputs"] and len(parts) == 3:
            if parts[1] not in self.outputs:
                return err(404, "E_NOT_FOUND", "Unknown output.")
            return httpx.Response(200, content=jpeg(2560, 1707, color=(200, 150, 100)),
                                  headers={"content-type": "image/jpeg"})
        if parts == ["films"]:
            return httpx.Response(200, json=[{"id": "kodak_ektar_100", "name": "Kodak Ektar 100", "type": "color",
                                              "status": "trained", "tier": "spec", "scans": 0,
                                              "swatch_url": None}])
        if parts == ["recipe", "default"]:
            return httpx.Response(200, json={k: v["default"] for k, v in SCHEMA.items()})
        if parts == ["recipe", "schema"]:
            return httpx.Response(200, json=SCHEMA)
        if parts == ["cull", "auto"] and m == "POST":
            return httpx.Response(200, json={"job_id": self._job("cull_technical", body)})
        if parts in (["develop", "sample"], ["develop", "batch"]) and m == "POST":
            if body["recipe"].get("film") not in TRAINED:
                return err(409, "E_FILM_UNTRAINED", "Film profile not trained yet.", "Perfil aún no entrenado")
            kind = parts[1]
            return httpx.Response(200, json={"job_id": self._job(kind, body), "recipe": body["recipe"]})
        if parts == ["export"] and m == "POST":
            return httpx.Response(200, json={"job_id": self._job("export", body)})
        if parts[:1] == ["jobs"] and len(parts) == 2:
            j = self.jobs.get(parts[1])
            if not j:
                return err(404, "E_NOT_FOUND", "Unknown job.")
            self._advance(j)
            return httpx.Response(200, json={k: v for k, v in j.items() if not k.startswith("_")})
        if parts[:1] == ["jobs"] and len(parts) == 3 and parts[2] == "cancel":
            j = self.jobs.get(parts[1])
            if j:
                j["state"] = "cancelled"
            return httpx.Response(200, json={"ok": bool(j)})
        return err(404, "E_NOT_FOUND", f"no route {m} {path}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
