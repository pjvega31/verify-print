"""Fast STL info — bbox, volume, mass estimate WITHOUT invoking slicer.

P2 addition: the slicer takes 30s-5min depending on STL complexity. For
"will this fit on the bed?" / "is this approximately the size I expect?"
questions, trimesh-only inspection answers in milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import trimesh


@dataclass
class MeshInfoReport:
    file: str
    bbox_mm: Tuple[float, float, float]
    volume_mm3: float
    surface_area_mm2: float
    face_count: int
    vertex_count: int
    fits_build_volume: Optional[bool] = None
    build_volume_mm: Optional[Tuple[float, float, float]] = None
    estimated_grams: Optional[float] = None
    material_density: Optional[float] = None
    issues: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def format(self) -> str:
        lines = [f"Mesh info report: {self.file}"]
        bx, by, bz = self.bbox_mm
        lines.append(f"  bbox (mm):           {bx:.2f} x {by:.2f} x {bz:.2f}")
        lines.append(f"  volume (mm^3):       {self.volume_mm3:.2f}")
        lines.append(f"  surface area (mm^2): {self.surface_area_mm2:.2f}")
        lines.append(f"  faces / vertices:    {self.face_count} / {self.vertex_count}")
        if self.estimated_grams is not None:
            lines.append(
                f"  estimated mass:      {self.estimated_grams:.1f} g "
                f"(@ density {self.material_density} g/cm^3)"
            )
        if self.build_volume_mm:
            bvx, bvy, bvz = self.build_volume_mm
            lines.append(f"  build volume (mm):   {bvx:.0f} x {bvy:.0f} x {bvz:.0f}")
            lines.append(f"  fits build volume:   {self.fits_build_volume}")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS")
        return "\n".join(lines)


def mesh_info(
    stl_path: Path | str,
    *,
    build_volume_mm: Optional[Tuple[float, float, float]] = None,
    material_density: Optional[float] = None,
) -> MeshInfoReport:
    """Fast STL inspection without slicer invocation.

    Args:
        stl_path: STL to inspect.
        build_volume_mm: (W, D, H). Bambu Lab P1S = (256, 256, 256).
            If provided, check that the part fits and report.
        material_density: g/cm^3 for mass estimate. PETG ≈ 1.27,
            PLA ≈ 1.24, ABS ≈ 1.04. If provided, report estimated grams.
    """
    stl_path = Path(stl_path)
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    mesh = trimesh.load(str(stl_path), file_type="stl")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)

    bbox = tuple(float(x) for x in mesh.bounding_box.extents)
    volume = float(mesh.volume) if mesh.is_volume else 0.0
    surface = float(mesh.area)

    issues: List[str] = []

    fits = None
    if build_volume_mm is not None:
        bvx, bvy, bvz = build_volume_mm
        # Compare each STL dim to ANY rotation of the build volume — for now
        # assume aligned orientation (rotate the part manually if needed).
        fits = bbox[0] <= bvx and bbox[1] <= bvy and bbox[2] <= bvz
        if not fits:
            issues.append(
                f"STL bbox {bbox[0]:.1f}x{bbox[1]:.1f}x{bbox[2]:.1f}mm "
                f"exceeds build volume {bvx:.0f}x{bvy:.0f}x{bvz:.0f}mm"
            )

    estimated_g = None
    if material_density is not None and volume > 0:
        estimated_g = volume * material_density / 1000.0  # mm^3 → cm^3, then × g/cm^3

    return MeshInfoReport(
        file=str(stl_path),
        bbox_mm=bbox,
        volume_mm3=volume,
        surface_area_mm2=surface,
        face_count=len(mesh.faces),
        vertex_count=len(mesh.vertices),
        fits_build_volume=fits,
        build_volume_mm=build_volume_mm,
        estimated_grams=estimated_g,
        material_density=material_density,
        issues=issues,
    )
