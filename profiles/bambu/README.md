# Bambu Studio CLI profiles (P1S 0.4, PETG)

`verify-print slice` runs **Bambu Studio 2.8 CLI** by default since 2026-09-14
(`--slicer orca` still works). Bambu's CLI does **not** resolve `inherits` for
user-supplied JSON, so the profiles must be flattened first.

**`flat/` is generated on your machine, not shipped.** A flattened profile is ~94 % Bambu Studio's
own system profile (AGPL-3.0); this repo only ships the overrides in `src/`. Generate once:

```bash
python3 profiles/bambu/flatten.py --all   # reads your local Bambu Studio install
```

| Dir | What | Edit? |
|---|---|---|
| `src/` | Overrides on top of a BBL base (`inherits`) | yes |
| `flat/` | Fully resolved profiles the CLI accepts (generated locally, git-ignored) | no — run `python3 flatten.py --all` |

Machine profile: use Bambu's stock
`~/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json`
(it needs no flattening — the CLI resolves the system chain on its own).

## Canonical call

```bash
B="$HOME/Library/Application Support/BambuStudio/system/BBL"
P="profiles/bambu/flat"   # from the repo root
verify-print slice model.stl \
  --filament "$P/Volta-PETG-Black.json" \
  --process  "$P/Volta-Tech-PETG-P1S-Standard.json" \
  --machine  "$B/machine/Bambu Lab P1S 0.4 nozzle.json" \
  --min-speed 5.0 --json
```

## Profiles

| Name | Intent | Notes |
|---|---|---|
| `Volta-Tech-PETG-P1S-Standard` | **canonical** — 0.20 mm, 3 walls, 15 % gyroid, organic tree, textured PEI | speeds/accels = BBL 0.20mm Standard (P1S stock) |
| `Volta-Tech-PETG-P1S-Standard-accel500` | Orca-parity: same geometry + the old DRAFT 500/1500 mm/s² accelerations | reproduces older Orca estimates; ~2× slower than stock, never validated on hardware |
| `Volta-PETG-Black` | Bambu PETG Basic @BBL X1C (255 °C, 15 mm³/s, ρ 1.25) + Volta colour | replaces the hollow Orca `Volta-Generic-PETG-Black` (which fell back to 2 mm³/s / 200 °C) |

## Verified 2026-09-14 (topper_burger.stl v0.4, 191 layers, 25.15 g)

| Profile | Slicer | Time |
|---|---|---|
| Orca Volta-Standard + Bambu PETG Basic (old setup) | Orca 2.3.2 | 2h 08m |
| `-accel500` flat | Bambu 2.8.2 | 1h 49m |
| `Standard` flat (canonical) | Bambu 2.8.2 | 55m 42s |
| BBL 0.20 Standard stock (2 walls, 20 %) | Bambu 2.8.2 | 40m 45s |

CLI wall time ≈ 1 s per part on Bambu vs ≈ 18 s on Orca 2.3.2.

## Gotchas (all hit during migration)

- `--skip-useless-pick` is a switch. `--skip-useless-pick 1` → "1" is taken as an input file → `return_code -3`.
- PETG flat profiles have `cool_plate_temp = 0`; without `curr_bed_type` in the process JSON the CLI defaults to Cool Plate and fails with `return_code -61 "Filaments are not compatible with the plate type"`. `src/` sets `Textured PEI Plate`.
- Bambu 2.8 does not write `; estimated printing time` into `plate_1.gcode`; read `Metadata/slice_info.config` (`prediction`, `used_g`, `used_m`). `slicer_simulate.py` does this.
- Both CLIs drop `result.json` in the **cwd**; `slice_stl` now runs with `cwd=output_dir`.
