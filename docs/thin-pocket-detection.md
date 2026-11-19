# Thin-pocket (magnet flotation) detection — design note

**Status:** investigated, not implemented yet. Target for a future release.

## The bug

A designer added 4 retention magnets (6 mm × 3 mm) in the corners of a part whose wall was 3 mm thick. The 2.8 mm-deep pocket left a 0.2 mm floor. During slicing / first-layer adhesion the floor cracked or floated up. The magnets were removed in the next iteration.

The first scope note said this was "out of scope — requires a physical test". A later review asked whether a thin-wall + feature-proximity heuristic could catch it. This note answers that.

## Two possible detection paths

### Path A — STL-side post-hoc (what would catch it after CAD export)

**Approach:** After CAD exports STL, run `check_wall_thickness` (P1, already shipped). For magnet-pocket areas, the wall thickness at the pocket bottom would be 0.2mm — well below the 0.8mm threshold. The wall thickness check would flag the region.

**Tested feasibility:** YES — but with caveats. The thickness sampler casts random rays. To reliably catch a 6mm pocket footprint on a large part, sample density must be high enough that several rays land inside the pocket. With `sample_count=1000` on a ~250×178×163 mm enclosure (~50k faces), the expected hit rate per pocket is ~0.5-2 rays. Marginal.

**Improvement (v0.5):** weight sampling toward small face-clusters (high-curvature regions). Magnet pockets create concave circular face groups distinct from the otherwise-planar walls. Targeted sampling there would catch even small thin-floor cases reliably.

**Cost:** ~2-4h dev. Reuses existing `check_wall_thickness` infrastructure.

### Path B — CAD-side ex-ante (what would catch it BEFORE export)

**Approach:** Static analysis of the CADQuery script. For each `cut()` call that removes a cylindrical or rectangular pocket, infer:
1. Pocket depth (from extrude length)
2. Local wall thickness at pocket location (from outer geometry — bbox + offset)
3. If `wall - pocket_depth < safety_margin`, flag at CAD time

**Tested feasibility:** Hard. CADQuery scripts use procedural geometry — `cq.Workplane(...).circle(R).extrude(D)` — and the relationship between the cut depth and the local wall thickness is non-trivial without symbolic execution. Would need a CADQuery-aware analyzer that understands `.workplane(offset=)`, `.cut()`, `.union()`, etc.

**Cost:** ~12-20h dev. Higher value (catches at authoring time) but much more complex.

### Path C — User-declared annotation

**Approach:** Have the CAD author annotate magnet pockets explicitly:

```python
from verify_print.feature_color import featured

@featured("magnet_pocket", magnet_thickness=3.0, wall_floor_min=0.8)
def make_pocket(...):
    return cq.Workplane(...).circle(...).extrude(...)
```

The decorator records the contract; a post-build check verifies actual floor thickness matches the declared minimum.

**Cost:** ~4-6h dev. Requires CAD-script refactor — sit alongside `feature_color` Layer E.

## Recommendation for v0.5

Path A (improved sampling) gives the best cost / detection ratio. Path C would be elegant but couples to feature_color refactor on the CAD side. Path B is research-grade and deferred.

**Specifically:** Add a `check_thin_pockets` function to `mesh_integrity.py` that:
1. Identifies small concave face clusters via curvature analysis
2. Within each cluster, runs targeted thickness rays
3. Flags clusters where minimum thickness < threshold AND cluster footprint matches a "pocket signature" (small contiguous concave region, not just a chamfer)

Validation case: an STL of the failing part (with magnets) and verify the function flags exactly those 4 pocket locations.

## Why it is not implemented yet

The heuristic needs at least one ground-truth STL with the bug visible to validate against, and that artifact was not preserved. Without validation data it would be speculative code.

**If you hit this bug, please open an issue and attach the failing STL** — that is exactly the ground truth this check needs.

## Verdict

"Out of scope" was right as an early boundary, but "physical test required" was too strong. Path A is implementable as an STL-side check and is the right next target.
