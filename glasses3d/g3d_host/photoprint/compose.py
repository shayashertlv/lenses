"""WP5 step 7: compose with the byte-identity guarantee (WP5_DESIGN §6.7, A3) and render a bake at a camera.

``compose``: ``final = where(protected, P0, edited)`` on uint8, where P0 is the projected photo layer (gained,
highlight-free photo texels) and ``protected`` = the photo texels no step was scheduled to edit (not in a seam band,
not a highlight, not text to be filled, not a chart-seam pair that had to change). Checked: every protected texel is
byte-identical to P0. MultiBandBlender-style whole-image copies are never made (C: they moved pixels > 100 px from a
seam by up to 6 levels); every PatchMatch call is separately checked to change nothing outside its hole and to change
its hole (``fill.patchmatch_call``).

``render_bake``: the baked atlas at a view's pixels of the given faces (first hit through ``cam`` on S'), uv by the
layout's per-corner coordinates, bilinear in the atlas (texel (i, j) centre at (j + 0.5, i + 0.5)).
"""
from __future__ import annotations

import numpy as np

from .. import texture_utils as T
from .pose import bary3

CLASS_CODES = {"empty": 0, "photo_tier1": 1, "photo_anchor": 2, "mirror": 3, "patchmatch": 4, "seam_band": 5,
               "highlight_fill": 6, "text_fill": 7, "chart_seam": 8, "padding": 9, "no_texture": 10}


def compose(P0: np.ndarray, protected: np.ndarray, edited: np.ndarray) -> tuple[np.ndarray, dict]:
    P0 = np.asarray(P0, np.uint8)
    ed = np.clip(np.rint(np.asarray(edited, float)), 0, 255).astype(np.uint8) if edited.dtype != np.uint8 else edited
    prot = np.asarray(protected, bool)
    final = np.where(prot[..., None], P0, ed)
    ident = bool(np.array_equal(final[prot], P0[prot]))
    return final, {"protected_texels": int(prot.sum()), "protected_byte_identical": ident,
                   "edited_texels_differing_from_P0": int(((final != P0).any(-1) & ~prot).sum())}


def render_bake(job, layout, atlas: np.ndarray, view: str, cam, V: np.ndarray | None = None, faces=None,
                scene=None) -> dict:
    """{'ren' (H, W, 3), 'mask' (H, W): pixels whose first hit is a layout face (or one of ``faces``), 'face'}."""
    v = job.views[view]
    H, W = v.shape
    V = job.mesh.V if V is None else V
    sc = scene if scene is not None else job.scene(V)
    r = sc.render(cam, (H, W), 1, want_bary=True)
    fid = r["face_id"]
    lrow = layout.face_index(len(job.mesh.F))
    hit = (fid >= 0) & (fid < len(lrow))                # decal faces of an occlusion scene are not layout faces
    rowimg = np.full((H, W), -1, np.int64)
    rowimg[hit] = lrow[fid[hit]]
    m = rowimg >= 0
    if faces is not None:
        keep = np.zeros(len(job.mesh.F) + 1, bool)
        keep[np.asarray(faces)] = True
        m &= keep[np.where(hit, fid, -1)]
    ii, jj = np.nonzero(m)
    rows = rowimg[ii, jj]
    b = bary3(r["bary"][ii, jj])
    uv = np.einsum("kj,kjd->kd", b, layout.uv[rows])
    col = T.sample_bilinear(atlas.astype(np.float32), uv[:, 0] - 0.5, uv[:, 1] - 0.5)
    ren = np.zeros((H, W, 3))
    ren[ii, jj] = col
    face = np.full((H, W), -1, np.int64)
    face[ii, jj] = fid[ii, jj]
    return {"ren": ren, "mask": m, "face": face, "uv": uv, "ii": ii, "jj": jj}
