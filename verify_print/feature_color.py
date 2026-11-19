"""Feature-color export — Layer E.

Solves a real visual ambiguity: 4 alignment posts looked identical
to 4 magnet pockets in mesh viewers. This module exports a CADQuery model with
each feature class as a distinct colored object, so when loaded in Orca/Bambu
Studio the feature classes are visually unambiguous.

Two output formats:
  - "stls": one STL file per feature, naming convention `feature_<name>.stl`
            Plus a `colors.json` manifest mapping name → hex color.
  - "gltf": single GLTF with per-mesh color (load directly into Orca/Bambu).
  - "step": single STEP with named instances (color baked).

CADQuery's Assembly type handles the multi-color geometry. This module is a
thin convenience wrapper specialized for the verify-print pre-print pipeline.

Usage:
    from verify_print.feature_color import export_features

    export_features(
        {
            "main_body":        (case_body, "#00BCD4"),
            "alignment_posts":  (posts,      "#FFD600"),
            "magnet_pockets":   (pockets,    "#E53935"),  # empty if removed
            "lip_frame":        (lip,        "#7CB342"),
        },
        output_dir="out/",
        format="stls",
    )

CADQuery is an optional dependency. Install via:
    python3 -m pip install -e ".[cad]"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import cadquery as cq
    _CADQUERY_AVAILABLE = True
except ImportError:
    _CADQUERY_AVAILABLE = False


@dataclass
class ExportReport:
    output_dir: str
    format: str
    features_exported: List[str] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    color_manifest: Dict[str, str] = field(default_factory=dict)

    def format(self) -> str:
        lines = [f"Feature-color export report: {self.output_dir}"]
        lines.append(f"  format:              {self.format}")
        lines.append(f"  features exported:   {len(self.features_exported)}")
        for name in self.features_exported:
            color = self.color_manifest.get(name, "?")
            lines.append(f"    - {name:25s}  {color}")
        lines.append(f"  files written:       {len(self.files_written)}")
        return "\n".join(lines)


def _require_cadquery():
    if not _CADQUERY_AVAILABLE:
        raise ImportError(
            "CADQuery is required for feature_color. Install: "
            "python3 -m pip install -e \".[cad]\""
        )


def _hex_to_rgb01(hex_color: str) -> Tuple[float, float, float, float]:
    """#RRGGBB → (r, g, b, alpha) floats 0..1."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #RRGGBB, got {hex_color!r}")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b, 1.0)


def export_features(
    features: Dict[str, Tuple[Any, str]],
    output_dir: Path | str,
    *,
    format: str = "stls",
    base_name: str = "model",
) -> ExportReport:
    """Export CADQuery features as colored geometry.

    Args:
        features: {feature_name: (cq.Workplane or cq.Shape, "#RRGGBB")}.
        output_dir: directory to write outputs into.
        format: "stls" (one STL per feature + colors.json), "gltf" (single colored gltf),
                or "step" (single STEP).
        base_name: base name for single-file formats.
    """
    _require_cadquery()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    color_manifest = {name: color for name, (_, color) in features.items()}
    files_written: List[str] = []
    features_exported: List[str] = []

    if format == "stls":
        for name, (shape, _color) in features.items():
            stl_path = output_dir / f"feature_{name}.stl"
            cq.exporters.export(shape, str(stl_path))
            files_written.append(str(stl_path))
            features_exported.append(name)

        manifest_path = output_dir / "colors.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "colors": color_manifest,
                    "instructions": (
                        "Open all feature_*.stl files in Orca/Bambu Studio. "
                        "Assign the listed color to each part for visual disambiguation."
                    ),
                },
                indent=2,
            )
        )
        files_written.append(str(manifest_path))

    elif format in ("gltf", "step"):
        asm = cq.Assembly(name=base_name)
        for name, (shape, color_hex) in features.items():
            rgba = _hex_to_rgb01(color_hex)
            asm.add(shape, name=name, color=cq.Color(*rgba))
            features_exported.append(name)

        if format == "gltf":
            out_path = output_dir / f"{base_name}.gltf"
            asm.save(str(out_path), "GLTF")
        else:
            out_path = output_dir / f"{base_name}.step"
            asm.save(str(out_path), "STEP")
        files_written.append(str(out_path))

    else:
        raise ValueError(f"format must be 'stls', 'gltf', or 'step', got {format!r}")

    return ExportReport(
        output_dir=str(output_dir),
        format=format,
        features_exported=features_exported,
        files_written=files_written,
        color_manifest=color_manifest,
    )
