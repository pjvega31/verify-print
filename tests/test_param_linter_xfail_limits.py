"""Documented limitations of param_linter as xfail tests.

These tests fail intentionally — they describe known false-negative
cases that v0.3 of the AST-based linter cannot resolve. Promoting any
of these to passing requires upgrading to a dataflow / type-inference
approach (out of scope for v0.3).

Documented honestly with xfail rather than silently missing.
"""
from pathlib import Path

import pytest

from verify_print.param_linter import lint_params


@pytest.fixture
def dynamic_key_params(tmp_path: Path) -> Path:
    p = tmp_path / "params.py"
    p.write_text(
        "CASE = {'top_left_inset': 5.0, 'top_right_inset': 5.0}\n"
    )
    return p


@pytest.fixture
def dynamic_key_code(tmp_path: Path) -> Path:
    p = tmp_path / "build.py"
    p.write_text(
        "from params import CASE\n"
        "for side in ('left', 'right'):\n"
        "    value = CASE[f'top_{side}_inset']\n"
        "    print(value)\n"
    )
    return p


@pytest.fixture
def aliased_dict_params(tmp_path: Path) -> Path:
    p = tmp_path / "params.py"
    p.write_text("CASE = {'used_via_alias': 99}\n")
    return p


@pytest.fixture
def aliased_dict_code(tmp_path: Path) -> Path:
    p = tmp_path / "build.py"
    p.write_text(
        "from params import CASE\n"
        "params = CASE\n"  # alias
        "x = params['used_via_alias']\n"
    )
    return p


@pytest.fixture
def hardcoded_fallback_params(tmp_path: Path) -> Path:
    p = tmp_path / "params.py"
    p.write_text("CASE = {'outer_width': 250.0}\n")
    return p


@pytest.fixture
def hardcoded_fallback_code(tmp_path: Path) -> Path:
    p = tmp_path / "build.py"
    p.write_text(
        "from params import CASE\n"
        "width = 250.0  # hardcoded duplicate, params drift risk\n"
        # Note: CASE['outer_width'] is NOT used here — orphan from linter's POV
    )
    return p


def test_dynamic_fstring_key_access_triggers_indirect_warning(
    dynamic_key_params: Path, dynamic_key_code: Path
):
    """v0.3 actually handles this conservatively: dynamic key access marks the
    whole dict as 'indirect access', which suppresses orphan detection for ALL
    its keys. False-negative-prone (real orphans hide too) but no false positives."""
    report = lint_params(dynamic_key_params, [dynamic_key_code])
    # CASE should appear in indirect_access_dicts
    assert "CASE" in report.indirect_access_dicts
    # And NO orphans reported for CASE keys
    case_orphans = [o for o in report.orphans if o.dict_name == "CASE"]
    assert case_orphans == []


@pytest.mark.xfail(
    reason="v0.3 limitation: dict aliasing (`p = CASE; p['x']`) is not resolved. "
           "Linter does not track variable assignments from known dict names.",
    strict=False,
)
def test_aliased_dict_access(aliased_dict_params: Path, aliased_dict_code: Path):
    report = lint_params(aliased_dict_params, [aliased_dict_code])
    assert ("CASE", "used_via_alias") not in {(o.dict_name, o.key) for o in report.orphans}


def test_hardcoded_value_duplicating_param_is_orphan_today(
    hardcoded_fallback_params: Path, hardcoded_fallback_code: Path
):
    """Today, CASE['outer_width'] is reported as orphan even though the code has
    a hardcoded `width = 250.0` that duplicates its value. v0.4 could add value-
    equivalence check; for now we document the gap explicitly."""
    report = lint_params(hardcoded_fallback_params, [hardcoded_fallback_code])
    orphan_keys = {(o.dict_name, o.key) for o in report.orphans}
    # Today this IS reported as orphan — that's the gap, not a bug.
    assert ("CASE", "outer_width") in orphan_keys
