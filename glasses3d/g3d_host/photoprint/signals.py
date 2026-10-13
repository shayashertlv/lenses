"""Photo pixel masks of the WP5 bake (WP5_DESIGN §2 "Signal"): which photo pixels may carry print.

Ported from the scratch `wp5lib.py` (acetate_mask, source_valid); PORT_REVIEW section 10. Colours are sRGB 0..255.

- ``acetate_mask``: dark, or saturated and not bright (the probe's rule); ``valid_mask`` = that inside the matte eroded
  1 px: the photo pixels that may carry print (glare, metal and background excluded).
- ``SPECULAR_LUM`` / ``SPECULAR_SAT``: the bright achromatic (specular) colour limits shared by ``gains``,
  ``highlights`` and ``shading``.
- The assembled print tool fits a geometry-bound ``colour_policy`` first. Its supported warm mode retains those
  historical masks exactly. General mode admits unclipped pigment of any channel order, including neutral grey;
  geometry, hardware and spatial highlight filters still decide whether an admitted pixel may donate.

The registration signals of step 0 (luminance, warmth, irange, the masked DoG band images ``BandImages``, ``ncc``)
and the unused ``specular_mask`` were deleted on 2026-10-13 with the step-0 experiment; see git tag
review-20261013-pre-cleanup.
"""
from __future__ import annotations

import hashlib

import numpy as np
from scipy import ndimage

ACETATE_LUM_MAX = 170.0
ACETATE_SAT_MIN = 25.0
ACETATE_DARK = 90.0
SPECULAR_LUM = 175.0
SPECULAR_SAT = 60.0
COLOUR_POLICY_SCHEMA = "g3d.print_colour_policy/1"
WARM_SHARE_MIN = 0.9
COLOUR_MIN_SAMPLES = 150


def fit_colour_policy(image, support) -> dict:
    """Choose the historical warm model only when geometry-selected pigment samples support its channel order.

    The caller supplies eroded rendered-print support away from other parts/hardware. Sampling must precede the
    acetate rule: otherwise grey would be excluded before the model could learn that it is pigment. Only chromatic
    pixels can establish the red-to-blue ordering of a warm colour family; neutral darkness is not hue evidence.
    No model/part/photo name or existing texture influences this choice.
    """
    img = np.asarray(image, np.float64)
    sup = np.asarray(support, bool) & np.isfinite(img).all(-1) & ~(img >= 250).any(-1)
    lum = img.mean(-1)
    chroma = img.max(-1) - img.min(-1)
    informative = sup & (chroma > ACETATE_SAT_MIN)
    count = int(informative.sum())
    warm = (img[..., 0] >= img[..., 1]) & (img[..., 1] >= img[..., 2])
    share = float(warm[informative].mean()) if count else 0.0
    # Neutral pixels cannot distinguish actual grey pigment from reflections on warm pigment. Record that
    # ambiguity; do not use their prevalence to veto otherwise supported chromatic evidence.
    neutral = sup & (lum >= ACETATE_DARK) & (chroma <= ACETATE_SAT_MIN)
    bright = sup & (lum >= ACETATE_DARK)
    neutral_share = float(neutral.sum() / max(int(bright.sum()), 1))
    mode = "warm" if count >= COLOUR_MIN_SAMPLES and share >= WARM_SHARE_MIN else "general"
    return {"schema": COLOUR_POLICY_SCHEMA, "mode": mode, "samples": int(sup.sum()),
            "informative_samples": count, "warm_share": share, "neutral_share": neutral_share,
            "image_pixels_sha256": hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest(),
            "support_sha256": hashlib.sha256(np.packbits(sup).tobytes()).hexdigest(),
            "support_shape": list(sup.shape),
            "limitations": (["Warm-dominant mixed neutral pigment cannot be distinguished from achromatic glare "
                             "by this colour model; the historical warm masks are retained."]
                            if mode == "warm" and neutral.any() else []),
            "reason": "supported warm pigment ordering" if mode == "warm" else
                      "neutral, non-warm, mixed, or insufficient warm evidence"}


def general_colour(policy) -> bool:
    if policy is None:
        return False
    if (not isinstance(policy, dict) or policy.get("schema") != COLOUR_POLICY_SCHEMA
            or policy.get("mode") not in ("warm", "general")):
        raise ValueError("invalid photo print colour policy")
    return policy["mode"] == "general"


def acetate_mask(img, policy=None) -> np.ndarray:
    """Pigment candidates: historical warm rule, or finite unclipped RGB under a fitted general policy."""
    f = np.asarray(img, np.float64)
    if general_colour(policy):
        # Geometry establishes that these pixels can be print. Bright/neutral pigment is not intrinsically
        # glare; clipping, hardware and spatial highlight evidence are tested separately by the caller.
        return np.isfinite(f).all(-1) & (f >= 0).all(-1) & (f < 250).all(-1)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    return ((lum < ACETATE_LUM_MAX) & (sat > ACETATE_SAT_MIN)) | (lum < ACETATE_DARK)


def valid_mask(img, matte, policy=None) -> np.ndarray:
    """Photo pixels that may donate print: inside the matte eroded 1 px and on acetate."""
    return ndimage.binary_erosion(np.asarray(matte, bool), iterations=1) & acetate_mask(img, policy)
