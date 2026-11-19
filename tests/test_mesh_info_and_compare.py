"""Tests for P2/P3 additions: mesh_info fast mode + iteration_compare."""
import json
from pathlib import Path

import pytest
import trimesh

from verify_print.iteration_compare import compare_reports
from verify_print.mesh_info import mesh_info


# ---------- mesh_info ----------


@pytest.fixture
def small_box_stl(tmp_path: Path) -> Path:
    box = trimesh.creation.box(extents=(30, 20, 10))
    p = tmp_path / "small.stl"
    box.export(str(p), file_type="stl")
    return p


@pytest.fixture
def oversized_stl(tmp_path: Path) -> Path:
    box = trimesh.creation.box(extents=(300, 20, 10))  # 300mm exceeds P1S 256mm
    p = tmp_path / "oversized.stl"
    box.export(str(p), file_type="stl")
    return p


def test_mesh_info_basic(small_box_stl: Path):
    r = mesh_info(small_box_stl)
    assert r.bbox_mm == pytest.approx((30, 20, 10))
    assert r.volume_mm3 == pytest.approx(30 * 20 * 10)
    assert r.face_count == 12  # box = 12 triangles
    assert r.passed


def test_mesh_info_fits_build_volume(small_box_stl: Path):
    r = mesh_info(small_box_stl, build_volume_mm=(256, 256, 256))
    assert r.fits_build_volume is True
    assert r.passed


def test_mesh_info_oversized_fails(oversized_stl: Path):
    r = mesh_info(oversized_stl, build_volume_mm=(256, 256, 256))
    assert r.fits_build_volume is False
    assert not r.passed
    assert any("exceeds build volume" in i for i in r.issues)


def test_mesh_info_mass_estimate_petg(small_box_stl: Path):
    r = mesh_info(small_box_stl, material_density=1.27)  # PETG
    # 30 × 20 × 10 mm^3 = 6000 mm^3 = 6 cm^3 × 1.27 g/cm^3 = 7.62 g
    assert r.estimated_grams == pytest.approx(7.62, rel=0.01)


def test_mesh_info_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        mesh_info(tmp_path / "nope.stl")


# ---------- iteration_compare ----------


def _make_report(orphans=(), ghosts=(), mesh_fails=()):
    """Helper to build a minimal verify-print-all-style report dict."""
    return {
        "params": {
            "orphans": [{"dict_name": d, "key": k} for (d, k) in orphans],
            "ghosts": [{"dict_name": d, "key": k} for (d, k) in ghosts],
        },
        "mesh": [
            {"file": f, "passed": False, "issues": ["broken"]}
            for f in mesh_fails
        ],
        "slice": [],
    }


def test_compare_orphans_resolved(tmp_path: Path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(_make_report(orphans=[("CASE", "x"), ("CASE", "y")])))
    after.write_text(json.dumps(_make_report(orphans=[("CASE", "x")])))

    r = compare_reports(before, after)
    assert r.orphans_resolved == 1  # y was fixed
    assert r.orphans_introduced == 0
    assert r.overall_verdict == "IMPROVED"


def test_compare_orphan_regression(tmp_path: Path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(_make_report(orphans=[("CASE", "x")])))
    after.write_text(json.dumps(_make_report(orphans=[("CASE", "x"), ("CASE", "new")])))

    r = compare_reports(before, after)
    assert r.orphans_resolved == 0
    assert r.orphans_introduced == 1
    assert r.overall_verdict == "REGRESSED"
    assert not r.passed


def test_compare_mixed_changes(tmp_path: Path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(_make_report(
        orphans=[("CASE", "old")],
        mesh_fails=["bad1.stl"],
    )))
    after.write_text(json.dumps(_make_report(
        orphans=[("CASE", "new")],
        mesh_fails=["bad2.stl"],
    )))

    r = compare_reports(before, after)
    assert r.orphans_resolved == 1  # old gone
    assert r.orphans_introduced == 1  # new added
    assert r.overall_verdict == "MIXED"


def test_compare_slice_time_delta(tmp_path: Path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before_report = _make_report()
    before_report["slice"] = [{
        "file": "case.stl",
        "passed": True,
        "print_time_seconds": 44 * 3600,  # 44h bug
    }]
    after_report = _make_report()
    after_report["slice"] = [{
        "file": "case.stl",
        "passed": True,
        "print_time_seconds": 22 * 3600,  # post-fix
    }]
    before.write_text(json.dumps(before_report))
    after.write_text(json.dumps(after_report))

    r = compare_reports(before, after)
    assert len(r.slice_print_time_delta_seconds) == 1
    delta = r.slice_print_time_delta_seconds[0]
    assert delta["delta_seconds"] == -22 * 3600  # 22h reduction
