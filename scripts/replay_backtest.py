#!/usr/bin/env python3
"""Replay verify-print against historical git states of a CAD project.

Answers the question: "would this tool have caught the bugs found
through v2.0 → v2.4, before anyone ran the slicer/printer?"

Usage:
    python3 replay_backtest.py <git_root> <cad_subdir> <ref_1> <ref_2> ... [--output-dir DIR]

Example (any git repo with CAD history):
    python3 replay_backtest.py \\
        ~/code/my-cad-repo \\
        cad/enclosure \\
        HEAD~5 HEAD~4 HEAD~3 HEAD~2 HEAD~1 HEAD \\
        --output-dir /tmp/backtest/

For each ref:
    1. git checkout <ref> (in a temp worktree to avoid touching working tree)
    2. Run `verify-print all <cad_subdir> --json` against that state
    3. Save report to <output-dir>/<ref>_report.json
    4. After all refs: emit a matrix showing which checks fail per ref.

Output: backtest_matrix.md table with refs as rows, check categories as cols.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List


def _git_create_worktree(repo: Path, ref: str, dest: Path) -> bool:
    """Create a temporary worktree at the given ref. Returns True if succeeded."""
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(dest), ref],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _git_remove_worktree(repo: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(dest)],
        capture_output=True,
    )


def _run_verify_print(cad_dir: Path) -> dict:
    """Run `verify-print all <cad_dir> --json` and return parsed dict."""
    result = subprocess.run(
        ["verify-print", "all", str(cad_dir), "--json"],
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return {"error": "no stdout", "passed": False, "stderr": result.stderr[-500:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"json decode: {e}", "passed": False}


def _summarize_report(report: dict) -> Dict[str, str]:
    """Reduce a verify-print all JSON to a single-row matrix entry."""
    if report.get("error"):
        return {"params": "ERR", "mesh": "ERR", "overall": "ERR"}

    if report.get("params"):
        n_orphans = len(report["params"].get("orphans", []))
        n_ghosts = len(report["params"].get("ghosts", []))
        params_summary = f"{n_orphans}o/{n_ghosts}g"
    else:
        params_summary = "—"

    mesh_failures = sum(1 for m in report.get("mesh", []) if not m.get("passed", True))
    mesh_total = len(report.get("mesh", []))
    mesh_summary = f"{mesh_failures}/{mesh_total}" if mesh_total else "—"

    overall = "PASS" if report.get("passed", False) else "FAIL"

    return {
        "params": params_summary,
        "mesh": mesh_summary,
        "overall": overall,
    }


def replay_backtest(
    repo: Path,
    cad_subdir: str,
    refs: List[str],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_rows = []

    for ref in refs:
        print(f"\n[{ref}] creating worktree...")
        with tempfile.TemporaryDirectory(prefix=f"verify-replay-{ref}-") as tmp:
            worktree = Path(tmp) / "tree"
            if not _git_create_worktree(repo, ref, worktree):
                print(f"  ERROR: could not create worktree for {ref}")
                matrix_rows.append({
                    "ref": ref,
                    **_summarize_report({"error": "worktree creation failed"}),
                })
                continue

            cad_dir = worktree / cad_subdir
            if not cad_dir.is_dir():
                print(f"  WARNING: {cad_subdir} not in this ref")
                matrix_rows.append({
                    "ref": ref,
                    **_summarize_report({"error": f"{cad_subdir} missing"}),
                })
                _git_remove_worktree(repo, worktree)
                continue

            print(f"  running verify-print all...")
            report = _run_verify_print(cad_dir)
            ref_safe = ref.replace("/", "_").replace("~", "_")
            (output_dir / f"{ref_safe}_report.json").write_text(json.dumps(report, indent=2))

            summary = _summarize_report(report)
            matrix_rows.append({"ref": ref, **summary})
            print(f"  → params: {summary['params']}  mesh: {summary['mesh']}  overall: {summary['overall']}")

            _git_remove_worktree(repo, worktree)

    # Write matrix markdown
    matrix_path = output_dir / "backtest_matrix.md"
    with matrix_path.open("w") as f:
        f.write("# Backtest matrix\n\n")
        f.write(f"Repo: {repo}\nCAD subdir: {cad_subdir}\n\n")
        f.write("| Ref | Params (orphans/ghosts) | Mesh (failed/total) | Overall |\n")
        f.write("|---|---|---|---|\n")
        for row in matrix_rows:
            f.write(f"| {row['ref']} | {row['params']} | {row['mesh']} | {row['overall']} |\n")
        f.write("\n")
        f.write("**Reading:** rows are git refs (commits). 'Params' shows orphan/ghost ")
        f.write("counts the linter would have flagged at that state. 'Mesh' shows how ")
        f.write("many STL files (if present) would have failed integrity. **A FAIL ")
        f.write("at an earlier ref means the tool would have caught the issue there.**\n")

    print(f"\nMatrix written to {matrix_path}")
    return matrix_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay verify-print across git history.")
    parser.add_argument("repo", help="git repository root")
    parser.add_argument("cad_subdir", help="path within repo to CAD directory (relative)")
    parser.add_argument("refs", nargs="+", help="git refs to check (e.g. HEAD~5 HEAD~4 ... HEAD)")
    parser.add_argument("--output-dir", default="./verify_print_backtest")
    args = parser.parse_args()

    replay_backtest(
        Path(args.repo),
        args.cad_subdir,
        args.refs,
        Path(args.output_dir),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
