"""Slicer simulation — Layer B.

Wraps OrcaSlicer and Bambu Studio CLI to slice STL files headlessly with the
production filament/process/machine profile, then parses output to catch:
  - Excessive print time (e.g. the 44h vs 22h bug from filament_max_volumetric_speed)
  - Filament weight beyond budget
  - Volumetric speed below safety threshold
  - Layer count anomalies

Both slicers share the same CLI architecture (Bambu Studio is a fork of Orca).
Same wrapper handles both via the `slicer` parameter.
"""
from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from verify_print._safezip import MemberTooLarge, read_head_tail, read_member
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


DEFAULT_BAMBU_BIN = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
DEFAULT_ORCA_BIN = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"


@dataclass
class SliceReport:
    file: str
    slicer: str  # "orca" or "bambu"
    success: bool
    print_time_seconds: Optional[int]
    print_time_human: str
    filament_grams: Optional[float]
    filament_meters: Optional[float]
    layer_count: Optional[int]
    max_volumetric_speed: Optional[float]  # mm^3/s, from filament profile
    bbox_mm: Optional[Tuple[float, float, float]]
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw_stdout_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.success and len(self.issues) == 0

    def format(self) -> str:
        lines = [f"Slicer simulation report: {self.file}"]
        lines.append(f"  slicer:              {self.slicer}")
        lines.append(f"  invocation:          {'success' if self.success else 'FAILED'}")
        if self.print_time_human:
            lines.append(f"  estimated time:      {self.print_time_human} ({self.print_time_seconds}s)")
        if self.filament_grams is not None:
            lines.append(f"  filament:            {self.filament_grams:.1f} g")
            if self.filament_meters is not None:
                lines.append(f"                       {self.filament_meters:.2f} m")
        if self.layer_count is not None:
            lines.append(f"  layers:              {self.layer_count}")
        if self.max_volumetric_speed is not None:
            lines.append(f"  max volumetric:      {self.max_volumetric_speed:.2f} mm^3/s")
        if self.bbox_mm:
            bx, by, bz = self.bbox_mm
            lines.append(f"  bbox (mm):           {bx:.2f} x {by:.2f} x {bz:.2f}")
        elif self.success:
            lines.append(f"  bbox (mm):           (could not load STL for bbox)")
        if self.warnings:
            lines.append(f"  warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    - {w}")
        if self.issues:
            lines.append(f"  FAIL — {len(self.issues)} issue(s):")
            for i in self.issues:
                lines.append(f"    - {i}")
        else:
            lines.append("  PASS" if self.passed else "  FAIL (see warnings)")
        return "\n".join(lines)


# ---------- parsers ----------


_TIME_PATTERNS = [
    # "estimated printing time (normal mode) = 21h 50m 33s"
    re.compile(r"estimated\s+printing\s+time.*?=\s*((?:\d+\s*[dhms]\s*)+)", re.IGNORECASE),
    # "Print time: 21h 50m 33s"
    re.compile(r"print\s+time:\s*((?:\d+\s*[dhms]\s*)+)", re.IGNORECASE),
    # Bambu/Orca alternative formats — total time = NNNN s
    re.compile(r"total[_\s]+time\s*=\s*(\d+)\s*s", re.IGNORECASE),
]

_FILAMENT_G_PATTERNS = [
    re.compile(r"total\s+filament\s+used\s*\[g\]\s*=\s*([\d.]+)", re.IGNORECASE),
    # Bambu Studio 2.8 g-code header: "; total filament weight [g] : 25.15"
    re.compile(r"total\s+filament\s+weight\s*\[g\]\s*[:=]\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"filament\s+used\s*\[?g\]?\s*[:=]\s*([\d.]+)", re.IGNORECASE),
]

_FILAMENT_M_PATTERNS = [
    re.compile(r"total\s+filament\s+used\s*\[mm\]\s*=\s*([\d.]+)", re.IGNORECASE),
    # Bambu Studio 2.8 g-code header: "; total filament length [mm] : 8366.40"
    re.compile(r"total\s+filament\s+length\s*\[mm\]\s*[:=]\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"total\s+filament\s+used\s*\[m\]\s*=\s*([\d.]+)", re.IGNORECASE),
]

_LAYER_PATTERNS = [
    re.compile(r"total\s+layers?\s+count\s*=\s*(\d+)", re.IGNORECASE),
    re.compile(r"total\s+layer\s+number:\s*(\d+)", re.IGNORECASE),
    re.compile(r"layer\s+count:\s*(\d+)", re.IGNORECASE),
]

# Effective settings dumped by slicer into the gcode AFTER inheritance resolution.
# These are AUTHORITATIVE — they reflect what actually slices the file.
_EFFECTIVE_VOL_SPEED = re.compile(
    r"filament_max_volumetric_speed\s*=\s*([\d.]+)",
    re.IGNORECASE,
)


def _parse_time_string(s: str) -> Optional[int]:
    """Convert '21h 50m 33s' to total seconds, or 'NNNN' if already seconds."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total = 0
    for match in re.finditer(r"(\d+)\s*([dhms])", s):
        n = int(match.group(1))
        unit = match.group(2).lower()
        total += n * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total if total > 0 else None


def _human_time(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _parse_slicer_output(stdout: str, stderr: str) -> dict:
    """Extract time / filament / layers from slicer stdout/stderr (whichever matched)."""
    combined = stdout + "\n" + stderr
    out: dict = {}

    for pat in _TIME_PATTERNS:
        m = pat.search(combined)
        if m:
            t = _parse_time_string(m.group(1))
            if t:
                out["print_time_seconds"] = t
                break

    for pat in _FILAMENT_G_PATTERNS:
        m = pat.search(combined)
        if m:
            out["filament_grams"] = float(m.group(1))
            break

    for pat in _FILAMENT_M_PATTERNS:
        m = pat.search(combined)
        if m:
            val = float(m.group(1))
            # If pattern matched [mm], convert to m. (Pattern source has the
            # brackets escaped — checking for "[mm]" never matched, so Orca
            # metres were silently left in mm until 2026-09-14.)
            if r"\[mm\]" in pat.pattern:
                val = val / 1000
            out["filament_meters"] = val
            break

    for pat in _LAYER_PATTERNS:
        m = pat.search(combined)
        if m:
            out["layer_count"] = int(m.group(1))
            break

    # Effective volumetric speed (post-inheritance, from gcode comments)
    m = _EFFECTIVE_VOL_SPEED.search(combined)
    if m:
        try:
            out["effective_volumetric_speed"] = float(m.group(1))
        except ValueError:
            pass

    return out


def _parse_3mf_gcode(threemf_path: Path) -> dict:
    """If a 3MF was exported, the embedded plate_*.gcode header AND footer have metadata.

    Orca/Bambu put setup info at the top but the print-time summary
    (`; estimated printing time = ...`) goes at the TAIL — after the geometry.
    Read both head and tail to catch both forms.
    """
    out: dict = {}
    if not threemf_path.exists():
        return out
    try:
        with zipfile.ZipFile(threemf_path, "r") as zf:
            gcode_names = [n for n in zf.namelist() if n.endswith(".gcode")]
            if not gcode_names:
                return out
            # Stream: g-code can be hundreds of MB; keep only head + tail chunks
            head_b, tail_b = read_head_tail(zf, gcode_names[0])
            head = head_b.decode("utf-8", errors="replace")
            tail = tail_b.decode("utf-8", errors="replace")
        parsed_head = _parse_slicer_output(head, "")
        parsed_tail = _parse_slicer_output(tail, "")
        # Tail wins for time/filament; head wins for layer count
        out.update(parsed_head)
        out.update(parsed_tail)
    except (zipfile.BadZipFile, KeyError):
        pass
    return out


def _parse_3mf_slice_info(threemf_path: Path) -> dict:
    """Bambu Studio 2.8 writes `Metadata/slice_info.config` (XML) into the exported 3MF.

    It is the same summary the GUI shows after slicing and is more reliable than
    grepping the g-code: Bambu 2.8 no longer emits `; estimated printing time`
    in plate_N.gcode. Keys used:
      <metadata key="prediction" value="3342"/>   -> print_time_seconds
      <filament ... used_m="8.37" used_g="25.15"/> -> filament_meters / filament_grams
      <metadata key="support_used" value="false"/>
    A filament with no density (hollow profile) shows used_g="0.00" — reported as
    0.0 so the caller can warn instead of silently passing.
    """
    out: dict = {}
    if not threemf_path.exists():
        return out
    try:
        with zipfile.ZipFile(threemf_path, "r") as zf:
            names = [n for n in zf.namelist() if n.endswith("slice_info.config")]
            if not names:
                return out
            root = ET.fromstring(read_member(zf, names[0]))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, MemberTooLarge):
        return out

    plate = root.find("plate")
    if plate is None:
        return out
    meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
    try:
        if meta.get("prediction"):
            out["print_time_seconds"] = int(float(meta["prediction"]))
    except ValueError:
        pass
    if "support_used" in meta:
        out["support_used"] = meta["support_used"] == "true"

    grams = 0.0
    meters = 0.0
    seen = False
    for fil in plate.findall("filament"):
        try:
            grams += float(fil.get("used_g") or 0.0)
            meters += float(fil.get("used_m") or 0.0)
            seen = True
        except ValueError:
            continue
    if seen:
        out["filament_grams"] = grams
        out["filament_meters"] = meters
    return out


def _read_result_json(cwd: Path) -> dict:
    """Both CLIs drop `result.json` in the working directory with a return_code and
    (on failure) an `error_string`. We run the slicer with cwd=output_dir so it
    lands next to the 3MF instead of polluting the caller's directory."""
    try:
        return json.loads((cwd / "result.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def build_slicer_cmd(
    slicer: str,
    slicer_bin: str,
    stl_path: Path,
    filament_profile: Path,
    process_profile: Path,
    machine_profile: Path,
    output_3mf: Path,
    slicedata_dir: Path,
    timeout_seconds: int,
) -> List[str]:
    """Assemble the headless command. Flags shared by Orca 2.3 and Bambu 2.8 are
    identical (Orca is a fork); Bambu-only flags are added when slicer == "bambu":
      --mstpp N           max slicing seconds per plate (kills pathological models
                          inside the slicer instead of relying on our subprocess timeout)
      --skip-useless-pick boolean switch (NO value — "--skip-useless-pick 1" makes the
                          CLI treat "1" as an input file and abort with return_code -3)
    """
    cmd = [
        slicer_bin,
        "--load-filaments", str(filament_profile),
        "--load-settings", f"{process_profile};{machine_profile}",
        "--orient", "1",
        "--arrange", "1",
        "--slice", "0",
    ]
    if slicer == "bambu":
        cmd += ["--mstpp", str(max(30, timeout_seconds - 30)), "--skip-useless-pick"]
    cmd += [
        "--export-3mf", str(output_3mf),
        "--export-slicedata", str(slicedata_dir),
        str(stl_path),
    ]
    return cmd


def _read_max_volumetric_speed(filament_profile: Path) -> Optional[float]:
    """Read filament_max_volumetric_speed from filament JSON (resolves inheritance one level)."""
    try:
        data = json.loads(filament_profile.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if "filament_max_volumetric_speed" in data:
        val = data["filament_max_volumetric_speed"]
        if isinstance(val, list) and val:
            try:
                return float(val[0])
            except (TypeError, ValueError):
                return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # Try to resolve inherited from base (Orca uses "inherits" key)
    inherits = data.get("inherits")
    if inherits and isinstance(inherits, str):
        # Search in same directory tree for the parent
        for candidate in filament_profile.parent.glob(f"*{inherits}*.json"):
            return _read_max_volumetric_speed(candidate)
        # Search common Orca filament library paths
        orca_root = Path.home() / "Library/Application Support/OrcaSlicer/system"
        for candidate in orca_root.rglob(f"{inherits}.json"):
            return _read_max_volumetric_speed(candidate)

    return None


# ---------- main entry ----------


def slice_stl(
    stl_path: Path | str,
    *,
    slicer: str = "orca",
    filament_profile: Path | str,
    process_profile: Path | str,
    machine_profile: Path | str,
    output_dir: Path | str,
    slicer_bin: Optional[str] = None,
    timeout_seconds: int = 900,
    max_print_time_hours: Optional[float] = None,
    max_filament_grams: Optional[float] = None,
    min_volumetric_speed: Optional[float] = None,
) -> SliceReport:
    """Slice an STL with OrcaSlicer or Bambu Studio in headless mode.

    Args:
        stl_path: STL file to slice.
        slicer: "orca" or "bambu".
        filament_profile / process_profile / machine_profile: JSON profile paths.
        output_dir: where to write the 3MF + slicedata.
        slicer_bin: override default slicer binary path.
        timeout_seconds: subprocess timeout.
        max_print_time_hours: gate — flag if estimated time exceeds this.
        max_filament_grams: gate — flag if filament weight exceeds this.
        min_volumetric_speed: gate — flag if filament profile volumetric speed below this
            (e.g. set to 5.0 for PETG; a hollow profile falling back to speed=2 would trigger).
    """
    stl_path = Path(stl_path)
    filament_profile = Path(filament_profile)
    process_profile = Path(process_profile)
    machine_profile = Path(machine_profile)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if slicer not in ("orca", "bambu"):
        raise ValueError(f"slicer must be 'orca' or 'bambu', got {slicer!r}")

    # Populate bbox via trimesh (cheap — milliseconds — vs subprocess).
    # This dogfoods our own param_linter discipline: declared dataclass fields
    # must be populated, not left orphan.
    bbox_mm: Optional[Tuple[float, float, float]] = None
    try:
        import trimesh
        _m = trimesh.load(str(stl_path), file_type="stl")
        if hasattr(_m, "dump"):  # Scene
            _m = _m.dump(concatenate=True)
        bbox_mm = tuple(float(x) for x in _m.bounding_box.extents)
    except Exception:
        pass

    if slicer_bin is None:
        slicer_bin = DEFAULT_ORCA_BIN if slicer == "orca" else DEFAULT_BAMBU_BIN

    if not Path(slicer_bin).exists():
        return SliceReport(
            file=str(stl_path),
            slicer=slicer,
            success=False,
            print_time_seconds=None,
            print_time_human="",
            filament_grams=None,
            filament_meters=None,
            layer_count=None,
            max_volumetric_speed=None,
            bbox_mm=None,
            issues=[f"slicer binary not found: {slicer_bin}"],
        )

    output_3mf = output_dir / f"{stl_path.stem}_verify.3mf"
    slicedata_dir = output_dir / f"{stl_path.stem}_slicedata"

    cmd = build_slicer_cmd(
        slicer, slicer_bin, stl_path, filament_profile, process_profile,
        machine_profile, output_3mf, slicedata_dir, timeout_seconds,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(output_dir),  # result.json lands next to the 3MF
        )
        success = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as e:
        return SliceReport(
            file=str(stl_path),
            slicer=slicer,
            success=False,
            print_time_seconds=None,
            print_time_human="",
            filament_grams=None,
            filament_meters=None,
            layer_count=None,
            max_volumetric_speed=None,
            bbox_mm=None,
            issues=[f"slicer timed out after {timeout_seconds}s"],
            raw_stdout_tail=(e.stdout or "")[-2000:] if e.stdout else "",
        )

    parsed = _parse_slicer_output(stdout, stderr)

    # 3MF g-code header is authoritative — override stdout parsing if available
    threemf_parsed = _parse_3mf_gcode(output_3mf)
    parsed.update(threemf_parsed)
    # Bambu Studio 2.8: slice_info.config carries the GUI-equivalent summary
    # (prediction seconds, used_g/used_m). Wins over g-code grep when present.
    parsed.update(_parse_3mf_slice_info(output_3mf))
    result_json = _read_result_json(output_dir)

    # Effective volumetric speed from gcode (post-inheritance) takes priority.
    # Falls back to direct profile read if gcode didn't have it.
    max_vol = parsed.get("effective_volumetric_speed")
    if max_vol is None:
        max_vol = _read_max_volumetric_speed(filament_profile)

    issues: List[str] = []
    warnings: List[str] = []

    if not success:
        err = result_json.get("error_string")
        issues.append(
            f"slicer exited with code {result.returncode}"
            + (f": {err}" if err else "")
        )

    print_time = parsed.get("print_time_seconds")
    filament_g = parsed.get("filament_grams")
    filament_m = parsed.get("filament_meters")
    layer_count = parsed.get("layer_count")

    if print_time is None and success:
        warnings.append("could not parse print time from output (parsing pattern miss)")

    if success and filament_g is not None and filament_g == 0.0 and (filament_m or 0) > 0:
        warnings.append(
            "filament weight reported as 0 g — filament profile has no density "
            "(hollow/unresolved profile; generate flattened profiles with profiles/bambu/flatten.py --all)"
        )

    if max_print_time_hours is not None and print_time is not None:
        max_seconds = max_print_time_hours * 3600
        if print_time > max_seconds:
            issues.append(
                f"print time {_human_time(print_time)} exceeds budget {max_print_time_hours}h"
            )

    if max_filament_grams is not None and filament_g is not None:
        if filament_g > max_filament_grams:
            issues.append(
                f"filament {filament_g:.1f}g exceeds budget {max_filament_grams}g"
            )

    if min_volumetric_speed is not None:
        if max_vol is None:
            warnings.append(
                "could not read filament_max_volumetric_speed from profile "
                "(may be inherited from base — verify manually)"
            )
        elif max_vol < min_volumetric_speed:
            issues.append(
                f"filament_max_volumetric_speed = {max_vol} mm^3/s "
                f"below threshold {min_volumetric_speed} — likely print-time bottleneck "
                f"(a speed of 2 turned a 22 h print into 44 h in a real project)"
            )

    return SliceReport(
        file=str(stl_path),
        slicer=slicer,
        success=success,
        print_time_seconds=print_time,
        print_time_human=_human_time(print_time) if print_time else "",
        filament_grams=filament_g,
        filament_meters=filament_m,
        layer_count=layer_count,
        max_volumetric_speed=max_vol,
        bbox_mm=bbox_mm,
        issues=issues,
        warnings=warnings,
        raw_stdout_tail=(stdout + stderr)[-2000:],
    )
