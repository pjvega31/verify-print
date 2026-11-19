"""Tests for mesh_integrity — using trimesh primitives as synthetic STLs."""
from pathlib import Path

import pytest
import trimesh

from verify_print.mesh_integrity import check_mesh


@pytest.fixture
def watertight_box_stl(tmp_path: Path) -> Path:
    """A clean watertight box — should pass all checks."""
    box = trimesh.creation.box(extents=(50, 30, 20))
    stl_path = tmp_path / "good_box.stl"
    box.export(str(stl_path), file_type="stl")
    return stl_path


@pytest.fixture
def non_watertight_stl(tmp_path: Path) -> Path:
    """A box with one face removed → non-watertight."""
    box = trimesh.creation.box(extents=(50, 30, 20))
    # Remove top face (last 2 faces) to create a hole
    faces = box.faces[:-2]
    broken = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)
    stl_path = tmp_path / "broken_box.stl"
    broken.export(str(stl_path), file_type="stl")
    return stl_path


@pytest.fixture
def two_disjoint_boxes_stl(tmp_path: Path) -> Path:
    """Two boxes far apart in one STL — flotation pattern (bug #2 analog)."""
    box1 = trimesh.creation.box(extents=(20, 20, 20))
    box2 = trimesh.creation.box(extents=(20, 20, 20))
    box2.apply_translation((100, 0, 0))
    combined = trimesh.util.concatenate([box1, box2])
    stl_path = tmp_path / "two_boxes.stl"
    combined.export(str(stl_path), file_type="stl")
    return stl_path


def test_watertight_box_passes(watertight_box_stl: Path):
    report = check_mesh(watertight_box_stl)
    assert report.passed, f"Expected pass, got issues: {report.issues}"
    assert report.is_watertight
    assert report.is_winding_consistent
    assert report.is_volume
    assert report.num_disconnected_shells == 1
    assert report.volume_mm3 == pytest.approx(50 * 30 * 20)


def test_non_watertight_box_fails(non_watertight_stl: Path):
    report = check_mesh(non_watertight_stl)
    assert not report.passed
    assert not report.is_watertight
    assert any("watertight" in issue for issue in report.issues)


def test_two_disjoint_boxes_flagged_as_floating(two_disjoint_boxes_stl: Path):
    """Two disjoint boxes should flag as floating geometry (max_shells=1)."""
    report = check_mesh(two_disjoint_boxes_stl, max_disconnected_shells=1)
    assert not report.passed
    assert report.num_disconnected_shells == 2
    assert any("shells" in issue.lower() for issue in report.issues)


def test_two_boxes_allowed_when_max_shells_2(two_disjoint_boxes_stl: Path):
    """Same two boxes pass when max_shells permits."""
    report = check_mesh(two_disjoint_boxes_stl, max_disconnected_shells=2)
    # Shells check passes, but watertight + winding still need to be OK
    assert report.num_disconnected_shells == 2
    # The flotation issue specifically about shell count should NOT be in issues
    shell_issues = [i for i in report.issues if "shells" in i.lower()]
    assert len(shell_issues) == 0


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        check_mesh(tmp_path / "nope.stl")
