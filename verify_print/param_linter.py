"""CAD-aware param hygiene linter — Layer D build.

Catches bug class #4: keys defined in a params dict but never accessed in
active code (the "18 magnet keys live in params.py but code commented out"
pattern).

Detection strategy:
  1. AST-parse the params file: collect top-level dict assignments
     (CASE = {...}, ENCAJE = {...}, etc.) and their string keys.
  2. AST-parse all other code files: collect every DICT["key"] subscript and
     DICT.get("key", ...) call where the dict name matches one we defined.
     Also handles params.DICT["key"] (module-prefixed access).
  3. Diff:
     - ORPHAN: defined in params, never accessed in code (the magnet bug)
     - GHOST:  accessed in code but not defined in params (would crash at runtime)

Known limitations (v0.1):
  - Cannot resolve indirect access: `for k in CASE.keys()`, `**CASE`, etc.
  - Cannot resolve dynamic keys: `key = "x"; CASE[key]`, f-strings.
  - Cannot resolve aliased dicts: `params = CASE; params["key"]`.
  When in doubt, the linter reports orphans conservatively — user reviews.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple


@dataclass
class OrphanKey:
    dict_name: str
    key: str
    defined_at: Tuple[str, int]  # (file, line)


@dataclass
class GhostReference:
    dict_name: str
    key: str
    accessed_at: Tuple[str, int]  # (file, line)


@dataclass
class LinterReport:
    params_file: str
    code_files: List[str]
    defined_keys: Dict[str, Set[str]] = field(default_factory=dict)
    accessed_keys: Dict[str, Set[str]] = field(default_factory=dict)
    orphans: List[OrphanKey] = field(default_factory=list)
    ghosts: List[GhostReference] = field(default_factory=list)
    indirect_access_dicts: Set[str] = field(default_factory=set)

    @property
    def passed(self) -> bool:
        return len(self.orphans) == 0 and len(self.ghosts) == 0

    def format(self) -> str:
        lines = [f"Param linter report: {self.params_file}"]
        lines.append(f"  code files scanned: {len(self.code_files)}")
        for d, keys in sorted(self.defined_keys.items()):
            accessed = self.accessed_keys.get(d, set())
            lines.append(f"  {d}: {len(keys)} defined, {len(accessed & keys)} accessed")

        if self.indirect_access_dicts:
            lines.append(
                f"  ⚠  indirect access detected on: {sorted(self.indirect_access_dicts)}"
            )
            lines.append("     (linter cannot determine which keys are used — manual review)")

        if self.orphans:
            lines.append(f"\n  ORPHAN keys ({len(self.orphans)}) — defined but never accessed:")
            by_dict: Dict[str, List[OrphanKey]] = {}
            for o in self.orphans:
                by_dict.setdefault(o.dict_name, []).append(o)
            for d in sorted(by_dict):
                lines.append(f"    {d}:")
                for o in sorted(by_dict[d], key=lambda x: x.defined_at[1]):
                    lines.append(f"      - {o.key:30s}  (defined at {o.defined_at[0]}:{o.defined_at[1]})")

        if self.ghosts:
            lines.append(f"\n  GHOST references ({len(self.ghosts)}) — accessed but not defined:")
            for g in self.ghosts:
                lines.append(
                    f"    - {g.dict_name}[{g.key!r}]  at {g.accessed_at[0]}:{g.accessed_at[1]}"
                )

        if self.passed and not self.indirect_access_dicts:
            lines.append("\n  PASS")
        elif self.passed:
            lines.append("\n  PASS (with indirect-access warning)")
        else:
            lines.append(f"\n  FAIL — {len(self.orphans)} orphan(s), {len(self.ghosts)} ghost(s)")
        return "\n".join(lines)


def _extract_dict_definitions(params_file: Path) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], int]]:
    """Return {dict_name: {keys}} and {(dict_name, key): line_number}."""
    tree = ast.parse(params_file.read_text())
    defs: Dict[str, Set[str]] = {}
    locations: Dict[Tuple[str, str], int] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id.isupper()  # convention: dict consts are UPPERCASE
                and isinstance(node.value, ast.Dict)
            ):
                dict_name = target.id
                keys: Set[str] = set()
                for k_node, _ in zip(node.value.keys, node.value.values):
                    if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                        keys.add(k_node.value)
                        locations[(dict_name, k_node.value)] = k_node.lineno
                defs[dict_name] = keys

    return defs, locations


def _find_subscript_target(value_node: ast.expr, dict_names: Set[str]) -> str | None:
    """Match either Name(DICT) or Attribute(value=Name(*), attr=DICT)."""
    if isinstance(value_node, ast.Name) and value_node.id in dict_names:
        return value_node.id
    if isinstance(value_node, ast.Attribute) and value_node.attr in dict_names:
        return value_node.attr
    return None


def _extract_accesses(
    code_files: List[Path], dict_names: Set[str]
) -> Tuple[Dict[str, Set[str]], List[GhostReference], Set[str]]:
    """Scan code files for DICT["key"] and DICT.get("key", ...) accesses.

    Returns (accessed_keys_by_dict, ghost_candidate_list, dicts_with_indirect_access).
    """
    accessed: Dict[str, Set[str]] = {d: set() for d in dict_names}
    ghost_candidates: List[GhostReference] = []
    indirect: Set[str] = set()

    for f in code_files:
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            # Pattern 1: DICT["key"]
            if isinstance(node, ast.Subscript):
                target = _find_subscript_target(node.value, dict_names)
                if target is None:
                    continue
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    accessed[target].add(slice_node.value)
                else:
                    indirect.add(target)

            # Pattern 2: DICT.get("key", ...) or DICT.get("key")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = _find_subscript_target(node.func.value, dict_names)
                if target is None:
                    continue
                if node.func.attr == "get" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        accessed[target].add(arg0.value)
                    else:
                        indirect.add(target)
                elif node.func.attr in ("keys", "values", "items", "update", "pop"):
                    # Wholesale access — can't determine individual keys
                    indirect.add(target)

            # Pattern 3: **DICT spread
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if k is None:
                        # ** unpack — the value would be the dict; we'd need more analysis
                        pass

    return accessed, ghost_candidates, indirect


def lint_params(
    params_file: Path | str,
    code_files: List[Path] | List[str],
) -> LinterReport:
    """Lint a params file against a set of CAD code files.

    Args:
        params_file: path to params.py (or whatever holds the dicts).
        code_files: list of .py files that consume those dicts.

    Returns:
        LinterReport with orphans + ghosts + indirect-access warnings.
    """
    params_file = Path(params_file)
    code_files_paths = [Path(p) for p in code_files]

    if not params_file.exists():
        raise FileNotFoundError(params_file)

    defined, locations = _extract_dict_definitions(params_file)
    dict_names = set(defined.keys())

    accessed, _ghost_candidates, indirect = _extract_accesses(code_files_paths, dict_names)

    orphans: List[OrphanKey] = []
    for dict_name, keys in defined.items():
        if dict_name in indirect:
            # Skip orphan detection for dicts with indirect access — can't tell
            continue
        accessed_for_dict = accessed.get(dict_name, set())
        for key in keys - accessed_for_dict:
            line = locations.get((dict_name, key), 0)
            orphans.append(
                OrphanKey(
                    dict_name=dict_name,
                    key=key,
                    defined_at=(str(params_file), line),
                )
            )

    # Ghost detection: any access whose key is not in the dict's defined set
    ghosts: List[GhostReference] = []
    for dict_name, accessed_for_dict in accessed.items():
        defined_for_dict = defined.get(dict_name, set())
        for key in accessed_for_dict - defined_for_dict:
            ghosts.append(
                GhostReference(
                    dict_name=dict_name,
                    key=key,
                    accessed_at=("<aggregated>", 0),  # specific line tracking deferred
                )
            )

    return LinterReport(
        params_file=str(params_file),
        code_files=[str(p) for p in code_files_paths],
        defined_keys=defined,
        accessed_keys=accessed,
        orphans=sorted(orphans, key=lambda o: (o.dict_name, o.key)),
        ghosts=sorted(ghosts, key=lambda g: (g.dict_name, g.key)),
        indirect_access_dicts=indirect,
    )
