"""Registration signals of WP5 step 0 (WP5_DESIGN §2 "Signal", §5.3, §5.4).

Ported from the scratch `wp5lib.py` (lum, masked_gauss, bandpass, corr, acetate_mask, specular_mask, source_valid) and
`aplib.py` (warmth, irange); PORT_REVIEW section 10. Colours are sRGB 0..255.

- ``luminance`` = mean(R, G, B).
- ``warmth`` = R - B: havana's amber-versus-dark-brown axis. A white highlight adds (about) equally to every channel, so
  it cancels in R - B. The gate decides on warmth.
- ``irange`` = max - min over the channels: the dichromatic model's specular-free chroma magnitude. The gate requires the
  irange peak to agree with the warmth peak.
- ``acetate_mask``: dark, or saturated and not bright (the probe's rule); ``valid_mask`` = that inside the matte eroded
  1 px: the photo pixels that may carry print (glare, metal and background excluded).
- ``bandpass``: masked difference of Gaussians, G_s(x m) / G_s(m) at two sigmas (px); outside the mask the value is the
  normalised neighbourhood average, so the filter never sees the background.

Bands are given in mm and converted with a view's px/mm at the front pivot (``BandImages``). A ``BandImages`` may be
given a support per view (default: the view's ``valid`` mask): the registration's fold fits pass the fitted fold's
region pixels only (fix round 1), because a masked DoG at 1.6-3.2 mm reads about 10 mm around a pixel and the stripes
are 4 mm wide: on the full ``valid`` mask a fold-A fit's band images carried the held-out fold-B photo pixels.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

SIGNALS = ("warmth", "lum", "irange")
OBJECTIVE_SIGNALS = ("warmth", "lum")              # §2: objective = warmth + luminance
REG_BANDS_MM = ((1.6, 3.2), (0.8, 1.6), (0.4, 0.8))  # §5.3: coarse -> fine
GATE_BAND_MM = (0.2, 1.6)                          # §5.4 step 2
ACETATE_LUM_MAX = 170.0
ACETATE_SAT_MIN = 25.0
ACETATE_DARK = 90.0
SPECULAR_LUM = 175.0
SPECULAR_SAT = 60.0


def luminance(img) -> np.ndarray:
    return np.asarray(img, np.float64).mean(-1)


def warmth(img) -> np.ndarray:
    f = np.asarray(img, np.float64)
    return f[..., 0] - f[..., 2]


def irange(img) -> np.ndarray:
    f = np.asarray(img, np.float64)
    return f.max(-1) - f.min(-1)


_FN = {"lum": luminance, "warmth": warmth, "irange": irange}


def signal(name: str, img) -> np.ndarray:
    try:
        return _FN[name](img)
    except KeyError:
        raise KeyError(f"unknown signal {name!r}; known: {sorted(_FN)}") from None


def acetate_mask(img) -> np.ndarray:
    """Pixels that look like acetate print: (luminance < 170 and max-min > 25) or luminance < 90."""
    f = np.asarray(img, np.float64)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    return ((lum < ACETATE_LUM_MAX) & (sat > ACETATE_SAT_MIN)) | (lum < ACETATE_DARK)


def specular_mask(img) -> np.ndarray:
    f = np.asarray(img, np.float64)
    return (f.mean(-1) > SPECULAR_LUM) & (f.max(-1) - f.min(-1) < SPECULAR_SAT)


def valid_mask(img, matte) -> np.ndarray:
    """Photo pixels that may donate print: inside the matte eroded 1 px and on acetate."""
    return ndimage.binary_erosion(np.asarray(matte, bool), iterations=1) & acetate_mask(img)


def masked_gauss(x, m, s: float) -> np.ndarray:
    """Normalised (masked) Gaussian G_s(x m) / G_s(m); s in px (s <= 0 returns x)."""
    x = np.asarray(x, np.float64)
    if s <= 0:
        return x
    mf = np.asarray(m, np.float64)
    num = ndimage.gaussian_filter(x * mf, s)
    den = ndimage.gaussian_filter(mf, s)
    return num / np.maximum(den, 1e-6)


def bandpass(x, m, s1: float, s2: float) -> np.ndarray:
    """Masked DoG: masked_gauss(s1) - masked_gauss(s2) (sigmas in px)."""
    return masked_gauss(x, m, s1) - masked_gauss(x, m, s2)


def band_px(band_mm: tuple[float, float], ppm: float) -> tuple[float, float]:
    return float(band_mm[0]) * ppm, float(band_mm[1]) * ppm


def ncc(a, b, min_n: int = 100) -> float:
    """Pearson correlation of two 1D samples; 0.0 below ``min_n`` samples or with zero variance (the probe's
    corr_or0, which keeps an optimizer's loss finite)."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    if len(a) < min_n:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


def sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """``texture_utils.sample_bilinear`` (same arithmetic: integer = pixel centre, clamped at the border) without its
    per-call float32 copy of the whole image: ``img`` must already be a C-contiguous float32 (H, W, C) array."""
    H, W = img.shape[:2]
    u = np.clip(np.asarray(u, float), 0, W - 1)
    v = np.clip(np.asarray(v, float), 0, H - 1)
    x0 = np.minimum(np.floor(u).astype(int), W - 2)
    y0 = np.minimum(np.floor(v).astype(int), H - 2)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    t = img.reshape(H, W, -1)
    return (t[y0, x0] * (1 - fx) * (1 - fy) + t[y0, x0 + 1] * fx * (1 - fy)
            + t[y0 + 1, x0] * (1 - fx) * fy + t[y0 + 1, x0 + 1] * fx * fy)


class BandImages:
    """Per (view, signal, band mm) the band-passed photo (float32), masked with the view's support (``supports[view]``
    when given, else the view's ``valid`` mask); sigmas in px = mm x the view's px/mm at the front pivot through its
    WP3b camera (fixed during a fit). Every value, inside or outside the support, is a function of the photo on the
    support only (normalised masked Gaussians)."""

    def __init__(self, job, supports: dict | None = None):
        self.job = job
        self.supports = dict(supports or {})
        self._cache: dict = {}

    def support(self, view: str) -> np.ndarray:
        m = self.supports.get(view)
        return self.job.views[view].valid if m is None else np.asarray(m, bool)

    def get(self, view: str, sig: str, band_mm: tuple[float, float]) -> np.ndarray:
        key = (view, sig, tuple(float(b) for b in band_mm))
        img = self._cache.get(key)
        if img is None:
            v = self.job.views[view]
            s1, s2 = band_px(band_mm, self.job.ppm(view))
            img = bandpass(signal(sig, v.image), self.support(view), s1, s2).astype(np.float32)
            self._cache[key] = img
        return img
