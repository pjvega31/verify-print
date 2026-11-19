"""Synthetic fixture — params.py with orphan keys.

Simulates a real bug: magnet keys still in CASE dict but never
referenced in active code (magnet code commented out, dict not cleaned up).
"""

CASE = {
    # Used keys (active in cad_with_some_usage.py)
    "outer_width": 250.0,
    "outer_depth": 178.0,
    "outer_height": 160.0,
    "wall_thickness": 3.0,

    # ORPHAN: magnets removed from code, params dict not cleaned
    "top_magnet_count": 4,
    "top_magnet_diameter": 6.0,
    "top_magnet_thickness": 3.0,
}

ENCAJE = {
    "lip_height": 3.0,
    "lip_width": 3.0,
    # ORPHAN: a key never used
    "lip_color": "blue",
}
