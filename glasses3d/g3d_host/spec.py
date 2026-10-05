"""validate_spec(glb): the owner's GLB requirements (PLAN.md §2), and nothing else, as hard errors.

Rows: format, units, origin, orientation, transforms, geometry, components. Anything outside the table is a warning.
The live site's stricter acceptance (RULES_AUDIT.md §C) is reported separately as `live_site_would_fail`; it is
never an error and never an instruction to Astra. This module is the single source of truth for the §2 rows; arview's
runtime checks follow the same rules (WP1_DESIGN.md §1) and must agree with it on shared fixtures.

The validator never raises on file content: every section runs in its own guard, and an unexpected structural error
fails the row it belongs to. Primitives that fail a geometry check (unreadable data, non-triangle mode, bad index
count, signed / non-scalar / negative / out-of-range indices, non-finite positions, morph targets) are left out of
every later measurement, compressed ones included.

Rules (calibrated on 8 real models, 2026-10-05; the Studio deliveries q001, FT1119 scratch, Prada, INVU and Oakley
have their bridge underside within 0.02 mm of the origin, identity transforms and temples reaching -143..-173 mm):
- units: overall X width 0.095-0.20 m passes (warning outside 0.115-0.170 m); 95-200 or 9.5-20 units = mm / cm.
- origin: frame AND lens triangles are sliced with the planes x in {-1, -0.5, 0, 0.5, 1} mm; the lowest crossing
  in the front 25 mm is the bridge underside (a shield's nose notch is lens geometry). It must be at y = 0 +/- 1 mm
  with z = 0 inside its depth. Nothing crossing = "origin unverifiable" (a failure). The x position is judged on the
  lens-class bounding box (the bridge sits between the lenses), never on the whole model or the front region: an
  opened temple or a one-sided ornament reaching into the front band must not move it, and a bounding box of whole
  lenses does not depend on tessellation. Only when lens geometry is missing on one side of x = 0 is x judged on the
  front region (z >= front - 25 mm, the surface clipped at that plane). Off-centre whole-model or front-region boxes
  are warnings. (All 41 real Studio/ar GLBs and r9: lens x-centre within 0.1 mm.)
- orientation: Y is the thinnest axis; per side, non-lens geometry at |x| > 45 mm reaches z <= -60 mm; +Y up: the
  temple roots (non-lens, |x| > 45 mm, -40 < z < -15 mm) sit above the lens bbox centre (real +4.0..+18.0 mm;
  warning below +2 mm); roll: left/right lens halves (split at x = 0) have bbox-centre heights within 3 mm (real
  <= 0.16 mm); pitch: each temple's straight segment (-90 < z < -30 mm) slopes less than 15 deg (real -9.4..+4.7;
  sign: + rises toward +Z); lenses sit at the front. As in the calibration, the temple roots are the mean y of the
  non-lens VERTICES in that slab and the slope is a least-squares line through them; a coarse mesh with no vertex
  there (a box temple) is measured on plane cross-sections instead.
- transforms: every mesh node's world matrix within 1e-6 (absolute) of identity; EXT_mesh_gpu_instancing refused.
- geometry: finite positions, valid in-range indices (unsigned SCALAR, never negative), triangles (TRIANGLES / STRIP /
  FAN), static, at least one triangle.
Draco/meshopt geometry is not decoded here. Its POSITION min/max (required by glTF, kept by Draco) bound it: the boxes
join the overall and lens bounding boxes (units, thinnest axis, front band, lens centre and position are then exact),
and a failure of the other measured checks becomes an "unverified" warning only when a compressed box reaches the
region that check measures (e.g. the origin only when a compressed box reaches the x = 0 front band below the measured
underside). A compressed primitive without min/max cannot be bounded: then every measured failure is unverified.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from . import lens_id
from .glb import TRIANGLE_MODES, Glb, image_size, load

ROWS = ("format", "units", "origin", "orientation", "transforms", "geometry", "components")
MEASURED_ROWS = ("units", "origin", "orientation")

WIDTH_PASS_M = (0.095, 0.20)          # overall X width that passes the units row (metres)
WIDTH_TYPICAL_M = (0.115, 0.170)      # outside this: a warning (unusual but real sizes)
ORIGIN_TOL_MM = 1.0                   # bridge underside must be within this of (0,0,0)
ORIGIN_WARN_MM = 0.25
ORIGIN_SLICE_X_MM = (-1.0, -0.5, 0.0, 0.5, 1.0)
FRONT_BAND_MM = 25.0                  # "the front": z >= max z - this
TEMPLE_X_MM = 45.0                    # temples are non-lens geometry beyond this |x|
TEMPLE_REACH_Z_MM = -60.0             # ... reaching at least this far back
TEMPLE_ROOT_Z_MM = (-40.0, -15.0)     # the temple roots: -40 < z < -15
TEMPLE_ROOT_PLANES_MM = (-35.0, -30.0, -25.0, -20.0)        # fallback sample planes inside that slab
UP_WARN_MM = 2.0
ROLL_TOL_MM = 3.0
PITCH_Z_MM = (-90.0, -30.0)           # the temples' straight segment: -90 < z < -30
PITCH_PLANES_MM = tuple(float(z) for z in np.arange(-85.0, -30.0, 5.0))  # fallback sample planes inside it
PITCH_MIN_SPAN_MM = 15.0
PITCH_MAX_DEG = 15.0
LENS_FRONT_FRACTION = 0.3             # lens centre must be in the front 30 % of the depth
IDENTITY_ATOL = 1e-6                  # absolute, no relative term
VOLUME_THICKNESS_SUSPECT_M = 0.01     # KHR_materials_volume thicknessFactor above this looks like millimetres
ARVIEW_MAX_TRIANGLES = 300_000
ARVIEW_MAX_TEXTURE = 4096
LIVE_MAX_TRIANGLES = 100_000
LIVE_MAX_TEXTURE = 2048
LIVE_MAX_BYTES = 8 * 1024 * 1024
LIVE_TEMPLE_X_M = 0.045
LIVE_FORBIDDEN_EXTENSIONS = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression",
                             "KHR_texture_basisu")
INSTANCING_EXTENSION = "EXT_mesh_gpu_instancing"
KNOWN_EXTENSIONS = frozenset({     # what three.js GLTFLoader (arview, live site) understands
    "KHR_binary_glTF", "KHR_draco_mesh_compression", "KHR_lights_punctual", "KHR_materials_anisotropy",
    "KHR_materials_clearcoat", "KHR_materials_dispersion", "KHR_materials_emissive_strength", "KHR_materials_ior",
    "KHR_materials_iridescence", "KHR_materials_sheen", "KHR_materials_specular", "KHR_materials_transmission",
    "KHR_materials_unlit", "KHR_materials_variants", "KHR_materials_volume", "KHR_mesh_quantization",
    "KHR_texture_basisu", "KHR_texture_transform", "EXT_materials_bump", "EXT_mesh_gpu_instancing",
    "EXT_meshopt_compression", "KHR_meshopt_compression", "EXT_texture_avif", "EXT_texture_webp"})


class Report:
    def __init__(self, path: Path):
        self.path = path
        self.rows = {r: {"pass": True, "detail": []} for r in ROWS}
        self.warnings: list[str] = []
        self.live_site_would_fail: list[str] = []
        self.measurements: dict = {}
        self.materials: list[dict] = []
        self.primitives: list[dict] = []

    def fail(self, row: str, msg: str) -> None:
        self.rows[row]["pass"] = False
        self.rows[row]["detail"].append(msg)

    def note(self, row: str, msg: str) -> None:
        self.rows[row]["detail"].append(msg)

    def fail_unless(self, row: str, msg: str, could: list[str]) -> None:
        """Fail `row`, unless compressed (undecoded) primitives `could` change the verdict: then an unverified warning."""
        if could:
            self.warnings.append(f"{row} unverified: {msg} (compressed primitive(s) {sorted(set(could))[:6]}, not "
                                 f"decoded here, reach the measured region and could change this)")
            self.note(row, f"unverified: {msg}")
        else:
            self.fail(row, msg)

    def guard(self, rows: tuple[str, ...], fn, *args):
        """Run one section; an unexpected error fails `rows` instead of escaping."""
        try:
            return fn(*args)
        except Exception as exc:                                    # noqa: BLE001 - the report must come back
            for r in rows:
                self.fail(r, f"check could not run ({type(exc).__name__}: {exc}); treat the file as malformed")
            return None

    @property
    def ok(self) -> bool:
        return all(r["pass"] for r in self.rows.values())

    @property
    def errors(self) -> list[str]:
        return [f"{name}: {d}" for name, r in self.rows.items() if not r["pass"] for d in r["detail"]]

    def to_dict(self) -> dict:
        try:
            sha = hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.is_file() else None
        except OSError:
            sha = None
        return {
            "path": str(self.path),
            "sha256": sha,
            "ok": self.ok,
            "errors": self.errors,
            "rows": self.rows,
            "warnings": list(dict.fromkeys(self.warnings)),
            "live_site_would_fail": list(dict.fromkeys(self.live_site_would_fail)),
            "measurements": self.measurements,
            "materials": self.materials,
            "primitives": self.primitives,
        }


# --------------------------------------------------------------------------------------------- geometry helpers
def triangle_soup(prims) -> np.ndarray:
    """(M, 3, 3) world-space triangles of `prims`."""
    arrs = [p.positions[p.triangles] for p in prims if len(p.triangles)]
    return np.concatenate(arrs) if arrs else np.zeros((0, 3, 3))


def plane_points(T: np.ndarray, axis: int, value: float) -> np.ndarray:
    """Points where the triangles' edges cross the plane `coord[axis] == value` (plus vertices on the plane).
    Sampling the surface, not the vertices, keeps measurements independent of tessellation."""
    if not len(T):
        return np.zeros((0, 3))
    c = T[:, :, axis] - value
    keep = (c.min(1) <= 0.0) & (c.max(1) >= 0.0)
    T, c = T[keep], c[keep]
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        ca, cb = c[:, a], c[:, b]
        cross = ca * cb < 0.0
        if cross.any():
            s = (ca[cross] / (ca[cross] - cb[cross]))[:, None]
            pts.append(T[cross, a] + s * (T[cross, b] - T[cross, a]))
        on = ca == 0.0
        if on.any():
            pts.append(T[on, a])
    return np.vstack(pts) if pts else np.zeros((0, 3))


def _mid(a: np.ndarray) -> float:
    return float((a.min() + a.max()) / 2)


def _temple_points(V: np.ndarray, T: np.ndarray, sign: int, z_range_mm: tuple[float, float], planes_mm,
                   min_span_mm: float = 0.0) -> np.ndarray:
    """Non-lens points on one side (sign * x > TEMPLE_X_MM) with z strictly inside `z_range_mm`, in metres.

    The calibration (8 real models) used the mesh VERTICES in that slab, so those are used whenever there are at least
    3 of them spanning `min_span_mm` in z. A coarse mesh (a box temple has no vertex between its ends) falls back to
    the cross-sections of the sample planes `planes_mm` (one point per plane: the middle of the cross-section), so the
    measurement never depends on tessellation alone."""
    tx = TEMPLE_X_MM / 1000.0
    zlo, zhi = z_range_mm[0] / 1000.0, z_range_mm[1] / 1000.0
    sel = (V[:, 0] * sign > tx) & (V[:, 2] > zlo) & (V[:, 2] < zhi)
    pts = V[sel]
    if len(pts) >= 3 and (pts[:, 2].max() - pts[:, 2].min()) * 1000.0 >= min_span_mm:
        return pts
    mids = []
    for z in planes_mm:
        P = plane_points(T, 2, z / 1000.0)
        P = P[P[:, 0] * sign > tx]
        if len(P):
            mids.append((_mid(P[:, 0]), _mid(P[:, 1]), z / 1000.0))
    return np.array(mids).reshape(-1, 3)


def _is_opaque(mat: dict | None) -> bool:
    return lens_id.material_is_see_through(mat) is None and (mat or {}).get("alphaMode", "OPAQUE") != "BLEND"


def primitive_refs(glb: Glb) -> list[lens_id.PrimitiveRef]:
    refs = []
    for i, p in enumerate(glb.primitives):
        mat = glb.material(p.material_index)
        node = glb.nodes[p.node_index] if isinstance(glb.nodes[p.node_index], dict) else {}
        mesh = glb.meshes[p.mesh_index] if isinstance(glb.meshes[p.mesh_index], dict) else {}
        refs.append(lens_id.PrimitiveRef(
            key=str(i), node_name=p.node_name, node_extras=node.get("extras"), mesh_extras=mesh.get("extras"),
            material_name=mat.get("name") if mat else None, material_extras=mat.get("extras") if mat else None,
            material=mat))
    return refs


def _texture_infos(obj, path=""):
    """(json path, textureInfo dict) for every texture reference inside a material."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("Texture") and isinstance(v, dict) and "index" in v:
                yield path + k, v
            else:
                yield from _texture_infos(v, path + k + ".")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _texture_infos(v, f"{path}{i}.")


# --------------------------------------------------------------------------------------------- sections
class _State:
    """What the sections share."""
    def __init__(self, glb: Glb):
        self.glb = glb
        self.prims = glb.primitives
        self.measurable: list[int] = []        # primitives used for units/origin/orientation
        self.excluded: list[str] = []          # node names left out after a geometry failure
        self.compressed: list[str] = []        # node names of compressed (undecoded) primitives
        self.compressed_ok: list[int] = []     # compressed primitives that passed the geometry checks (bounded)
        self.decisions: dict = {}


def _format(rep: Report, st: _State) -> None:
    glb = st.glb
    rep.note("format", f"GLB 2.0, {glb.file_bytes} bytes")
    rep.measurements["file_bytes"] = glb.file_bytes
    if glb.scene_issue:
        rep.fail("format", glb.scene_issue)
    if glb.missing_uris:
        rep.fail("format", f"missing external resource(s) next to the file: {glb.missing_uris}")
    if glb.external_uris:
        rep.warnings.append(f"not self-contained: external resources {glb.external_uris}")
    used_ext = list(glb.doc.get("extensionsUsed", []) or [])
    req_ext = list(glb.doc.get("extensionsRequired", []) or [])
    rep.measurements["extensions_used"] = used_ext
    if req_ext:
        rep.measurements["extensions_required"] = req_ext
    unknown = [e for e in req_ext if e not in KNOWN_EXTENSIONS]
    if unknown:
        rep.warnings.append(f"extensionsRequired lists extensions three.js does not load: {unknown}")
    for e in LIVE_FORBIDDEN_EXTENSIONS:
        if e in used_ext:
            rep.live_site_would_fail.append(f"uses {e} (live site loads plain glTF only)")
    if glb.file_bytes > LIVE_MAX_BYTES:
        rep.live_site_would_fail.append(f"file is {glb.file_bytes / 1048576:.1f} MB (live limit 8 MB)")
    for p in st.prims:
        if p.material_index is not None and glb.material(p.material_index) is None:
            rep.fail("format", f"{p.node_name}: material {p.material_index!r} does not exist "
                               f"({len(glb.materials)} material(s))")
        if p.error:
            rep.fail("format", f"{p.node_name}: geometry unreadable ({p.error})")


def _textures(rep: Report, st: _State) -> None:
    glb = st.glb
    textures = []
    for i, _ in enumerate(glb.doc.get("images", []) or []):
        try:
            data = glb.image_bytes(i)
        except Exception as exc:                                    # noqa: BLE001
            rep.fail("format", f"image {i} is unreadable ({type(exc).__name__}: {exc})")
            textures.append({"image": i, "size": None})
            continue
        if data is None:                                            # missing external file: failed in _format
            textures.append({"image": i, "size": None})
            continue
        size = image_size(data)
        textures.append({"image": i, "size": size})
        if size is None:
            rep.warnings.append(f"image {i}: size could not be read (not PNG, JPEG or WebP); texture limits "
                                f"not checked")
            continue
        if max(size) > ARVIEW_MAX_TEXTURE:
            rep.warnings.append(f"image {i} is {size[0]}x{size[1]} (> {ARVIEW_MAX_TEXTURE}; 8K renders black in EEVEE)")
        if max(size) > LIVE_MAX_TEXTURE:
            rep.live_site_would_fail.append(f"image {i} is {size[0]}x{size[1]} (live limit {LIVE_MAX_TEXTURE})")
    rep.measurements["textures"] = textures
    n_tex = len(glb.doc.get("textures", []) or [])
    for mi, mat in enumerate(glb.materials):
        if not isinstance(mat, dict):
            continue
        name = mat.get("name", f"material{mi}")
        users = [p for p in st.prims if p.material_index == mi]
        for where, info in _texture_infos(mat):
            idx = info.get("index")
            if not (isinstance(idx, int) and 0 <= idx < n_tex):
                rep.fail("format", f"material {name!r} {where}: texture {idx!r} does not exist")
            tc = info.get("texCoord", 0)
            tc = ((info.get("extensions") or {}).get("KHR_texture_transform") or {}).get("texCoord", tc)
            if not (isinstance(tc, int) and tc >= 0):
                rep.fail("format", f"material {name!r} {where}: texCoord {tc!r} is not valid glTF (an integer >= 0)")
                continue
            missing = sorted({p.node_name for p in users if not p.error and not p.compressed
                              and f"TEXCOORD_{tc}" not in p.attributes})
            if missing:
                rep.warnings.append(f"material {name!r} {where} samples TEXCOORD_{tc}, which {missing[:6]} do not "
                                    f"have: the texture cannot map onto them")
        vol = (mat.get("extensions") or {}).get("KHR_materials_volume") or {}
        t = vol.get("thicknessFactor")
        if isinstance(t, (int, float)) and t > VOLUME_THICKNESS_SUSPECT_M:
            rep.warnings.append(f"material {name!r}: KHR_materials_volume thicknessFactor {t:g} looks like "
                                f"millimetres (glTF lengths are metres; a lens is about 0.0015)")


def _geometry(rep: Report, st: _State) -> None:
    glb = st.glb
    if not st.prims:
        rep.fail("geometry", "no mesh primitives in the default scene")
    tri_total = 0
    for i, p in enumerate(st.prims):
        if p.error:
            st.excluded.append(p.node_name)
            continue
        bad = []
        if p.mode not in TRIANGLE_MODES:
            kind = {0: "POINTS", 1: "LINES", 2: "LINE_LOOP", 3: "LINE_STRIP"}.get(p.mode, "unknown")
            bad.append(f"primitive mode {p.mode} ({kind}) is not triangles")
        if p.compressed:
            st.compressed.append(p.node_name)
            if p.has_morph_targets:
                bad.append("morph targets (the model must be static)")
            for b in bad:
                rep.fail("geometry", f"{p.node_name}: {b}")
            if bad:
                st.excluded.append(p.node_name)
            else:
                st.compressed_ok.append(i)
            continue
        if p.mode == 4 and p.raw_index_count % 3:
            bad.append(f"{p.raw_index_count} indices is not a multiple of 3")
        if not np.isfinite(p.positions).all():
            bad.append("non-finite vertex positions")
        if p.index_issue:
            bad.append(p.index_issue)
        if p.max_index >= len(p.positions):
            bad.append(f"index {p.max_index} >= vertex count {len(p.positions)}")
        if p.has_morph_targets:
            bad.append("morph targets (the model must be static)")
        if p.mode in TRIANGLE_MODES:
            tri_total += len(p.triangles)
        for b in bad:
            rep.fail("geometry", f"{p.node_name}: {b}")
        if bad:
            st.excluded.append(p.node_name)
            continue
        if not len(p.triangles):
            rep.warnings.append(f"{p.node_name}: primitive has no triangles")
            continue
        t = p.positions[p.triangles]
        area2 = np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
        deg = int((area2 < 1e-14).sum())
        if deg:
            rep.warnings.append(f"{p.node_name}: {deg} degenerate (zero-area) triangles")
        st.measurable.append(i)
    if st.prims and tri_total == 0 and not st.compressed:
        rep.fail("geometry", "the file has no triangles")
    if glb.doc.get("skins") or any(isinstance(n, dict) and "skin" in n for n in glb.nodes):
        rep.fail("geometry", "skins present (the model must be static)")
    if glb.doc.get("animations"):
        rep.fail("geometry", "animations present (the model must be static)")
    rep.measurements["triangles"] = tri_total
    if st.excluded:
        rep.measurements["excluded_from_measurement"] = sorted(set(st.excluded))
    if tri_total > ARVIEW_MAX_TRIANGLES:
        rep.warnings.append(f"{tri_total} triangles (> {ARVIEW_MAX_TRIANGLES}; heavy for real-time AR)")
    if tri_total > LIVE_MAX_TRIANGLES:
        rep.live_site_would_fail.append(f"{tri_total} triangles (live limit {LIVE_MAX_TRIANGLES})")
    for p in st.prims:
        if p.compressed:
            rep.warnings.append(f"{p.node_name}: geometry compressed with {p.compressed}; not decoded or "
                                f"verified here")


def _transforms(rep: Report, st: _State) -> None:
    eye = np.eye(4)
    bad = sorted({p.node_name for p in st.prims if not (np.abs(p.world - eye) <= IDENTITY_ATOL).all()})
    if bad:
        rep.fail("transforms", f"{len(bad)} mesh node(s) with a non-identity world transform "
                               f"(tolerance {IDENTITY_ATOL:g} absolute): {bad[:8]}")
    inst = [n.get("name", f"node{i}") for i, n in enumerate(st.glb.nodes)
            if isinstance(n, dict) and INSTANCING_EXTENSION in (n.get("extensions") or {})]
    if inst:
        rep.fail("transforms", f"{len(inst)} node(s) use {INSTANCING_EXTENSION}: instanced copies are drawn at "
                               f"their own transforms, which are not baked: {inst[:8]}")
    uses: dict[int, set] = {}
    names: dict[int, str] = {}
    for p in st.prims:
        uses.setdefault(p.mesh_index, set()).add(p.node_index)
        names[p.mesh_index] = p.mesh_name
    shared = sorted(names[m] for m, n in uses.items() if len(n) > 1)
    if shared:
        rep.warnings.append(f"mesh(es) drawn by more than one node (duplicate geometry): {shared[:8]}")


def _components(rep: Report, st: _State) -> None:
    glb, prims = st.glb, st.prims
    decisions, lens_warn = lens_id.classify(primitive_refs(glb))
    st.decisions = decisions
    rep.warnings += lens_warn
    lens = [i for i in range(len(prims)) if decisions[str(i)].is_lens]
    frame = [i for i in range(len(prims)) if not decisions[str(i)].is_lens]
    for i, p in enumerate(prims):
        d = decisions[str(i)]
        rep.primitives.append({"index": i, "node": p.node_name, "mesh": p.mesh_name, "material": p.material_index,
                               "class": "lens" if d.is_lens else "frame", "rule": d.rule, "signal": d.signal})
    by_mat: dict = {}
    for i, p in enumerate(prims):
        mi = p.material_index if glb.material(p.material_index) is not None else None
        by_mat.setdefault(mi, set()).add("lens" if decisions[str(i)].is_lens else "frame")
    for mi, classes in sorted(by_mat.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        name = glb.materials[mi].get("name") if mi is not None else None
        signals = sorted({decisions[str(i)].signal for i, p in enumerate(prims)
                          if (p.material_index if glb.material(p.material_index) is not None else None) == mi})
        rep.materials.append({"index": mi, "name": name, "class": "/".join(sorted(classes)), "signals": signals})
        if len(classes) > 1 and mi is not None:
            rep.fail("components", f"material {name!r} is used by both lens and non-lens primitives")
    if not lens:
        rep.fail("components", "no lens identified (partRole 'lens', a lens/glass name token, or transmission)")
    if not frame:
        rep.fail("components", "no frame/temple primitives (everything was identified as lens)")
    for i in lens:
        if glb.material(prims[i].material_index) is None:
            rep.fail("components", f"lens primitive {prims[i].node_name!r} has no material, so it is not "
                                   f"distinguished by material")
    rep.measurements["lens_primitives"] = len(lens)


INF = float("inf")


def _is_lens(st: _State, i: int) -> bool:
    d = st.decisions.get(str(i))
    return bool(d and d.is_lens)


def _reach(boxes, x=(-INF, INF), y=(-INF, INF), z=(-INF, INF)) -> list:
    """The compressed boxes (name, lo, hi) that reach the region x/y/z (closed intervals, metres)."""
    return [b for b in boxes if b[1][0] <= x[1] and b[2][0] >= x[0] and b[1][1] <= y[1] and b[2][1] >= y[0]
            and b[1][2] <= z[1] and b[2][2] >= z[0]]


def _names(boxes) -> list[str]:
    return [b[0] for b in boxes]


def _side(sign: int) -> tuple[float, float]:
    """The x interval of one side beyond the temple line (|x| > 45 mm)."""
    tx = TEMPLE_X_MM / 1000.0
    return (tx, INF) if sign > 0 else (-INF, -tx)


class _Geo:
    """Measured geometry (metres), plus the world boxes of compressed (undecoded) primitives from their POSITION
    min/max. The boxes join the overall and lens bounding boxes, which are then exact for identity transforms."""
    def __init__(self, st: _State):
        mp = [st.prims[i] for i in st.measurable]
        lens = [st.prims[i] for i in st.measurable if _is_lens(st, i)]
        frame = [st.prims[i] for i in st.measurable if not _is_lens(st, i)]
        empty = np.zeros((0, 3))
        self.V = np.vstack([p.positions[np.unique(p.triangles)] for p in mp] or [empty])
        self.Vl = np.vstack([p.positions[np.unique(p.triangles)] for p in lens] or [empty])
        self.Vf = np.vstack([p.positions[np.unique(p.triangles)] for p in frame] or [empty])
        self.Tl = triangle_soup(lens)
        self.Tf = triangle_soup(frame)
        self.cframe, self.clens = [], []
        for i in st.compressed_ok:
            p = st.prims[i]
            if p.bounds is not None:
                (self.clens if _is_lens(st, i) else self.cframe).append((p.node_name, p.bounds[0], p.bounds[1]))
        self.cboxes = self.cframe + self.clens
        allp = np.vstack([self.V] + [np.stack([b[1], b[2]]) for b in self.cboxes])
        if not len(allp):
            raise ValueError("no measurable geometry and no compressed bounds")
        self.lo, self.hi = allp.min(0), allp.max(0)
        self.ext = self.hi - self.lo
        lp = np.vstack([self.Vl] + [np.stack([b[1], b[2]]) for b in self.clens])
        self.lens_box = (lp.min(0), lp.max(0)) if len(lp) else None


def _units(rep: Report, g: _Geo) -> None:
    w = float(g.ext[0])
    rep.measurements["bbox_mm"] = {"min": (g.lo * 1000).round(2).tolist(), "max": (g.hi * 1000).round(2).tolist()}
    rep.measurements["width_mm"] = round(w * 1000, 2)
    lo_w, hi_w = WIDTH_PASS_M
    if lo_w <= w <= hi_w:
        rep.note("units", f"width {w:.4f} units = {w * 1000:.1f} mm (metres)")
        if not WIDTH_TYPICAL_M[0] <= w <= WIDTH_TYPICAL_M[1]:
            rep.warnings.append(f"overall width {w * 1000:.1f} mm is outside the usual {WIDTH_TYPICAL_M[0] * 1000:.0f}-"
                                f"{WIDTH_TYPICAL_M[1] * 1000:.0f} mm: check the scale")
    elif lo_w * 1000 <= w <= hi_w * 1000:
        rep.fail("units", f"width {w:.1f} units: millimetre-scaled (expected metres, about {w / 1000:.3f})")
    elif lo_w * 100 <= w <= hi_w * 100:
        rep.fail("units", f"width {w:.2f} units: centimetre-scaled (expected metres, about {w / 100:.3f})")
    else:
        rep.fail("units", f"width {w:.4g} units is not a plausible frame width in metres ({lo_w}-{hi_w})")


def _orientation(rep: Report, g: _Geo) -> None:
    ext, lo, hi = g.ext, g.lo, g.hi                                 # compressed boxes included: exact
    if not (ext[1] < ext[0] and ext[1] < ext[2]):
        rep.fail("orientation", f"Y is not the thinnest axis (extent mm x {ext[0]*1000:.1f}, y {ext[1]*1000:.1f}, "
                                f"z {ext[2]*1000:.1f}): not +Y up")
    tx, reach = TEMPLE_X_MM / 1000.0, TEMPLE_REACH_Z_MM / 1000.0
    temples, missing = {}, []
    for side, sign in (("left", -1), ("right", 1)):
        sel = g.Vf[:, 0] * sign > tx
        zmin = float(g.Vf[sel][:, 2].min()) if sel.any() else None
        temples[side] = None if zmin is None else round(zmin * 1000, 2)
        if zmin is None or zmin > reach:
            where = "x < -45 mm" if sign < 0 else "x > +45 mm"
            got = "none there" if zmin is None else f"it reaches only z {zmin * 1000:.1f} mm"
            rep.fail_unless("orientation", f"temple {side} missing: no non-lens geometry at {where} reaches z <= "
                                           f"{TEMPLE_REACH_Z_MM:.0f} mm ({got}); temples must extend along -Z",
                            _names(_reach(g.cframe, x=_side(sign), z=(-INF, reach))))
            missing.append(side)
    rep.measurements["temple_reach_z_mm"] = temples
    side_geo = g.Vf[np.abs(g.Vf[:, 0]) > tx]
    if len(missing) == 2 and len(side_geo) and side_geo[:, 2].max() >= -reach:
        rep.fail_unless("orientation", f"geometry at |x| > 45 mm extends to z +{side_geo[:, 2].max() * 1000:.0f} mm: "
                                       f"the model faces backwards (not +Z forward)",
                        _names(_reach(g.cframe, x=_side(-1), z=(-INF, reach))
                               + _reach(g.cframe, x=_side(1), z=(-INF, reach))))

    if g.lens_box is not None:
        llo, lhi = g.lens_box
        lens_cy = float((llo[1] + lhi[1]) / 2)
        # +Y up: the temple roots sit above the lens centre
        roots = []
        for sign in (-1, 1):
            P = _temple_points(g.Vf, g.Tf, sign, TEMPLE_ROOT_Z_MM, TEMPLE_ROOT_PLANES_MM)
            if len(P):
                roots.append(float(P[:, 1].mean()))
        zr = (TEMPLE_ROOT_Z_MM[0] / 1000.0, TEMPLE_ROOT_Z_MM[1] / 1000.0)
        could = _names(_reach(g.cframe, x=_side(-1), z=zr) + _reach(g.cframe, x=_side(1), z=zr))
        if roots:
            dy = float(np.mean(roots)) - lens_cy
            rep.measurements["temple_roots_above_lens_centre_mm"] = round(dy * 1000, 2)
            if dy <= 0.0:
                rep.fail_unless("orientation", f"not +Y up: the temple roots (|x| > 45 mm, z -40..-15 mm) sit "
                                               f"{-dy * 1000:.1f} mm BELOW the lens centre (real frames: +4 to +18 mm "
                                               f"above); the model is upside down or mirrored in Y", could)
            elif dy * 1000 < UP_WARN_MM:
                rep.warnings.append(f"the temple roots sit only {dy * 1000:.1f} mm above the lens centre (real frames: "
                                    f"+4 to +18 mm): check +Y up")
        elif not missing:
            rep.warnings.append("+Y up not measured: no non-lens geometry at |x| > 45 mm between z -40 and -15 mm"
                                + (f" (compressed {could[:6]} there is not decoded)" if could else ""))
        # roll: both lens halves at the same height (a compressed lens box on one side joins that half exactly)
        left, right, crossing = [g.Vl[g.Vl[:, 0] < 0.0][:, 1]], [g.Vl[g.Vl[:, 0] > 0.0][:, 1]], []
        for name, blo, bhi in g.clens:
            if bhi[0] <= 0.0:
                left.append(np.array([blo[1], bhi[1]]))
            elif blo[0] >= 0.0:
                right.append(np.array([blo[1], bhi[1]]))
            else:
                crossing.append(name)
        left, right = np.concatenate(left), np.concatenate(right)
        if len(left) and len(right):
            d = _mid(right) - _mid(left)
            rep.measurements["lens_halves_dy_mm"] = round(d * 1000, 3)
            if abs(d) * 1000 > ROLL_TOL_MM:
                rep.fail_unless("orientation", f"rolled: the right lens half's centre is {d * 1000:+.1f} mm higher than "
                                               f"the left's (must be within {ROLL_TOL_MM:.0f} mm; real frames <= "
                                               f"0.16 mm)", crossing)
        else:
            rep.warnings.append("roll not measured: lens geometry on one side of x = 0 only"
                                + (f" (compressed {crossing[:6]} crosses x = 0 and is not decoded)" if crossing else ""))
        # lenses at the front
        lc = (llo + lhi) / 2
        rep.measurements["lens_bbox_centre_mm"] = (lc * 1000).round(2).tolist()
        if lc[2] < hi[2] + LENS_FRONT_FRACTION * (lo[2] - hi[2]):
            rep.fail("orientation", f"lenses sit at z {lc[2]*1000:.1f} mm, behind the front (z {hi[2]*1000:.1f} mm): "
                                    f"not +Z forward")
    else:
        rep.warnings.append("+Y up, roll and lens position not measured: no measurable lens geometry")
    # pitch: the temples' straight segment runs along -Z
    slopes = {}
    zp = (PITCH_Z_MM[0] / 1000.0, PITCH_Z_MM[1] / 1000.0)
    for side, sign in (("left", -1), ("right", 1)):
        if side in missing:
            continue
        P = _temple_points(g.Vf, g.Tf, sign, PITCH_Z_MM, PITCH_PLANES_MM, min_span_mm=PITCH_MIN_SPAN_MM)
        if len(P) < 2 or (P[:, 2].max() - P[:, 2].min()) * 1000.0 < PITCH_MIN_SPAN_MM:
            rep.warnings.append(f"pitch not measured on the {side}: too little temple between z -90 and -30 mm")
            continue
        b = float(np.polyfit(P[:, 2], P[:, 1], 1)[0])               # dy/dz
        slope = math.degrees(math.atan(b))                           # the calibration's sign: + rises toward +Z
        slopes[side] = round(slope, 2)
        if abs(slope) >= PITCH_MAX_DEG:
            rep.fail_unless("orientation", f"pitched: the {side} temple's straight segment (z -90..-30 mm) slopes "
                                           f"{slope:+.1f} deg (must be under {PITCH_MAX_DEG:.0f}; real frames -9.4 to "
                                           f"+4.7)", _names(_reach(g.cframe, x=_side(sign), z=zp)))
    if slopes:
        rep.measurements["temple_slope_deg"] = slopes


def _origin(rep: Report, g: _Geo, st: _State) -> None:
    tol = ORIGIN_TOL_MM
    band_lo = g.hi[2] - FRONT_BAND_MM / 1000.0
    xs = (min(ORIGIN_SLICE_X_MM) / 1000.0, max(ORIGIN_SLICE_X_MM) / 1000.0)
    near = _reach(g.cboxes, x=xs, z=(band_lo, INF))                 # compressed parts that could hold the bridge
    cuts = {}
    for name, T in (("frame", g.Tf), ("lens", g.Tl)):
        pts = np.vstack([plane_points(T, 0, x / 1000.0) for x in ORIGIN_SLICE_X_MM])
        cuts[name] = pts[pts[:, 2] >= band_lo]
    band = np.vstack([cuts["frame"], cuts["lens"]])
    if not len(band):
        why = f" ({len(set(st.excluded))} primitive(s) excluded after geometry failures)" if st.excluded else ""
        rep.fail_unless("origin", f"origin unverifiable: no frame or lens surface crosses x = 0 (+/-1 mm) within the "
                                  f"front {FRONT_BAND_MM:.0f} mm, so the bridge underside cannot be measured{why}",
                        _names(near))
    else:
        y0 = float(band[:, 1].min())
        surface = "lens" if len(cuts["lens"]) and float(cuts["lens"][:, 1].min()) <= y0 else "frame"
        bottom = band[band[:, 1] <= y0 + 0.002]
        z_lo, z_hi = float(bottom[:, 2].min()), float(bottom[:, 2].max())
        dz = 0.0 if z_lo - 0.001 <= 0.0 <= z_hi + 0.001 else min(abs(z_lo), abs(z_hi))
        rep.measurements["bridge_underside_mm"] = {"y": round(y0 * 1000, 3),
                                                   "z_range": [round(z_lo * 1000, 2), round(z_hi * 1000, 2)],
                                                   "surface": surface}
        below = _names([b for b in near if b[1][1] < y0])
        if abs(y0) * 1000 > tol:
            rep.fail_unless("origin", f"bridge underside is at y {y0*1000:+.2f} mm (must be 0 +/- {tol} mm)", below)
        elif below:
            rep.warnings.append(f"origin y unverified: compressed primitive(s) {below[:6]} (not decoded) reach the "
                                f"x = 0 front band below the measured bridge underside (y {y0*1000:+.2f} mm)")
        elif abs(y0) * 1000 > ORIGIN_WARN_MM:
            rep.warnings.append(f"bridge underside is at y {y0*1000:+.2f} mm (allowed, but not exact)")
        if dz * 1000 > tol:
            rep.fail_unless("origin", f"origin z is {dz*1000:.2f} mm outside the bridge underside's depth "
                                      f"({z_lo*1000:.1f}..{z_hi*1000:.1f} mm)",
                            _names([b for b in near if b[1][1] <= y0 + 0.002]))
    # x: judged on the lens-class bounding box (the bridge sits between the lenses). The front region (its surface
    # clipped at z = band_lo, so independent of tessellation) and the whole model are reported, and the front region
    # is judged only when no lens geometry spans x = 0.
    T_all = np.concatenate([g.Tf, g.Tl])
    front_pts = np.vstack([g.V[g.V[:, 2] >= band_lo], plane_points(T_all, 2, band_lo)])
    fx = _mid(front_pts[:, 0]) if len(front_pts) else None
    if fx is not None:
        rep.measurements["front_centre_x_mm"] = round(fx * 1000, 3)
    whole = float((g.lo[0] + g.hi[0]) / 2)
    lb = g.lens_box
    if lb is not None and lb[0][0] < 0.0 < lb[1][0]:
        xc, judged, could = float((lb[0][0] + lb[1][0]) / 2), "lenses", []
        rep.measurements["lens_centre_x_mm"] = round(xc * 1000, 3)
    else:
        xc, judged, could = fx, "front", _names(_reach(g.cboxes, z=(band_lo, INF)))
        rep.warnings.append(f"origin x judged on the front region (z >= {band_lo*1000:.1f} mm): no lens geometry "
                            f"spans x = 0")
    rep.measurements["origin_x_judged_on"] = judged
    if xc is None:
        rep.fail_unless("origin", "origin x unverifiable: no lens geometry spans x = 0 and no surface reaches the "
                                  "front band", could)
    elif abs(xc) * 1000 > tol:
        what = "the lenses are" if judged == "lenses" else f"the front (z >= {band_lo*1000:.1f} mm) is"
        rep.fail_unless("origin", f"{what} centred at x {xc*1000:+.2f} mm, not on the x = 0 plane (must be within "
                                  f"{tol} mm)", could)
    elif judged == "lenses":
        off = [f"the {n} is centred at x {v*1000:+.2f} mm" for n, v in (("front region", fx), ("whole model", whole))
               if v is not None and abs(v) * 1000 > tol]
        if off:
            rep.warnings.append(f"{' and '.join(off)} although the lenses are centred at x {xc*1000:+.2f} mm "
                                f"(asymmetric temples or ornaments?)")


def _live_site(rep: Report, st: _State) -> None:
    glb, prims = st.glb, st.prims
    frame = [i for i in range(len(prims)) if not st.decisions[str(i)].is_lens]
    for i in frame:
        mat = glb.material(prims[i].material_index)
        if mat is not None and not _is_opaque(mat):
            rep.live_site_would_fail.append(f"frame material {mat.get('name')!r} is not fully opaque")
    opaque = [prims[i].positions for i in frame if i in st.measurable
              and (glb.material(prims[i].material_index) is None or _is_opaque(glb.material(prims[i].material_index)))]
    if not opaque:
        rep.live_site_would_fail.append("no opaque part")
        return
    O = np.vstack(opaque)
    for side, sel in (("left", O[:, 0] < -LIVE_TEMPLE_X_M), ("right", O[:, 0] > LIVE_TEMPLE_X_M)):
        if not (O[sel][:, 2] < -0.03).any():
            rep.live_site_would_fail.append(f"no opaque temple geometry beyond {LIVE_TEMPLE_X_M*1000:.0f} mm on "
                                            f"the {side} reaching back past the hinge")


def _measured(rep: Report, st: _State) -> None:
    """units, orientation and origin on the measurable primitives and the compressed primitives' bounds."""
    if not st.measurable and not st.compressed_ok:
        why = (f"every primitive was excluded after geometry failures: {sorted(set(st.excluded))[:8]}"
               if st.excluded else "no readable triangle geometry")
        for r in MEASURED_ROWS:
            rep.fail(r, f"not checked: {why}")
        return
    unbounded = sorted({st.prims[i].node_name for i in st.compressed_ok if st.prims[i].bounds is None})
    if not st.measurable and all(st.prims[i].bounds is None for i in st.compressed_ok):
        for r in MEASURED_ROWS:                                     # compressed only, and not even bounds
            rep.note(r, "unverified: the geometry is compressed (Draco/meshopt is not decoded here)")
        rep.warnings.append(f"units, origin and orientation unverified: geometry compressed "
                            f"({sorted(set(st.compressed))[:6]}) is not decoded here and has no POSITION min/max")
        return
    g = rep.guard(MEASURED_ROWS, _Geo, st)
    if g is None:
        return
    rep.guard(("units",), _units, rep, g)
    if not rep.rows["units"]["pass"]:
        for r in ("origin", "orientation"):
            rep.fail(r, "not checked: units are wrong")
    elif not st.measurable:                                         # every primitive compressed: only bounds known
        for r in ("origin", "orientation"):
            rep.note(r, "unverified: every primitive is compressed (not decoded here); only their bounds are known")
        rep.warnings.append(f"origin and orientation unverified: every primitive is compressed "
                            f"({sorted(set(st.compressed))[:6]}); Draco/meshopt is not decoded here")
    else:
        rep.guard(("orientation",), _orientation, rep, g)
        rep.guard(("origin",), _origin, rep, g, st)
    if unbounded:
        _downgrade_unbounded(rep, unbounded)


def _downgrade_unbounded(rep: Report, names: list[str]) -> None:
    """Compressed primitives without POSITION min/max (glTF requires it) cannot even be bounded: every measured
    failure becomes an unverified warning."""
    rep.warnings.append(f"compressed primitive(s) {names[:6]} have no POSITION min/max (glTF requires it): their "
                        f"extent is unknown")
    for r in MEASURED_ROWS:
        row = rep.rows[r]
        if not row["pass"]:
            rep.warnings += [f"{r} unverified (compressed primitive(s) {names[:6]} are not decoded and not bounded): "
                             f"{d}" for d in row["detail"]]
            row["pass"], row["detail"] = True, ["unverified: compressed primitives without bounds"]


def validate_spec(path: str | Path) -> dict:
    path = Path(path)
    rep = Report(path)
    try:
        glb = load(path)
    except Exception as exc:                                        # noqa: BLE001 - unreadable file: every row fails
        rep.fail("format", f"unreadable GLB: {type(exc).__name__}: {exc}")
        for r in ROWS[1:]:
            rep.fail(r, "not checked: the file could not be read")
        return rep.to_dict()
    st = _State(glb)
    rep.guard(("format",), _format, rep, st)
    rep.guard(("geometry",), _geometry, rep, st)
    rep.guard(("transforms",), _transforms, rep, st)
    rep.guard(("format",), _textures, rep, st)
    rep.guard(("components",), _components, rep, st)
    if not st.decisions:                                            # components crashed: classify nothing as lens
        st.decisions = {str(i): lens_id.Decision(False, 0, "not classified") for i in range(len(st.prims))}
    rep.guard(MEASURED_ROWS, _measured, rep, st)
    try:
        _live_site(rep, st)
    except Exception as exc:                                        # noqa: BLE001 - live-site notes are advisory
        rep.warnings.append(f"live-site checks could not run ({type(exc).__name__}: {exc})")
    return rep.to_dict()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m glasses3d.g3d_host.spec <model.glb> [...]", file=sys.stderr)
        return 2
    worst = 0
    for a in argv:
        r = validate_spec(a)
        print(json.dumps({k: r[k] for k in ("path", "ok", "errors", "warnings", "live_site_would_fail",
                                             "measurements")}, indent=1))
        worst = max(worst, 0 if r["ok"] else 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
