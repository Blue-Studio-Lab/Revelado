# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera name and generic sensor class (no RAW files needed)."""
from revelado.metadata import camera_name
from revelado.profiles import resolve_camera, sensor_format


def test_camera_name_no_duplicate_maker():
    assert camera_name("Canon", "Canon EOS 800D") == "Canon EOS 800D"
    assert camera_name("Sony", "ZV-E10") == "Sony ZV-E10"
    assert camera_name("Nikon", None) == "Nikon"
    assert camera_name("Nikon", "NIKON D850") == "Nikon D850"
    assert camera_name(None, None) is None


def test_sensor_format_from_crop_factor():
    assert sensor_format({"crop_factor": 1.5}) == "aps-c"
    assert sensor_format({"crop_factor": 1.6}) == "aps-c"
    assert sensor_format({"crop_factor": 1.0}) == "full-frame"
    assert sensor_format({"crop_factor": 2.0}) == "m43"
    assert sensor_format({"focal": 16, "focal35": 24}) == "aps-c"


def test_sensor_format_from_model_name():
    assert sensor_format({"make": "Sony", "model": "ZV-E10"}) == "aps-c"
    assert sensor_format({"make": "Canon", "model": "Canon EOS 800D"}) == "aps-c"
    assert sensor_format({"make": "Sony", "model": "ILCE-6400"}) == "aps-c"
    assert sensor_format({"make": "Sony", "model": "ILCE-7M4"}) == "unknown"
    assert sensor_format({"make": "Canon", "model": "Canon EOS 5D Mark IV"}) == "unknown"


def test_generic_profile_is_not_full_frame_for_apsc():
    cam = resolve_camera({"make": "Canon", "model": "Canon EOS 800D", "width": 6000})
    assert cam["sensor"]["format"] == "aps-c" and cam["sensor"]["width_mm"] == 23.5
