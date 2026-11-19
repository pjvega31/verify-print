"""Tests for slicer_simulate — parser-only unit tests.

Real slicer invocation is too slow for pytest; covered by manual integration run.
"""
import json
from pathlib import Path

import pytest

from verify_print.slicer_simulate import (
    _parse_3mf_gcode,
    _parse_slicer_output,
    _parse_time_string,
    _human_time,
    _read_max_volumetric_speed,
)


def test_parse_time_string_hms():
    assert _parse_time_string("21h 50m 33s") == 21 * 3600 + 50 * 60 + 33
    assert _parse_time_string("2h 5m") == 2 * 3600 + 5 * 60
    assert _parse_time_string("45s") == 45


def test_parse_time_string_seconds_only():
    assert _parse_time_string("1800") == 1800


def test_human_time():
    assert _human_time(21 * 3600 + 50 * 60 + 33) == "21h 50m 33s"
    assert _human_time(125) == "2m 05s"


def test_parse_slicer_output_orca_style():
    stdout = """
    Slicing plate 1...
    ; estimated printing time (normal mode) = 21h 50m 33s
    ; total filament used [g] = 245.6
    ; total filament used [mm] = 81234.5
    ; total layers count = 815
    Done.
    """
    parsed = _parse_slicer_output(stdout, "")
    assert parsed["print_time_seconds"] == 21 * 3600 + 50 * 60 + 33
    assert parsed["filament_grams"] == pytest.approx(245.6)
    assert parsed["layer_count"] == 815


def test_parse_slicer_output_empty():
    parsed = _parse_slicer_output("", "")
    assert parsed == {}


def test_read_max_volumetric_speed_direct(tmp_path: Path):
    profile = tmp_path / "test.json"
    profile.write_text(json.dumps({"filament_max_volumetric_speed": ["12.0"]}))
    assert _read_max_volumetric_speed(profile) == pytest.approx(12.0)


def test_read_max_volumetric_speed_low_value(tmp_path: Path):
    """Real-world bug: speed=2 in a hollow profile."""
    profile = tmp_path / "buggy.json"
    profile.write_text(json.dumps({"filament_max_volumetric_speed": [2.0]}))
    assert _read_max_volumetric_speed(profile) == pytest.approx(2.0)


def test_read_max_volumetric_speed_missing(tmp_path: Path):
    """Profile inherits from base — direct lookup returns None."""
    profile = tmp_path / "minimal.json"
    profile.write_text(json.dumps({"name": "test", "inherits": "nonexistent_parent"}))
    # Since parent doesn't exist anywhere, returns None
    result = _read_max_volumetric_speed(profile)
    assert result is None


def test_parse_3mf_gcode_handles_missing_file(tmp_path: Path):
    # File doesn't exist — should return empty dict, not crash
    assert _parse_3mf_gcode(tmp_path / "nope.3mf") == {}


# ---------- Bambu Studio 2.8 CLI migration (2026-09-14) ----------

import zipfile

from verify_print.slicer_simulate import _parse_3mf_slice_info, build_slicer_cmd


def _fake_bambu_3mf(path: Path, prediction="3342", used_g="25.15", used_m="8.37") -> Path:
    slice_info = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="prediction" value="{prediction}"/>
    <metadata key="weight" value="{used_g}"/>
    <metadata key="support_used" value="false"/>
    <filament id="1" type="PETG" color="#0A0A1A" used_m="{used_m}" used_g="{used_g}"/>
  </plate>
</config>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/slice_info.config", slice_info)
        zf.writestr("Metadata/plate_1.gcode", "; total layer number: 191\n; total filament weight [g] : 25.15\n; total filament length [mm] : 8366.40\nG28\n")
    return path


def test_parse_3mf_slice_info_bambu_28(tmp_path: Path):
    """Bambu 2.8 stopped writing `; estimated printing time` in the gcode; the
    summary lives in Metadata/slice_info.config (verified on topper_burger.stl)."""
    parsed = _parse_3mf_slice_info(_fake_bambu_3mf(tmp_path / "out.3mf"))
    assert parsed["print_time_seconds"] == 3342
    assert parsed["filament_grams"] == pytest.approx(25.15)
    assert parsed["filament_meters"] == pytest.approx(8.37)
    assert parsed["support_used"] is False


def test_parse_3mf_slice_info_hollow_filament_reports_zero(tmp_path: Path):
    """A filament profile without density yields used_g=0.00 — must surface as 0.0,
    not None, so slice_stl can warn instead of silently passing."""
    parsed = _parse_3mf_slice_info(_fake_bambu_3mf(tmp_path / "out.3mf", used_g="0.00"))
    assert parsed["filament_grams"] == 0.0
    assert parsed["filament_meters"] == pytest.approx(8.37)


def test_parse_3mf_slice_info_missing(tmp_path: Path):
    assert _parse_3mf_slice_info(tmp_path / "nope.3mf") == {}
    with zipfile.ZipFile(tmp_path / "nosi.3mf", "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", "G28\n")
    assert _parse_3mf_slice_info(tmp_path / "nosi.3mf") == {}


def test_parse_3mf_gcode_bambu_header(tmp_path: Path):
    parsed = _parse_3mf_gcode(_fake_bambu_3mf(tmp_path / "out.3mf"))
    assert parsed["layer_count"] == 191
    assert parsed["filament_grams"] == pytest.approx(25.15)
    assert parsed["filament_meters"] == pytest.approx(8.3664)


def test_build_slicer_cmd_bambu_flags(tmp_path: Path):
    """--skip-useless-pick takes NO value (a trailing "1" is parsed as an input file
    -> return_code -3 "input files not found"); --mstpp bounds slicing time in-process."""
    cmd = build_slicer_cmd(
        "bambu", "/bin/bambu", tmp_path / "m.stl", tmp_path / "f.json",
        tmp_path / "p.json", tmp_path / "mc.json", tmp_path / "o.3mf",
        tmp_path / "sd", timeout_seconds=300,
    )
    assert "--skip-useless-pick" in cmd
    assert cmd[cmd.index("--skip-useless-pick") + 1].startswith("--export-3mf")
    assert cmd[cmd.index("--mstpp") + 1] == "270"
    assert cmd[-1] == str(tmp_path / "m.stl")


def test_build_slicer_cmd_orca_has_no_bambu_flags(tmp_path: Path):
    cmd = build_slicer_cmd(
        "orca", "/bin/orca", tmp_path / "m.stl", tmp_path / "f.json",
        tmp_path / "p.json", tmp_path / "mc.json", tmp_path / "o.3mf",
        tmp_path / "sd", timeout_seconds=300,
    )
    assert "--skip-useless-pick" not in cmd and "--mstpp" not in cmd
