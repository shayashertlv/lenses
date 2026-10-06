"""WP5 step 3b: highlights are DETECTED AND REPLACED, never subtracted (WP5_DESIGN §6.3; AP1, AP4).

Ported in spirit from the scratch `aplib.py` rules (`shen_zheng` is not run: §6.3 runs it only behind a keep test on
registered overlaps, and single-source mode has none; reported as off).
- clipped photo pixels (any channel >= 250, dilated 1 px) are holes for their view (``texels.view_samples``);
- a painted texel is flagged by the specular rule (gained luminance > 175 and saturation < 60), or when it is more
  than 25 levels brighter than the darkest gained WITNESS (another admitted view that sees the texel as a donor) and
  less saturated than that witness. In single-source mode a flagged texel becomes a hole (filled later): taking the
  witness's value would mix unregistered views. Flags are dilated by 1 texel inside the painted set.
- **Correction round (2026-10-06, task item 2):** in single-source mode the witnesses are UNREGISTERED views (FT1119:
  print residual 1.9-2.4 mm against a print correlation length of about 1 mm), so "the same texel" in a witness is a
  different print point. Measured on the -x brow front (photo 05 paints it, 01 and 04 witness): of 2,390 witness-flagged
  texels only 22 % were brighter than their own photo's surroundings (> 15 levels over the 1 mm median; 5 % of the
  photo texels are), their median pixel luminance 56 with saturation 32 (honey print, not glare): the rule flagged
  print the witness saw darker because it was looking 2 mm away. A witness flag now also needs the texel to be a LOCAL
  highlight in its own painting view: its gained luminance >= ``LOCAL_EXCESS`` levels over the masked Gaussian mean
  (sigma ``LOCAL_SIGMA_MM``) of the painted texels around it. The specular rule is unchanged.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import signals as S

BRIGHTER_BY = 25.0
DILATE = 1
LOCAL_EXCESS = 15.0              # correction round: a witness flag must also be a local highlight in its own view
LOCAL_SIGMA_MM = 0.5


def flag(tex, painted: np.ndarray, col: np.ndarray, label: np.ndarray, witnesses: dict[int, list], *,
         tx: float | None = None) -> dict:
    """``col`` (n, 3) gained painted colours, ``label`` (n,) view index; ``witnesses[label]`` = [(view index, ok (n,),
    gained colours (n, 3))] of the other admitted views. ``tx`` (texels per mm; correction round): witness flags must
    also be local highlights (module docstring); None = the step-4 rule. Returns {'mask', 'specular', 'witness',
    'dilated', 'witness_not_local'}."""
    f = np.asarray(col, float)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    spec = painted & (lum > S.SPECULAR_LUM) & (sat < S.SPECULAR_SAT)
    local = np.ones(len(f), bool)
    if tx is not None:
        sg = LOCAL_SIGMA_MM * float(tx)
        pim = tex.image(painted, False, bool).astype(float)
        lim = tex.image(np.where(painted, lum, 0.0), 0.0, float)
        mean = ndimage.gaussian_filter(lim, sg) / np.maximum(ndimage.gaussian_filter(pim, sg), 1e-6)
        local = lum - mean[tex.ii, tex.jj] >= LOCAL_EXCESS
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
    not_local = wit & ~local
    wit &= local
    m = spec | wit
    img = tex.image(m, False, bool)
    pim = tex.image(painted, False, bool)
    dil = ndimage.binary_dilation(img, iterations=DILATE) & pim
    out = dil[tex.ii, tex.jj]
    return {"mask": out, "specular": int(spec.sum()), "witness": int((wit & ~spec).sum()),
            "dilated": int((out & ~m).sum()), "witness_not_local": int((not_local & ~spec).sum()),
            "local_rule": None if tx is None else {"excess": LOCAL_EXCESS, "sigma_mm": LOCAL_SIGMA_MM}}
