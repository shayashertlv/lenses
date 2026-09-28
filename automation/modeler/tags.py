"""Asset tags for calibration coverage.

What kind of glasses an asset is comes from the intake's front measurement (``layout`` pair/single, ``rim_class``
full/half/rimless/mixed) and which modifiers it carries from the candidate's ``materials.json`` (``translucent`` when a
gl.material_translucent is present, ``mirrored`` when a lens has a mirror coat) or, when no material record exists (a
baseline, an unreadable build), from the GLB itself. The calibration set reports the owner's verdicts per tag
(``modeler.calibration``); a job whose delivered asset carries a tag with too few owner verdicts is not awarded
``accepted`` on its own, however calibrated the bar is overall: the bar has never been judged on that kind.
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import AUTOMATION, JOBS

TAG_ORDER = ("pair", "single", "rim_full", "rim_half", "rim_rimless", "rim_mixed", "translucent", "mirrored")
MIRROR_REFLECTANCE = 0.15      # a canonical descriptor's head-on reflectance above this is a mirror coat (glass is 0.04)


def kind_tags(evidence: dict | None) -> list[str]:
    """The kind of glasses: the intake reading's classes when a reading exists (vision), else the front measurement's.
    A rim class of 'unknown' (the intake's rim guard: a rim the contrast matte could not see) yields no rim tag."""
    front = (evidence or {}).get("front") or {}
    reading = (((evidence or {}).get("intake_reading") or {}).get("product")) or {}
    out = []
    layout = reading.get("layout") or front.get("layout")
    if layout in ("pair", "single"):
        out.append(layout)
    rim = reading.get("rim_class") or front.get("rim_class")
    if rim in ("full", "half", "rimless", "mixed"):
        out.append(f"rim_{rim}")
    return out


def modifier_tags(materials: dict | None) -> list[str]:
    mats = (materials or {}).get("materials", materials) or {}
    out = []
    if any((s or {}).get("kind") == "translucent" for s in mats.values()):
        out.append("translucent")
    if any(bool(((s or {}).get("lens") or {}).get("mirror")) for s in mats.values() if (s or {}).get("kind") == "lens"):
        out.append("mirrored")
    return out


def modifier_tags_from_glb(glb_path: Path) -> list[str]:
    """The modifier tags read off the exported asset: a transmissive material on a frame/temple node is translucent; a
    canonical lens descriptor with a mirror reflectance or an angular table is mirrored."""
    from bsa.contract import CANONICAL_LENS_EXTENSION, PART_ROLE, read_glb, transmission_of
    g = read_glb(glb_path)
    translucent = mirrored = False
    for n in g["nodes"]:
        role = PART_ROLE.get(n["name"]) or (n.get("extras") or {}).get("partRole")
        for p in n["primitives"]:
            m = p["material"]
            ext = m.get("extensions", {})
            if CANONICAL_LENS_EXTENSION in ext:
                app = (ext[CANONICAL_LENS_EXTENSION] or {}).get("appearance") or {}
                refl = app.get("normal_reflectance_rgb") or [0, 0, 0]
                if app.get("angular_reflectance_keyframes") or sum(float(x) for x in refl) / max(len(refl), 1) > MIRROR_REFLECTANCE:
                    mirrored = True
            elif role in ("frame", "temple") and transmission_of(m) > 0:
                translucent = True
    return [t for t in ("translucent", "mirrored") if (translucent if t == "translucent" else mirrored)]


def _ordered(tags: set[str]) -> list[str]:
    return [t for t in TAG_ORDER if t in tags] + sorted(tags - set(TAG_ORDER))


def asset_tags(evidence: dict | None, materials: dict | None, glb_path: Path | str | None = None) -> list[str]:
    """Kind tags from the evidence plus modifier tags from the material records, or from the GLB when no record exists."""
    tags = set(kind_tags(evidence))
    if materials is not None:
        tags |= set(modifier_tags(materials))
    elif glb_path is not None:
        p = Path(glb_path)
        p = p if p.is_absolute() else AUTOMATION / p
        if p.is_file():
            try:
                tags |= set(modifier_tags_from_glb(p))
            except Exception:  # noqa: BLE001 - an unreadable GLB leaves the kind tags alone
                pass
    return _ordered(tags)


def job_evidence(job_dir: Path) -> dict | None:
    p = Path(job_dir) / "evidence" / "evidence.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def asset_materials(asset_dir: Path) -> dict | None:
    """The candidate's Blender-side material records (``build/materials.json``); None for a baseline or a missing build."""
    p = Path(asset_dir) / "build" / "materials.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def asset_glb(asset_dir: Path) -> Path | None:
    """The asset's GLB: a candidate's ``model.glb`` or the path a baseline's ``baseline.json`` names (relative to
    automation/). None when there is neither, or when baseline.json is unreadable or names no GLB (an empty ``glb``
    included). The one implementation: ``modeler.calibration`` uses it as is and ``modeler.evaluate_asset`` raises
    FileNotFoundError on None (until 2026-09-29 each had its own copy: calibration raised on a malformed baseline.json,
    evaluate_asset raised KeyError on a missing ``glb``, and here an empty ``glb`` resolved to automation/ itself)."""
    d = Path(asset_dir)
    if (d / "model.glb").is_file():
        return d / "model.glb"
    b = d / "baseline.json"
    if b.is_file():
        try:
            named = json.loads(b.read_text(encoding="utf-8"))["glb"]
            if not named:
                return None
            glb = Path(named)
            return glb if glb.is_absolute() else AUTOMATION / glb
        except Exception:  # noqa: BLE001
            return None
    return None


def tags_for_asset_dir(job_dir: Path, asset_dir: Path) -> list[str]:
    """The tags of one asset folder (candidate or baseline) of a job."""
    return asset_tags(job_evidence(job_dir), asset_materials(asset_dir), asset_glb(asset_dir))


def tags_for_row(row: dict, jobs: Path = JOBS) -> list[str]:
    """The tags of a calibration row: recorded on the row, else derived from the job's evidence and the asset."""
    if row.get("tags"):
        return list(row["tags"])
    job_dir = Path(jobs) / row["job"]
    asset_dir = job_dir / ("baselines" if row.get("kind") == "baseline" else "candidates") / str(row.get("baseline") or row.get("candidate"))
    return tags_for_asset_dir(job_dir, asset_dir)
