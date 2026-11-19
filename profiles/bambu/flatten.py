#!/usr/bin/env python3
"""Flatten a Bambu Studio profile: resolve the `inherits` chain against the BBL
system library and merge overrides on top, producing a self-contained JSON the
Bambu Studio CLI accepts without inheritance resolution.

Usage:
  flatten.py <override.json> <out.json>     flatten one profile
  flatten.py --all                          flatten every src/*.json into flat/

flat/ is NOT committed: the resolved profiles are mostly Bambu Studio's own
system profiles (AGPL-3.0), so each user generates them from their local
Bambu Studio install. Run --all once after installing, and after editing src/.

Set BAMBU_SYSTEM_DIR to point at a non-default BBL system profile folder.
"""
import json, os, sys
from pathlib import Path

SYS = Path(os.environ.get("BAMBU_SYSTEM_DIR",
           Path.home() / "Library/Application Support/BambuStudio/system/BBL"))
KIND = {"process": "process", "filament": "filament", "machine": "machine"}

def find(name: str, kind: str) -> Path:
    if not SYS.is_dir():
        raise FileNotFoundError(f"Bambu Studio system profiles not found at {SYS} (install Bambu Studio or set BAMBU_SYSTEM_DIR)")
    p = SYS / KIND[kind] / f"{name}.json"
    if p.exists():
        return p
    hits = list((SYS / KIND[kind]).glob(f"{name}.json"))
    if not hits:
        raise FileNotFoundError(f"{kind} profile not found in BBL system: {name}")
    return hits[0]

def resolve(data: dict, kind: str, chain: list) -> dict:
    parent = data.get("inherits")
    if not parent:
        return dict(data)
    chain.append(parent)
    base = resolve(json.loads(find(parent, kind).read_text()), kind, chain)
    base.update({k: v for k, v in data.items() if k != "inherits"})
    return base

def flatten(src: Path, dst: Path) -> None:
    d = json.loads(src.read_text())
    kind = d["type"]
    chain = []
    flat = resolve(d, kind, chain)
    flat["from"] = "User"
    flat["instantiation"] = "true"
    flat.pop("inherits", None)
    flat["_flattened_from"] = chain
    dst.write_text(json.dumps(flat, indent=2, ensure_ascii=False))
    print(f"{src.name}: {len(d)} keys -> {len(flat)} keys via {' <- '.join(chain)}")

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    if len(sys.argv) == 2 and sys.argv[1] == "--all":
        (here / "flat").mkdir(exist_ok=True)
        for src in sorted((here / "src").glob("*.json")):
            flatten(src, here / "flat" / src.name)
    elif len(sys.argv) == 3:
        flatten(Path(sys.argv[1]), Path(sys.argv[2]))
    else:
        print(__doc__); sys.exit(2)
