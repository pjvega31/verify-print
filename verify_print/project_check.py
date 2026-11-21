"""Layer B+ — pre-print gate for a Bambu Studio / Orca 3MF project.

Answers a recurring question: "why did a 4 h part become 17 h?"  Every
time it happened it was ONE setting inherited from a downloaded project or a
bad profile, not the printer.  This module opens the 3MF *before* you hit
Print and flags the known time-multipliers, lists exactly what the project
changed from the stock profile, and (optionally) runs the Bambu Studio CLI to
get the per-plate time + the per-feature breakdown so the culprit is named.

Rules (severity: FAIL blocks, WARN needs a look, INFO is just so you know):
  R0  no slicer settings in the file                FAIL  (nothing could be checked)
  R1  filament max volumetric speed below the floor FAIL  when a custom profile or the
      for the nozzle (5 mm^3/s at 0.4 mm and up,          project set it (the 44h-vs-22h bug);
      0.5 mm^3/s at 0.2 mm)                         INFO  when it is the stock profile's own
                                                          value or the slot is not in use
  R2  layer_height < 0.16 mm                        WARN  (x1.5-2.5 time)
  R3  sparse_infill_density > 30% or wall_loops > 4 WARN  (decorative parts)
  R4  enable_support on                             WARN  (only if the part needs it)
      R2/R3/R4/R6 also run on per-object overrides stored in model_settings.config
  R5  default / outer-wall acceleration < 2000      WARN  (draft/foreign profile)
  R6  fuzzy_skin on                                 INFO  (+30-60% outer-wall time)
  R7  different_settings_to_system                  INFO  (everything the project changed)
  R8  printer preset != expected                    WARN  (wrong nozzle / machine)
  R9  --slice: plate time > --max-hours             FAIL

Verified on Deco_Pumpkin_0.20.3mf (2026-09-14): fuzzy skin = 94 min of a
229 min plate; nothing else out of range -> 3h49m accepted as-is.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from verify_print._safezip import read_member
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from verify_print.slicer_simulate import (
    DEFAULT_BAMBU_BIN,
    _human_time,
    _parse_3mf_slice_info,
)

# ---------- thresholds (single place to tune) ----------
MIN_VOLUMETRIC_SPEED = 5.0      # mm^3/s, for a 0.4 mm nozzle and larger (see _speed_floor)
MIN_VOLUMETRIC_SPEED_SMALL_NOZZLE = 0.5   # mm^3/s, for a 0.2 mm nozzle
REFERENCE_NOZZLE = 0.4          # mm
SMALL_NOZZLE = 0.2              # mm
MIN_LAYER_HEIGHT = 0.16         # mm
MAX_INFILL_PCT = 30.0           # %
MAX_WALL_LOOPS = 4
MIN_ACCELERATION = 2000.0       # mm/s^2 (BBL stock is 6000-10000)

# Per-object overrides in model_settings.config that the time rules understand.
OVERRIDE_KEYS = ("layer_height", "sparse_infill_density", "wall_loops", "enable_support",
                 "support_type", "support_style", "fuzzy_skin",
                 "fuzzy_skin_point_distance", "fuzzy_skin_thickness")


@dataclass
class Finding:
    rule: str
    severity: str        # FAIL | WARN | INFO
    message: str
    value: Any = None


@dataclass
class PlateInfo:
    index: int
    objects: List[str]
    extruders: List[int]
    print_time_seconds: Optional[int] = None
    filament_grams: Optional[float] = None
    top_features: List[Dict[str, float]] = field(default_factory=list)


@dataclass
class ProjectReport:
    file: str
    printer: Optional[str]
    process: Optional[str]
    filaments: List[str]
    filament_colours: List[str]
    plates: List[PlateInfo]
    findings: List[Finding]
    sliced: bool = False

    @property
    def issues(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "FAIL"]

    @property
    def passed(self) -> bool:
        return not self.issues

    def format(self) -> str:
        lines = [f"Project gate: {self.file}"]
        lines.append(f"  printer:   {self.printer}")
        lines.append(f"  process:   {self.process}")
        for i, (fid, col) in enumerate(zip(self.filaments, self.filament_colours), 1):
            lines.append(f"  filament {i}: {fid} {col}")
        for p in self.plates:
            objs = ", ".join(f"{o} (ext {e})" for o, e in zip(p.objects, p.extruders)) or "(empty)"
            lines.append(f"  plate {p.index}: {objs}")
            if p.print_time_seconds is not None:
                g = f", {p.filament_grams:.1f} g" if p.filament_grams is not None else ""
                lines.append(f"           {_human_time(p.print_time_seconds)}{g}")
                for ft in p.top_features:
                    lines.append(f"             {ft['feature']:<22s} {ft['minutes']:6.1f} min  {ft['pct']:5.1f}%")
        for sev in ("FAIL", "WARN", "INFO"):
            group = [f for f in self.findings if f.severity == sev]
            if group:
                lines.append(f"  {sev} ({len(group)}):")
                for f in group:
                    lines.append(f"    [{f.rule}] {f.message}")
        if any(f.rule == "R0" and f.severity == "FAIL" for f in self.findings):
            lines.append("  NOT CHECKED — this file carries no slicer settings to check")
        elif self.passed:
            lines.append("  PASS — no blocking settings found")
        else:
            lines.append("  BLOCKED — fix FAIL items before printing")
        return "\n".join(lines)


# ---------- helpers ----------

def _num(v: Any) -> Optional[float]:
    """Config values arrive as '0.2', '15%', ['22','22'], 22 ... normalise to float."""
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    try:
        return float(str(v).strip().rstrip("%"))
    except ValueError:
        return None


def _nums(v: Any) -> List[float]:
    if not isinstance(v, list):
        v = [v]
    out = []
    for x in v:
        n = _num(x)
        if n is not None:
            out.append(n)
    return out


def _int(v: Any, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _speed_floor(nozzle_mm: Optional[float]) -> float:
    """Volumetric-speed floor for a nozzle size, in mm^3/s.

    Anchored on the stock Bambu Studio 2.8 filament profiles, where the 10th percentile of
    filament_max_volumetric_speed is 6 at a 0.4 mm nozzle and 0.5 at a 0.2 mm nozzle (a
    0.2 mm nozzle is not a quarter of the flow in practice, it is closer to a tenth).
    Floors: 5.0 at 0.4 mm and above, 0.5 at 0.2 mm and below, linear in between.
    """
    if not nozzle_mm or nozzle_mm >= REFERENCE_NOZZLE:
        return MIN_VOLUMETRIC_SPEED
    if nozzle_mm <= SMALL_NOZZLE:
        return MIN_VOLUMETRIC_SPEED_SMALL_NOZZLE
    t = (nozzle_mm - SMALL_NOZZLE) / (REFERENCE_NOZZLE - SMALL_NOZZLE)
    return round(MIN_VOLUMETRIC_SPEED_SMALL_NOZZLE + t * (MIN_VOLUMETRIC_SPEED - MIN_VOLUMETRIC_SPEED_SMALL_NOZZLE), 2)


def _looks_stock(preset_name: str) -> bool:
    """Bambu Studio / OrcaSlicer system presets are named '<material> @<vendor/printer>'.

    This is a naming heuristic, chosen over reading the locally installed profiles so the
    verdict is the same on every machine (CI included). A custom preset that keeps the
    '@...' suffix and leaves no trace in different_settings_to_system is treated as stock.
    """
    return " @" in preset_name


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def read_project(threemf: Path) -> Dict[str, Any]:
    """Parse the two config members of a Bambu Studio / Orca 3MF.

    Returns a dict with:
      settings              project_settings.config as a dict ({} when the member is absent)
      has_settings          False for a 3MF without slicer settings (e.g. a plain CAD export)
      plates                [PlateInfo]
      objects               one dict per object placed on a plate:
                            {"name", "extruders" (object + part level), "overrides"}
      model_settings_error  parse error text when model_settings.config is malformed
    """
    with zipfile.ZipFile(threemf, "r") as zf:
        names = set(zf.namelist())
        settings: Dict[str, Any] = {}
        has_settings = "Metadata/project_settings.config" in names
        if has_settings:
            settings = json.loads(read_member(zf, "Metadata/project_settings.config"))
        plates: List[PlateInfo] = []
        placed: List[Dict[str, Any]] = []
        model_settings_error: Optional[str] = None
        if "Metadata/model_settings.config" in names:
            try:
                root = ET.fromstring(read_member(zf, "Metadata/model_settings.config"))
            except ET.ParseError as e:
                root = None
                model_settings_error = str(e)
            objects: Dict[str, Dict[str, Any]] = {}
            for obj in (root.findall("object") if root is not None else []):
                meta = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
                extruder = _int(meta.get("extruder"), 1)
                extruders = {extruder}
                for part in obj.findall("part"):
                    pmeta = {m.get("key"): m.get("value") for m in part.findall("metadata")}
                    if pmeta.get("extruder"):
                        extruders.add(_int(pmeta.get("extruder"), extruder))
                objects[obj.get("id")] = {
                    "name": meta.get("name", f"object {obj.get('id')}"),
                    "extruder": extruder,
                    "extruders": sorted(extruders),
                    "overrides": {k: meta[k] for k in OVERRIDE_KEYS if meta.get(k) is not None},
                }
            for plate in (root.findall("plate") if root is not None else []):
                meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
                idx = _int(meta.get("plater_id"), len(plates) + 1)
                objs, exts = [], []
                for inst in plate.findall("model_instance"):
                    imeta = {m.get("key"): m.get("value") for m in inst.findall("metadata")}
                    o = objects.get(imeta.get("object_id"))
                    if o:
                        objs.append(o["name"])
                        exts.append(o["extruder"])
                        if o not in placed:
                            placed.append(o)
                plates.append(PlateInfo(index=idx, objects=objs, extruders=exts))
    return {"settings": settings, "has_settings": has_settings, "plates": plates,
            "objects": placed, "model_settings_error": model_settings_error}


# ---------- rules ----------

def _time_rules(s: Dict[str, Any], where: str = "") -> List[Finding]:
    """R2/R3/R4/R6 on a settings dict: the project's globals, or one object's overrides."""
    out: List[Finding] = []

    # R2 layer height
    lh = _num(s.get("layer_height"))
    if lh is not None and 0 < lh < MIN_LAYER_HEIGHT:
        out.append(Finding("R2", "WARN",
            f"{where}layer_height = {lh} mm — below {MIN_LAYER_HEIGHT}; expect x{round(0.2/lh, 1)} print time vs 0.20", lh))

    # R3 infill / walls
    inf = _num(s.get("sparse_infill_density"))
    if inf is not None and inf > MAX_INFILL_PCT:
        out.append(Finding("R3", "WARN",
            f"{where}sparse_infill_density = {inf:g}% — above {MAX_INFILL_PCT:g}%; decorative parts rarely need it", inf))
    wl = _num(s.get("wall_loops"))
    if wl is not None and wl > MAX_WALL_LOOPS:
        out.append(Finding("R3", "WARN", f"{where}wall_loops = {wl:g} — above {MAX_WALL_LOOPS}", wl))

    # R4 supports
    if _num(s.get("enable_support")) == 1:
        kind = ", ".join(str(s[k]) for k in ("support_type", "support_style") if s.get(k))
        out.append(Finding("R4", "WARN",
            f"{where}supports enabled{f' ({kind})' if kind else ''} — confirm the part needs them", True))

    # R6 fuzzy skin
    fz = s.get("fuzzy_skin")
    if fz and str(fz).lower() not in ("none", "0", ""):
        out.append(Finding("R6", "INFO",
            f"{where}fuzzy_skin = {fz} (point {s.get('fuzzy_skin_point_distance')} mm, thickness "
            f"{s.get('fuzzy_skin_thickness')} mm) — +30-60% outer-wall time; keep if the look needs it", fz))
    return out


def _volumetric_speed_rule(s: Dict[str, Any], min_speed: Optional[float],
                           used_filaments: Optional[set]) -> List[Finding]:
    """R1 — a low filament_max_volumetric_speed caps every move (22 h became 44 h once).

    A low number is only a problem when somebody *set* it. Stock profiles for 0.2 mm
    nozzles and flexible filaments legitimately sit at 0.5-4 mm^3/s, so the rule asks
    where the value came from before it blocks:
      - stock-named preset, value not listed as changed -> INFO (that profile's own value)
      - slot not assigned to any object                 -> INFO
      - custom preset, or the project changed the value -> FAIL
    `min_speed` replaces the nozzle-based floor as given.
    """
    out: List[Finding] = []
    raw = s.get("filament_max_volumetric_speed")
    raw = raw if isinstance(raw, list) else [raw]
    names = _as_list(s.get("filament_settings_id"))
    diffs = _as_list(s.get("different_settings_to_system"))
    nozzles = _nums(s.get("nozzle_diameter"))
    nozzle = min(nozzles) if nozzles else None
    floor = min_speed if min_speed is not None else _speed_floor(nozzle)
    nozzle_txt = f" for a {nozzle:g} mm nozzle" if nozzle and min_speed is None else ""

    for i, item in enumerate(raw):
        v = _num(item)
        if v is None or v >= floor:
            continue
        slot = i + 1
        name = names[i] if i < len(names) else "unknown preset"
        # layout: [process, filament 1..N, (printer)]
        changed = slot < len(diffs) and "filament_max_volumetric_speed" in diffs[slot].split(";")
        label = f"filament {slot} ({name}): max volumetric speed {v:g} mm^3/s"
        if _looks_stock(name) and not changed:
            out.append(Finding("R1", "INFO",
                f"{label} — this stock profile's own value; print speed will be flow-limited, "
                f"which is normal for small nozzles and flexible filaments", v))
        elif used_filaments is not None and slot not in used_filaments:
            out.append(Finding("R1", "INFO",
                f"{label} — below {floor:g}{nozzle_txt}, but this slot is not assigned to any object "
                f"(painted colours are not detected)", v))
        else:
            why = "the project changed it from the stock profile" if changed else "set by a custom profile"
            out.append(Finding("R1", "FAIL",
                f"{label} — below {floor:g}{nozzle_txt} and {why}; "
                f"the slicer caps every move to this flow", v))
    return out


def check_settings(s: Dict[str, Any], expected_printer: Optional[str] = None, *,
                   min_speed: Optional[float] = None,
                   used_filaments: Optional[set] = None) -> List[Finding]:
    out: List[Finding] = _volumetric_speed_rule(s, min_speed, used_filaments)
    time_findings = _time_rules(s)
    out += [f for f in time_findings if f.rule != "R6"]

    # R5 accelerations
    for key in ("default_acceleration", "outer_wall_acceleration", "inner_wall_acceleration", "travel_acceleration"):
        a = _num(s.get(key))
        # 0 means "inherit default_acceleration" in Bambu/Orca — not a limit
        if a is not None and 0 < a < MIN_ACCELERATION:
            out.append(Finding("R5", "WARN",
                f"{key} = {a:g} mm/s^2 — below {MIN_ACCELERATION:g}; BBL stock is 5000-10000 "
                f"(draft/foreign profile, ~x1.5-2 time)", a))

    # R6 fuzzy skin
    out += [f for f in time_findings if f.rule == "R6"]

    # R7 what the project changed vs stock profiles
    diffs = _as_list(s.get("different_settings_to_system"))
    n_fil = len(_as_list(s.get("filament_settings_id")))
    # Bambu Studio writes [process, filament 1..N, printer]
    labels = ["process"] + [f"filament {i}" for i in range(1, n_fil + 1)] + ["printer"]
    labels += [f"preset {i}" for i in range(len(labels) + 1, len(diffs) + 1)]
    for label, d in zip(labels, diffs):
        keys = [k for k in d.split(";") if k]
        if not keys:
            continue
        shown = ", ".join(f"{k}={s.get(k)}" for k in keys[:12])
        more = f" (+{len(keys)-12} more)" if len(keys) > 12 else ""
        out.append(Finding("R7", "INFO", f"{label} differs from stock in {len(keys)} key(s): {shown}{more}", keys))

    # R8 printer preset
    printer = s.get("printer_settings_id")
    if expected_printer and printer != expected_printer:
        out.append(Finding("R8", "WARN", f"printer preset is '{printer}', expected '{expected_printer}'", printer))

    return out


# ---------- optional slice via Bambu CLI ----------

def slice_project(threemf: Path, *, slicer_bin: str = DEFAULT_BAMBU_BIN,
                  timeout_seconds: int = 900) -> Dict[int, Dict[str, Any]]:
    """Slice every plate headlessly; return {plate_index: {time, grams, features}}."""
    if not Path(slicer_bin).exists():
        raise FileNotFoundError(f"slicer binary not found: {slicer_bin}")
    work = Path(tempfile.mkdtemp(prefix="verify_print_project_"))
    out_3mf = work / "sliced.3mf"
    cmd = [slicer_bin, "--slice", "0", "--mstpp", str(max(30, timeout_seconds - 30)),
           "--skip-useless-pick", "--export-3mf", str(out_3mf), str(threemf)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, cwd=str(work))
        result_json: Dict[str, Any] = {}
        try:
            result_json = json.loads((work / "result.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        if res.returncode != 0 or not out_3mf.exists():
            raise RuntimeError(
                f"slicer exit {res.returncode}: {result_json.get('error_string') or res.stdout[-400:]}")

        # per-plate time/grams from slice_info.config (all plates)
        per_plate: Dict[int, Dict[str, Any]] = {}
        with zipfile.ZipFile(out_3mf) as zf:
            root = ET.fromstring(read_member(zf, "Metadata/slice_info.config"))
        for plate in root.findall("plate"):
            meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            idx = int(meta.get("index", "0") or 0)
            grams = sum(float(f.get("used_g") or 0) for f in plate.findall("filament"))
            per_plate[idx] = {
                "print_time_seconds": int(float(meta.get("prediction", "0") or 0)),
                "filament_grams": grams,
                "features": [],
            }
        # feature breakdown from result.json (same order as plates sliced)
        sliced = result_json.get("sliced_plates", [])
        for i, sp in enumerate(sliced):
            ft = sp.get("feature_type_times") or {}
            tot = sum(ft.values()) or 1.0
            top = sorted(ft.items(), key=lambda kv: -kv[1])[:3]
            key = sp.get("plate_index") or (i + 1)
            target = per_plate.get(key) or per_plate.get(i + 1)
            if target is not None:
                target["features"] = [
                    {"feature": k, "minutes": round(v / 60, 1), "pct": round(100 * v / tot, 1)} for k, v in top
                ]
        return per_plate
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------- entry ----------

def check_project(threemf: Path | str, *, expected_printer: Optional[str] = None,
                  do_slice: bool = False, max_hours: Optional[float] = None,
                  min_speed: Optional[float] = None,
                  slicer_bin: str = DEFAULT_BAMBU_BIN, timeout_seconds: int = 900) -> ProjectReport:
    threemf = Path(threemf)
    data = read_project(threemf)
    s = data["settings"]
    plates: List[PlateInfo] = data["plates"]
    placed: List[Dict[str, Any]] = data["objects"]
    sliced = False

    findings: List[Finding] = []
    if not data["has_settings"]:
        findings.append(Finding("R0", "FAIL",
            "no Bambu Studio / OrcaSlicer settings in this 3MF (Metadata/project_settings.config "
            "is missing) — nothing was checked. Open it in the slicer and save it as a project first."))
    if data["model_settings_error"]:
        findings.append(Finding("R0", "WARN",
            f"model_settings.config is not valid XML ({data['model_settings_error']}) — plates, "
            f"filament usage and per-object overrides were not checked"))

    used = {e for o in placed for e in o["extruders"]} or None
    findings += check_settings(s, expected_printer, min_speed=min_speed, used_filaments=used)
    for o in placed:
        if o["overrides"]:
            findings += _time_rules(o["overrides"], where=f"object '{o['name']}' overrides: ")

    if do_slice:
        try:
            per_plate = slice_project(threemf, slicer_bin=slicer_bin, timeout_seconds=timeout_seconds)
            sliced = True
            for p in plates:
                info = per_plate.get(p.index)
                if not info:
                    continue
                p.print_time_seconds = info["print_time_seconds"]
                p.filament_grams = info["filament_grams"]
                p.top_features = info["features"]
                if max_hours is not None and p.print_time_seconds > max_hours * 3600:
                    findings.append(Finding("R9", "FAIL",
                        f"plate {p.index} estimated {_human_time(p.print_time_seconds)} exceeds budget {max_hours}h",
                        p.print_time_seconds))
        except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as e:
            findings.append(Finding("R9", "WARN", f"could not slice project: {e}"))

    return ProjectReport(
        file=str(threemf),
        printer=s.get("printer_settings_id"),
        process=s.get("print_settings_id"),
        filaments=_as_list(s.get("filament_settings_id")),
        filament_colours=_as_list(s.get("filament_colour")),
        plates=plates,
        findings=findings,
        sliced=sliced,
    )


def report_to_dict(r: ProjectReport) -> Dict[str, Any]:
    d = asdict(r)
    d["passed"] = r.passed
    return d
