"""Bounded 3MF reads: untrusted downloads must not be able to exhaust memory."""
import json
import zipfile
from pathlib import Path

import pytest

from verify_print._safezip import MemberTooLarge, read_head_tail, read_member
from verify_print.cli import main
from verify_print.project_check import read_project


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_read_member_within_limit(tmp_path: Path):
    z = _zip(tmp_path / "ok.3mf", {"a.txt": b"hello"})
    with zipfile.ZipFile(z) as zf:
        assert read_member(zf, "a.txt", limit=10) == b"hello"


def test_read_member_rejects_oversized(tmp_path: Path):
    # 2 MB of zeros compresses to a few KB: the classic zip-bomb shape
    z = _zip(tmp_path / "bomb.3mf", {"Metadata/project_settings.config": b"0" * 2_000_000})
    assert z.stat().st_size < 50_000
    with zipfile.ZipFile(z) as zf:
        with pytest.raises(MemberTooLarge):
            read_member(zf, "Metadata/project_settings.config", limit=1_000_000)


def test_read_head_tail_keeps_only_ends(tmp_path: Path):
    body = b"H" * 10 + b"x" * 1_000_000 + b"T" * 10
    z = _zip(tmp_path / "g.3mf", {"plate_1.gcode": body})
    with zipfile.ZipFile(z) as zf:
        head, tail = read_head_tail(zf, "plate_1.gcode", n=10)
    assert head == b"H" * 10 and tail == b"T" * 10


def test_read_project_rejects_oversized_settings(tmp_path: Path, monkeypatch):
    import verify_print._safezip as sz
    monkeypatch.setattr(sz.read_member, "__defaults__", (1_000,))
    z = _zip(tmp_path / "big.3mf", {"Metadata/project_settings.config": json.dumps({"k": "v" * 5_000})})
    with pytest.raises(MemberTooLarge):
        read_project(z)


def test_cli_project_bad_zip_is_usage_error(tmp_path: Path, capsys):
    bad = tmp_path / "not_a_zip.3mf"
    bad.write_bytes(b"this is not a zip")
    assert main(["project", str(bad), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["passed"] is False
