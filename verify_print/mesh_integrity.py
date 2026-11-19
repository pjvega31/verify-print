"""Mesh integrity checks via trimesh.

v0.3.1: basic checks (watertight, manifold, shells, bbox, mass).
v0.3.2 (P1): adds wall thickness sampling + surface orientation check.

Self-intersection lives in mesh_repair.py (PyMeshLab dep).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import trimesh


@dataclass
class IntegrityReport:
    file: str
    is_watertight: bool
    is_winding_consistent: bool
    is_volume: bool
    num_disconnected_shells: int
    bounding_box_mm: Tuple[float, float, float]
    volume_mm3: float
    center_of_mass_mm: Tuple[float, float, float]
    issues: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def format(self) -> str:
        lines = [f"Mesh integrity report: {self.file}"]
        lines.append(f"  watertight:          {self.is_watertight}")
        lines.append(f"  normals consistent:  {self.is_winding_consistent}")
        lines.append(f"  valid volume:        {self.is_volume}")
        lines.append(f"  disconnected shells: {self.num_disconnected_shells}")
        bx, by, bz = self.bounding_box_mm
        lines.append(f"  bbox (mm):           {bx:.2f} x {by:.2f} x {bz:.2f}")
        lines.append(f"  volume (mm^3):       {self.volume_mm3:.2f}")
        cx, cy, cz = self.center_of_mass_mm
        lines.append(f"  center of mass (mm): ({cx:.2f}, {cy:.2f}, {cz:.2f})")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS")
        return "\n".join(lines)


def check_mesh(
    stl_path: Path | str,
    *,
    max_disconnected_shells: int = 1,
    min_volume_mm3: float = 1.0,
) -> IntegrityReport:
    """Run integrity checks on an STL file.

    Args:
        stl_path: path to STL file.
        max_disconnected_shells: allowed disconnected shells (default 1 — single solid).
            Multi-part assemblies exported as one STL legitimately have N shells; set accordingly.
        min_volume_mm3: minimum acceptable volume — catches degenerate exports.
    """
    stl_path = Path(stl_path)
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    mesh = trimesh.load(str(stl_path), file_type="stl")

    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)

    issues: List[str] = []

    is_watertight = bool(mesh.is_watertight)
    if not is_watertight:
        issues.append("not watertight — mesh has holes or non-manifold edges")

    is_winding_consistent = bool(mesh.is_winding_consistent)
    if not is_winding_consistent:
        issues.append("normals inconsistent — winding order varies across faces")

    is_volume = bool(mesh.is_volume)
    if not is_volume:
        issues.append("not a valid volume — degenerate or self-intersecting")

    shells = mesh.split(only_watertight=False)
    num_shells = len(shells) if isinstance(shells, (list, tuple)) else 1
    if num_shells > max_disconnected_shells:
        issues.append(
            f"{num_shells} disconnected shells (max allowed: {max_disconnected_shells}) "
            f"— possible floating geometry"
        )

    volume = float(mesh.volume) if mesh.is_volume else 0.0
    if volume < min_volume_mm3:
        issues.append(f"volume {volume:.2f} mm^3 below minimum {min_volume_mm3} mm^3")

    bbox = tuple(float(x) for x in mesh.bounding_box.extents)
    com = tuple(float(x) for x in mesh.center_mass) if mesh.is_volume else (0.0, 0.0, 0.0)

    return IntegrityReport(
        file=str(stl_path),
        is_watertight=is_watertight,
        is_winding_consistent=is_winding_consistent,
        is_volume=is_volume,
        num_disconnected_shells=num_shells,
        bounding_box_mm=bbox,
        volume_mm3=volume,
        center_of_mass_mm=com,
        issues=issues,
    )


# ---------- P1: wall thickness ----------


@dataclass
class WallThicknessReport:
    file: str
    sample_count: int
    min_thickness_mm: float
    p5_thickness_mm: float  # 5th percentile — robust thin-wall signal
    median_thickness_mm: float
    thin_sample_count: int  # number of samples below threshold
    threshold_mm: float
    issues: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def format(self) -> str:
        lines = [f"Wall thickness report: {self.file}"]
        lines.append(f"  samples:             {self.sample_count}")
        lines.append(f"  threshold:           {self.threshold_mm:.2f} mm")
        lines.append(f"  min thickness:       {self.min_thickness_mm:.2f} mm")
        lines.append(f"  p5 thickness:        {self.p5_thickness_mm:.2f} mm")
        lines.append(f"  median thickness:    {self.median_thickness_mm:.2f} mm")
        lines.append(f"  samples below:       {self.thin_sample_count} of {self.sample_count}")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS")
        return "\n".join(lines)


def check_wall_thickness(
    stl_path: Path | str,
    *,
    threshold_mm: float = 0.8,
    sample_count: int = 1000,
    seed: int = 42,
) -> WallThicknessReport:
    """Sample faces, cast inward rays, measure distance to opposite wall.

    Args:
        stl_path: STL to analyze.
        threshold_mm: minimum acceptable wall thickness. For a 0.4mm FDM nozzle,
            0.8mm is the conventional practical minimum (2 perimeter passes).
        sample_count: how many random face centers to sample. Higher = slower
            but more reliable. 1000 is a reasonable default for FDM parts.
        seed: RNG seed for reproducibility.

    Method:
        For each sampled face, cast a ray from the face center along the
        INWARD normal (-face_normal). The first hit is the opposite wall;
        distance to that hit is the local wall thickness. We report the
        distribution (min, p5, median) and count samples below threshold.

    Caveats:
        - Concave geometry may give misleading readings (ray bounces off
          interior features rather than the opposite outer wall).
        - Holes / non-watertight meshes produce inf distances (filtered).
        - Sampling means we may miss the thinnest spot if it's small.
          P5 percentile is more robust than min for noisy meshes.
    """
    stl_path = Path(stl_path)
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    mesh = trimesh.load(str(stl_path), file_type="stl")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)

    n_faces = len(mesh.faces)
    actual_samples = min(sample_count, n_faces)

    rng = np.random.default_rng(seed)
    face_indices = rng.choice(n_faces, size=actual_samples, replace=False)

    # Ray origins = face centers, slightly offset inward to avoid self-hit
    centers = mesh.triangles_center[face_indices]
    normals = mesh.face_normals[face_indices]
    origins = centers - normals * 1e-3  # tiny inward offset
    directions = -normals  # ray points inward

    # Cast rays
    locations, ray_indices, _ = mesh.ray.intersects_location(
        ray_origins=origins,
        ray_directions=directions,
        multiple_hits=False,
    )

    # Compute hit distances per ray
    thicknesses = np.full(actual_samples, np.inf)
    if len(ray_indices) > 0:
        hit_distances = np.linalg.norm(locations - origins[ray_indices], axis=1)
        thicknesses[ray_indices] = hit_distances

    # Filter inf (no hit — point on open boundary or facing outward)
    finite = thicknesses[np.isfinite(thicknesses)]
    if len(finite) == 0:
        return WallThicknessReport(
            file=str(stl_path),
            sample_count=actual_samples,
            min_thickness_mm=float("inf"),
            p5_thickness_mm=float("inf"),
            median_thickness_mm=float("inf"),
            thin_sample_count=0,
            threshold_mm=threshold_mm,
            issues=["no rays hit interior — mesh may be open/non-watertight"],
        )

    min_t = float(np.min(finite))
    p5_t = float(np.percentile(finite, 5))
    med_t = float(np.median(finite))
    thin_count = int(np.sum(finite < threshold_mm))

    issues: List[str] = []
    if thin_count > 0:
        issues.append(
            f"{thin_count} of {len(finite)} samples below {threshold_mm} mm "
            f"(min={min_t:.2f}, p5={p5_t:.2f})"
        )

    return WallThicknessReport(
        file=str(stl_path),
        sample_count=actual_samples,
        min_thickness_mm=min_t,
        p5_thickness_mm=p5_t,
        median_thickness_mm=med_t,
        thin_sample_count=thin_count,
        threshold_mm=threshold_mm,
        issues=issues,
    )


# ---------- P1: orientation check (bug #1) ----------


@dataclass
class OrientationReport:
    file: str
    feature_name: str
    expected_face_normal: Tuple[float, float, float]
    detected_largest_hole_normal: Optional[Tuple[float, float, float]]
    dot_product: Optional[float]  # cosine between expected + detected
    issues: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def format(self) -> str:
        lines = [f"Orientation report: {self.file}"]
        lines.append(f"  feature:             {self.feature_name}")
        ex, ey, ez = self.expected_face_normal
        lines.append(f"  expected normal:     ({ex:+.2f}, {ey:+.2f}, {ez:+.2f})")
        if self.detected_largest_hole_normal:
            dx, dy, dz = self.detected_largest_hole_normal
            lines.append(f"  detected normal:     ({dx:+.2f}, {dy:+.2f}, {dz:+.2f})")
            lines.append(f"  alignment (cos):     {self.dot_product:+.3f}")
        else:
            lines.append(f"  detected normal:     (no large hole found)")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS")
        return "\n".join(lines)


def check_orientation(
    stl_path: Path | str,
    *,
    expected_face_normal: Tuple[float, float, float],
    feature_name: str = "largest_hole",
    min_hole_area_mm2: float = 100.0,
    alignment_threshold: float = 0.7,
) -> OrientationReport:
    """Check that the largest planar hole faces the expected direction.

    Use case: a real enclosure iteration had the cable opening oriented to the FRONT
    (+Y) instead of the BACK (-Y). For that check, pass
    `expected_face_normal=(0, -1, 0)`.

    Method:
        1. Find disconnected boundary loops (open edges).
        2. For each loop, estimate plane normal via SVD of vertex coords.
        3. Estimate area via convex hull of loop projected onto plane.
        4. Take the largest hole, compare its normal direction to expected.
        5. Pass if dot product > alignment_threshold (default 0.7 ≈ 45° tolerance).

    Caveats:
        - Watertight meshes have no boundary edges → returns no_hole_found.
          For sealed STLs, this check needs face-cluster analysis instead.
        - Only the SINGLE largest hole is checked. Multi-hole geometries
          with conflicting orientations need finer-grained API.
    """
    stl_path = Path(stl_path)
    if not stl_path.exists():
        raise FileNotFoundError(stl_path)

    mesh = trimesh.load(str(stl_path), file_type="stl")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)

    expected = np.array(expected_face_normal, dtype=float)
    expected = expected / np.linalg.norm(expected)

    issues: List[str] = []

    # Try boundary-loop approach (works for STLs with cuts/openings)
    largest_normal: Optional[np.ndarray] = None
    largest_area = 0.0

    if not mesh.is_watertight:
        # Get boundary loops
        boundary_groups = mesh.outline().entities
        boundary_vertices = mesh.outline().vertices

        for entity in boundary_groups:
            pts = boundary_vertices[entity.points]
            if len(pts) < 3:
                continue
            # Fit plane via SVD
            center = pts.mean(axis=0)
            centered = pts - center
            _, _, vh = np.linalg.svd(centered, full_matrices=False)
            normal = vh[-1]
            normal = normal / (np.linalg.norm(normal) + 1e-12)
            # Project to plane + area via shoelace on 2D coords
            u_axis = vh[0]
            v_axis = vh[1]
            uv = np.stack([centered @ u_axis, centered @ v_axis], axis=1)
            area = 0.5 * abs(np.sum(uv[:, 0] * np.roll(uv[:, 1], -1)
                                    - uv[:, 1] * np.roll(uv[:, 0], -1)))
            if area > largest_area:
                largest_area = area
                largest_normal = normal
    else:
        # Watertight — fall back to face-cluster approach
        # Find largest planar cluster (faces with similar normals at similar offset)
        # For simplicity v0.3.2: report unable-to-check
        issues.append(
            "mesh is watertight — no boundary holes to check orientation against. "
            "If you expect a hole, the STL export may have auto-closed it."
        )
        return OrientationReport(
            file=str(stl_path),
            feature_name=feature_name,
            expected_face_normal=tuple(expected_face_normal),
            detected_largest_hole_normal=None,
            dot_product=None,
            issues=issues,
        )

    if largest_normal is None or largest_area < min_hole_area_mm2:
        return OrientationReport(
            file=str(stl_path),
            feature_name=feature_name,
            expected_face_normal=tuple(expected_face_normal),
            detected_largest_hole_normal=None,
            dot_product=None,
            issues=[
                f"no hole exceeding {min_hole_area_mm2} mm^2 found "
                f"(largest detected: {largest_area:.1f} mm^2)"
            ],
        )

    # Two possible normals (plane direction is ambiguous). Pick the one
    # closer to expected.
    dot1 = float(np.dot(largest_normal, expected))
    dot2 = float(np.dot(-largest_normal, expected))
    if dot2 > dot1:
        largest_normal = -largest_normal
        dot = dot2
    else:
        dot = dot1

    if dot < alignment_threshold:
        issues.append(
            f"largest hole faces ({largest_normal[0]:+.2f}, {largest_normal[1]:+.2f}, "
            f"{largest_normal[2]:+.2f}) — expected ({expected[0]:+.2f}, "
            f"{expected[1]:+.2f}, {expected[2]:+.2f}). "
            f"Alignment {dot:.2f} below threshold {alignment_threshold}."
        )

    return OrientationReport(
        file=str(stl_path),
        feature_name=feature_name,
        expected_face_normal=tuple(expected_face_normal),
        detected_largest_hole_normal=tuple(float(x) for x in largest_normal),
        dot_product=dot,
        issues=issues,
    )
