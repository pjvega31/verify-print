"""Tests for mesh_repair (PyMeshLab wrapper)."""
from pathlib import Path

import pytest
import trimesh

pymeshlab = pytest.importorskip("pymeshlab")

from verify_print.mesh_repair import check_and_repair


@pytest.fixture
def watertight_box_stl(tmp_path: Path) -> Path:
    box = trimesh.creation.box(extents=(50, 30, 20))
    p = tmp_path / "good_box.stl"
    box.export(str(p), file_type="stl")
    return p


@pytest.fixture
def box_with_hole_stl(tmp_path: Path) -> Path:
    """Box with one face removed → open hole."""
    box = trimesh.creation.box(extents=(50, 30, 20))
    faces = box.faces[:-2]
    broken = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)
    p = tmp_path / "holed_box.stl"
    broken.export(str(p), file_type="stl")
    return p


def test_clean_box_passes(watertight_box_stl: Path):
    report = check_and_repair(watertight_box_stl)
    assert report.passed, f"Expected pass, got issues: {report.issues}"
    assert report.self_intersecting_faces == 0
    assert report.holes == 0


def test_holed_box_detected(box_with_hole_stl: Path):
    report = check_and_repair(box_with_hole_stl)
    assert not report.passed
    assert report.holes > 0


def test_repair_closes_hole(box_with_hole_stl: Path, tmp_path: Path):
    repaired_path = tmp_path / "repaired.stl"
    report = check_and_repair(
        box_with_hole_stl,
        repair=True,
        output_path=repaired_path,
    )
    assert report.repaired
    assert repaired_path.exists()


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        check_and_repair(tmp_path / "nope.stl")
