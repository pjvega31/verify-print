"""Compare two `verify-print all --json` reports to track regression / progress.

Use case: run `verify-print all my_part/ --json > v2_4_report.json`,
makes changes, runs again → `v2_5_report.json`. The `compare` subcommand
shows the diff: orphans fixed, new ghosts introduced, mesh shells delta, etc.

Provides quantitative answer to "is this iteration better than the last".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class CompareReport:
    before_file: str
    after_file: str
    orphans_resolved: int
    orphans_introduced: int
    ghosts_resolved: int
    ghosts_introduced: int
    mesh_failures_resolved: List[str] = field(default_factory=list)
    mesh_failures_introduced: List[str] = field(default_factory=list)
    slice_print_time_delta_seconds: List[Dict[str, Any]] = field(default_factory=list)
    overall_verdict: str = "NO CHANGE"

    @property
    def passed(self) -> bool:
        return (
            self.orphans_introduced == 0
            and self.ghosts_introduced == 0
            and len(self.mesh_failures_introduced) == 0
        )

    def format(self) -> str:
        lines = [
            f"Compare report",
            f"  before: {self.before_file}",
            f"  after:  {self.after_file}",
        ]
        lines.append(f"  orphans resolved:   {self.orphans_resolved}")
        lines.append(f"  orphans introduced: {self.orphans_introduced}")
        lines.append(f"  ghosts resolved:    {self.ghosts_resolved}")
        lines.append(f"  ghosts introduced:  {self.ghosts_introduced}")
        if self.mesh_failures_resolved:
            lines.append(f"  mesh failures resolved:")
            for f in self.mesh_failures_resolved:
                lines.append(f"    + {f}")
        if self.mesh_failures_introduced:
            lines.append(f"  mesh failures introduced:")
            for f in self.mesh_failures_introduced:
                lines.append(f"    - {f}")
        if self.slice_print_time_delta_seconds:
            lines.append(f"  print time deltas:")
            for d in self.slice_print_time_delta_seconds:
                lines.append(
                    f"    {d['file']}: "
                    f"{d['before_seconds']}s → {d['after_seconds']}s "
                    f"({d['delta_seconds']:+d}s)"
                )
        lines.append(f"  verdict: {self.overall_verdict}")
        return "\n".join(lines)


def _orphan_set(report: Dict[str, Any]) -> set:
    if not report.get("params") or not report["params"].get("orphans"):
        return set()
    return {(o["dict_name"], o["key"]) for o in report["params"]["orphans"]}


def _ghost_set(report: Dict[str, Any]) -> set:
    if not report.get("params") or not report["params"].get("ghosts"):
        return set()
    return {(g["dict_name"], g["key"]) for g in report["params"]["ghosts"]}


def _mesh_failures(report: Dict[str, Any]) -> Dict[str, List[str]]:
    out = {}
    for entry in report.get("mesh", []):
        if not entry.get("passed", True):
            out[entry["file"]] = list(entry.get("issues", []))
    return out


def _slice_times(report: Dict[str, Any]) -> Dict[str, int]:
    out = {}
    for entry in report.get("slice", []):
        if entry.get("print_time_seconds"):
            out[entry["file"]] = entry["print_time_seconds"]
    return out


def compare_reports(before_path: Path | str, after_path: Path | str) -> CompareReport:
    """Compare two `verify-print all --json` outputs."""
    before_path = Path(before_path)
    after_path = Path(after_path)
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())

    o_before = _orphan_set(before)
    o_after = _orphan_set(after)
    orphans_resolved = len(o_before - o_after)
    orphans_introduced = len(o_after - o_before)

    g_before = _ghost_set(before)
    g_after = _ghost_set(after)
    ghosts_resolved = len(g_before - g_after)
    ghosts_introduced = len(g_after - g_before)

    m_before = _mesh_failures(before)
    m_after = _mesh_failures(after)
    mesh_resolved = [f for f in m_before if f not in m_after]
    mesh_introduced = [f for f in m_after if f not in m_before]

    t_before = _slice_times(before)
    t_after = _slice_times(after)
    time_deltas = []
    for f in set(t_before) & set(t_after):
        time_deltas.append({
            "file": f,
            "before_seconds": t_before[f],
            "after_seconds": t_after[f],
            "delta_seconds": t_after[f] - t_before[f],
        })

    # Verdict
    improvements = orphans_resolved + ghosts_resolved + len(mesh_resolved)
    regressions = orphans_introduced + ghosts_introduced + len(mesh_introduced)
    if improvements > 0 and regressions == 0:
        verdict = "IMPROVED"
    elif regressions > 0 and improvements == 0:
        verdict = "REGRESSED"
    elif improvements > 0 and regressions > 0:
        verdict = "MIXED"
    else:
        verdict = "NO CHANGE"

    return CompareReport(
        before_file=str(before_path),
        after_file=str(after_path),
        orphans_resolved=orphans_resolved,
        orphans_introduced=orphans_introduced,
        ghosts_resolved=ghosts_resolved,
        ghosts_introduced=ghosts_introduced,
        mesh_failures_resolved=mesh_resolved,
        mesh_failures_introduced=mesh_introduced,
        slice_print_time_delta_seconds=time_deltas,
        overall_verdict=verdict,
    )
