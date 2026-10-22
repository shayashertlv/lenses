"""Coating colours by viewing angle, and the two-colour coating search, for Astra (audit B3).

A numpy translation of the dev UI's dev_ui/lens_renderer/coating.js (itself translated from the pinned Three r185
iridescence_fragment.glsl.js, MIT) and dev_ui/static/coating-match.js. The same function draws the Edit page's
coating bar, so Astra and the owner see the same answer. Colours are the coating's own reflection at one angle,
not a room-lit render: candidates still need a look comparison.
"""
from __future__ import annotations

import math

import numpy as np

STRAIGHT_ON, SIDES = 1.0, 0.4
FILM_IORS = tuple(round(1.2 + .05 * i, 2) for i in range(25))
THICKNESSES = np.arange(0, 1201, dtype=np.float64)
_AMPLITUDE = np.array([5.4856e-13, 4.4201e-13, 5.2481e-13])
_POSITION = np.array([1.6810e6, 1.7953e6, 2.2084e6])
_VARIANCE = np.array([4.3278e9, 9.3046e9, 6.6121e9])
_XYZ_TO_RGB = np.array([[3.2404542, -1.5371385, -.4985314], [-.9692660, 1.8760108, .0415560],
                        [.0556434, -.2040259, 1.0572252]])


def _schlick(f0, cosine):
    return f0 + (1 - f0) * 2 ** ((-5.55473 * cosine - 6.98316) * cosine)


def _fresnel0(transmitted, incident):
    return ((transmitted - incident) / (transmitted + incident)) ** 2


def _sensitivity(opd, shift):
    """opd (n,), shift (n, 3) -> linear RGB (n, 3)."""
    phase = 2 * math.pi * opd[:, None] * 1e-9
    xyz = (_AMPLITUDE * np.sqrt(2 * math.pi * _VARIANCE) * np.cos(_POSITION * phase + shift)
           * np.exp(-(phase ** 2) * _VARIANCE))
    xyz[:, 0] += (9.7470e-14 * math.sqrt(2 * math.pi * 4.5282e9) * np.cos(2.2399e6 * phase[:, 0] + shift[:, 0])
                  * np.exp(-4.5282e9 * phase[:, 0] ** 2))
    return (xyz / 1.0685e-7) @ _XYZ_TO_RGB.T


def coating_reflectance(outside_ior, film_ior, cosine, thickness, base_f0):
    """Three's evalIridescence for many thicknesses at one angle: (n,) nm -> (n, 3) linear reflectance."""
    thickness = np.asarray(thickness, dtype=np.float64).reshape(-1)
    t = np.clip(thickness / .03, 0, 1)
    eta = outside_ior + (film_ior - outside_ior) * (t * t * (3 - 2 * t))
    cos_squared = 1 - (outside_ior / eta) ** 2 * (1 - cosine ** 2)
    total = cos_squared < 0
    cos_film = np.sqrt(np.where(total, 0, cos_squared))
    r12 = _schlick(_fresnel0(eta, outside_ior), cosine)
    transmission = 1 - r12
    phi21 = math.pi - np.where(eta < outside_ior, math.pi, 0)
    root = np.sqrt(np.clip(np.asarray(base_f0, dtype=np.float64), 0, .9999))
    base_ior = (1 + root) / (1 - root)
    r23 = _schlick(_fresnel0(base_ior[None, :], eta[:, None]), cos_film[:, None])
    phi = phi21[:, None] + np.where(base_ior[None, :] < eta[:, None], math.pi, 0)
    opd = 2 * eta * thickness * cos_film
    r12, transmission = r12[:, None], transmission[:, None]
    r123 = np.clip(r12 * r23, 1e-5, .9999)
    rs = transmission ** 2 * r23 / (1 - r123)
    intensity, cm = r12 + rs, rs - transmission
    for m in (1, 2):
        cm = cm * np.sqrt(r123)
        intensity = intensity + cm * 2 * _sensitivity(m * opd, m * phi)
    intensity = np.maximum(0, intensity)
    return np.where(total[:, None], 1.0, intensity)


def swatch_srgb(values, cosine, thickness=None, film_ior=None):
    """coatingSwatchRGB: sRGB bytes of the coating colour at one angle, for one or many thicknesses."""
    cosine = min(1.0, max(0.0, float(cosine)))
    dielectric = _fresnel0(float(values["ior"]), 1)
    metallic = float(values["metallic"])
    base = np.array([dielectric * (1 - metallic) + v * metallic for v in values["tint"]])
    thickness = values["iridescence_thickness_max"] if thickness is None else thickness
    film = coating_reflectance(1.0, float(values["iridescence_ior"] if film_ior is None else film_ior),
                               cosine, thickness, base)
    plain = _schlick(base, cosine)
    linear = np.clip(plain * (1 - values["iridescence"]) + film * values["iridescence"], 0, 1)
    srgb = np.where(linear <= .0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - .055)
    return np.floor(255 * srgb + .5).astype(int)  # JavaScript Math.round


def chromaticity(rgb):
    """Lab a*, b* at a fixed lightness (rows of sRGB bytes); None where the colour has no luminance."""
    rgb = np.asarray(rgb, dtype=np.float64).reshape(-1, 3) / 255
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    r, g, b = linear.T
    luminance = .2126729 * r + .7151522 * g + .0721750 * b
    ok = luminance > 0
    scale = np.where(ok, .5 / np.where(ok, luminance, 1), 0)
    xyz = np.stack([(.4124564 * r + .3575761 * g + .1804375 * b) * scale / .95047, np.full_like(r, .5),
                    (.0193339 * r + .1191920 * g + .9503041 * b) * scale / 1.08883], axis=1)
    lab = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([500 * (lab[:, 0] - lab[:, 1]), 200 * (lab[:, 1] - lab[:, 2])], axis=1), ok


def find_coating(straight_on, sides, values):
    """findCoatingForColours: every thickness 0-1200 nm and film IOR 1.2-2.4, judged at straight-on and sides.

    Lens IOR, metallic, tint and coating strength stay as given. Returns up to three distinct choices."""
    (centre,), (centre_ok,) = chromaticity([straight_on])
    (side,), (side_ok,) = chromaticity([sides])
    if not centre_ok or not side_ok or math.hypot(*centre) < 2 or math.hypot(*side) < 2:
        return {"choices": [], "reason": "Choose clear colours (not white, grey or black) for the coating."}
    strength = values["iridescence"] if values["iridescence"] > 0 else 1
    trial = {**values, "iridescence": strength}
    best = {"distance": np.full(len(THICKNESSES), np.inf), "film": np.zeros(len(THICKNESSES)),
            "straight": np.zeros(len(THICKNESSES)), "sides": np.zeros(len(THICKNESSES))}
    for film in FILM_IORS:
        a, a_ok = chromaticity(swatch_srgb(trial, STRAIGHT_ON, THICKNESSES, film))
        b, b_ok = chromaticity(swatch_srgb(trial, SIDES, THICKNESSES, film))
        straight, side_error = np.hypot(*(a - centre).T), np.hypot(*(b - side).T)
        total = np.where(a_ok & b_ok, straight + side_error, np.inf)
        better = total < best["distance"]  # strict: the first (lowest) film IOR wins ties, as in the page
        for key, value in (("distance", total), ("film", film), ("straight", straight), ("sides", side_error)):
            best[key] = np.where(better, value, best[key])
    distance = best["distance"]
    minima = [i for i in range(len(distance)) if np.isfinite(distance[i])
              and (i == 0 or distance[i] < distance[i - 1]) and (i == len(distance) - 1 or distance[i] <= distance[i + 1])]
    minima.sort(key=lambda i: (distance[i], i))
    choices = []
    for i in minima:
        def distinct(j):
            low, high = min(i, j), max(i, j)
            return high - low >= 40 and bool((distance[low:high + 1] > max(distance[i], distance[j]) + 3).any())
        if all(distinct(c["thickness_nm"]) for c in choices):
            straight, side_error = float(best["straight"][i]), float(best["sides"][i])
            choices.append({"thickness_nm": i, "film_ior": float(best["film"][i]),
                            "error_straight_on": round(straight, 2), "error_sides": round(side_error, 2),
                            "quality": ("close match" if straight < 8 and side_error < 8 else
                                        "approximate match" if straight < 18 and side_error < 18 else
                                        "closest a real coating can get")})
        if len(choices) == 3:
            break
    for choice in choices:
        chosen = {**trial, "iridescence_ior": choice["film_ior"], "iridescence_thickness_max": choice["thickness_nm"]}
        choice["straight_on_srgb"] = swatch_srgb(chosen, STRAIGHT_ON)[0].tolist()
        choice["sides_srgb"] = swatch_srgb(chosen, SIDES)[0].tolist()
    return {"choices": choices, "reason": None if choices else "No coating could be matched. Try other colours."}
