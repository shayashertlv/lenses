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
- **Step 6 (2026-10-07, the light marks on the +x endpiece block): thin light lines** (``thin_light_lines``). The
  photo's glint detector (``metal.thin_lines``: >= 25 levels over a 0.4 mm opening, elongated >= 1 mm, its shoulder
  >= 12.5) leaves the studio's dimmer glint remnants along curved bevels: broken rows of light, achromatic texels
  10-25 levels over their surroundings, which the AR renders as thin light marks (the +x block's top band at the
  front pose and at pitch +20; white top-hat > 8 L* at 5 px: 60 / 77 AR pixels with the step-5 print against Astra's
  34 / 26). In the texel domain, per painting view, a painted texel whose luminance is >= ``THIN_EXCESS`` levels over
  its masked opening at radius ``THIN_HALF_MM`` (only features narrower than about 0.4 mm survive: a honey patch does
  not) and whose blue rises with its red (``THIN_ACHROMATIC``: white light adds to every channel; an amber fleck
  raises red only) is a highlight: a hole, filled later (``thin=False`` turns the rule off).

Assembly M4c passes each painting view's geometry-bound colour policy. For general palettes, thin highlights must
raise all three channels comparably; bright neutral pigment needs local contrast before the absolute specular rule
may remove it. The supported warm branch retains the accepted historical calculation exactly.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import metal as MT
from . import signals as S

BRIGHTER_BY = 25.0
DILATE = 1
LOCAL_EXCESS = 15.0              # correction round: a witness flag must also be a local highlight in its own view
LOCAL_SIGMA_MM = 0.5
THIN_HALF_MM = 0.2               # step 6: thin light lines narrower than about 2 x this ...
THIN_EXCESS = 10.0               # ... at least this many levels over their masked opening ...
THIN_ACHROMATIC = 0.6            # ... with a blue rise >= 0.6 x the red one


def thin_light_lines(tex, painted: np.ndarray, col: np.ndarray, label: np.ndarray, tx: float, *,
                     colour_policies=None) -> np.ndarray:
    """(n,) bool: painted texels on thin, light, achromatic lines of their own painting view (step 6; module
    docstring): luminance >= ``THIN_EXCESS`` over the masked opening (disk radius ``THIN_HALF_MM`` x ``tx``) of that
    view's painted texels, and a blue rise >= ``THIN_ACHROMATIC`` x the red rise over the same opening."""
    f = np.asarray(col, float)
    out = np.zeros(len(f), bool)
    fp = MT._disk(max(1, int(round(THIN_HALF_MM * float(tx)))))
    lab = np.asarray(label)
    for li in np.unique(lab[painted]):
        sel = painted & (lab == li)
        M = tex.image(sel, False, bool)
        ch = [tex.image(np.where(sel, f[:, c], 0.0), 0.0, float) for c in range(3)]
        L = (ch[0] + ch[1] + ch[2]) / 3.0
        tw = (L - MT._masked_open(L, M, fp))[tex.ii, tex.jj]
        tR = (ch[0] - MT._masked_open(ch[0], M, fp))[tex.ii, tex.jj]
        tB = (ch[2] - MT._masked_open(ch[2], M, fp))[tex.ii, tex.jj]
        if S.general_colour((colour_policies or {}).get(int(li))):
            tG = (ch[1] - MT._masked_open(ch[1], M, fp))[tex.ii, tex.jj]
            rise = np.stack([tR, tG, tB], -1)
            light = (rise.min(-1) > 0) & (rise.min(-1) >= THIN_ACHROMATIC * np.maximum(rise.max(-1), 1e-6))
        else:
            light = tB >= THIN_ACHROMATIC * np.maximum(tR, 1e-6)
        out |= sel & (tw >= THIN_EXCESS) & light
    return out


def flag(tex, painted: np.ndarray, col: np.ndarray, label: np.ndarray, witnesses: dict[int, list], *,
         tx: float | None = None, thin: bool = True, colour_policies=None) -> dict:
    """``col`` (n, 3) gained painted colours, ``label`` (n,) view index; ``witnesses[label]`` = [(view index, ok (n,),
    gained colours (n, 3))] of the other admitted views. ``tx`` (texels per mm; correction round): witness flags must
    also be local highlights (module docstring); None = the step-4 rule. ``thin`` (step 6, needs ``tx``): thin light
    achromatic lines are highlights too. Returns {'mask', 'specular', 'witness', 'dilated', 'witness_not_local',
    'thin_light_line'}."""
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
    general = np.zeros(len(f), bool)
    for li, policy in (colour_policies or {}).items():
        if S.general_colour(policy):
            general |= painted & (np.asarray(label) == li)
    if general.any():
        if tx is None or not np.isfinite(tx) or tx <= 0:
            raise ValueError("general-colour highlight detection requires positive texels per mm")
        # Bright grey can be pigment. The absolute bright/neutral rule needs local light evidence on these
        # palettes, rather than deleting a whole phase of the print solely for being achromatic.
        spec &= ~general | local
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
    thin_m = (thin_light_lines(tex, painted, f, label, tx, colour_policies=colour_policies)
              if (thin and tx is not None) else np.zeros(len(f), bool))
    m = spec | wit | thin_m
    img = tex.image(m, False, bool)
    pim = tex.image(painted, False, bool)
    dil = ndimage.binary_dilation(img, iterations=DILATE) & pim
    out = dil[tex.ii, tex.jj]
    return {"mask": out, "specular": int(spec.sum()), "witness": int((wit & ~spec).sum()),
            "dilated": int((out & ~m).sum()), "witness_not_local": int((not_local & ~spec).sum()),
            "thin_light_line": int((thin_m & ~spec & ~wit).sum()),
            "thin_rule": ({"half_mm": THIN_HALF_MM, "excess": THIN_EXCESS, "achromatic": THIN_ACHROMATIC}
                          if (thin and tx is not None) else None),
            "local_rule": None if tx is None else {"excess": LOCAL_EXCESS, "sigma_mm": LOCAL_SIGMA_MM}}
