"""Regenerate docs/demo.svg, the terminal screenshot at the top of the README.

Builds a small sample 3MF project in a temp folder (the kind of settings a
downloaded model can carry), runs the real `verify-print project` on it, and
draws whatever the CLI printed. Nothing in the image is typed by hand, so it
cannot drift from the tool's actual output.

    python scripts/make_demo.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "demo.svg"
SAMPLE = "downloaded_model.3mf"

# What the uploader saved into the project: extra-fine layers, dense infill, thick
# walls, supports, and a custom filament profile capped at 2 mm^3/s.
PROJECT_SETTINGS = {
    "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
    "print_settings_id": "0.08mm Extra Fine @BBL X1C",
    "filament_settings_id": ["My-PETG"],
    "filament_colour": ["#1F1F1F"],
    "nozzle_diameter": ["0.4"],
    "filament_max_volumetric_speed": ["2"],
    "layer_height": "0.08",
    "sparse_infill_density": "40%",
    "wall_loops": "6",
    "enable_support": "1",
    "support_type": "tree(auto)",
    "default_acceleration": "10000",
    "outer_wall_acceleration": "5000",
    "fuzzy_skin": "none",
    "different_settings_to_system": ["sparse_infill_density;wall_loops;enable_support", "", ""],
}
MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="1"><metadata key="name" value="phone_stand"/><metadata key="extruder" value="1"/></object>
  <plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="1"/></model_instance></plate>
</config>"""


def build_sample(folder: Path) -> str:
    with zipfile.ZipFile(folder / SAMPLE, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(PROJECT_SETTINGS))
        zf.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
    return SAMPLE


def run_cli(folder: Path, threemf: str) -> tuple[list[str], int]:
    proc = subprocess.run(
        [sys.executable, "-m", "verify_print.cli", "project", threemf],
        cwd=folder, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    return proc.stdout.rstrip("\n").splitlines(), proc.returncode


# --- drawing -----------------------------------------------------------------

BG, BAR, TEXT, DIM = "#0d1117", "#161b22", "#c9d1d9", "#8b949e"
RED, GREEN, YELLOW, WHITE = "#ff7b72", "#3fb950", "#d29922", "#f0f6fc"
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SIZE, LINE_H, PAD_X, BAR_H, WIDTH, COLS = 14, 21, 22, 36, 860, 92


def wrap(line: str) -> list[str]:
    """Wrap like a terminal would, keeping the line's own indent on continuation rows."""
    indent = " " * (len(line) - len(line.lstrip()) + 5)
    return textwrap.wrap(line, COLS, subsequent_indent=indent, drop_whitespace=False) or [""]


def colours(lines: list[str]) -> list[tuple[str, str]]:
    out, current = [], TEXT
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FAIL") or stripped.startswith("BLOCKED") or stripped.startswith("NOT CHECKED"):
            current = RED
        elif stripped.startswith("WARN"):
            current = YELLOW
        elif stripped.startswith("INFO"):
            current = DIM
        elif stripped.startswith("PASS"):
            current = GREEN
        for row in wrap(line):
            out.append((row, current))
    return out


def render(threemf: str, lines: list[str], code: int) -> str:
    rows = [f'<tspan fill="{DIM}">$ </tspan><tspan fill="{WHITE}">verify-print project {escape(threemf)}</tspan>']
    rows += [f'<tspan fill="{c}">{escape(t)}</tspan>' for t, c in colours(lines)]
    rows.append(f'<tspan fill="{DIM}">$ </tspan><tspan fill="{WHITE}">echo $?</tspan>')
    rows.append(f'<tspan fill="{RED if code else GREEN}">{code}</tspan>')

    height = BAR_H + 18 + LINE_H * len(rows) + 14
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'width="{WIDTH}" height="{height}" role="img" '
        f'aria-label="verify-print project blocking a 3MF whose custom filament profile is capped '
        f'at 2 cubic millimetres per second, and warning about fine layers, dense infill, thick walls and supports">',
        f'<rect width="{WIDTH}" height="{height}" rx="10" fill="{BG}"/>',
        f'<path d="M0 10a10 10 0 0 1 10-10h{WIDTH - 20}a10 10 0 0 1 10 10v{BAR_H - 10}H0z" fill="{BAR}"/>',
        '<circle cx="20" cy="18" r="6" fill="#ff5f56"/>',
        '<circle cx="40" cy="18" r="6" fill="#ffbd2e"/>',
        '<circle cx="60" cy="18" r="6" fill="#27c93f"/>',
        f'<text x="{WIDTH / 2}" y="23" text-anchor="middle" fill="{DIM}" '
        f'font-family="{FONT}" font-size="12">verify-print</text>',
        f'<g font-family="{FONT}" font-size="{SIZE}">',
    ]
    y = BAR_H + 18 + SIZE
    for row in rows:
        out.append(f'<text x="{PAD_X}" y="{y}" xml:space="preserve">{row}</text>')
        y += LINE_H
    out += ["</g>", "</svg>", ""]
    return "\n".join(out)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        threemf = build_sample(folder)
        lines, code = run_cli(folder, threemf)
    print(f"$ verify-print project {threemf}", *lines, f"[exit {code}]", sep="\n")
    OUT.write_text(render(threemf, lines, code), encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
