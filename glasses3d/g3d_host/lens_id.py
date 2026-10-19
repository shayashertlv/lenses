"""Lens identification (PLAN.md WP1.3, glasses3d/plan/archive/20261019/WP1_DESIGN.md §2). The same ordered rules run in arview (TypeScript); both are
tested against `tests/fixtures/lens_id_cases.json` so they cannot drift apart.

Order, per primitive:
  1. extras on the material, mesh or node (in that priority): `partRole` equal to "lens" (trimmed, case-insensitive),
     or legacy `part_role` starting with "lens" (case-insensitive). Any other explicit role (frame, temple,
     hardware...) means "not a lens". A disagreeing lower-priority role is a warning.
  2. a whole-word token `lens`, `lenses` or `glass` (never "glasses") in the material or node name (camelCase,
     ACRONYMCase and digit runs split), unless the material or node name marks it as an etch, lettering, inscription
     or print.
  3. only when rules 1-2 found no lens anywhere in the file: transmission > 0 or alpha < 1, with a warning (a crystal
     acetate frame is transmissive too).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LENS_TOKENS = {"lens", "lenses", "glass"}
EXCLUDE_STEMS = ("etch", "letter", "inscri", "print")


@dataclass(frozen=True)
class Decision:
    is_lens: bool
    rule: int            # 1, 2, 3, or 0 when no rule matched (default: not a lens)
    signal: str          # human-readable deciding signal, e.g. "material.extras.partRole=lens"


def tokens(name: str | None) -> list[str]:
    """Lower-case word tokens; splits on non-letters (digits, separators), camelCase and ACRONYMCase boundaries
    ('LENSRight' -> ['lens', 'right'], 'LensRight.001' -> ['lens', 'right'])."""
    if not name:
        return []
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)       # ACRONYMCase: LENSRight -> LENS Right
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", spaced)            # camelCase: lensRight -> lens Right
    return [t for t in re.split(r"[^A-Za-z]+", spaced.lower()) if t]


def _explicit_role(extras: dict | None) -> tuple[str | None, str | None]:
    """(role, key) from an extras dict, or (None, None). The role is trimmed."""
    if not isinstance(extras, dict):
        return None, None
    for key in ("partRole", "part_role"):
        val = extras.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip(), key
    return None, None


def role_says_lens(role: str, key: str) -> bool:
    """partRole: equal to 'lens' after trimming, in any case. part_role (legacy): starts with 'lens', any case."""
    r = role.strip().lower()
    return r == "lens" if key == "partRole" else r.startswith("lens")


def excluded(name: str | None) -> bool:
    return any(t.startswith(EXCLUDE_STEMS) for t in tokens(name))


def decide_explicit(sources: list[tuple[str, dict | None]]) -> tuple[Decision | None, list[str]]:
    """Rule 1. `sources` = [("material", extras), ("mesh", extras), ("node", extras)] in priority order."""
    found = []
    for where, extras in sources:
        role, key = _explicit_role(extras)
        if role is not None:
            found.append((where, role, key))
    if not found:
        return None, []
    where, role, key = found[0]
    lens = role_says_lens(role, key)
    warnings = [f"{w}.extras.{k}={r!r} disagrees with {where}.extras.{key}={role!r} (using {where})"
                for w, r, k in found[1:] if role_says_lens(r, k) != lens]
    return Decision(lens, 1, f"{where}.extras.{key}={role}"), warnings


def decide_name(material_name: str | None, node_name: str | None) -> Decision | None:
    """Rule 2."""
    if excluded(material_name) or excluded(node_name):
        return None
    for where, name in (("material", material_name), ("node", node_name)):
        hit = LENS_TOKENS.intersection(tokens(name))
        if hit:
            return Decision(True, 2, f"{where} name token {sorted(hit)[0]!r} in {name!r}")
    return None


def material_is_see_through(material: dict | None) -> str | None:
    """Rule 3 signal, or None."""
    if not isinstance(material, dict):
        return None
    tr = (material.get("extensions") or {}).get("KHR_materials_transmission") or {}
    try:
        factor = float(tr.get("transmissionFactor", 0.0))
    except (TypeError, ValueError):
        factor = 0.0
    if factor > 0.0:
        return f"transmissionFactor={tr['transmissionFactor']}"
    try:
        alpha = float(((material.get("pbrMetallicRoughness") or {}).get("baseColorFactor") or [1, 1, 1, 1])[3])
    except (TypeError, ValueError, IndexError):
        alpha = 1.0
    if material.get("alphaMode") == "BLEND" and alpha < 1.0:
        return f"alphaMode=BLEND alpha={alpha}"
    return None


@dataclass(frozen=True)
class PrimitiveRef:
    """What lens identification needs to know about one primitive (format-neutral, so tests need no GLB)."""
    key: str
    node_name: str | None
    node_extras: dict | None
    mesh_extras: dict | None
    material_name: str | None
    material_extras: dict | None
    material: dict | None


def classify(prims: list[PrimitiveRef]) -> tuple[dict[str, Decision], list[str]]:
    """Classify every primitive. Returns ({key: Decision}, warnings)."""
    out: dict[str, Decision] = {}
    warnings: list[str] = []
    undecided = []
    for p in prims:
        d, w = decide_explicit([("material", p.material_extras), ("mesh", p.mesh_extras), ("node", p.node_extras)])
        warnings += [f"{p.key}: {x}" for x in w]
        if d is None:
            d = decide_name(p.material_name, p.node_name)
        if d is None:
            undecided.append(p)
        else:
            out[p.key] = d
    any_lens = any(d.is_lens for d in out.values())
    for p in undecided:
        sig = None if any_lens else material_is_see_through(p.material)
        if sig:
            out[p.key] = Decision(True, 3, sig)
            warnings.append(f"{p.key}: identified as lens only by {sig} (last resort: no partRole or lens name "
                            f"anywhere in the file; a crystal frame would be misread)")
        else:
            out[p.key] = Decision(False, 0, "no lens signal")
    return out, warnings
