"""Mesh repair + advanced integrity checks — Layer A complement.

Wraps PyMeshLab for the checks trimesh doesn't cover natively:
  - Self-intersecting faces detection
  - Repair pipeline (close holes, remove duplicates, fix non-manifold)
  - Quantitative integrity metrics

PyMeshLab is an optional dependency. Install via:
    python3 -m pip install -e ".[mesh-repair]"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import pymeshlab
    _PYMESHLAB_AVAILABLE = True
except ImportError:
    _PYMESHLAB_AVAILABLE = False


@dataclass
class RepairReport:
    file: str
    self_intersecting_faces: int
    non_manifold_edges: int
    non_manifold_vertices: int
    duplicate_faces: int
    duplicate_vertices: int
    holes: int
    repaired: bool
    repaired_file: Optional[str] = None
    issues: List[str] = field(default_factory=list)
    filter_warnings: List[str] = field(default_factory=list)  # version-safe filter call warnings
    repair_validation: Optional[dict] = None  # post-repair re-check summary (when repair=True)

    @property
    def passed(self) -> bool:
        return (
            self.self_intersecting_faces == 0
            and self.non_manifold_edges == 0
            and self.non_manifold_vertices == 0
            and self.duplicate_faces == 0
            and self.holes == 0
        )

    def format(self) -> str:
        lines = [f"Mesh repair report: {self.file}"]
        lines.append(f"  self-intersecting faces:  {self.self_intersecting_faces}")
        lines.append(f"  non-manifold edges:       {self.non_manifold_edges}")
        lines.append(f"  non-manifold vertices:    {self.non_manifold_vertices}")
        lines.append(f"  duplicate faces:          {self.duplicate_faces}")
        lines.append(f"  duplicate vertices:       {self.duplicate_vertices}")
        lines.append(f"  holes (open boundaries):  {self.holes}")
        if self.filter_warnings:
            lines.append(f"  filter warnings ({len(self.filter_warnings)}):")
            for w in self.filter_warnings:
                lines.append(f"    - {w}")
        if self.repaired:
            lines.append(f"  REPAIRED → {self.repaired_file}")
            if self.repair_validation:
                rv = self.repair_validation
                lines.append(f"    pre-repair issues:  {rv.get('pre_issues_count', '?')}")
                lines.append(f"    post-repair issues: {rv.get('post_issues_count', '?')}")
                lines.append(f"    improvement:        {rv.get('verdict', '?')}")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS")
        return "\n".join(lines)


def _require_pymeshlab():
    if not _PYMESHLAB_AVAILABLE:
        raise ImportError(
            "PyMeshLab is required for mesh_repair. Install: "
            "python3 -m pip install -e \".[mesh-repair]\""
        )


# Filter name aliases (PyMeshLab renames these across versions — try each in order).
_FILTER_ALIASES = {
    "self_intersection": [
        "compute_selection_by_self_intersections_per_face",
        "select_self_intersecting_faces",  # older alias
    ],
    "remove_dup_faces": [
        "meshing_remove_duplicate_faces",
        "remove_duplicate_faces",  # older alias
    ],
    "remove_dup_verts": [
        "meshing_remove_duplicate_vertices",
        "remove_duplicate_vertices",  # older alias
    ],
    "repair_nm_edges": [
        "meshing_repair_non_manifold_edges",
        "repair_non_manifold_edges",  # older alias
    ],
    "repair_nm_verts": [
        "meshing_repair_non_manifold_vertices",
        "repair_non_manifold_vertices",  # older alias
    ],
    "close_holes": [
        "meshing_close_holes",
        "close_holes",  # older alias
    ],
}


def _safe_apply_filter(ms, op_key: str, warnings: list, **kwargs):
    """Try each alias for an operation; return result or None if all fail.

    Records a warning for visibility instead of silently failing.
    """
    last_error = None
    for filter_name in _FILTER_ALIASES.get(op_key, []):
        try:
            return ms.apply_filter(filter_name, **kwargs)
        except Exception as e:
            last_error = e
            continue
    warnings.append(
        f"all filter aliases failed for {op_key!r}: tried "
        f"{_FILTER_ALIASES.get(op_key, [])}. Last error: {last_error}"
    )
    return None


def check_and_repair(
    stl_path: Path | str,
    *,
    repair: bool = False,
    output_path: Path | str | None = None,
) -> RepairReport:
    """Run PyMeshLab checks; optionally write a repaired STL.

    Args:
        stl_path: input STL.
        repair: if True, run repair filters and write to output_path.
        output_path: where to write repaired STL (default: <stl>_repaired.stl).
    """
    _require_pymeshlab()
    stl_path = Path(stl_path)
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(stl_path))
    mesh = ms.current_mesh()

    issues: List[str] = []
    filter_warnings: List[str] = []

    # Self-intersection (version-safe filter call)
    self_intersect_count = 0
    res = _safe_apply_filter(ms, "self_intersection", filter_warnings)
    if res is not None:
        self_intersect_count = int(mesh.selected_face_number())

    # Topological measures (stable API across PyMeshLab versions)
    topo = ms.get_topological_measures()
    non_manifold_edges = int(topo.get("non_two_manifold_edges", 0))
    non_manifold_verts = int(topo.get("non_two_manifold_vertices", 0))
    holes = int(topo.get("number_holes", 0))

    # Duplicates (version-safe)
    dup_faces = 0
    dup_verts = 0
    res = _safe_apply_filter(ms, "remove_dup_faces", filter_warnings)
    if isinstance(res, dict):
        dup_faces = int(res.get("removed_faces", 0))
    res = _safe_apply_filter(ms, "remove_dup_verts", filter_warnings)
    if isinstance(res, dict):
        dup_verts = int(res.get("removed_vertices", 0))

    if self_intersect_count > 0:
        issues.append(f"{self_intersect_count} self-intersecting faces")
    if non_manifold_edges > 0:
        issues.append(f"{non_manifold_edges} non-manifold edges")
    if non_manifold_verts > 0:
        issues.append(f"{non_manifold_verts} non-manifold vertices")
    if holes > 0:
        issues.append(f"{holes} open hole boundary loops")

    pre_issues_count = len(issues)
    repaired_file = None
    repair_validation: Optional[dict] = None

    if repair and pre_issues_count > 0:
        if output_path is None:
            output_path = stl_path.with_name(f"{stl_path.stem}_repaired.stl")
        output_path = Path(output_path)

        # Repair pipeline (version-safe)
        _safe_apply_filter(ms, "repair_nm_edges", filter_warnings)
        _safe_apply_filter(ms, "repair_nm_verts", filter_warnings)
        _safe_apply_filter(ms, "close_holes", filter_warnings, maxholesize=1000)

        ms.save_current_mesh(str(output_path))
        repaired_file = str(output_path)

        # Re-validate the repaired output (P2 safety check)
        try:
            post = check_and_repair(output_path, repair=False)
            post_issues_count = len(post.issues)
            verdict = (
                "BETTER" if post_issues_count < pre_issues_count
                else "SAME" if post_issues_count == pre_issues_count
                else "WORSE"
            )
            repair_validation = {
                "pre_issues_count": pre_issues_count,
                "post_issues_count": post_issues_count,
                "verdict": verdict,
                "post_issues": post.issues,
            }
            if verdict == "WORSE":
                issues.append(
                    f"repair output has MORE issues than input "
                    f"({pre_issues_count} → {post_issues_count}) — original kept; manual review needed"
                )
            elif verdict == "SAME":
                issues.append(
                    f"repair produced no improvement ({pre_issues_count} → {post_issues_count}) — "
                    f"filters could not fix the underlying topology"
                )
        except Exception as e:
            filter_warnings.append(f"re-validation failed: {e}")

    return RepairReport(
        file=str(stl_path),
        self_intersecting_faces=self_intersect_count,
        non_manifold_edges=non_manifold_edges,
        non_manifold_vertices=non_manifold_verts,
        duplicate_faces=dup_faces,
        duplicate_vertices=dup_verts,
        holes=holes,
        repaired=repaired_file is not None,
        repaired_file=repaired_file,
        issues=issues,
        filter_warnings=filter_warnings,
        repair_validation=repair_validation,
    )
