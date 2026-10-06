"""WP5 print UV layout core (WP5_DESIGN §6.1, §10): strip + cap charts, mirror-partner index copy, shelf packing and
the A1 checks. **numpy only** (no bpy, scipy or open3d), so the same code runs on host arrays (photoprint, tests) and,
later, inside Blender 5.2 through ``g3d.print_uv`` (not built yet).

    strip   strip parametrisation (s along a smooth centreline, t around the section from a cut direction)
    charts  strip chart + planar cap charts of one part, overlap resolution, numpy graph helpers
    mirror  mirror-partner detection (x -> -x) and the index copy of a layout
    pack    shelf packing into one atlas <= 4096, 8-texel gutters, density fitting
    check   the A1 report (overlaps, flips, anisotropy p95, density p1, mirror index identity)

``layout(V, F, part, names, parts, tx_per_mm)`` is the entry point. Atlas texel convention: texel (i, j) has its centre
at (x, y) = (j + 0.5, i + 0.5) (``g3d_host.texture_utils.rasterize_triangles``); glTF uv = (x / width, y / height).
Sides are never read from part names: strip seeds come from the part's own principal axis and the x sign of its
centroid (q001 names temples by the viewer's side).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import charts as CH
from . import check as CK
from . import mirror as MR
from . import pack as PK

STRIP_ELONGATION = 2.5          # longest / second bbox side for a strip part
GUTTER = PK.GUTTER
ATLAS_MAX = PK.ATLAS_MAX


@dataclass
class PartSpec:
    name: str
    faces: np.ndarray               # global face ids
    kind: str                       # 'strip' or 'caps'
    ref: np.ndarray | None = None   # strip frame seed (theta = 0)
    cut_dir: np.ndarray | None = None


@dataclass
class Layout:
    faces: np.ndarray               # (K,) global face ids
    uv: np.ndarray                  # (K, 3, 2) atlas texel coordinates per corner
    chart: np.ndarray               # (K,) chart index
    part_of: np.ndarray             # (K,) part name index into ``parts``
    parts: list[str]
    charts: list[dict]
    width: int
    height: int
    tx_per_mm: float
    tx_requested: float
    partners: dict = field(default_factory=dict)     # partner part -> source part
    face_corr: dict = field(default_factory=dict)    # partner part -> correspondence (mirror.find_partner)
    info: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)

    def uv01(self) -> np.ndarray:
        """glTF texture coordinates (u = x / width, v = y / height; v down, origin at the top-left)."""
        return self.uv / np.array([self.width, self.height], float)

    def face_index(self, n_faces: int) -> np.ndarray:
        """(n_faces,) row of ``faces`` for every global face (-1: not in the layout)."""
        out = -np.ones(int(n_faces), np.int64)
        out[self.faces] = np.arange(len(self.faces))
        return out

    def to_json(self) -> dict:
        return {"width": self.width, "height": self.height, "tx_per_mm": self.tx_per_mm,
                "tx_requested": self.tx_requested, "parts": self.parts, "partners": self.partners,
                "charts": self.charts, "info": self.info, "a1": self.report}


def default_spec(V: np.ndarray, F: np.ndarray, faces: np.ndarray, name: str) -> PartSpec:
    """Strip when the part's bbox is elongated (longest >= 2.5 x second side), else caps. Strip seeds by the canonical
    frame (+y up, +z front): a part along x (a brow) is seeded at the front (+z) and cut on its back face (-z); a part
    along z (a temple) is seeded on its outer side (x sign of its centroid) and cut on its inner-bottom edge."""
    P = np.asarray(V, float)[np.unique(np.asarray(F)[faces])]
    ext = np.sort(P.max(0) - P.min(0))[::-1]
    if ext[1] <= 0 or ext[0] / ext[1] < STRIP_ELONGATION:
        return PartSpec(name, np.asarray(faces), "caps")
    mu = P.mean(0)
    a1 = np.linalg.svd(P - mu, full_matrices=False)[2][0]
    k = int(np.argmax(np.abs(a1)))
    if k == 0 or k == 1:
        return PartSpec(name, np.asarray(faces), "strip", np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, -1.0]))
    sx = 1.0 if mu[0] >= 0 else -1.0
    cut = np.array([-sx, -1.0, 0.0]) / np.sqrt(2.0)
    return PartSpec(name, np.asarray(faces), "strip", np.array([sx, 0.0, 0.0]), cut)


def layout(V: np.ndarray, F: np.ndarray, part: np.ndarray, names: list[str], parts, tx_per_mm: float, *,
           atlas_max: int = ATLAS_MAX, gutter: int = GUTTER, mirror: bool = True, specs: dict | None = None,
           mirror_tol_mm: float = MR.MIRROR_TOL_MM) -> Layout:
    """The print layout of ``parts`` (part names; ``part`` = per-face part id, ``names`` = per-part name) at
    ``tx_per_mm`` (lowered when the atlas would exceed ``atlas_max``). With ``mirror``, a part that is another listed
    part reflected in x = 0 gets that part's charts by index copy (its own atlas boxes)."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    part = np.asarray(part)
    parts = list(parts)
    face_sets = {}
    for nm in parts:
        ids = [i for i, n in enumerate(names) if n == nm]
        if not ids:
            raise ValueError(f"part {nm!r} is not in the mesh")
        face_sets[nm] = np.nonzero(np.isin(part, ids))[0]
    specs = dict(specs or {})
    for nm in parts:
        specs.setdefault(nm, default_spec(V, F, face_sets[nm], nm))
    partners, corrs = {}, {}
    if mirror:
        for i, b in enumerate(parts):
            for a in parts[:i]:
                if a in partners:
                    continue
                c = MR.find_partner(V, F, face_sets[a], face_sets[b], mirror_tol_mm)
                if c is not None:
                    partners[b] = a
                    corrs[b] = c
                    break
    tx = float(tx_per_mm)
    per_part, info = {}, {}
    for nm in parts:
        if nm in partners:
            continue
        sp = specs[nm]
        ch, inf = CH.part_charts(V, F, face_sets[nm], kind=sp.kind, tx=tx, ref=sp.ref, cut_dir=sp.cut_dir)
        per_part[nm] = ch
        info[nm] = inf
    for b, a in partners.items():
        per_part[b] = MR.copy_layout(per_part[a], len(face_sets[b]), corrs[b])
        info[b] = {**info[a], "mirror_of": a}
    order, uvs = [], []
    for nm in parts:
        for k, (kind, fl, uv) in enumerate(per_part[nm]):
            order.append((nm, k, kind, fl))
            uvs.append(uv)
    packed = PK.fit_density(uvs, tx, atlas_max=atlas_max, gutter=gutter)
    faces_l, uv_l, chart_l, partof_l, charts_meta = [], [], [], [], []
    for ci, ((nm, k, kind, fl), uvt) in enumerate(zip(order, packed["uv"])):
        faces_l.append(face_sets[nm][fl])
        uv_l.append(uvt)
        chart_l.append(np.full(len(fl), ci, np.int64))
        partof_l.append(np.full(len(fl), parts.index(nm), np.int64))
        x0, y0 = (int(v) for v in packed["offsets"][ci])
        w, h = packed["boxes"][ci]
        charts_meta.append({"chart": ci, "part": nm, "index_in_part": k, "kind": kind, "faces": int(len(fl)),
                            "x0": x0, "y0": y0, "w": int(w), "h": int(h)})
    lay = Layout(faces=np.concatenate(faces_l), uv=np.concatenate(uv_l), chart=np.concatenate(chart_l),
                 part_of=np.concatenate(partof_l), parts=parts, charts=charts_meta, width=packed["width"],
                 height=packed["height"], tx_per_mm=packed["tx_per_mm"], tx_requested=float(tx_per_mm),
                 partners=partners, face_corr=corrs, info=info)
    lay.report = CK.a1_report(V, F, lay.faces, lay.uv, lay.tx_per_mm,
                              partners=mirror_identity(lay, per_part, V, F, face_sets))
    return lay


def mirror_identity(lay: Layout, per_part: dict, V: np.ndarray, F: np.ndarray, face_sets: dict) -> dict:
    """Per partner part B of A: (1) geometry: every corner of B is the reflection (x -> -x) of its corresponding
    corner of A (max error mm); (2) index identity on the matched faces: B's chart-local uv equal A's with u negated at
    every corresponding corner; vertex identity on the re-triangulated faces: each corner's uv equals (u negated) that
    of every A quad corner at the same point (max |difference| in texels, 0 up to float rounding); (3) every face of
    B is in exactly one chart."""
    out = {}
    mirror_x = np.array([-1.0, 1.0, 1.0])
    for b, a in lay.partners.items():
        corr = lay.face_corr[b]
        fa_g, fb_g = face_sets[a], face_sets[b]
        ua = {}
        for kind, fl, uv in per_part[a]:
            for row, f in enumerate(fl):
                ua[int(f)] = uv[row]
        geo, worst, seen = 0.0, 0.0, np.zeros(len(fb_g), np.int64)
        for kind, fl, uv in per_part[b]:
            seen[fl] += 1
            for row, f in enumerate(fl):
                ia = int(corr["face_of_b"][f])
                Pb = V[F[fb_g[f]]]
                if ia >= 0:
                    pa = corr["perm"][f]
                    src_uv = ua[ia][pa]
                    src_P = V[F[fa_g[ia]]][pa]
                else:
                    opts = corr["quad"][int(f)]
                    src_uv = np.stack([ua[o[0][0]][o[0][1]] for o in opts])
                    src_P = np.stack([V[F[fa_g[o[0][0]]]][o[0][1]] for o in opts])
                    for c, o in enumerate(opts):
                        for (a2, c2) in o:
                            worst = max(worst, float(np.abs(ua[a2][c2] - src_uv[c]).max()) * lay.tx_per_mm)
                geo = max(geo, float(np.abs(Pb - src_P * mirror_x).max()))
                worst = max(worst, float(np.abs(uv[row] - src_uv * np.array([-1.0, 1.0])).max()) * lay.tx_per_mm)
        out[b] = {"source": a, "faces": int(len(fb_g)), "index_copied": int(corr["matched"]),
                  "retriangulated_vertex_copied": int(corr["retriangulated"]), "geometry_max_err_mm": geo,
                  "max_abs_texel": worst, "every_face_once": bool(np.all(seen == 1)),
                  "pass": bool(worst < 1e-6 and geo <= MR.MIRROR_TOL_MM and np.all(seen == 1))}
    return out


__all__ = ["layout", "Layout", "PartSpec", "default_spec", "mirror_identity"]
