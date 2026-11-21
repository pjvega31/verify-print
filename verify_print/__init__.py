"""Pre-print STL + CAD verification pipeline.

Modules:
- mesh_integrity: trimesh-based watertight/manifold/shells, wall thickness, orientation
- mesh_info: fast bbox / volume / build-volume fit / mass estimate
- param_linter: AST-based orphan and ghost param key detector
- slicer_simulate: Bambu Studio + OrcaSlicer CLI wrappers with profile-aware
  print time / filament / volumetric-speed bottleneck detection
- project_check: pre-print gate for 3MF projects
- iteration_compare: diff two JSON reports
- mesh_repair: PyMeshLab self-intersection detection + repair (optional extra)
- feature_color: CadQuery Assembly multi-color export (optional extra)
"""

__version__ = "0.6.0"

from verify_print.iteration_compare import CompareReport, compare_reports
from verify_print.mesh_info import MeshInfoReport, mesh_info
from verify_print.mesh_integrity import (
    IntegrityReport,
    OrientationReport,
    WallThicknessReport,
    check_mesh,
    check_orientation,
    check_wall_thickness,
)
from verify_print.param_linter import LinterReport, OrphanKey, GhostReference, lint_params
from verify_print.slicer_simulate import SliceReport, slice_stl

__all__ = [
    "CompareReport",
    "compare_reports",
    "IntegrityReport",
    "MeshInfoReport",
    "OrientationReport",
    "WallThicknessReport",
    "check_mesh",
    "check_orientation",
    "check_wall_thickness",
    "mesh_info",
    "LinterReport",
    "OrphanKey",
    "GhostReference",
    "lint_params",
    "SliceReport",
    "slice_stl",
]
