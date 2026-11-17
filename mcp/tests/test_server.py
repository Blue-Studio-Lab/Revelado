# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import base64
import io
import json

import pytest
from mcp import Client
from PIL import Image

from revelado_mcp.server import TOOL_NAMES, build_server

pytestmark = pytest.mark.anyio


def texts(result) -> list[str]:
    return [c.text for c in result.content if c.type == "text"]


def data(result) -> dict:
    return json.loads(texts(result)[0])


def images(result) -> list[Image.Image]:
    return [Image.open(io.BytesIO(base64.b64decode(c.data))) for c in result.content if c.type == "image"]


@pytest.fixture
def server(config, engine):
    return build_server(config, transport=engine.transport())


async def open_shoot(c, dirs) -> str:
    r = await c.call_tool("open_folder", {"path": str(dirs["shoot"])})
    assert not r.is_error, texts(r)
    return data(r)["session_id"]


async def test_tool_list_and_annotations(server):
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == set(TOOL_NAMES)
    assert tools["export"].annotations.destructive_hint is True
    assert tools["get_preview"].annotations.read_only_hint is True
    for name in ("develop_batch", "develop_sample", "set_rating", "open_folder"):
        assert tools[name].annotations.read_only_hint is False
    assert all(t.description for t in tools.values())


async def test_engine_status_and_token_header(server, engine):
    async with Client(server) as c:
        r = await c.call_tool("engine_status", {})
    d = data(r)
    assert d["engine"]["ok"] and d["engine"]["trained_films"] == ["kodak_ektar_100"]
    assert d["mcp"]["token_source"] == "file"


async def test_token_reloaded_after_engine_restart(server, engine, dirs):
    async with Client(server) as c:
        assert not (await c.call_tool("engine_status", {})).is_error
        engine.token = "tok-2"
        dirs["token"].write_text("tok-2")
        assert not (await c.call_tool("engine_status", {})).is_error


async def test_engine_down_is_clear(server, engine):
    engine.down = True
    async with Client(server) as c:
        r = await c.call_tool("engine_status", {})
    assert r.is_error and "E_ENGINE_DOWN" in texts(r)[0]


async def test_open_folder_allow_list(server, dirs):
    async with Client(server) as c:
        bad = await c.call_tool("open_folder", {"path": str(dirs["outside"])})
        assert bad.is_error and "E_NOT_ALLOWED" in texts(bad)[0]
        rel = await c.call_tool("open_folder", {"path": "photos/shoot_a"})
        assert rel.is_error
        sid = await open_shoot(c, dirs)
        assert sid.startswith("s")


async def test_list_photos_filters_and_no_paths(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        d = data(await c.call_tool("list_photos", {"session_id": sid}))
        assert d["total"] == 4 and d["matched"] == 4
        assert "raw_path" not in json.dumps(d) and str(dirs["shoot"]) not in json.dumps(d)
        d = data(await c.call_tool("list_photos", {"session_id": sid, "camera": "fakecam"}))
        assert d["matched"] == 2
        d = data(await c.call_tool("list_photos", {"session_id": sid, "has_raw": False}))
        assert d["matched"] == 1


async def test_unknown_session_and_photo_rejected(server, engine, dirs):
    other = engine.add_folder(dirs["outside"])          # opened earlier by the GUI, outside the allow-list
    async with Client(server) as c:
        r = await c.call_tool("list_photos", {"session_id": other})
        assert r.is_error and "E_NOT_ALLOWED" in texts(r)[0]
        await open_shoot(c, dirs)
        r = await c.call_tool("get_preview", {"photo_id": f"{other}-p0000"})
        assert r.is_error and "E_NOT_FOUND" in texts(r)[0]


async def test_preview_small_and_metadata_free(server, dirs, engine):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        r = await c.call_tool("get_preview", {"photo_id": f"{sid}-p0000", "size": 1024})
    assert not r.is_error
    (im,) = images(r)
    assert max(im.size) <= 1024
    assert not im.getexif() and "exif" not in im.info and "icc_profile" not in im.info


async def test_photo_info_strips_private_tags(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        d = data(await c.call_tool("get_photo_info", {"photo_id": f"{sid}-p0000"}))
    blob = json.dumps(d).lower()
    assert "gps" not in blob and "serial" not in blob and "/x/y" not in blob
    assert d["exif_tags"]["EXIF:ISO"] == 100 and d["profile"]["camera_profile"] == "fake"


async def test_diagnose_with_masks(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        r = await c.call_tool("diagnose", {"photo_id": f"{sid}-p0000", "kinds": ["noise", "ca"],
                                           "include_masks": True})
    d = data(r)
    assert set(d["stats"]) == {"noise", "ca"}
    assert len(images(r)) == 2 and all(max(i.size) <= 1024 for i in images(r))


async def test_set_rating_body(server, dirs, engine):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        pid = f"{sid}-p0001"
        assert data(await c.call_tool("set_rating", {"photo_id": pid, "rating": 4, "flag": "pick"})) == {
            "photo_id": pid, "rating": 4, "flag": "pick"}
        assert data(await c.call_tool("set_rating", {"photo_id": pid, "flag": "none"}))["flag"] is None
        assert ("POST", f"/api/photos/{pid}/rating", {"flag": None}) in engine.requests
        assert (await c.call_tool("set_rating", {"photo_id": pid})).is_error
        assert (await c.call_tool("set_rating", {"photo_id": pid, "rating": 9})).is_error


async def test_auto_cull_waits_and_counts(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        d = data(await c.call_tool("auto_cull_technical", {"session_id": sid, "wait_seconds": 5}))
        assert d["state"] == "done" and d["verdicts"] == {"keep": 2, "reject": 2}
        k = data(await c.call_tool("list_photos", {"session_id": sid, "verdict": "keep"}))
        assert k["matched"] == 2


async def test_recipe_patch_validation(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        g = data(await c.call_tool("get_recipe", {"session_id": sid}))
        assert g["recipe"]["film"] == "kodak_ektar_100" and "exposure_ev" in g["schema"]
        bad = await c.call_tool("patch_recipe", {"session_id": sid, "patch": {"exposure_ev": 5}})
        assert bad.is_error and "exposure_ev" in texts(bad)[0]
        bad = await c.call_tool("patch_recipe", {"session_id": sid, "patch": {"hair": "max", "nope": 1}})
        assert bad.is_error and "nope" in texts(bad)[0] and "hair" in texts(bad)[0]
        ok = data(await c.call_tool("patch_recipe", {"session_id": sid,
                                                     "patch": {"exposure_ev": 0.3, "nr": "strong"}}))
        assert ok["recipe"]["exposure_ev"] == 0.3 and ok["changed"]["exposure_ev"]["to"] == 0.3
        assert ok["ignored_by_engine"] == ["nr"]
        # other sessions / default recipe untouched
        assert data(await c.call_tool("get_recipe", {}))["recipe"]["exposure_ev"] == 0.0


async def test_develop_sample_before_after(server, dirs, engine):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        await c.call_tool("patch_recipe", {"session_id": sid, "patch": {"grain": 0}})
        r = await c.call_tool("develop_sample", {"photo_id": f"{sid}-p0000", "wait_seconds": 5,
                                                 "recipe_patch": {"exposure_ev": -0.5}})
    assert not r.is_error, texts(r)
    d = data(r)
    assert d["state"] == "done" and d["recipe"]["grain"] == 0 and d["recipe"]["exposure_ev"] == -0.5
    ims = images(r)
    assert len(ims) == 2 and all(max(i.size) <= 1024 for i in ims)
    assert any("BEFORE" in t for t in texts(r)) and any("AFTER" in t for t in texts(r))


async def test_develop_sample_needs_raw_and_trained_film(server, dirs):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        r = await c.call_tool("develop_sample", {"photo_id": f"{sid}-p0003"})
        assert r.is_error and "RAW" in texts(r)[0]
        r = await c.call_tool("develop_sample", {"photo_id": f"{sid}-p0000", "recipe_patch": {"film": "other"}})
        assert r.is_error and "E_FILM_UNTRAINED" in texts(r)[0]


async def test_one_heavy_job_at_a_time(server, dirs, engine):
    engine.job_steps_to_finish = 3
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        b = data(await c.call_tool("develop_batch", {"photo_ids": [f"{sid}-p0000", f"{sid}-p0001"]}))
        r = await c.call_tool("develop_batch", {"photo_ids": [f"{sid}-p0002"]})
        assert r.is_error and "E_BUSY" in texts(r)[0]
        s = data(await c.call_tool("job_status", {"job_id": b["job_id"], "wait_seconds": 5}))
        assert s["state"] == "done" and len(s["outputs"]) == 2
        assert not (await c.call_tool("develop_batch", {"photo_ids": [f"{sid}-p0002"]})).is_error


async def test_job_status_preview_and_cancel(server, dirs, engine):
    engine.job_steps_to_finish = 10
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        r = data(await c.call_tool("develop_sample", {"photo_id": f"{sid}-p0000", "wait_seconds": 0}))
        assert r["state"] in ("queued", "running") and "job_status" in r["next"]
        assert data(await c.call_tool("cancel_job", {"job_id": r["job_id"]}))["cancel_requested"]
        assert (await c.call_tool("cancel_job", {"job_id": "j-not-mine"})).is_error
        engine.job_steps_to_finish = 1
        r2 = data(await c.call_tool("develop_sample", {"photo_id": f"{sid}-p0001", "wait_seconds": 0}))
        s = await c.call_tool("job_status", {"job_id": r2["job_id"], "wait_seconds": 5, "include_preview": True})
        assert len(images(s)) == 1


async def test_export_requires_confirm_and_allowed_dest(server, dirs, engine):
    async with Client(server) as c:
        sid = await open_shoot(c, dirs)
        ids = [f"{sid}-p0000"]
        r = await c.call_tool("export", {"photo_ids": ids, "dest_dir": str(dirs["exports"] / "out")})
        assert r.is_error and "E_CONFIRM_REQUIRED" in texts(r)[0]
        r = await c.call_tool("export", {"photo_ids": ids, "dest_dir": str(dirs["outside"]), "confirm": True})
        assert r.is_error and "E_NOT_ALLOWED" in texts(r)[0]
        r = await c.call_tool("export", {"photo_ids": ids, "dest_dir": str(dirs["shoot"] / "out"), "confirm": True})
        assert r.is_error
        assert not any(p == "/api/export" for _, p, _ in engine.requests)
        ok = data(await c.call_tool("export", {"photo_ids": ids, "dest_dir": str(dirs["exports"] / "out"),
                                               "confirm": True, "long_edge": 2048}))
        sent = next(b for m, p, b in engine.requests if p == "/api/export")
        assert sent["develop_missing"] is False and sent["long_edge"] == 2048
        assert ok["dest_dir"] == str((dirs["exports"] / "out").resolve())


async def test_list_films_and_sessions(server, dirs, engine):
    engine.add_folder(dirs["outside"])
    async with Client(server) as c:
        await open_shoot(c, dirs)
        films = data(await c.call_tool("list_films", {}))
        assert films["films"][0]["status"] == "trained" and "swatch_url" not in films["films"][0]
        sessions = data(await c.call_tool("list_sessions", {}))["sessions"]
    assert [s["name"] for s in sessions] == ["shoot_a"]
