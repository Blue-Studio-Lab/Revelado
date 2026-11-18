# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP API tests (FastAPI TestClient) on throw-away libraries: health/capabilities, tokens, JPG-only and RAW-only
folders, develop policy (RAW needed unless develop_source='jpg'), an end-to-end develop of a small JPG through the
real worker subprocess, presets."""
from __future__ import annotations

import os
import time

import pytest

from conftest import raw_file


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient
    from revelado.api import app as appmod
    appmod.CTX = appmod.build_ctx(settings)
    with TestClient(appmod.app) as c:
        c.headers["X-Revelado-Token"] = appmod.CTX.token
        yield c
    appmod.CTX = None


def _wait(client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "error", "cancelled", "partial"):
            return j
        time.sleep(0.3)
    raise AssertionError("job timeout")


def test_health_capabilities_and_tokens(client, settings):
    from revelado.api import app as appmod
    h = client.get("/api/health").json()
    assert h["capabilities"] == {"develop_jpg": True, "tiff16_rotation": True, "raw_only_ok": True,
                                 "jpg_only_ok": True}
    assert h["develop_engine"]["engine"] == "native"
    tok = appmod.CTX.token
    assert client.get("/api/health", headers={"X-Revelado-Token": "nope"}).status_code == 401
    assert client.get("/api/health", headers={"X-Revelado-Token": tok}).status_code == 200
    # runtime token also written to the per-user state folder, mode 0600 (POSIX only: Windows has no mode bits,
    # access there is governed by the per-user profile ACLs)
    up = settings.user_token_path
    assert up.read_text(encoding="utf-8").strip() == tok
    if os.name != "nt":
        assert (up.stat().st_mode & 0o777) == 0o600
    assert settings.runtime_token_path.read_text(encoding="utf-8").strip() == tok


def test_presets_default_is_neutral(client):
    assert client.get("/api/recipe/default").json()["film"] == "none"
    ps = {p["id"]: p for p in client.get("/api/recipe/presets").json()}
    assert ps["neutral"]["default"] and ps["editorial_ektar"]["recipe"]["film"] == "kodak_ektar_100"
    films = [f["id"] for f in client.get("/api/films").json()]
    assert films[:2] == ["none", "kodak_ektar_100"]


def _open(client, folder):
    r = client.post("/api/library/open", json={"path": str(folder)})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool no instalado")
def test_jpg_only_folder_view_cull_and_develop_policy(client, jpg_only_folder):
    lib = _open(client, jpg_only_folder)
    assert lib["count"] == 3 and lib["jpg_only"] == 3 and lib["raw_only"] == 0
    photos = client.get("/api/photos", params={"session_id": lib["session_id"]}).json()
    p = photos[0]
    assert p["has_jpg"] and not p["has_raw"] and p["can_develop"] is False and p["develop_block_reason"] == "needs_raw"
    # viewing works
    for path in (f"/api/photos/{p['id']}/thumb", f"/api/photos/{p['id']}/preview"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/photos/{p['id']}/crop", params=dict(x=10, y=10, w=100, h=80)).status_code == 200
    # culling works (rating + technical cull)
    assert client.post(f"/api/photos/{p['id']}/rating", json={"rating": 4, "flag": "pick"}).json()["rating"] == 4
    j = client.post("/api/cull/auto", json={"session_id": lib["session_id"], "mode": "technical"}).json()
    assert _wait(client, j["job_id"])["state"] in ("done", "partial")
    # develop refuses clearly without the RAW...
    r = client.post("/api/develop/sample", json={"photo_id": p["id"]})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "E_NEEDS_RAW" and "se necesita el raw" in r.json()["detail"].lower()
    assert e["reason"] == "needs_raw" and e["photo_id"] == p["id"]


@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool no instalado")
def test_jpg_develop_mode_end_to_end(client, jpg_only_folder):
    """...unless the JPG mode is explicitly chosen: real worker subprocess, neutral and Ektar renders."""
    lib = _open(client, jpg_only_folder)
    pid = client.get("/api/photos", params={"session_id": lib["session_id"]}).json()[0]["id"]
    for rcp in ({"develop_source": "jpg"}, {"develop_source": "jpg", "film": "kodak_ektar_100", "grain": 1.0,
                                            "reframe": "apply"}):
        r = client.post("/api/develop/sample", json={"photo_id": pid, "recipe": rcp, "tiff16": True})
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job_id"])
        assert j["state"] == "done", j
        out = client.get(f"/api/photos/{pid}/outputs").json()[0]
        assert out["has_tiff16"] and out["size"][0] > 0
        assert client.get(out["url"]).status_code == 200 and client.get(out["web_url"]).status_code == 200


@pytest.mark.needs_raw
@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool no instalado")
def test_raw_only_folder_thumbs_from_embedded_preview(client, raw_only_folder):
    lib = _open(client, raw_only_folder)
    assert lib["raw_only"] == 1 and lib["jpg_only"] == 0
    p = client.get("/api/photos", params={"session_id": lib["session_id"]}).json()[0]
    assert p["has_raw"] and not p["has_jpg"] and p["can_develop"] and p["develop_block_reason"] is None
    r = client.get(f"/api/photos/{p['id']}/thumb")
    assert r.status_code == 200 and len(r.content) > 2000
    assert client.get(f"/api/photos/{p['id']}/preview").status_code == 200


@pytest.mark.heavy
@pytest.mark.needs_rawtherapee
@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool no instalado")
def test_raw_only_develop_end_to_end(client, raw_only_folder, settings):
    if not settings.rawtherapee:
        pytest.skip("rawtherapee-cli no instalado")
    lib = _open(client, raw_only_folder)
    pid = client.get("/api/photos", params={"session_id": lib["session_id"]}).json()[0]["id"]
    r = client.post("/api/develop/sample", json={"photo_id": pid})
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job_id"], timeout=900)
    assert j["state"] == "done", j
    raw_file("canon")


@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool no instalado")
def test_batch_per_photo_overrides(client, jpg_only_folder):
    import json
    lib = _open(client, jpg_only_folder)
    ids = [p["id"] for p in client.get("/api/photos", params={"session_id": lib["session_id"]}).json()[:2]]
    body = {"photo_ids": ids, "recipe": {"develop_source": "jpg"},
            "overrides": {ids[1]: {"skin_retouch": 60, "film": "kodak_ektar_100"}}}
    r = client.post("/api/develop/batch", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["recipes"][ids[1]]["skin_retouch"] == 60
    j = _wait(client, r.json()["job_id"])
    assert j["state"] == "done", j
    res = j["result"]["recipes"]
    assert res[ids[0]]["skin_retouch"] == 0 and res[ids[0]]["film"] == "none"
    assert res[ids[1]]["skin_retouch"] == 60 and res[ids[1]]["film"] == "kodak_ektar_100"
    assert res[ids[1]]["develop_source"] == "jpg"                    # batch recipe kept under the override
    from revelado.api import app as appmod
    for pid in ids:                                                   # the per-photo sidecar has the resolved recipe
        row = appmod.CTX.catalog.outputs(pid)[0]
        assert json.loads(row["sidecar_json"])["recipe"] == res[pid]
    bad = client.post("/api/develop/batch", json={"photo_ids": ids[:1], "overrides": {"nope": {}}})
    assert bad.status_code == 400


@pytest.mark.needs_raw
def test_preview_source_header_for_raw_only(client, raw_only_folder):
    if __import__("shutil").which("exiftool") is None:
        pytest.skip("exiftool no instalado")
    lib = _open(client, raw_only_folder)
    p = client.get("/api/photos", params={"session_id": lib["session_id"]}).json()[0]
    r = client.get(f"/api/photos/{p['id']}/preview")
    assert r.status_code == 200 and r.headers["x-preview-source"] in ("embedded", "quick_render")


def test_quick_render_when_embedded_preview_is_tiny(tmp_path, monkeypatch):
    from conftest import raw_file
    from revelado import imaging
    src = raw_file("high_iso")
    monkeypatch.setattr(imaging, "MIN_EMBEDDED_PX", 100000)           # force the fallback
    st = imaging.ImageStore(tmp_path)
    photo = {"id": "x", "raw_path": str(src), "jpg_path": None, "meta": {"orientation": 1}}
    p = st.source_jpeg(photo)
    assert p.name.endswith(".render.jpg") and st.preview_source(photo) == "quick_render"
    im, _ = st.reduced(photo, 800)
    assert max(im.size) == 800
