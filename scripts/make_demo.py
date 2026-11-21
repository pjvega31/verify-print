"""Regenerate docs/demo.svg, the terminal screenshot at the top of the README.

Builds two small parts in a temp folder, runs the real `verify-print mesh`
on each, and draws whatever the CLI printed. Nothing in the image is typed
by hand, so it cannot drift from the tool's actual output.

    pip install -e ".[dev]"      # needs manifold3d for the boolean union
    python scripts/make_demo.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import trimesh
from trimesh.creation import box, cylinder

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "demo.svg"

FLOOR_TOP_Z = -13.0  # tray is 30 mm tall, centred on z=0, with a 2 mm floor


def tray() -> trimesh.Trimesh:
    """Open-top tray, 120 x 80 x 30 mm, 2 mm walls and floor."""
    outer = box(extents=(120, 80, 30))
    cavity = box(extents=(116, 76, 28))
    cavity.apply_translation((0, 0, 2))
    opening = box(extents=(116, 76, 10))
    opening.apply_translation((0, 0, 14))
    return outer.difference(cavity.union(opening))


def posts(z_offset: float) -> list[trimesh.Trimesh]:
    """Four 12 mm alignment posts; z_offset > 0 leaves a gap above the floor."""
    out = []
    for x in (-50, 50):
        for y in (-30, 30):
            post = cylinder(radius=3, height=12, sections=48)
            post.apply_translation((x, y, FLOOR_TOP_Z + 6 + z_offset))
            out.append(post)
    return out


def build_parts(folder: Path) -> list[str]:
    body = tray()
    # The bug: posts sit 0.05 mm above the floor and were never unioned.
    trimesh.util.concatenate([body] + posts(0.05)).export(folder / "enclosure_v2.stl")
    # The fix: sink the posts 0.5 mm into the floor and union them.
    fixed = body
    for post in posts(-0.5):
        fixed = fixed.union(post)
    fixed.export(folder / "enclosure_v2_fixed.stl")
    return ["enclosure_v2.stl", "enclosure_v2_fixed.stl"]


def run_cli(folder: Path, stl: str) -> tuple[list[str], int]:
    proc = subprocess.run(
        [sys.executable, "-m", "verify_print.cli", "mesh", stl],
        cwd=folder, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    return proc.stdout.rstrip("\n").splitlines(), proc.returncode


# --- drawing -----------------------------------------------------------------

BG, BAR, TEXT, DIM = "#0d1117", "#161b22", "#c9d1d9", "#8b949e"
RED, GREEN, WHITE = "#ff7b72", "#3fb950", "#f0f6fc"
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SIZE, LINE_H, PAD_X, BAR_H, WIDTH = 14, 21, 22, 36, 760


def colour(line: str, failing: bool) -> str:
    stripped = line.strip()
    if stripped.startswith("FAIL") or (failing and stripped.startswith("- ")):
        return RED
    if stripped.startswith("PASS"):
        return GREEN
    return TEXT


def render(sessions: list[tuple[str, list[str], int]]) -> str:
    rows: list[str] = []  # pre-built <text> bodies
    for i, (stl, lines, code) in enumerate(sessions):
        if i:
            rows.append("")
        rows.append(f'<tspan fill="{DIM}">$ </tspan>'
                    f'<tspan fill="{WHITE}">verify-print mesh {escape(stl)}</tspan>')
        failing = code != 0
        rows += [f'<tspan fill="{colour(l, failing)}">{escape(l)}</tspan>' for l in lines]
        rows.append(f'<tspan fill="{DIM}">$ </tspan><tspan fill="{WHITE}">echo $?</tspan>')
        rows.append(f'<tspan fill="{RED if failing else GREEN}">{code}</tspan>')

    height = BAR_H + 18 + LINE_H * len(rows) + 14
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'width="{WIDTH}" height="{height}" role="img" '
        f'aria-label="verify-print mesh failing a part with 5 disconnected shells, '
        f'then passing the fixed part">',
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
        if row:
            out.append(f'<text x="{PAD_X}" y="{y}" xml:space="preserve">{row}</text>')
        y += LINE_H
    out += ["</g>", "</svg>", ""]
    return "\n".join(out)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        sessions = [(stl, *run_cli(folder, stl)) for stl in build_parts(folder)]
    for stl, lines, code in sessions:
        print(f"$ verify-print mesh {stl}", *lines, f"[exit {code}]", sep="\n")
    OUT.write_text(render(sessions), encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
