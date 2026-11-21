"""CLI entry point — `verify-print` command.

Subcommands:
  mesh    <stl>                                — mesh integrity (trimesh)
  params  <params.py> <code...>                — param hygiene (AST)
  repair  <stl>                                — PyMeshLab deep checks / repair
  slice   <stl> --filament/--process/--machine — headless slice + bottleneck gate (Bambu Studio CLI default; --slicer orca)
  project <file.3mf> [--slice] — pre-print gate on a Bambu/Orca project: time-multiplier settings, what changed vs stock, per-plate time
  all     <directory>                          — orchestrate above

Global flag:
  --json                                       — emit machine-readable JSON
                                                 instead of human format

Exit codes: 0 pass · 1 fail · 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from verify_print.iteration_compare import compare_reports
from verify_print.mesh_info import mesh_info
from verify_print.mesh_integrity import (
    check_mesh,
    check_orientation,
    check_wall_thickness,
)
from verify_print.param_linter import lint_params
from verify_print.slicer_simulate import slice_stl
from verify_print.project_check import check_project, report_to_dict
from verify_print._safezip import MemberTooLarge

try:
    from verify_print.mesh_repair import check_and_repair
    _MESH_REPAIR_AVAILABLE = True
except ImportError:
    _MESH_REPAIR_AVAILABLE = False


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert sets/Paths/non-primitives to JSON-serializable types."""
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(x) for x in obj]
    if isinstance(obj, set):
        try:
            return sorted(_make_json_safe(x) for x in obj)
        except TypeError:
            return list(_make_json_safe(x) for x in obj)
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj):
        return _make_json_safe(asdict(obj))
    return obj


def _emit(report: Any, *, as_json: bool) -> None:
    if as_json:
        payload = _make_json_safe(asdict(report) if is_dataclass(report) else report)
        # Include `passed` boolean for downstream parsing
        if hasattr(report, "passed"):
            payload["passed"] = report.passed
        print(json.dumps(payload, indent=2))
    else:
        print(report.format())


def _cmd_mesh(args: argparse.Namespace) -> int:
    report = check_mesh(
        Path(args.stl),
        max_disconnected_shells=args.max_shells,
        min_volume_mm3=args.min_volume,
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_params(args: argparse.Namespace) -> int:
    report = lint_params(
        Path(args.params_file),
        [Path(p) for p in args.code_files],
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_info(args: argparse.Namespace) -> int:
    bv = None
    if args.build_volume:
        parts = [float(x) for x in args.build_volume.split(",")]
        if len(parts) != 3:
            msg = f"--build-volume expects 3 comma-separated values, got {args.build_volume!r}"
            if args.json:
                print(json.dumps({"error": msg, "passed": False}))
            else:
                print(f"error: {msg}", file=sys.stderr)
            return 2
        bv = tuple(parts)
    report = mesh_info(
        Path(args.stl),
        build_volume_mm=bv,
        material_density=args.density,
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_compare(args: argparse.Namespace) -> int:
    report = compare_reports(Path(args.before), Path(args.after))
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_thickness(args: argparse.Namespace) -> int:
    report = check_wall_thickness(
        Path(args.stl),
        threshold_mm=args.threshold,
        sample_count=args.samples,
        seed=args.seed,
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_orientation(args: argparse.Namespace) -> int:
    normal = tuple(float(x) for x in args.normal.split(","))
    if len(normal) != 3:
        msg = f"--normal expects 3 comma-separated values, got {args.normal!r}"
        if args.json:
            print(json.dumps({"error": msg, "passed": False}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2
    report = check_orientation(
        Path(args.stl),
        expected_face_normal=normal,
        feature_name=args.feature,
        min_hole_area_mm2=args.min_area,
        alignment_threshold=args.alignment,
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_repair(args: argparse.Namespace) -> int:
    if not _MESH_REPAIR_AVAILABLE:
        msg = "mesh_repair requires pymeshlab. Install: python3 -m pip install -e \".[mesh-repair]\""
        if args.json:
            print(json.dumps({"error": msg, "passed": False}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2
    report = check_and_repair(
        Path(args.stl),
        repair=args.repair,
        output_path=Path(args.output) if args.output else None,
    )
    _emit(report, as_json=args.json)
    return 0 if report.passed else 1


def _cmd_slice(args: argparse.Namespace) -> int:
    """Slice one or more STLs. Returns 0 only if ALL slices pass."""
    # Expand directories: if any arg is a directory, glob for *.stl
    stls: List[Path] = []
    for s in args.stls:
        p = Path(s)
        if p.is_dir():
            stls.extend(sorted(p.glob("*.stl")))
        elif p.exists():
            stls.append(p)
        else:
            msg = f"STL not found: {p}"
            if args.json:
                print(json.dumps({"error": msg, "passed": False}))
            else:
                print(f"error: {msg}", file=sys.stderr)
            return 2

    if not stls:
        msg = "no STL files matched"
        if args.json:
            print(json.dumps({"error": msg, "passed": False}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2

    reports = []
    failures = 0
    for stl in stls:
        report = slice_stl(
            stl,
            slicer=args.slicer,
            filament_profile=Path(args.filament),
            process_profile=Path(args.process),
            machine_profile=Path(args.machine),
            output_dir=Path(args.output_dir),
            timeout_seconds=args.timeout,
            max_print_time_hours=args.max_hours,
            max_filament_grams=args.max_grams,
            min_volumetric_speed=args.min_speed,
        )
        reports.append(report)
        if not report.passed:
            failures += 1

    if args.json:
        entries = []
        for r in reports:
            entry = _make_json_safe(asdict(r))
            entry["passed"] = r.passed
            entries.append(entry)
        print(json.dumps({"slices": entries, "failures": failures, "passed": failures == 0}, indent=2))
    else:
        for r in reports:
            print(r.format())
            print()
        if failures:
            print(f"FAILED — {failures} slice(s) had issues")
        else:
            print("ALL SLICES PASSED")
    return 0 if failures == 0 else 1


def _cmd_project(args: argparse.Namespace) -> int:
    """Pre-print gate on a 3MF project. Returns 0 if no FAIL findings."""
    threemf = Path(args.threemf)
    if not threemf.exists():
        msg = f"3MF not found: {threemf}"
        print(json.dumps({"error": msg, "passed": False}) if args.json else f"error: {msg}", file=sys.stdout if args.json else sys.stderr)
        return 2
    try:
        report = check_project(
            threemf,
            expected_printer=args.printer,
            do_slice=args.slice,
            max_hours=args.max_hours,
            min_speed=args.min_speed,
            timeout_seconds=args.timeout,
        )
    except (zipfile.BadZipFile, MemberTooLarge, ValueError) as e:
        msg = f"cannot read 3MF {threemf}: {e}"
        print(json.dumps({"error": msg, "passed": False}) if args.json else f"error: {msg}", file=sys.stdout if args.json else sys.stderr)
        return 2
    if args.json:
        print(json.dumps(_make_json_safe(report_to_dict(report)), indent=2))
    else:
        print(report.format())
    return 0 if report.passed else 1


def _cmd_all(args: argparse.Namespace) -> int:
    root = Path(args.directory)
    if not root.is_dir():
        msg = f"{root} is not a directory"
        if args.json:
            print(json.dumps({"error": msg, "passed": False}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2

    params_file = root / "params.py"
    code_files = [
        p for p in root.glob("*.py")
        if p.name != "params.py" and not p.name.startswith("_")
    ]
    # Recursive STL search but de-duplicate
    stl_files = sorted({p.resolve() for p in root.glob("**/*.stl")})

    # Determine which checks to run
    run_slice = bool(args.filament and args.process and args.machine)
    run_repair = args.include_repair and _MESH_REPAIR_AVAILABLE

    results = {
        "directory": str(root),
        "params_file": str(params_file) if params_file.exists() else None,
        "code_files_scanned": len(code_files),
        "stl_files_found": len(stl_files),
        "include_repair": run_repair,
        "include_slice": run_slice,
        "params": None,
        "mesh": [],
        "repair": [],
        "slice": [],
    }
    failures = 0

    # 1) Param linter
    if params_file.exists():
        p_report = lint_params(params_file, code_files)
        results["params"] = _make_json_safe(asdict(p_report))
        results["params"]["passed"] = p_report.passed
        if not p_report.passed:
            failures += 1

    # 2) Mesh integrity
    for stl in stl_files:
        m_report = check_mesh(
            stl,
            max_disconnected_shells=args.max_shells,
            min_volume_mm3=args.min_volume,
        )
        entry = _make_json_safe(asdict(m_report))
        entry["passed"] = m_report.passed
        results["mesh"].append(entry)
        if not m_report.passed:
            failures += 1

    # 3) Mesh repair (opt-in)
    if run_repair:
        for stl in stl_files:
            r_report = check_and_repair(stl)
            entry = _make_json_safe(asdict(r_report))
            entry["passed"] = r_report.passed
            results["repair"].append(entry)
            if not r_report.passed:
                failures += 1

    # 4) Slicer simulation (opt-in — requires --filament/--process/--machine)
    if run_slice:
        for stl in stl_files:
            s_report = slice_stl(
                stl,
                slicer=args.slicer,
                filament_profile=Path(args.filament),
                process_profile=Path(args.process),
                machine_profile=Path(args.machine),
                output_dir=Path(args.output_dir),
                timeout_seconds=args.timeout,
                max_print_time_hours=args.max_hours,
                max_filament_grams=args.max_grams,
                min_volumetric_speed=args.min_speed,
            )
            entry = _make_json_safe(asdict(s_report))
            entry["passed"] = s_report.passed
            results["slice"].append(entry)
            if not s_report.passed:
                failures += 1

    results["failures"] = failures
    results["passed"] = failures == 0

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("=" * 60)
        print(f"verify-print all — {root}")
        print(f"  params: {'found' if params_file.exists() else 'not found'}  "
              f"({len(code_files)} code files)")
        print(f"  STLs:   {len(stl_files)}")
        print(f"  repair: {'on' if run_repair else 'off'}    "
              f"slice: {'on' if run_slice else 'off'}")
        print("=" * 60)

        if results["params"]:
            print("\n[params]")
            print("-" * 60)
            p_report = lint_params(params_file, code_files)
            print(p_report.format())

        for entry in results["mesh"]:
            print(f"\n[mesh] {entry['file']}")
            print("-" * 60)
            print(check_mesh(
                Path(entry["file"]),
                max_disconnected_shells=args.max_shells,
                min_volume_mm3=args.min_volume,
            ).format())

        if run_repair:
            for entry in results["repair"]:
                print(f"\n[repair] {entry['file']}")
                print("-" * 60)
                print(check_and_repair(Path(entry["file"])).format())

        if run_slice:
            for entry in results["slice"]:
                print(f"\n[slice] {entry['file']}")
                print("-" * 60)
                # Reconstruct human format from JSON entry
                _print_slice_entry(entry)

        print("\n" + "=" * 60)
        if failures == 0:
            print("ALL CHECKS PASSED")
        else:
            print(f"FAILED — {failures} check(s) had issues")

    return 0 if failures == 0 else 1


def _print_slice_entry(entry: dict) -> None:
    """Pretty-print a slice JSON entry — used by _cmd_all human mode."""
    print(f"  slicer:              {entry.get('slicer')}")
    print(f"  invocation:          {'success' if entry.get('success') else 'FAILED'}")
    if entry.get("print_time_human"):
        print(f"  estimated time:      {entry['print_time_human']} ({entry.get('print_time_seconds')}s)")
    if entry.get("filament_grams") is not None:
        print(f"  filament:            {entry['filament_grams']:.1f} g")
    if entry.get("layer_count") is not None:
        print(f"  layers:              {entry['layer_count']}")
    if entry.get("max_volumetric_speed") is not None:
        print(f"  max volumetric:      {entry['max_volumetric_speed']:.2f} mm^3/s")
    if entry.get("bbox_mm"):
        bx, by, bz = entry["bbox_mm"]
        print(f"  bbox (mm):           {bx:.2f} x {by:.2f} x {bz:.2f}")
    for w in entry.get("warnings", []):
        print(f"  warning: {w}")
    for i in entry.get("issues", []):
        print(f"  ISSUE: {i}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-print",
        description="Pre-print STL + CAD verification for FDM 3D printing",
    )

    # Parent parser supplies --json to every subcommand
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human format",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_mesh = subparsers.add_parser("mesh", parents=[parent], help="Mesh integrity (trimesh)")
    p_mesh.add_argument("stl")
    p_mesh.add_argument("--max-shells", type=int, default=1)
    p_mesh.add_argument("--min-volume", type=float, default=1.0)
    p_mesh.set_defaults(func=_cmd_mesh)

    p_params = subparsers.add_parser("params", parents=[parent], help="Param hygiene (AST)")
    p_params.add_argument("params_file")
    p_params.add_argument("code_files", nargs="+")
    p_params.set_defaults(func=_cmd_params)

    p_thickness = subparsers.add_parser(
        "thickness",
        parents=[parent],
        help="Wall thickness sampling via inward ray-casting",
    )
    p_thickness.add_argument("stl")
    p_thickness.add_argument("--threshold", type=float, default=0.8,
                             help="min wall thickness (default 0.8mm for 0.4mm nozzle)")
    p_thickness.add_argument("--samples", type=int, default=1000)
    p_thickness.add_argument("--seed", type=int, default=42)
    p_thickness.set_defaults(func=_cmd_thickness)

    p_orient = subparsers.add_parser(
        "orientation",
        parents=[parent],
        help="Check that the largest hole faces an expected direction",
    )
    p_orient.add_argument("stl")
    p_orient.add_argument("--normal", required=True,
                          help="expected face normal as 'x,y,z' (e.g. '0,-1,0' for -Y back)")
    p_orient.add_argument("--feature", default="largest_hole")
    p_orient.add_argument("--min-area", type=float, default=100.0,
                          help="min hole area mm^2 to consider (default 100)")
    p_orient.add_argument("--alignment", type=float, default=0.7,
                          help="min cosine alignment (default 0.7, ~45 deg tolerance)")
    p_orient.set_defaults(func=_cmd_orientation)

    p_repair = subparsers.add_parser("repair", parents=[parent], help="PyMeshLab deep checks / repair")
    p_repair.add_argument("stl")
    p_repair.add_argument("--repair", action="store_true")
    p_repair.add_argument("--output")
    p_repair.set_defaults(func=_cmd_repair)

    p_info = subparsers.add_parser(
        "info",
        parents=[parent],
        help="Fast STL inspection: bbox, volume, fits-build-volume, mass estimate (no slicer)",
    )
    p_info.add_argument("stl")
    p_info.add_argument("--build-volume",
                        help="build volume as 'W,D,H' (Bambu P1S = '256,256,256')")
    p_info.add_argument("--density", type=float, default=None,
                        help="material density g/cm^3 (PETG=1.27, PLA=1.24, ABS=1.04)")
    p_info.set_defaults(func=_cmd_info)

    p_compare = subparsers.add_parser(
        "compare",
        parents=[parent],
        help="Diff two `verify-print all --json` outputs (iteration regression check)",
    )
    p_compare.add_argument("before", help="path to earlier --json report")
    p_compare.add_argument("after", help="path to current --json report")
    p_compare.set_defaults(func=_cmd_compare)

    p_slice = subparsers.add_parser("slice", parents=[parent], help="Headless slice with bottleneck gate (multi-STL)")
    p_slice.add_argument("stls", nargs="+", help="STL file(s) or director(y/ies) containing *.stl")
    p_slice.add_argument("--slicer", choices=["orca", "bambu"], default="bambu")
    p_slice.add_argument("--filament", required=True)
    p_slice.add_argument("--process", required=True)
    p_slice.add_argument("--machine", required=True)
    p_slice.add_argument("--output-dir", default="./verify_print_slicedata")
    p_slice.add_argument("--timeout", type=int, default=900)
    p_slice.add_argument("--max-hours", type=float, default=None)
    p_slice.add_argument("--max-grams", type=float, default=None)
    p_slice.add_argument("--min-speed", type=float, default=None)
    p_slice.set_defaults(func=_cmd_slice)

    p_project = subparsers.add_parser("project", parents=[parent],
                                      help="Pre-print gate on a Bambu Studio / Orca 3MF project (time-multiplier settings)")
    p_project.add_argument("threemf", help="project .3mf (unsliced is fine)")
    p_project.add_argument("--slice", action="store_true",
                           help="also slice every plate with Bambu Studio CLI for time + feature breakdown")
    p_project.add_argument("--printer", default=None,
                           help='expected printer preset, e.g. "Bambu Lab P1S 0.4 nozzle" (R8)')
    p_project.add_argument("--max-hours", type=float, default=None, help="FAIL if any plate exceeds (needs --slice)")
    p_project.add_argument("--min-speed", type=float, default=None,
                           help="volumetric-speed floor in mm^3/s for custom or changed filament profiles "
                                "(default: 5.0 at a 0.4 mm nozzle and larger, 0.5 at 0.2 mm)")
    p_project.add_argument("--timeout", type=int, default=900)
    p_project.set_defaults(func=_cmd_project)

    p_all = subparsers.add_parser("all", parents=[parent], help="Run all checks on a directory")
    p_all.add_argument("directory")
    p_all.add_argument("--max-shells", type=int, default=1)
    p_all.add_argument("--min-volume", type=float, default=1.0)
    p_all.add_argument("--include-repair", action="store_true",
                       help="also run PyMeshLab repair pass (requires [mesh-repair])")
    # Slicer args — optional. If filament/process/machine all provided, run slice on every STL.
    p_all.add_argument("--slicer", choices=["orca", "bambu"], default="bambu")
    p_all.add_argument("--filament", default=None)
    p_all.add_argument("--process", default=None)
    p_all.add_argument("--machine", default=None)
    p_all.add_argument("--output-dir", default="./verify_print_slicedata")
    p_all.add_argument("--timeout", type=int, default=900)
    p_all.add_argument("--max-hours", type=float, default=None)
    p_all.add_argument("--max-grams", type=float, default=None)
    p_all.add_argument("--min-speed", type=float, default=None)
    p_all.set_defaults(func=_cmd_all)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
