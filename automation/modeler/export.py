"""Export bridge: Blender part arrays -> the AR contract GLB through the tested ``bsa.export`` writer.

Groups the registered objects by part identity (frame, temple_R, temple_L, lens_R/lens_L or lens_C), converts
the recorded material specs, turns a lens's ``optics`` into the runtime's canonical LensAppearance descriptor and
writes the GLB in metres with the bridge underside at the origin. Then ``bsa.contract.check`` verifies it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from bsa import contract as bsa_contract
from bsa import export as bsa_export
from bsa.lens import lens_appearance_descriptor

LENS_PARTS = ("lens_R", "lens_L", "lens_C")


def load_parts(npz_path: Path, materials_json: Path) -> tuple[dict, dict, dict]:
    arrays = np.load(npz_path)
    meta = json.loads(Path(materials_json).read_text(encoding="utf-8"))
    objects = {}
    for key, m in meta["objects"].items():
        objects[key] = {**m, "V": arrays[f"{key}__V"].astype(float), "F": arrays[f"{key}__F"].astype(np.int64),
                        "M": arrays[f"{key}__M"].astype(np.int64),
                        "UV": arrays[f"{key}__UV"].astype(float) if f"{key}__UV" in arrays else None}
    return objects, meta["materials"], meta.get("declarations", {})


def transmission_profile(optics: dict, n: int = 16) -> np.ndarray:
    """Rows top (0) -> bottom (n-1) of linear transmission, as ``lens_appearance_descriptor`` expects."""
    top = np.asarray(optics["transmission_top_rgb"], float)
    bot = np.asarray(optics["transmission_bottom_rgb"], float)
    t = np.linspace(0.0, 1.0, n)                      # 0 = top
    prof = optics.get("profile", "smooth")
    if prof == "flat":
        w = np.zeros(n)
    elif prof == "linear":
        w = t
    else:
        w = t * t * (3 - 2 * t)
    return top[None, :] * (1 - w[:, None]) + bot[None, :] * w[:, None]


def material_spec(name: str, spec: dict, *, lens: bool) -> dict:
    """bsa.export material spec from the Blender-side record."""
    base = [float(x) for x in spec.get("base_color_linear", [0.5, 0.5, 0.5])[:3]]
    out = {"base_color": base + [float(spec.get("alpha", 1.0))], "metallic": float(spec.get("metallic", 0.0)),
           "roughness": float(spec.get("roughness", 0.5)), "double_sided": False}
    if spec.get("texture_path"):
        out["base_color_texture"] = spec["texture_path"]
    if lens:
        optics = spec.get("lens")
        if optics is None:
            optics = {"transmission_top_rgb": [0.9, 0.9, 0.9], "transmission_bottom_rgb": [0.9, 0.9, 0.9],
                      "profile": "flat", "reflectance_rgb": [0.04, 0.04, 0.04], "mirror": False, "roughness": 0.05}
        T = transmission_profile(optics)
        angular = optics.get("angular") or None
        if angular:
            angular = [(float(a), [float(x) for x in rgb]) for a, rgb in angular]   # the runtime's angular reflectance table
        out["lens_appearance"] = lens_appearance_descriptor(T, optics["reflectance_rgb"], angular, None,
                                                            roughness=float(optics.get("roughness", 0.05)))
        out["transmission"] = 1.0
        out["ior"] = 1.5
        out["base_color"] = [float(x) for x in np.clip(T.mean(0), 0.0, 1.0)] + [1.0]
        out["roughness"] = float(optics.get("roughness", 0.05))
        out["metallic"] = 0.0
    else:
        out["transmission"] = 0.0
    return out


def is_closed(V: np.ndarray, F: np.ndarray, weld_mm: float = 1e-3) -> bool:
    """Every edge of the position-welded triangle set shared by exactly two faces."""
    key = np.round(np.asarray(V, float) / weld_mm).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    Fw = inv.ravel()[np.asarray(F, np.int64)]
    e = np.sort(np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]]), axis=1)
    e = e[e[:, 0] != e[:, 1]]
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(len(counts) and (counts == 2).all())


def drop_degenerate(V: np.ndarray, F: np.ndarray, M: np.ndarray, UV, min_area_mm2: float = 1e-8) -> tuple:
    """Remove zero-area or repeated-vertex triangles (they give non-finite normals in the exporter). Returns the
    filtered (F, M, UV, dropped_count)."""
    if len(F) == 0:
        return F, M, UV, 0
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    area2 = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    ok = (area2 > 2 * min_area_mm2) & (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    ok &= np.isfinite(area2)
    dropped = int((~ok).sum())
    if dropped == 0:
        return F, M, UV, 0
    return F[ok], M[ok], (UV[ok] if UV is not None else None), dropped


def assemble(objects: dict, materials: dict) -> tuple[dict, dict, list[str]]:
    """Merge objects by part into ``bsa.export.write_glb`` parts; returns (parts, materials, notes)."""
    notes = []
    for key, o in objects.items():
        # degenerate triangles are dropped only from objects that are already open or non-manifold: removing a
        # zero-area face from a CLOSED mesh would open it (three boundary edges) and fail the contract instead
        if len(o["F"]) and not is_closed(o["V"], o["F"]):
            F2, M2, UV2, dropped = drop_degenerate(o["V"], o["F"], o["M"], o["UV"])
            if dropped:
                o["F"], o["M"], o["UV"] = F2, M2, UV2
                notes.append(f"{o['object']}: dropped {dropped} degenerate triangles before export (object was not closed)")
    by_part: dict[str, list] = {}
    for key, o in objects.items():
        by_part.setdefault(o["part"], []).append(o)
    parts, mats = {}, {}
    for part, objs in by_part.items():
        lens = part in LENS_PARTS
        V_all, F_all, UV_all, FM_all, names = [], [], [], [], []
        has_uv = all(o["UV"] is not None for o in objs)
        offset = 0
        for o in objs:
            V, F, M = o["V"], o["F"], o["M"]
            if len(F) == 0:
                notes.append(f"{o['object']}: no triangles, skipped")
                continue
            local_names = []
            for mn in o["materials"]:
                if mn is None or mn not in materials:
                    fallback = f"{part}_default"
                    if fallback not in mats:
                        mats[fallback] = material_spec(fallback, {"base_color_linear": [0.2, 0.2, 0.2], "roughness": 0.5}, lens=lens)
                        if mn is None:
                            notes.append(f"{o['object']}: unassigned material slot -> {fallback}")
                    local_names.append(fallback)
                else:
                    # a material shared by a lens part and an opaque part is exported twice: the lens copy carries
                    # the optics (until 2026-09-26 the opaque copy was reused and the lens failed the contract)
                    key = f"{mn} (lens)" if lens and materials[mn].get("kind") != "lens" else mn
                    if key not in mats:
                        mats[key] = material_spec(key, materials[mn], lens=lens)
                    local_names.append(key)
            for n in local_names:
                if n not in names:
                    names.append(n)
            remap = np.array([names.index(n) for n in local_names], np.int64)
            M = np.clip(M, 0, len(local_names) - 1)
            V_all.append(V)
            F_all.append(F + offset)
            FM_all.append(remap[M])
            if has_uv:
                UV_all.append(o["UV"])
            offset += len(V)
        if not F_all:
            continue
        p = {"V": np.vstack(V_all), "F": np.vstack(F_all), "material": names, "face_material": np.concatenate(FM_all)}
        if has_uv and UV_all:
            p["UV"] = np.vstack(UV_all)
        if lens:
            lens_kind = [n for n in names if (materials.get(n) or {}).get("kind") == "lens"]
            chosen = lens_kind[0] if lens_kind else names[0]
            if len(names) > 1:
                notes.append(f"{part}: several materials; {chosen} is used for the whole lens"
                             + ("" if lens_kind else " (none of them is a gl.material_lens: exported as a clear lens)"))
            elif not lens_kind:
                notes.append(f"{part}: its material {names[0]} is not a gl.material_lens; exported as a clear lens")
            p["material"] = chosen
            p.pop("face_material")
            p.pop("UV", None)                      # the canonical sheet writes its own height UVs
        parts[part] = p
    lens_nodes = [n for n in parts if n in LENS_PARTS]
    if "lens_C" in lens_nodes and len(lens_nodes) > 1:
        raise ValueError("A shield lens (lens_C) cannot coexist with lens_R/lens_L")
    return parts, mats, notes


def choose_origin(parts: dict, declared) -> tuple[np.ndarray, str | None]:
    """The bridge underside derived from the front parts (the contract's own rule); an author declaration is used
    only when it agrees within the contract tolerance (1 mm in x/y, 3 mm in z)."""
    derived, receipt = bsa_export.bridge_underside_mm(parts)
    if declared is None:
        return derived, None
    d = np.asarray(declared, float).reshape(3)
    if abs(d[0] - derived[0]) <= 1.0 and abs(d[1] - derived[1]) <= 1.0 and abs(d[2] - derived[2]) <= 3.0:
        return d, None
    return derived, (f"declared bridge underside {d.round(2).tolist()} disagrees with the geometry "
                     f"{derived.round(2).tolist()} ({receipt['method']}); the derived point is used")


def export_glb(npz_path: Path, materials_json: Path, out_path: Path, *, extras: dict | None = None) -> dict:
    """Write the contract GLB and check it. Returns {glb, sha256, bytes, receipt, contract, notes}."""
    objects, materials, declarations = load_parts(Path(npz_path), Path(materials_json))
    parts, mats, notes = assemble(objects, materials)
    if not any(p in parts for p in LENS_PARTS):
        notes.append("no lens part registered")
    if "frame" not in parts:
        notes.append("no frame part registered")
    origin, origin_note = choose_origin(parts, declarations.get("bridge_underside_mm"))
    if origin_note:
        notes.append(origin_note)
    receipt = bsa_export.write_glb(parts, mats, out_path, origin_mm=origin, extras=extras)
    raw = Path(out_path).read_bytes()
    check = bsa_contract.check(out_path)
    return {"glb": str(out_path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "receipt": receipt,
            "contract": check, "notes": notes, "parts": {n: {"triangles": int(len(p["F"])), "materials": p["material"]}
                                                          for n, p in parts.items()},
            "declarations": declarations}
