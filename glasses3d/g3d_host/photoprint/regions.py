"""Rigid groups, regions, stripes and samples (WP5_DESIGN §5.1), built for step 0 and used by the bake.

Ported from the scratch `refine.sample_surface`, `geomlib.sample_bary`, `design_reg/p2_parallax.stripe_masks` and
`p5_temple_hinge.group/hinge`; PORT_REVIEW section 10.

**Sides come from the x sign, never from part names.** q001 names its temples by the VIEWER's side (Temple_Left is at
-x, the wearer's right), so every region carries ``side`` = sign of its part's area centroid x and a name tag
``[+x]`` / ``[-x]``; nothing here reads 'Left' or 'Right'.

- **Rigid groups.** The front piece, plus one temple group per x sign (the WP1 rule, geometry at |x| > 45 mm): the
  candidates are the parts whose area centroid lies at s x > ``TEMPLE_X_MM``; the temple is the candidate with the
  longest z extent; the group is the temple plus every candidate whose z range lies at least half behind the temple's
  front end (q001: Temple, Temple_hinge, Hinge_screw, T_arm; the Front_hinge and the T_return ornament stay with the
  front, 0 % of their z range behind the temple's front end). The hinge axis is vertical (+y) through the centroid of
  the group's vertices within ``HINGE_SLAB_MM`` of its front end (q001: 0.38 mm from the hinge screw's centre in x-z).
- **Regions** = each selected part split by facing class: the nearest of the six axes to each face normal; +z front,
  -z back, +y top, -y bottom, +/-x named ``outer`` (away from x = 0 on the part's side) or ``inner``. Regions smaller
  than ``MIN_REGION_MM2`` are kept but marked ``minor`` (never registered).
- **Stripes** along the part's longest bounding-box axis (|x| for brows, |z| for temples): 4 mm wide, 0.5 mm guard band
  at each boundary; even stripes = fold A, odd = fold B (print correlation length 1.04 mm [R P3]).
- **Samples**: area-uniform (face, barycentric) pairs per region with a fixed seed. A sample is a BODY point: its
  position on any deformed surface S' (same triangles) is the barycentric combination of S' vertices.

The bake (``project``) uses the groups and regions. Step 0's registration, which read the stripes, folds and samples,
was deleted on 2026-10-13 with its helpers here (``Samples.select``, ``pixel_fold``, the ``to_json`` methods) and the
unused ``Region.s_of`` / ``region_face_lookup``; see git tag review-20261013-pre-cleanup. ``build_regions`` is
unchanged and still draws the samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TEMPLE_X_MM = 45.0
HINGE_SLAB_MM = 2.0
STRIPE_MM = 4.0
GUARD_MM = 0.5
MIN_REGION_MM2 = 100.0              # end caps (q001: 17-80 mm^2) are not registered
FOLD_A, FOLD_B, FOLD_GUARD = 0, 1, -1
AXES = (("front", np.array([0.0, 0.0, 1.0])), ("back", np.array([0.0, 0.0, -1.0])),
        ("top", np.array([0.0, 1.0, 0.0])), ("bottom", np.array([0.0, -1.0, 0.0])),
        ("+x", np.array([1.0, 0.0, 0.0])), ("-x", np.array([-1.0, 0.0, 0.0])))


def side_tag(side: int) -> str:
    return "+x" if side > 0 else ("-x" if side < 0 else "0x")


@dataclass(eq=False)
class Group:
    name: str                       # 'front', 'temple-x', 'temple+x'
    side: int                       # 0 for the front, -1 / +1 for a temple
    parts: list[int]
    face_mask: np.ndarray           # (M,) bool
    vidx: np.ndarray                # vertex ids
    pivot_mm: np.ndarray            # hinge pivot (temples), (3,)
    axis: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0, 0.0]))


@dataclass(eq=False)
class Region:
    rid: int
    name: str
    part: int
    part_name: str
    side: int
    facing: str
    group: str
    faces: np.ndarray               # face ids
    area_mm2: float
    axis: int                       # 0/1/2: stripe and parallax axis (the part's longest bbox side)
    vidx: np.ndarray                # vertex ids of the region's faces
    s_vert: np.ndarray              # parallax coordinate of those vertices in [-1, 1]
    s_mid: float = 0.0
    s_half: float = 1.0
    minor: bool = False
    samples: "Samples | None" = None


@dataclass(eq=False)
class Samples:
    face: np.ndarray                # (N,) int
    bary: np.ndarray                # (N, 3) float64
    fold: np.ndarray                # (N,) int8: FOLD_A / FOLD_B / FOLD_GUARD
    coord: np.ndarray               # (N,) stripe coordinate (mm, |axis|)

    def __len__(self) -> int:
        return len(self.face)


def part_centroids(V: np.ndarray, F: np.ndarray, part: np.ndarray, area: np.ndarray, n_parts: int) -> np.ndarray:
    c = V[F].mean(1)
    out = np.zeros((n_parts, 3))
    for p in range(n_parts):
        m = part == p
        out[p] = (c[m] * area[m, None]).sum(0) / max(area[m].sum(), 1e-12)
    return out


def rigid_groups(job, *, temple_x_mm: float = TEMPLE_X_MM, hinge_slab_mm: float = HINGE_SLAB_MM) -> list[Group]:
    V, F, part = job.mesh.V, job.mesh.F, job.mesh.part
    n_parts = len(job.mesh.names)
    cen = part_centroids(V, F, part, job.face_area, n_parts)
    lo = np.array([V[np.unique(F[part == p])].min(0) for p in range(n_parts)])
    hi = np.array([V[np.unique(F[part == p])].max(0) for p in range(n_parts)])
    temple_parts: set[int] = set()
    groups = []
    for side in (-1, 1):
        cand = [p for p in range(n_parts) if side * cen[p, 0] > temple_x_mm]
        if not cand:
            continue
        temple = max(cand, key=lambda p: hi[p, 2] - lo[p, 2])
        zt = hi[temple, 2]
        members = []
        for p in cand:
            ext = max(hi[p, 2] - lo[p, 2], 1e-9)
            behind = (min(hi[p, 2], zt) - lo[p, 2]) / ext
            if p == temple or behind >= 0.5:
                members.append(p)
        fm = np.isin(part, members)
        vidx = np.unique(F[fm])
        slab = vidx[V[vidx, 2] >= V[vidx, 2].max() - hinge_slab_mm]
        pivot = V[slab].mean(0)
        groups.append(Group(name=f"temple{side_tag(side)}", side=side, parts=sorted(members), face_mask=fm,
                            vidx=vidx, pivot_mm=pivot))
        temple_parts.update(members)
    front_parts = [p for p in range(n_parts) if p not in temple_parts]
    fm = np.isin(part, front_parts)
    groups.insert(0, Group(name="front", side=0, parts=front_parts, face_mask=fm, vidx=np.unique(F[fm]),
                           pivot_mm=np.zeros(3)))
    return groups


def group_of_part(groups: list[Group], p: int) -> str:
    for g in groups:
        if p in g.parts:
            return g.name
    return "front"


def facing_class(normals: np.ndarray, side: int) -> np.ndarray:
    """Per face: the nearest of the six axes, named front/back/top/bottom/outer/inner (outer = away from x = 0)."""
    dots = np.stack([normals @ a for _, a in AXES], 1)
    k = dots.argmax(1)
    names = []
    for _, (nm, _a) in enumerate(AXES):
        if nm in ("+x", "-x"):
            sgn = 1 if nm == "+x" else -1
            names.append("outer" if sgn * (side if side else 1) > 0 else "inner")
        else:
            names.append(nm)
    return np.asarray(names, dtype=object)[k]


def stripe_fold(coord: np.ndarray, stripe_mm: float = STRIPE_MM, guard_mm: float = GUARD_MM) -> np.ndarray:
    """FOLD_A for even 4 mm stripes, FOLD_B for odd, FOLD_GUARD within ``guard_mm`` of a stripe boundary."""
    c = np.abs(np.asarray(coord, float)) / stripe_mm
    k = np.floor(c).astype(np.int64)
    frac = c - k
    g = guard_mm / stripe_mm
    inner = (frac > g) & (frac < 1.0 - g)
    out = np.full(c.shape, FOLD_GUARD, np.int8)
    out[inner & (k % 2 == 0)] = FOLD_A
    out[inner & (k % 2 == 1)] = FOLD_B
    return out


def build_regions(job, parts, groups: list[Group] | None = None, *, n_samples: int = 30000, seed: int = 0,
                  min_area_mm2: float = MIN_REGION_MM2) -> list[Region]:
    """Regions of the selected parts (names), each with ``n_samples`` area-uniform samples (fixed seed)."""
    names = job.mesh.names
    groups = groups if groups is not None else rigid_groups(job)
    ids = job.part_ids(parts)
    missing = sorted(set(parts) - {names[i] for i in ids})
    if missing:
        raise ValueError(f"parts {missing} are not in the mesh")
    V, F, part = job.mesh.V, job.mesh.F, job.mesh.part
    area = job.face_area
    cen = part_centroids(V, F, part, area, len(names))
    regions: list[Region] = []
    for p in ids:
        side = int(np.sign(cen[p, 0])) or 1
        faces_p = np.nonzero(part == p)[0]
        vp = V[np.unique(F[faces_p])]
        axis = int(np.argmax(vp.max(0) - vp.min(0)))
        cls = facing_class(job.face_normals[faces_p], side)
        for facing in ("front", "back", "top", "bottom", "outer", "inner"):
            fc = faces_p[cls == facing]
            if len(fc) == 0:
                continue
            vidx = np.unique(F[fc])
            x = V[vidx, axis]
            mid, half = (x.min() + x.max()) / 2, max((x.max() - x.min()) / 2, 1e-6)
            a = float(area[fc].sum())
            r = Region(rid=len(regions), name=f"{names[p]}[{side_tag(side)}]/{facing}", part=p, part_name=names[p],
                       side=side, facing=facing, group=group_of_part(groups, p), faces=fc, area_mm2=a, axis=axis,
                       vidx=vidx, s_vert=(x - mid) / half, s_mid=float(mid), s_half=float(half),
                       minor=a < min_area_mm2)
            r.samples = sample_region(job, r, n_samples, seed)
            regions.append(r)
    return regions


def sample_region(job, r: Region, n: int, seed: int) -> Samples:
    rng = np.random.default_rng([int(seed), int(r.rid), 5])
    a = job.face_area[r.faces]
    f = rng.choice(r.faces, size=n, p=a / a.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    w = np.stack([1 - s, s * (1 - r2), s * r2], 1)
    P = np.einsum("kj,kjd->kd", w, job.mesh.V[job.mesh.F[f]])
    coord = np.abs(P[:, r.axis])
    return Samples(face=f, bary=w, fold=stripe_fold(coord), coord=coord)
