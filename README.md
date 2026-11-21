# verify-print

**Find out why a print will take 17 hours before you press Print.**

`verify-print` opens a Bambu Studio or OrcaSlicer 3MF project and tells you which settings will multiply the print time: a filament profile capped at a crawl, extra-fine layers, dense infill, supports, or anything else the project changed from the stock profile. It reads the file directly, so it needs no slicer and runs on any OS.

```bash
pip install verify-print
verify-print project downloaded_model.3mf
```

![verify-print project blocking a 3MF whose custom filament profile is capped at 2 mm³/s, and warning about 0.08 mm layers, 40 % infill, 6 walls and supports](https://raw.githubusercontent.com/pjvega31/verify-print/main/docs/demo.svg)

<sub>Real output on a sample project: `python scripts/make_demo.py` builds the 3MF, runs the command and redraws this image.</sub>

It also checks the other things that can be verified on the computer before a print: the STL mesh, wall thickness, the CadQuery parameters behind a part, and a real headless slice with limits on time and filament. Every check is deterministic and returns a CI-friendly exit code.

Built and used in production at **Volta Tech PR**, a 3D-printing shop in Puerto Rico running a small print farm (Bambu Lab P1S, Prusa XL, Anycubic Kobra).

> *Español:* hecho y usado en producción en **Volta Tech PR**, Puerto Rico. `verify-print` revisa el modelo, los parámetros CAD y el perfil del slicer **antes** de imprimir, para no perder horas de impresión ni filamento. Las instrucciones de abajo están en inglés; los issues en español son bienvenidos.

## Why it exists

It came out of a real enclosure project at Volta Tech PR that went through four design iterations (v2.0 → v2.4). Each failed print cost 20–44 hours of printer time. Looking back, every one of those bugs was detectable on the computer first:

| Real bug | Check that catches it |
|---|---|
| Cable opening modelled on the front face instead of the back | not caught automatically yet (`orientation` only reads open-boundary meshes, see limitations) |
| Magnet pockets left a 0.2 mm floor that cracked | `thickness` (partial — see [design note](https://github.com/pjvega31/verify-print/blob/main/docs/thin-pocket-detection.md)) |
| A hollow filament profile fell back to 2 mm³/s volumetric speed: **44 h instead of 22 h** | `slice --min-speed`, `project` |
| 18 magnet parameters still defined after the magnet code was commented out | `params` |
| Alignment posts and magnet pockets indistinguishable in the slicer preview | `feature_color` (library) |
| 4 alignment posts not fused to the body: 5 disconnected shells | `mesh` |

## Install

```bash
pip install verify-print                  # mesh, params, info, thickness, orientation, slice, project, compare
pip install "verify-print[mesh-repair]"   # + PyMeshLab deep checks and repair
pip install "verify-print[cad]"           # + CadQuery feature-color export
pip install "verify-print[all]"           # everything, including pytest
```

To work on the code instead, clone the repo and run `pip install -e ".[dev]"`.

Python ≥ 3.9. Slicing uses **Bambu Studio 2.8** (default) or **OrcaSlicer 2.3** (`--slicer orca`) installed at the standard macOS path (`/Applications/…`). Slicing on Linux/Windows is not wired up yet — the mesh, params, info and 3MF-settings checks work everywhere.

## Quick start

```bash
# Before printing a downloaded 3MF: which settings will multiply the print time?
verify-print project model.3mf

# Same, plus a real slice of every plate (macOS + Bambu Studio): fail over 6 h, name the top-3 time sinks
verify-print project model.3mf --printer "Bambu Lab P1S 0.4 nozzle" --slice --max-hours 6

# Is the mesh printable? (watertight, consistent normals, disconnected shells, bbox, volume)
verify-print mesh part.stl

# Bbox, volume, build-volume fit and mass — no slicer needed
verify-print info part.stl --build-volume 256,256,256 --density 1.27

# Thin walls (inward ray casting)
verify-print thickness part.stl --threshold 0.8 --samples 1000

# Parameters defined but never used (orphans) or used but never defined (ghosts)
verify-print params params.py body.py lid.py

# Real slice with gates: fail if volumetric speed < 5 mm³/s or the print exceeds 24 h.
# The profiles and flatten.py live in the repo, not in the pip package: run this from a clone.
python3 profiles/bambu/flatten.py --all   # once: builds flat/ from your Bambu Studio install
B="$HOME/Library/Application Support/BambuStudio/system/BBL"
verify-print slice part.stl \
  --filament profiles/bambu/flat/Volta-PETG-Black.json \
  --process  profiles/bambu/flat/Volta-Tech-PETG-P1S-Standard.json \
  --machine  "$B/machine/Bambu Lab P1S 0.4 nozzle.json" \
  --min-speed 5.0 --max-hours 24

# A whole part folder: mesh check on every STL plus the parameter linter
# (add --filament/--process/--machine to slice too)
verify-print all my_part/

# Was the last change better or worse?
verify-print all my_part/ --json > before.json
verify-print all my_part/ --json > after.json
verify-print compare before.json after.json
```

Exit codes: `0` pass · `1` fail · `2` usage error. Every subcommand accepts `--json`, and every report carries a `passed` boolean.

## What each check does

**Mesh (trimesh).** Watertight, consistent normals, valid volume, disconnected shells, bounding box, volume and centre of mass (`info` adds build-volume fit and a mass estimate). `thickness` samples wall thickness by inward ray casting (use the P5 percentile on noisy meshes). `orientation` (experimental) compares the normal of the largest open boundary with the one you expect.

**Mesh repair (PyMeshLab, optional).** Self-intersections, non-manifold edges and vertices, duplicate faces, open holes; `--repair` writes a repaired STL and re-validates it.

**Slicer simulation.** Runs the Bambu Studio or OrcaSlicer CLI and reads the real numbers: print time, grams, metres, layers, and the *effective* `filament_max_volumetric_speed` after profile inheritance. Optional gates: `--max-hours`, `--max-grams`, `--min-speed`. Accepts several STLs or a folder.

**3MF project gate.** Reads `project_settings.config` and `model_settings.config` and applies these rules:

| Rule | Looks for | Severity |
|---|---|---|
| R0 | No slicer settings in the file (a plain CAD export) | fail: nothing could be checked |
| R1 | Filament max volumetric speed below the floor for the nozzle (5 mm³/s at 0.4 mm and up, 0.5 mm³/s at 0.2 mm; `--min-speed` to set your own) | fail when a custom profile or the project set it; info when it is the stock profile's own value or the slot is not assigned to an object |
| R2 | Layer height < 0.16 mm | warn |
| R3 | Infill > 30 % or more than 4 walls | warn |
| R4 | Supports on | warn |
| R5 | Accelerations < 2000 mm/s² | warn |
| R6 | Fuzzy skin on | info |
| R7 | Every key the project changed versus the stock profile | info |
| R8 | Printer preset differs from `--printer` | warn |
| R9 | With `--slice`: plate time over `--max-hours`, with the top-3 features eating the time | fail |

R2, R3, R4 and R6 also run on per-object overrides, which a project-wide check would miss. Only a fail changes the exit code.

Why R1 asks where the number came from: stock profiles for 0.2 mm nozzles and flexible filaments legitimately run at 0.5–4 mm³/s, so a fixed threshold would block normal prints. The floors come from the stock Bambu Studio 2.8 filament profiles, where the 10th percentile is 6 mm³/s at 0.4 mm and 0.5 mm³/s at 0.2 mm.

**Parameter linter (AST).** Finds keys defined in a params dict but never read (orphans) and keys read but never defined (ghosts — a runtime crash waiting to happen). Dynamic access (`**DICT`, f-string keys) switches that dict to review mode instead of guessing.

**Feature colour (CadQuery, optional).** Exports each feature class as a separately coloured object (STLs + `colors.json`, or one GLTF/STEP), so posts and pockets look different in the slicer.

```python
from verify_print import check_mesh, lint_params, slice_stl
from verify_print.feature_color import export_features   # needs [cad]

export_features(
    {"body": (body, "#00BCD4"), "posts": (posts, "#FFD600"), "pockets": (pockets, "#E53935")},
    output_dir="out/", format="stls",
)
```

## Bundled slicer profiles

`profiles/bambu/src/` holds PETG overrides for a Bambu Lab P1S with a 0.4 mm nozzle. The Bambu Studio CLI does not resolve `inherits` in user JSON, so `flatten.py` merges them onto your locally installed Bambu system profiles into `flat/` — generated on your machine, not shipped, because those base profiles are Bambu's (AGPL-3.0). See [profiles/bambu/README.md](https://github.com/pjvega31/verify-print/blob/main/profiles/bambu/README.md) for the measured time differences and the CLI gotchas.

## Known limitations

- The project gate only understands Bambu Studio and OrcaSlicer projects. It tells stock presets from custom ones by name (`… @BBL …`), not by reading your installed profiles, so the verdict is the same on every machine; a custom preset that keeps the `@` suffix is treated as stock.
- Filament slots used only through painted colours are not detected as in use, so a slow profile there is reported as info, not fail.
- Mesh checks read STL only; the geometry inside a 3MF is not checked.
- Wall thickness samples faces at random, one ray per face, without weighting by area: a thin wall made of a few large triangles can be missed, and wedges or thin embossed details can fail. Raise `--samples`, and treat a fail as "look here", not as proof.
- `orientation` is experimental: it needs an open boundary, so on an ordinary watertight solid it reports "mesh is watertight" and fails. It cannot tell which face of a closed part a cut-out is on.
- Filament weight is 0 g when the profile has no density, which makes `--max-grams` inert.
- The project gate reads all plates; plain `slice` reads `plate_1` only.
- Slicer time estimates are typically ±15–30 % off real prints; no printer calibration is applied.
- Slicer binaries are looked up at macOS paths only.
- The parameter linter does not resolve dict aliasing (`p = CASE; p["x"]`); this is an `xfail` test on purpose.
- `feature_color` works at CAD-authoring time only; it cannot re-colour an existing STL.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
```

The test suite needs no slicer installed (slicer output parsing and command building are unit-tested); PyMeshLab and CadQuery tests skip when those extras are missing. `scripts/replay_backtest.py` replays the tool over the git history of a CAD repo to see which commits it would have flagged.

## Contributing

Bug reports with the failing STL or 3MF attached are the most useful thing you can send — see [CONTRIBUTING.md](https://github.com/pjvega31/verify-print/blob/main/CONTRIBUTING.md).

## License

MIT — see [LICENSE](https://github.com/pjvega31/verify-print/blob/main/LICENSE). Optional extras keep their own licenses: PyMeshLab is GPL-3.0, CadQuery is Apache-2.0. Bambu Studio and OrcaSlicer (AGPL-3.0) are called as external programs, never bundled.

Maintained as time allows; issues and PRs are reviewed but response times vary.
