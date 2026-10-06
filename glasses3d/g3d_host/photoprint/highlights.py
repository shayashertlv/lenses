"""WP5 step 3b: highlights are DETECTED AND REPLACED, never subtracted (WP5_DESIGN §6.3; AP1, AP4).

Ported in spirit from the scratch `aplib.py` rules (`shen_zheng` is not run: §6.3 runs it only behind a keep test on
registered overlaps, and single-source mode has none; reported as off).
- clipped photo pixels (any channel >= 250, dilated 1 px) are holes for their view (``texels.view_samples``);
- a painted texel is flagged by the specular rule (gained luminance > 175 and saturation < 60), or when it is more
  than 25 levels brighter than the darkest gained WITNESS (another admitted view that sees the texel as a donor) and
  less saturated than that witness. In single-source mode a flagged texel becomes a hole (filled later): taking the
  witness's value would mix unregistered views. Flags are dilated by 1 texel inside the painted set.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import signals as S

BRIGHTER_BY = 25.0
DILATE = 1


def flag(tex, painted: np.ndarray, col: np.ndarray, label: np.ndarray, witnesses: dict[int, list]) -> dict:
    """``col`` (n, 3) gained painted colours, ``label`` (n,) view index; ``witnesses[label]`` = [(view index, ok (n,),
    gained colours (n, 3))] of the other admitted views. Returns {'mask', 'specular', 'witness', 'dilated'}."""
    f = np.asarray(col, float)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    spec = painted & (lum > S.SPECULAR_LUM) & (sat < S.SPECULAR_SAT)
    wit = np.zeros(len(f), bool)
    for li, ws in witnesses.items():
        sel = painted & (label == li)
        if not sel.any() or not ws:
            continue
        lmin = np.full(len(f), np.inf)
        smin = np.zeros(len(f))
        for _vi, ok, wc in ws:
            wl = np.asarray(wc, float).mean(-1)
            ws_ = np.asarray(wc, float).max(-1) - np.asarray(wc, float).min(-1)
            better = ok & (wl < lmin)
            lmin[better] = wl[better]
            smin[better] = ws_[better]
        wit |= sel & np.isfinite(lmin) & (lum > lmin + BRIGHTER_BY) & (sat < smin)
    m = spec | wit
    img = tex.image(m, False, bool)
    pim = tex.image(painted, False, bool)
    dil = ndimage.binary_dilation(img, iterations=DILATE) & pim
    out = dil[tex.ii, tex.jj]
    return {"mask": out, "specular": int(spec.sum()), "witness": int((wit & ~spec).sum()),
            "dilated": int((out & ~m).sum())}
