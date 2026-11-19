"""Tests for feature_color — CADQuery Assembly multi-color export.

CADQuery skipif: tests only run if cadquery is installed.
"""
import json
from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from verify_print.feature_color import export_features, _hex_to_rgb01


def test_hex_to_rgb01_basic():
    r, g, b, a = _hex_to_rgb01("#FF0000")
    assert r == 1.0
    assert g == 0.0
    assert b == 0.0
    assert a == 1.0


def test_hex_to_rgb01_cyan():
    r, g, b, _a = _hex_to_rgb01("#00BCD4")
    assert g == pytest.approx(0xBC / 255)
    assert b == pytest.approx(0xD4 / 255)


def test_hex_to_rgb01_no_hash():
    r, _g, _b, _a = _hex_to_rgb01("FF0000")
    assert r == 1.0


def test_hex_to_rgb01_invalid_raises():
    with pytest.raises(ValueError):
        _hex_to_rgb01("#FFF")


@pytest.fixture
def two_box_features():
    body = cq.Workplane("XY").box(50, 30, 20)
    post = cq.Workplane("XY").circle(2).extrude(10)
    return {
        "main_body": (body, "#00BCD4"),
        "alignment_post": (post, "#FFD600"),
    }


def test_export_features_stls_creates_files(tmp_path: Path, two_box_features):
    report = export_features(two_box_features, output_dir=tmp_path, format="stls")
    assert (tmp_path / "feature_main_body.stl").exists()
    assert (tmp_path / "feature_alignment_post.stl").exists()
    assert (tmp_path / "colors.json").exists()
    assert "main_body" in report.features_exported
    assert "alignment_post" in report.features_exported


def test_export_features_stls_color_manifest(tmp_path: Path, two_box_features):
    export_features(two_box_features, output_dir=tmp_path, format="stls")
    manifest = json.loads((tmp_path / "colors.json").read_text())
    assert manifest["colors"]["main_body"] == "#00BCD4"
    assert manifest["colors"]["alignment_post"] == "#FFD600"


def test_export_features_gltf_creates_single_file(tmp_path: Path, two_box_features):
    report = export_features(
        two_box_features, output_dir=tmp_path, format="gltf", base_name="enclosure_test"
    )
    assert (tmp_path / "enclosure_test.gltf").exists()
    assert report.format == "gltf"


def test_export_features_invalid_format_raises(tmp_path: Path, two_box_features):
    with pytest.raises(ValueError):
        export_features(two_box_features, output_dir=tmp_path, format="obj")
