"""The mesh a camera is fitted to: a GLB (metres) as one triangle soup in model millimetres, with per-face part ids
(WP3b; replaces the prototype's glbload + inline assembly in make_uv_test.py:25-39; ported from the pre-WP0
prototype, deleted 2026-10-13; tag review-20261013-pre-cleanup).

- Every triangle primitive becomes one PART, in the GLB's scene traversal order (``glb.load``), its world-space
  positions x 1000 (metres -> mm) and its indices offset into one vertex array. ``names[i]`` strips only Blender's
  three-digit duplicate suffix (e.g. ".001"); embedded periods remain. ``node_names[i]`` is the full node name.
- ``skip``: parts whose node name contains any of these substrings are left out (the prototype skipped
  ``PROTOTYPE_SKIP``: the FT1119 brand print, lettering and medallion, thin decals on the surface).
- ``frame``: given (e.g. from a fits.json) or ``NormFrame.from_bbox`` of the kept vertices.
- ``front_pivot``: the camera parametrisation's pivot (``cameras.ViewFit``): the bounding-box centre of the named
  front parts (the prototype's rule; every name must exist) or, without names, of the faces within ``FRONT_DEPTH_MM``
  of the front-most vertex (new code; on q001 it lands within 1 mm of the named pivot, tests/test_fitmesh.py).

With the prototype's skip list and front-part names, V, F, part and the pivot are bit-identical to make_uv_test.py's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import glb as glbmod
from . import raster
from .camera import NormFrame

PROTOTYPE_SKIP = ("inner_brand_print", "TOM_FORD", "medallion")
PROTOTYPE_FRONT_PARTS = ("Brow_Left", "Brow_Right", "Eyewire_Left", "Eyewire_Right", "Lens_Left", "Lens_Right",
                         "Saddle_bridge")
FRONT_DEPTH_MM = 12.0        # geometric front piece (no names given): faces whose centroid is this close to the front
M_TO_MM = 1000.0


def part_name(node_name: str) -> str:
    """Preserve product names containing periods; strip only a Blender duplicate suffix."""
    base, dot, suffix = node_name.rpartition('.')
    return base if dot and base and len(suffix) == 3 and suffix.isascii() and suffix.isdigit() else node_name


@dataclass
class FitMesh:
    V: np.ndarray                 # (N, 3) float64 model millimetres
    F: np.ndarray                 # (M, 3) int64 indices into V
    part: np.ndarray              # (M,) int64 part id per face
    names: list[str]              # per part: node name without a Blender duplicate suffix
    node_names: list[str]         # per part: full node name
    materials: list[int | None]   # per part: glTF material index
    frame: NormFrame
    skipped: list[str] = field(default_factory=list)   # node names left out by ``skip``

    def scene(self) -> raster.RasterScene:
        return raster.get_scene(self.V, self.F, self.frame)

    def part_ids(self, names) -> list[int]:
        """Every part whose normalized ``names`` entry is in ``names``. Unlike the prototype
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
    off = 0
    for p in g.primitives:
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
    if not Vs:
        raise ValueError(f"{path}: no triangle geometry left after skipping {list(skip)}")
    V = np.vstack(Vs)
    F = np.vstack(Fs)
    part = np.concatenate(parts)
    fr = frame if frame is not None else NormFrame.from_bbox(V)
    return FitMesh(V=V, F=F, part=part, names=names, node_names=node_names, materials=mats, frame=fr,
                   skipped=skipped)
