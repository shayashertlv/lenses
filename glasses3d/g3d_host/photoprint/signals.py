"""Photo pixel masks of the WP5 bake (WP5_DESIGN §2 "Signal"): which photo pixels may carry print.

Ported from the scratch `wp5lib.py` (acetate_mask, source_valid); PORT_REVIEW section 10. Colours are sRGB 0..255.

- ``acetate_mask``: dark, or saturated and not bright (the probe's rule); ``valid_mask`` = that inside the matte eroded
  1 px: the photo pixels that may carry print (glare, metal and background excluded).
- ``SPECULAR_LUM`` / ``SPECULAR_SAT``: the bright achromatic (specular) colour limits shared by ``gains``,
  ``highlights`` and ``shading``.

The registration signals of step 0 (luminance, warmth, irange, the masked DoG band images ``BandImages``, ``ncc``)
and the unused ``specular_mask`` were deleted on 2026-10-13 with the step-0 experiment; see git tag
review-20261013-pre-cleanup.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

ACETATE_LUM_MAX = 170.0
ACETATE_SAT_MIN = 25.0
ACETATE_DARK = 90.0
SPECULAR_LUM = 175.0
SPECULAR_SAT = 60.0


def acetate_mask(img) -> np.ndarray:
    """Pixels that look like acetate print: (luminance < 170 and max-min > 25) or luminance < 90."""
    f = np.asarray(img, np.float64)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    return ((lum < ACETATE_LUM_MAX) & (sat > ACETATE_SAT_MIN)) | (lum < ACETATE_DARK)


def valid_mask(img, matte) -> np.ndarray:
    """Photo pixels that may donate print: inside the matte eroded 1 px and on acetate."""
    return ndimage.binary_erosion(np.asarray(matte, bool), iterations=1) & acetate_mask(img)
