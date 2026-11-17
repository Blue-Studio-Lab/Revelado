# SPDX-License-Identifier: GPL-3.0-or-later
"""Claude culling request building and response handling, with a MOCKED client (no network, no cost)."""
from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from revelado.cull import claude as C
from revelado.errors import ReveladoError


def _sent(oid: str, w: int = 512, h: int = 341) -> C.Sent:
    im = Image.new("RGB", (w, h), (120, 110, 100))
    b64, sha = C.encode_jpeg(im)
    return C.Sent(oid, b64, w, h, sha)


def test_encode_strips_exif():
    im = Image.new("RGB", (64, 64))
    exif = Image.Exif()
    exif[0x013B] = "Some Photographer"        # Artist
    exif[0x8825] = {}                          # GPS IFD pointer
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    with_exif = Image.open(io.BytesIO(buf.getvalue()))
    assert with_exif.getexif()
    b64, _ = C.encode_jpeg(with_exif)
    out = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert not out.getexif(), "EXIF must be removed before sending"


def test_api_schema_strips_constraints_and_closes_objects():
    s = C.to_api_schema(C.PrefilterGroup)
    txt = json.dumps(s)
    for k in ("minimum", "maximum", "maxLength", "title"):
        assert f'"{k}"' not in txt
    defs = s.get("$defs", {})
    pre = defs["Prefilter"]
    assert pre["additionalProperties"] is False
    assert set(pre["required"]) == set(pre["properties"])


def test_build_params_layout_and_models():
    rt = C.route("prefilter")
    frames = [{"oid": "p0001", "burst": "b0001", "burst_pos": 1, "burst_size": 2, "sent": _sent("p0001"),
               "tech": {"sharpness_face": 10.0, "verdict": "keep", "exposure": {"verdict": "correcta"}}},
              {"oid": "p0002", "burst": "b0001", "burst_pos": 2, "burst_size": 2, "sent": _sent("p0002"),
               "tech": None}]
    content = C.prefilter_content(C.session_context(n_frames=2), "g01", frames)
    p = C.build_params(rt, content, C.PrefilterGroup)
    assert p["model"] == "claude-haiku-5-5"
    assert p["output_config"]["effort"] == "low"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    blocks = p["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}          # session context = breakpoint 2
    assert [b["type"] for b in blocks[1:]] == ["text", "image", "text", "image", "text"]
    assert blocks[1]["text"].startswith("[p0001 · burst b0001 (1/2) · 512×341]")
    # nothing identifying leaves: no file names / paths
    raw = json.dumps(p)
    assert "DSC" not in raw and "/Users" not in raw and ".ARW" not in raw
    rt2 = C.route("finalist")
    assert rt2.model == "claude-opus-5-5" and rt2.effort == "medium"
    fc = C.finalist_content("ctx", "f01", [{"oid": "p0001", "burst": "b1", "sent": _sent("p0001", 1024, 683),
                                            "face": _sent("p0001_h1", 448, 448), "tech": {}, "stage1": {"verdict": "keep"}}])
    assert sum(1 for b in fc if b["type"] == "image") == 2
    assert any("p0001_h1 · 448×448" in b.get("text", "") for b in fc)


class FakeMessages:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        body = self.payloads.pop(0)
        stop = body.pop("_stop", "end_turn")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(body))], stop_reason=stop,
            model=params["model"], usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                         cache_read_input_tokens=500, cache_creation_input_tokens=0),
            _request_id="req_test")


def test_call_structured_parses_and_clamps():
    fm = FakeMessages([{"group_id": "g01", "burst_best": ["p0001"], "frames": [
        {"id": "p0001", "verdict": "keep", "score": 140, "moment": 4, "expression": 9, "composition": 3, "light": 3,
         "eyes": "open", "reject_reason": None, "note": "x" * 200}]}])
    client = SimpleNamespace(messages=fm)
    led = C.Ledger()
    out = C.call_structured(client, C.route("prefilter"), [{"type": "text", "text": "hi"}], C.PrefilterGroup, led)
    f = out.frames[0]
    assert f.score == 100 and f.expression == 5 and len(f.note) == 90       # clamped, not retried
    assert led.total()["calls"] == 1 and led.total()["usd"] > 0
    assert fm.calls[0]["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_refusal_is_reported():
    fm = FakeMessages([{"_stop": "refusal"}])
    with pytest.raises(ReveladoError) as e:
        C.call_structured(SimpleNamespace(messages=fm), C.route("finalist"), [], C.FinalGroup, C.Ledger())
    assert e.value.code == "E_AI_REFUSAL"


def test_estimate_has_no_side_effects():
    e = C.estimate(150)
    assert e["frames"] == 150 and e["est_usd"] > 0 and e["requests"] >= 2
