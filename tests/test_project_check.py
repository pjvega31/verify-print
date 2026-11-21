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


def _r1(findings):
    return [(x.severity, x.message) for x in findings if x.rule == "R1"]


def test_r1_custom_profile_with_low_speed_blocks():
    """The original bug: a hollow custom filament profile fell back to 2 mm^3/s."""
    f = check_settings({**GOOD, "filament_settings_id": ["Volta-PETG-Black", "Bambu PLA Matte @BBL X1C"],
                        "filament_max_volumetric_speed": ["2", "22"]})
    r1 = _r1(f)
    assert [sev for sev, _ in r1] == ["FAIL"]
    assert "filament 1 (Volta-PETG-Black)" in r1[0][1] and "custom profile" in r1[0][1]


def test_r1_project_override_of_stock_profile_blocks():
    f = check_settings({**GOOD, "filament_max_volumetric_speed": ["2", "22"],
                        "different_settings_to_system": ["", "filament_max_volumetric_speed", "", ""]})
    r1 = _r1(f)
    assert [sev for sev, _ in r1] == ["FAIL"] and "changed it from the stock profile" in r1[0][1]


def test_r1_stock_low_speed_is_info_not_fail():
    """Stock 0.2-nozzle and TPU profiles ship with 0.5-4 mm^3/s; that must not block."""
    tpu = check_settings({**GOOD, "filament_settings_id": ["Bambu TPU 95A @BBL P1S", "Bambu PLA Matte @BBL X1C"],
                          "filament_max_volumetric_speed": ["3.6", "22"]})
    assert [sev for sev, _ in _r1(tpu)] == ["INFO"]
    small = check_settings({**GOOD, "nozzle_diameter": ["0.2"],
                            "filament_settings_id": ["Bambu PETG HF @BBL A1 0.2 nozzle"] * 2,
                            "filament_max_volumetric_speed": ["1", "1"]})
    assert _r1(small) == []          # 1 mm^3/s is ordinary on a 0.2 mm nozzle
    slow_stock = check_settings({**GOOD, "nozzle_diameter": ["0.2"],
                                 "filament_settings_id": ["Generic HIPS @BBL H2D 0.2 nozzle"] * 2,
                                 "filament_max_volumetric_speed": ["0.4", "0.4"]})
    assert [sev for sev, _ in _r1(slow_stock)] == ["INFO", "INFO"]


def test_r1_floor_depends_on_nozzle():
    custom = {**GOOD, "filament_settings_id": ["My PETG", "My PETG"], "nozzle_diameter": ["0.2"]}
    # 0.2 mm nozzle -> floor 0.5 (stock profiles there run 0.5-2): 1 is fine, 0.4 is not
    assert not _r1(check_settings({**custom, "filament_max_volumetric_speed": ["1", "2"]}))
    assert [sev for sev, _ in _r1(check_settings({**custom, "filament_max_volumetric_speed": ["0.4", "2"]}))] == ["FAIL"]
    # the same 1 mm^3/s on a 0.4 mm nozzle is the bug
    wide = {**custom, "nozzle_diameter": ["0.4"], "filament_max_volumetric_speed": ["1", "22"]}
    assert [sev for sev, _ in _r1(check_settings(wide))] == ["FAIL"]
    # an explicit --min-speed is used as given
    assert len(_r1(check_settings({**custom, "filament_max_volumetric_speed": ["2", "2"]}, min_speed=3.0))) == 2


def test_r1_unused_slot_does_not_block():
    s = {**GOOD, "filament_settings_id": ["My PETG", "My PETG"], "filament_max_volumetric_speed": ["22", "2"]}
    assert [sev for sev, _ in _r1(check_settings(s, used_filaments={1}))] == ["INFO"]
    assert [sev for sev, _ in _r1(check_settings(s, used_filaments={1, 2}))] == ["FAIL"]


def test_r7_labels_printer_entry_as_printer():
    f = check_settings({**GOOD, "different_settings_to_system": ["", "", "", "nozzle_diameter"]})
    r7 = [x.message for x in f if x.rule == "R7"]
    assert len(r7) == 1 and r7[0].startswith("printer differs")


def test_3mf_without_slicer_settings_is_not_a_pass(tmp_path: Path):
    plain = tmp_path / "cad_export.3mf"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    r = check_project(plain)
    assert not r.passed and [f.rule for f in r.issues] == ["R0"]
    assert "NOT CHECKED" in r.format() and "PASS" not in r.format()


def test_per_object_overrides_are_checked(tmp_path: Path):
    model = MODEL_SETTINGS.replace(
        '<metadata key="name" value="Body113"/>',
        '<metadata key="name" value="Body113"/><metadata key="layer_height" value="0.06"/>'
        '<metadata key="sparse_infill_density" value="100%"/><metadata key="enable_support" value="1"/>')
    path = tmp_path / "p.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(GOOD))
        zf.writestr("Metadata/model_settings.config", model)
    r = check_project(path)
    msgs = [f.message for f in r.findings if f.message.startswith("object 'Body113' overrides")]
    assert sorted(f.rule for f in r.findings if f.message.startswith("object 'Body113'")) == ["R2", "R3", "R4"]
    assert any("layer_height = 0.06" in m for m in msgs) and r.passed  # WARNs do not block


def test_part_level_extruder_counts_as_in_use(tmp_path: Path):
    model = MODEL_SETTINGS.replace(
        '<metadata key="extruder" value="1"/></object>',
        '<metadata key="extruder" value="1"/><part id="1"><metadata key="extruder" value="2"/></part></object>')
    path = tmp_path / "p.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(GOOD))
        zf.writestr("Metadata/model_settings.config", model)
    used = {e for o in read_project(path)["objects"] for e in o["extruders"]}
    assert used == {1, 2}


def test_malformed_model_settings_warns_instead_of_crashing(tmp_path: Path):
    path = tmp_path / "p.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(GOOD))
        zf.writestr("Metadata/model_settings.config", "<config><object id=1></config>")
    r = check_project(path)
    assert r.passed and any(f.rule == "R0" and f.severity == "WARN" for f in r.findings)


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
