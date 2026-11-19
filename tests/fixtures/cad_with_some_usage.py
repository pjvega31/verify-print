"""Synthetic fixture — CAD script consuming some keys from params_with_orphans.

Demonstrates: direct subscript (CASE["x"]), .get with default (CASE.get("x", 0)),
and a ghost reference (CASE["nonexistent"]).
"""
from params_with_orphans import CASE, ENCAJE


def build():
    width = CASE["outer_width"]
    depth = CASE["outer_depth"]
    height = CASE["outer_height"]
    wall = CASE.get("wall_thickness", 2.0)

    lip = ENCAJE["lip_height"] + ENCAJE["lip_width"]

    # Ghost reference — key not defined in params_with_orphans.py
    bonus = CASE.get("nonexistent_key", 0)

    return width, depth, height, wall, lip, bonus
