"""Rigid groups and facing regions (WP5_DESIGN §5.1), used by the print bake.

Ported from the scratch `refine.sample_surface`, `geomlib.sample_bary`, `design_reg/p2_parallax.stripe_masks` and
`p5_temple_hinge.group/hinge`; glasses3d/plan/archive/20261019/PORT_REVIEW.md section 10.

**Sides come from the x sign, never from part names.** q001 names its temples by the VIEWER's side (Temple_Left is at
-x, the wearer's right), so every region carries ``side`` from its geometry and a name tag
``[+x]`` / ``[-x]``; nothing here reads 'Left' or 'Right'.

- **Rigid groups.** The front piece, plus one temple group per x sign (the WP1 rule, geometry at |x| > 45 mm): the
  candidates are the parts whose area centroid lies at s x > ``TEMPLE_X_MM``; the temple is the candidate with the
  longest z extent; the group is the temple plus every candidate whose z range lies at least half behind the temple's
  front end (q001: Temple, Temple_hinge, Hinge_screw, T_arm; the Front_hinge and the T_return ornament stay with the
  front, 0 % of their z range behind the temple's front end). The hinge axis is vertical (+y) through the centroid of
  the group's vertices within ``HINGE_SLAB_MM`` of its front end (q001: 0.38 mm from the hinge screw's centre in x-z).
- **Regions** = each selected part split by facing class: the nearest of the six axes to each face normal; +z front,
  -z back, +y top, -y bottom, +/-x named ``outer`` (away from x = 0 on the face's side) or ``inner``. Parts spanning
  both sides are also split by face-centroid x sign, so opposite ends can choose separate source photos. Regions smaller
  than ``MIN_REGION_MM2`` are kept but marked ``minor`` (never registered).

Step 0's registration was deleted on 2026-10-13 (tag ``review-20261013-pre-cleanup``). Its unused stripe, fold and
sample computation was removed in the 2026-10-19 cleanup (tag ``pre-cleanup-20261019``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TEMPLE_X_MM = 45.0
HINGE_SLAB_MM = 2.0
MIN_REGION_MM2 = 100.0              # end caps (q001: 17-80 mm^2) are not registered
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
    minor: bool = False


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


def facing_class(normals: np.ndarray, side: int | np.ndarray) -> np.ndarray:
    """Nearest axis per face, with a scalar or per-face side (outer = away from x = 0)."""
    dots = np.stack([normals @ a for _, a in AXES], 1)
    k = dots.argmax(1)
    sides = np.broadcast_to(np.asarray(side), k.shape)
    sides = np.where(sides == 0, 1, sides)
    names = np.asarray([nm for nm, _a in AXES], dtype=object)[k]
    xface = k >= 4
    names[xface] = np.where(np.where(k[xface] == 4, 1, -1) * sides[xface] > 0, "outer", "inner")
    return names


def build_regions(job, parts, groups: list[Group] | None = None, *,
                  min_area_mm2: float = MIN_REGION_MM2) -> list[Region]:
    """Regions of the selected parts (names), split by facing class and side."""
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
        face_side = np.sign(V[F[faces_p], 0].mean(1)).astype(int)
        split_sides = np.any(face_side < 0) and np.any(face_side > 0)
        sides = sorted(set(face_side.tolist())) if split_sides else [side]
        if not split_sides:
            face_side[:] = side
        cls = facing_class(job.face_normals[faces_p], face_side)
        for side, facing in ((s, f) for s in sides for f in ("front", "back", "top", "bottom", "outer", "inner")):
            fc = faces_p[(cls == facing) & (face_side == side)]
            if len(fc) == 0:
                continue
            a = float(area[fc].sum())
            r = Region(rid=len(regions), name=f"{names[p]}[{side_tag(side)}]/{facing}", part=p, part_name=names[p],
                       side=side, facing=facing, group=group_of_part(groups, p), faces=fc, area_mm2=a,
                       minor=a < min_area_mm2)
            regions.append(r)
    return regions
