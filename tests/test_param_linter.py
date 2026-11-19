"""Tests for param_linter — Layer D bug catcher."""
from pathlib import Path

import pytest

from verify_print.param_linter import lint_params


FIXTURES = Path(__file__).parent / "fixtures"


def test_finds_orphan_magnet_keys():
    """The magnet keys in CASE should be flagged as orphans (bug #4 reproduction)."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [FIXTURES / "cad_with_some_usage.py"],
    )
    orphan_keys = {(o.dict_name, o.key) for o in report.orphans}

    assert ("CASE", "top_magnet_count") in orphan_keys
    assert ("CASE", "top_magnet_diameter") in orphan_keys
    assert ("CASE", "top_magnet_thickness") in orphan_keys
    assert ("ENCAJE", "lip_color") in orphan_keys


def test_recognizes_used_keys():
    """Used keys must NOT be flagged as orphans."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [FIXTURES / "cad_with_some_usage.py"],
    )
    orphan_keys = {(o.dict_name, o.key) for o in report.orphans}

    assert ("CASE", "outer_width") not in orphan_keys
    assert ("CASE", "outer_depth") not in orphan_keys
    assert ("CASE", "outer_height") not in orphan_keys
    assert ("CASE", "wall_thickness") not in orphan_keys  # .get() access
    assert ("ENCAJE", "lip_height") not in orphan_keys
    assert ("ENCAJE", "lip_width") not in orphan_keys


def test_detects_ghost_reference():
    """Keys accessed in code but not defined in params should be flagged as ghosts."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [FIXTURES / "cad_with_some_usage.py"],
    )
    ghost_keys = {(g.dict_name, g.key) for g in report.ghosts}

    assert ("CASE", "nonexistent_key") in ghost_keys


def test_report_fails_when_orphans_present():
    """A report with orphans should not be 'passed'."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [FIXTURES / "cad_with_some_usage.py"],
    )
    assert not report.passed
    assert len(report.orphans) >= 4  # 3 magnet keys + lip_color


def test_dict_definitions_extracted():
    """The linter must find all UPPERCASE dict definitions in the params file."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [FIXTURES / "cad_with_some_usage.py"],
    )
    assert "CASE" in report.defined_keys
    assert "ENCAJE" in report.defined_keys
    assert "outer_width" in report.defined_keys["CASE"]
    assert "top_magnet_count" in report.defined_keys["CASE"]


def test_handles_missing_params_file():
    with pytest.raises(FileNotFoundError):
        lint_params(FIXTURES / "does_not_exist.py", [])


def test_handles_empty_code_file_list():
    """With no code files, every defined key is orphan."""
    report = lint_params(
        FIXTURES / "params_with_orphans.py",
        [],
    )
    # All defined keys become orphans
    total_defined = sum(len(keys) for keys in report.defined_keys.values())
    assert len(report.orphans) == total_defined
