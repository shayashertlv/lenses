"""The print manifest: what g3d.attach_print needs to know about a bake, and the host's staleness check of a GLB
(WP5_DESIGN §4: "geometry_sha is stored, so a later geometry edit makes ... check_print report 'print stale;
re-project'").

    build_manifest(bake_dir)   -> dict from the bake's report.json, layout.json and print.png, plus the print
                                  geometry's identity computed from the bake's own GLB (sha-checked):
                                  {print_png, print_sha256, width, height, tx_per_mm, tx_requested, parts, layer,
                                   geometry_sha, charts_sha, glb, glb_sha256, mode, label}
    write_manifest(bake_dir)   -> path of <bake_dir>/print_manifest.json
    check_glb(glb, manifest)   -> {ok, stale, reasons, parts}: every manifest part's node carries g3d_print_uv with the
                                  bake's geometry_sha / charts_sha / atlas / density, the GLB's own triangles still
                                  hash to the bake's geometry_sha, the node's TEXCOORD_k exists and, when its material
                                  samples a base-colour texture, through texCoord k; fix round 1 of step 4: that
                                  texture's image IS the bake's print (bytes sha256 = print_sha256, else decoded pixels
                                  equal to print.png), and TEXCOORD_k IS the bake's layout (every triangle's corners,
                                  matched by position, within 1e-3 texel of the layout recomputed from the bake's own
                                  sha-checked GLB, as g3d.print_uv computes it); a manifest without a GLB (a synthetic
                                  print) skips the uv comparison and says so in `notes`.

The identities come from ``glasses3d.g3d.printuv.glbmesh`` (geometry_sha: order- and split-independent; charts_sha:
the packing), the same code g3d.print_uv runs in Blender. Run as a script to write the manifest of an existing bake:

    python -m glasses3d.g3d_host.photoprint.manifest <bake_dir> [--label TEXT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from glasses3d.g3d.printuv import glbmesh as GM

from .. import glb as glbmod

MANIFEST = "print_manifest.json"
LAYER = "g3d_print"
PRINT_UV_KEY = "g3d_print_uv"
UV_TOL_TEXEL = 1e-3              # TEXCOORD_k vs the bake's layout (float32 round trip: ~1e-4 texel on q001)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    import struct
    head = Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def print_identity(V, F, part, names, layout) -> dict:
    """The block project_photo_texture writes into its report (and build_manifest recomputes for older bakes)."""
    return {"layer": LAYER, "parts": list(layout.parts), "tx_per_mm": float(layout.tx_per_mm),
            "tx_requested": float(layout.tx_requested), "atlas": [int(layout.width), int(layout.height)],
            "geometry_sha": GM.geometry_sha(V, F, part, names, list(layout.parts)),
            "charts_sha": GM.charts_sha(layout.charts, layout.width, layout.height)}


def texture_eligibility(record: dict, parts) -> tuple[list[str], list[str]]:
    """Explicit attachment eligibility; missing or contradictory coverage is never permission to print."""
    parts = list(parts)
    if (not parts or any(not isinstance(p, str) or not p for p in parts) or len(parts) != len(set(parts))):
        raise ValueError("print parts must be distinct exact object names")
    selected, excluded = record.get('texture_parts'), record.get('no_texture_parts')
    for key, value in (('texture_parts', selected), ('no_texture_parts', excluded)):
        if (not isinstance(value, list) or any(not isinstance(p, str) or not p for p in value)
                or len(value) != len(set(value)) or not set(value) <= set(parts)):
            raise ValueError(f"{key} must explicitly list distinct layout parts")
    if set(selected) & set(excluded) or set(selected) | set(excluded) != set(parts):
        raise ValueError("texture_parts and no_texture_parts must partition the layout parts")
    return list(selected), list(excluded)


def build_manifest(bake_dir, *, label: str | None = None) -> dict:
    out = Path(bake_dir).resolve()
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    lay = json.loads((out / "layout.json").read_text(encoding="utf-8"))
    png = out / "print.png"
    if not png.is_file():
        raise FileNotFoundError(f"{png} missing")
    w, h = _png_size(png)
    if (w, h) != (int(lay["width"]), int(lay["height"])):
        raise ValueError(f"print.png is {w}x{h}, layout.json says {lay['width']}x{lay['height']}")
    src = (rep.get("inputs") or {}).get("sources") or {}
    glb_path = Path(src["glb"])
    glb_sha = _sha(glb_path)
    if src.get("glb_sha256") and glb_sha != src["glb_sha256"]:
        raise ValueError(f"{glb_path} changed since the bake (sha256 {glb_sha[:12]} vs {src['glb_sha256'][:12]})")
    ident = rep.get("print_uv")
    if ident is None:                                    # a bake from before the manifest: recompute from its GLB
        V, F, part, names = GM.mesh_arrays(GM.read_glb(glb_path))
        ident = {"layer": LAYER, "parts": list(lay["parts"]), "tx_per_mm": float(lay["tx_per_mm"]),
                 "tx_requested": float(lay.get("tx_requested", lay["tx_per_mm"])),
                 "atlas": [int(lay["width"]), int(lay["height"])],
                 "geometry_sha": GM.geometry_sha(V, F, part, names, list(lay["parts"])),
                 "charts_sha": GM.charts_sha(lay["charts"], lay["width"], lay["height"]), "recomputed": True}
    charts = GM.charts_sha(lay["charts"], lay["width"], lay["height"])
    if charts != ident["charts_sha"]:
        raise ValueError("layout.json does not match the report's print identity (charts_sha)")
    if list(ident['parts']) != list(lay['parts']) or list(rep['parts']) != list(lay['parts']):
        raise ValueError("layout parts differ from the report's print identity")
    selected, excluded = texture_eligibility(rep, lay['parts'])
    return {"version": 1, "print_png": str(png), "print_sha256": _sha(png), "width": int(w), "height": int(h),
            "tx_per_mm": ident["tx_per_mm"], "tx_requested": ident["tx_requested"], "parts": ident["parts"],
            "texture_parts": selected, "no_texture_parts": excluded,
            "layer": ident["layer"], "geometry_sha": ident["geometry_sha"], "charts_sha": ident["charts_sha"],
            "glb": str(glb_path), "glb_sha256": glb_sha, "mode": rep.get("mode"),
            "label": label or "photo-painted print bake (glasses3d WP5)",
            "identity_recomputed": bool(ident.get("recomputed", False))}


def write_manifest(bake_dir, *, label: str | None = None) -> Path:
    man = build_manifest(bake_dir, label=label)
    path = Path(bake_dir) / MANIFEST
    path.write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return path


def load_manifest(where) -> dict:
    if isinstance(where, dict):
        return dict(where)
    p = Path(where)
    p = p / MANIFEST if p.is_dir() else p if p.name == MANIFEST else p.parent / MANIFEST
    return json.loads(p.read_text(encoding="utf-8"))


def canonical_corners(P: np.ndarray, UV: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(M, 3, 3) corner positions and (M, 3, 2) corner uv -> both with every triangle rotated so its lexicographically
    smallest corner comes first (winding kept; ``glbmesh.canonical_triangles``' rule) and the triangles sorted by their
    positions (then uv): two exports of the same geometry list the same triangles in the same order."""
    P = np.asarray(P, np.float64).reshape(-1, 3, 3) + 0.0
    UV = np.asarray(UV, np.float64).reshape(-1, 3, 2)
    m = len(P)
    if not m:
        return P, UV
    flat = P.reshape(-1, 3)
    order = np.lexsort((flat[:, 2], flat[:, 1], flat[:, 0]))
    rank = np.empty(len(flat), np.int64)
    rank[order] = np.arange(len(flat))
    r = np.argmin(rank.reshape(m, 3), axis=1)
    idx = (r[:, None] + np.arange(3)[None]) % 3
    Q, U = P[np.arange(m)[:, None], idx], UV[np.arange(m)[:, None], idx]
    keys = np.concatenate([Q.reshape(m, 9), U.reshape(m, 6)], 1)
    o = np.lexsort(tuple(keys[:, k] for k in range(14, -1, -1)))
    return Q[o], U[o]


def expected_corners(man: dict) -> tuple[dict | None, str | None]:
    """({part: (canonical positions, canonical uv texels)}, None) of the bake's layout, recomputed from the bake's own
    GLB (sha-checked against the manifest) exactly as g3d.print_uv lays it out; (None, why) when it cannot be."""
    from glasses3d.g3d import printuv as PU
    g = man.get("glb")
    if not g or not Path(g).is_file():
        return None, "the manifest names no bake GLB (a synthetic print): TEXCOORD_k not compared with a layout"
    if man.get("glb_sha256") and _sha(Path(g)) != man["glb_sha256"]:
        return None, f"the bake's GLB {g} changed since the bake: TEXCOORD_k not compared with a layout"
    V, F, part, names = GM.mesh_arrays(GM.read_glb(g))
    lay = PU.layout(V, F, part, names, list(man["parts"]), float(man["tx_requested"]))
    if GM.charts_sha(lay.charts, lay.width, lay.height) != man["charts_sha"]:
        return None, "the layout recomputed from the bake's GLB is not the manifest's (charts_sha)"
    fr = lay.face_index(len(F))
    out = {}
    for p in man["parts"]:
        f = GM.part_faces(part, names, p)
        out[p] = canonical_corners(V[F[f]], lay.uv[fr[f]])
    return out, None


def _image_bytes(doc: dict, binc: bytes, index: int) -> bytes | None:
    im = (doc.get("images") or [])[index]
    if "bufferView" not in im:
        return None
    bv = doc["bufferViews"][im["bufferView"]]
    off = int(bv.get("byteOffset", 0))
    return bytes(binc[off:off + int(bv["byteLength"])])


def _same_pixels(data: bytes, png) -> bool:
    import io
    from PIL import Image
    if not png or not Path(png).is_file():
        return False
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    b = np.asarray(Image.open(png).convert("RGBA"))
    return a.shape == b.shape and bool(np.array_equal(a, b))


def check_glb(glb_path, manifest, *, check_uv: bool = True) -> dict:
    """The host's staleness check of an exported GLB against a bake (module docstring). Never raises for a GLB
    problem: it is reported in `reasons`. ``check_uv`` compares TEXCOORD_k with the bake's layout (about 3 s on q001)."""
    man = load_manifest(manifest)
    reasons, rows, notes = [], {}, []
    try:
        eligible, _ = texture_eligibility(man, man['parts'])
    except (KeyError, ValueError, TypeError) as exc:
        return {'ok': False, 'stale': True, 'reasons': [f'invalid print eligibility: {exc}'], 'parts': {}}
    try:
        g = glbmod.load(glb_path)
        prims = GM.read_glb(glb_path)
        doc, binc = GM.chunks(Path(glb_path).read_bytes())
    except Exception as exc:
        return {"ok": False, "stale": True, "reasons": [f"unreadable GLB: {type(exc).__name__}: {exc}"], "parts": {}}
    V, F, part, names = GM.mesh_arrays(prims)
    missing = [p for p in man["parts"] if p not in names]
    if missing:
        reasons.append(f"parts missing from the GLB: {missing}")
    else:
        now = GM.geometry_sha(V, F, part, names, man["parts"])
        if now != man["geometry_sha"]:
            reasons.append("print stale; re-project: the GLB's print geometry is not the bake's (geometry_sha "
                           f"{now[:12]} vs {man['geometry_sha'][:12]})")
    nodes = {n.get("name"): n for n in g.nodes}
    exp, why = (None, "uv comparison not asked for (check_uv=False)") if not check_uv else (None, None)
    if check_uv and not missing:
        try:
            exp, why = expected_corners(man)
        except Exception as exc:                                         # reported, never raised
            exp, why = None, f"the bake's layout could not be recomputed: {type(exc).__name__}: {exc}"
    if why:
        notes.append(why)
        if check_uv and man.get('glb'):
            reasons.append(f'print stale; re-project: {why}')
    images_checked = {}
    for p in man["parts"]:
        node = nodes.get(p)
        row = {"node": node is not None}
        raw = ((node or {}).get("extras") or {}).get(PRINT_UV_KEY)
        if raw is None:
            reasons.append(f"{p}: no {PRINT_UV_KEY} in the node extras (run g3d.print_uv)")
            rows[p] = row
            continue
        info = json.loads(raw) if isinstance(raw, str) else dict(raw)
        k = info.get("texcoord")
        row.update(texcoord=k)
        for key in ("geometry_sha", "charts_sha"):
            if info.get(key) != man[key]:
                reasons.append(f"print stale; re-project: {p}'s {key} differs from the bake's")
        if list(info.get("atlas", [])) != [man["width"], man["height"]] or \
                abs(float(info.get("tx_per_mm", 0)) - float(man["tx_per_mm"])) > 1e-9:
            reasons.append(f"print stale; re-project: {p}'s atlas / density differ from the bake's")
        prs = [q for q in g.primitives if q.node_name == (node or {}).get("name")]
        has_tc = bool(prs) and all(f"TEXCOORD_{k}" in q.attributes for q in prs)
        row["has_texcoord"] = has_tc
        if not has_tc:
            reasons.append(f"{p}: TEXCOORD_{k} missing")
        for q in prs:
            m = g.material(q.material_index) or {}
            info_t = (m.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if info_t is None and p in eligible:
                reasons.append(f'{p}: eligible print part has no baseColorTexture')
            if info_t is not None and p in eligible:
                row["base_color_texcoord"] = info_t.get("texCoord", 0)
                if info_t.get("texCoord", 0) != k:
                    reasons.append(f"{p}: baseColorTexture uses TEXCOORD_{info_t.get('texCoord', 0)}, the print is "
                                   f"on TEXCOORD_{k}")
                # the image it samples is the bake's print (bytes, else decoded pixels)
                src = ((doc.get("textures") or [{}])[int(info_t.get("index", 0))] or {}).get("source")
                if src is None:
                    reasons.append(f"{p}: baseColorTexture has no image source")
                    continue
                if src not in images_checked:
                    data = _image_bytes(doc, binc, int(src))
                    if data is None:
                        images_checked[src] = "unreadable"
                    elif hashlib.sha256(data).hexdigest() == man.get("print_sha256"):
                        images_checked[src] = "bytes"
                    elif _same_pixels(data, man.get("print_png")):
                        images_checked[src] = "pixels"
                    else:
                        images_checked[src] = "other"
                row["print_image"] = images_checked[src]
                if images_checked[src] not in ("bytes", "pixels"):
                    reasons.append(f"{p}: baseColorTexture samples an image that is not the bake's print.png "
                                   f"(sha256 {man.get('print_sha256', '')[:12]}; {images_checked[src]})")
            elif info_t is not None:
                src = ((doc.get('textures') or [{}])[int(info_t.get('index', 0))] or {}).get('source')
                data = _image_bytes(doc, binc, int(src)) if src is not None else None
                if data is not None and (hashlib.sha256(data).hexdigest() == man.get('print_sha256')
                                         or _same_pixels(data, man.get('print_png'))):
                    reasons.append(f'{p}: no-texture part samples the print image')
        # TEXCOORD_k is the bake's layout, corner for corner (matched by position)
        if exp is not None and has_tc and p in exp:
            P_ = np.concatenate([q.positions_mm[q.triangles] for q in prims if q.node == p]) if prims else np.zeros((0, 3, 3))
            U_ = np.concatenate([GM.uv_texels(q, k, man["width"], man["height"]) for q in prims
                                 if q.node == p])
            Qe, Ue = exp[p]
            Qg, Ug = canonical_corners(P_, U_)
            if Qg.shape != Qe.shape or not np.array_equal(Qg, Qe):
                reasons.append(f"print stale; re-project: {p}'s triangles do not match the bake's one for one")
                row["uv_max_err_texel"] = None
            else:
                err = float(np.abs(Ug - Ue).max()) if len(Ug) else 0.0
                row["uv_max_err_texel"] = err
                if err > UV_TOL_TEXEL:
                    reasons.append(f"print stale; re-project: {p}'s TEXCOORD_{k} differs from the bake's layout by "
                                   f"up to {err:.4g} texel (> {UV_TOL_TEXEL:g})")
        row["uv_checked"] = bool(exp is not None and has_tc and p in exp)
        rows[p] = row
    stale = any("stale" in r for r in reasons)
    return {"ok": not reasons, "stale": stale, "reasons": reasons, "parts": rows, "notes": notes,
            "images": {str(k): v for k, v in images_checked.items()},
            "manifest": {k: man.get(k) for k in ("print_sha256", "geometry_sha", "charts_sha", "parts")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bake_dir")
    ap.add_argument("--label", default=None)
    a = ap.parse_args(argv)
    path = write_manifest(a.bake_dir, label=a.label)
    print(json.dumps({"manifest": str(path), **json.loads(path.read_text(encoding="utf-8"))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
