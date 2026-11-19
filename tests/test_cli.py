"""Tests for CLI — focus on --json output + exit codes.

Smoke tests that hit the argparse layer end-to-end. Real slicer subprocess
is NOT invoked here (too slow); slicer-specific parsing is in test_slicer_simulate.
"""
import json
import sys
from io import StringIO
from pathlib import Path

import pytest
import trimesh

from verify_print.cli import main


@pytest.fixture
def watertight_box_stl(tmp_path: Path) -> Path:
    box = trimesh.creation.box(extents=(50, 30, 20))
    p = tmp_path / "good.stl"
    box.export(str(p), file_type="stl")
    return p


FIXTURES = Path(__file__).parent / "fixtures"


def _run_cli(args: list[str], capsys) -> tuple[int, str]:
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out


def test_mesh_human_pass(watertight_box_stl: Path, capsys):
    code, out = _run_cli(["mesh", str(watertight_box_stl)], capsys)
    assert code == 0
    assert "PASS" in out
    assert "watertight" in out.lower()


def test_mesh_json_pass(watertight_box_stl: Path, capsys):
    code, out = _run_cli(["mesh", str(watertight_box_stl), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["passed"] is True
    assert payload["is_watertight"] is True
    assert payload["num_disconnected_shells"] == 1
    assert "bounding_box_mm" in payload
    assert len(payload["bounding_box_mm"]) == 3


def test_params_human_fail(capsys):
    code, out = _run_cli(
        [
            "params",
            str(FIXTURES / "params_with_orphans.py"),
            str(FIXTURES / "cad_with_some_usage.py"),
        ],
        capsys,
    )
    assert code == 1
    assert "ORPHAN" in out
    assert "top_magnet_count" in out


def test_params_json_fail(capsys):
    code, out = _run_cli(
        [
            "params",
            str(FIXTURES / "params_with_orphans.py"),
            str(FIXTURES / "cad_with_some_usage.py"),
            "--json",
        ],
        capsys,
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["passed"] is False
    assert len(payload["orphans"]) >= 4
    # Sets are serialized as sorted lists
    assert isinstance(payload["defined_keys"]["CASE"], list)


def test_all_human_no_slicer_or_repair(tmp_path: Path, capsys):
    # Build a synthetic project dir
    (tmp_path / "params.py").write_text("CASE = {'x': 1, 'orphan_key': 2}")
    (tmp_path / "build.py").write_text("from params import CASE\nprint(CASE['x'])")
    box = trimesh.creation.box(extents=(20, 20, 20))
    box.export(str(tmp_path / "model.stl"), file_type="stl")

    code, out = _run_cli(["all", str(tmp_path)], capsys)
    # Should fail due to orphan 'orphan_key'
    assert code == 1
    assert "params" in out.lower()
    assert "mesh" in out.lower()


def test_all_json_aggregate(tmp_path: Path, capsys):
    (tmp_path / "params.py").write_text("CASE = {'x': 1, 'orphan_key': 2}")
    (tmp_path / "build.py").write_text("from params import CASE\nprint(CASE['x'])")
    box = trimesh.creation.box(extents=(20, 20, 20))
    box.export(str(tmp_path / "model.stl"), file_type="stl")

    code, out = _run_cli(["all", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert payload["passed"] is False
    assert payload["failures"] >= 1
    assert payload["stl_files_found"] == 1
    assert payload["code_files_scanned"] == 1
    assert payload["params"]["passed"] is False
    assert payload["include_slice"] is False
    assert payload["include_repair"] is False
    assert len(payload["mesh"]) == 1
    assert payload["mesh"][0]["passed"] is True


def test_all_includes_repair_when_flag_set(tmp_path: Path, capsys):
    pymeshlab = pytest.importorskip("pymeshlab")
    (tmp_path / "params.py").write_text("CASE = {'x': 1}")
    (tmp_path / "build.py").write_text("from params import CASE\nprint(CASE['x'])")
    box = trimesh.creation.box(extents=(20, 20, 20))
    box.export(str(tmp_path / "model.stl"), file_type="stl")

    code, out = _run_cli(
        ["all", str(tmp_path), "--include-repair", "--json"],
        capsys,
    )
    payload = json.loads(out)
    assert payload["include_repair"] is True
    assert len(payload["repair"]) == 1
