"""The mesh a camera is fitted to: a GLB (metres) as one triangle soup in model millimetres, with per-face part ids
(WP3b; replaces the prototype's glbload + inline assembly in make_uv_test.py:25-39; ported from the pre-WP0
prototype, deleted 2026-10-13; tag review-20261013-pre-cleanup).

- Every triangle primitive becomes one PART, in the GLB's scene traversal order (``glb.load``), its world-space
  positions x 1000 (metres -> mm) and its indices offset into one vertex array. ``names[i]`` and ``node_names[i]``
  preserve the exact object name; ``Temple`` and ``Temple.001`` are distinct objects.
- ``skip``: parts whose node name contains any of these substrings are left out (the prototype skipped
  caller-supplied substrings, such as thin surface decals).
- ``frame``: given (e.g. from a fits.json) or ``NormFrame.from_bbox`` of the kept vertices.
- ``front_pivot``: the camera parametrisation's pivot (``cameras.ViewFit``): the bounding-box centre of the named
  front parts (the prototype's rule; every name must exist) or, without names, of the faces within ``FRONT_DEPTH_MM``
  of the front-most vertex (new code; on q001 it lands within 1 mm of the named pivot, tests/test_fitmesh.py).

Reproduction-specific skip lists and exact part names live in test fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import glb as glbmod
from . import raster, lens_id
from .camera import NormFrame

FRONT_DEPTH_MM = 12.0        # geometric front piece (no names given): faces whose centroid is this close to the front
M_TO_MM = 1000.0


def part_name(node_name: str) -> str:
    """An object's exact name is its identity, including Blender duplicate suffixes."""
    return node_name


@dataclass
class FitMesh:
    V: np.ndarray                 # (N, 3) float64 model millimetres
    F: np.ndarray                 # (M, 3) int64 indices into V
    part: np.ndarray              # (M,) int64 part id per face
    names: list[str]              # per part: exact node name
    node_names: list[str]         # per part: full node name
    materials: list[int | None]   # per part: glTF material index
    frame: NormFrame
    skipped: list[str] = field(default_factory=list)   # node names left out by ``skip``
    node_indices: list[int] = field(default_factory=list)  # exact GLB object identity per primitive
    lens_parts: list[bool] = field(default_factory=list)   # shared lens classifier, before any skipped geometry
    part_roles: list[str | None] = field(default_factory=list)
    # Data derived from this object's arrays (pose.py part boxes and temple layout; a posed copy's hull rows and
    # scene). FitMesh arrays are never modified in place; dataclasses.replace gives a copy an empty cache.
    cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __getstate__(self):
        return {**self.__dict__, "cache": {}}   # a ray-casting scene does not pickle; copies rebuild on demand

    def scene(self) -> raster.RasterScene:
        """The content-cached scene. A posed copy (``pose.posed``) builds its own once, with hull rows taken from the
        authored parts' hulls, instead of hashing and re-hulling the whole mesh for every trial pose."""
        rows = self.cache.get("hull_rows")
        if rows is None:
            return raster.get_scene(self.V, self.F, self.frame)
        if "scene" not in self.cache:
            self.cache["scene"] = raster.RasterScene(self.V, self.F, self.frame, hull_rows=rows)
        return self.cache["scene"]

    def part_ids(self, names) -> list[int]:
        """Every part whose exact ``names`` entry is in ``names``. Unlike the prototype
        (``names.index``: the FIRST primitive only), a node with several primitives contributes all of them; on q001
        every name is one primitive, so the two agree there."""
        want = set(names)
        return [i for i, n in enumerate(self.names) if n in want]

    def front_pivot(self, part_names=None, front_depth_mm: float = FRONT_DEPTH_MM) -> np.ndarray:
        """Normalized pivot: bbox centre (mm) of the named front parts' vertices, or of the geometric front piece.

        Every requested name must exist (the prototype's ``names.index`` raised on any missing name): a silently
        smaller set would move the pivot, and with it the optimizer's parametrisation and Powell boxes."""
        if part_names is not None:
            missing = sorted(set(part_names) - set(self.names))
            if missing:
                raise ValueError(f"front parts {missing} are not in the mesh (parts: {sorted(set(self.names))})")
            ids = self.part_ids(part_names)
            sel = np.isin(self.part, ids)
        else:
            zc = self.V[self.F].mean(axis=1)[:, 2]
            sel = zc >= float(self.V[:, 2].max()) - front_depth_mm
        fpV = self.V[np.unique(self.F[sel])]
        return self.frame.to_norm(((fpV.min(0) + fpV.max(0)) / 2)[None])[0]


def load_fit_mesh(path: str | Path, *, skip=(), frame: NormFrame | None = None,
                  scale_to_mm: float = M_TO_MM) -> FitMesh:
    """Load a GLB for camera fitting. Raises on compressed geometry (Draco/meshopt is not decoded) and when nothing is
    left; non-triangle primitives (lines, points) are skipped like ``skip`` matches and listed in ``skipped``."""
    g = glbmod.load(path)
    Vs, Fs, parts, names, node_names, mats, skipped = [], [], [], [], [], [], []
    refs = []
    for i, p in enumerate(g.primitives):
        material = g.material(p.material_index) or {}
        refs.append(lens_id.PrimitiveRef(str(i), p.node_name, g.nodes[p.node_index].get('extras'),
                    g.meshes[p.mesh_index].get('extras'), material.get('name'), material.get('extras'), material))
    decisions, _ = lens_id.classify(refs)
    node_indices, lens_parts, roles = [], [], []
    off = 0
    for primitive_index, p in enumerate(g.primitives):
        if any(s in p.node_name for s in skip):
            skipped.append(p.node_name)
            continue
        if p.compressed:
            raise ValueError(f"{path}: primitive of node {p.node_name!r} uses {p.compressed}; decode it first")
        if p.mode != 4 or len(p.triangles) == 0:
            skipped.append(p.node_name)
            continue
        i = len(names)
        Vs.append(p.positions * scale_to_mm)
        Fs.append(p.triangles + off)
        off += len(p.positions)
        parts.append(np.full(len(p.triangles), i))
        names.append(part_name(p.node_name))
        node_names.append(p.node_name)
        mats.append(p.material_index)
        node_indices.append(p.node_index)
        lens_parts.append(decisions[str(primitive_index)].is_lens)
        roles.append((g.nodes[p.node_index].get('extras') or {}).get('partRole'))
    if not Vs:
        raise ValueError(f"{path}: no triangle geometry left after skipping {list(skip)}")
    V = np.vstack(Vs)
    F = np.vstack(Fs)
    part = np.concatenate(parts)
    fr = frame if frame is not None else NormFrame.from_bbox(V)
    return FitMesh(V=V, F=F, part=part, names=names, node_names=node_names, materials=mats, frame=fr,
                   skipped=skipped, node_indices=node_indices, lens_parts=lens_parts, part_roles=roles)
