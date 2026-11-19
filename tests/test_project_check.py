"""Tests for project_check — parser + rules only (slicing covered by manual run)."""
import json
import zipfile
from pathlib import Path

from verify_print.project_check import check_project, check_settings, read_project

MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="4"><metadata key="name" value="Body113"/><metadata key="extruder" value="2"/></object>
  <object id="2"><metadata key="name" value="Body122"/><metadata key="extruder" value="1"/></object>
  <plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="4"/></model_instance></plate>
  <plate><metadata key="plater_id" value="2"/><model_instance><metadata key="object_id" value="2"/></model_instance></plate>
</config>"""

GOOD = {
    "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
    "print_settings_id": "0.20mm Standard @BBL X1C",
    "filament_settings_id": ["Bambu PLA Matte @BBL X1C", "Bambu PLA Matte @BBL X1C"],
    "filament_colour": ["#4D3324", "#D3B7A7"],
    "filament_max_volumetric_speed": ["22", "22"],
    "layer_height": "0.2", "sparse_infill_density": "5%", "wall_loops": "2",
    "enable_support": "0", "default_acceleration": "10000", "outer_wall_acceleration": "5000",
    "fuzzy_skin": "external", "fuzzy_skin_point_distance": "0.4", "fuzzy_skin_thickness": "0.3",
    "different_settings_to_system": ["fuzzy_skin;sparse_infill_density", "", ""],
}


def _fake_3mf(path: Path, settings: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        zf.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
    return path


def test_read_project_plates_and_extruders(tmp_path: Path):
    data = read_project(_fake_3mf(tmp_path / "p.3mf", GOOD))
    assert [p.index for p in data["plates"]] == [1, 2]
    assert data["plates"][0].objects == ["Body113"] and data["plates"][0].extruders == [2]
    assert data["plates"][1].objects == ["Body122"] and data["plates"][1].extruders == [1]


def test_pumpkin_like_project_passes_with_fuzzy_info(tmp_path: Path):
    r = check_project(_fake_3mf(tmp_path / "p.3mf", GOOD))
    assert r.passed
    rules = {f.rule for f in r.findings}
    assert "R6" in rules and "R7" in rules and "R1" not in rules
    r7 = next(f for f in r.findings if f.rule == "R7")
    assert "fuzzy_skin=external" in r7.message


def test_r1_low_volumetric_speed_blocks():
    f = check_settings({**GOOD, "filament_max_volumetric_speed": ["2", "22"]})
    fails = [x for x in f if x.severity == "FAIL"]
    assert len(fails) == 1 and fails[0].rule == "R1"


def test_r2_r3_r4_r5_warnings():
    f = check_settings({**GOOD, "layer_height": "0.08", "sparse_infill_density": "40%",
                        "wall_loops": "6", "enable_support": "1", "default_acceleration": "500"})
    rules = [x.rule for x in f if x.severity == "WARN"]
    assert rules.count("R2") == 1 and rules.count("R3") == 2 and rules.count("R4") == 1 and rules.count("R5") == 1


def test_r8_printer_mismatch():
    f = check_settings(GOOD, expected_printer="Bambu Lab P1S 0.2 nozzle")
    assert any(x.rule == "R8" for x in f)
    assert not any(x.rule == "R8" for x in check_settings(GOOD, expected_printer="Bambu Lab P1S 0.4 nozzle"))


def test_fuzzy_none_is_silent():
    assert not any(x.rule == "R6" for x in check_settings({**GOOD, "fuzzy_skin": "none"}))


def test_r5_zero_acceleration_means_inherit():
    """Bambu stores 0 for per-feature accelerations that fall back to default — not a slowdown."""
    assert not any(x.rule == "R5" for x in check_settings({**GOOD, "inner_wall_acceleration": "0"}))
