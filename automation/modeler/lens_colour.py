"""Lens colour: the runtime's lens predicted over the front photo's own backdrop, against the photo's lens core.

A transmissive lens shows its background, so the lens can only be compared with the photo over the photo's backdrop.
The runtime draws a lens exactly linearly in the background (lens-material.ts: transmission x background + reflectance x
room environment, not tone mapped), so per channel, in linear light, ``out = T_eff bg + A``. The see-through step's two
solid-fixture renders of the front view (skin #cba68d, blue #3a4f6e; see_through.render_fixtures, made for every lens)
give T_eff and A over the lens core; the photo's backdrop is sampled from the front photo's border; the lens over that
backdrop is predicted per pixel and compared with the photo's lens core: the hue error (0 = same hue, 0.5 = opposite),
saturation and value RATIOS (prediction / photo) describe the lens, not the fixture. On test-pilot-002 r0006 the fit
gave T_eff (0.745, 0.718, 0.664) for the authored (0.75, 0.725, 0.67) and predicted the harness's own white and checker
renders within 0.5 sRGB levels. The metric this replaced compared the lens over the harness's beige checker with the
photo's lens over white: the checker alone (no lens) scored hue 0.007, saturation 0.82, value 0.97, a near-match, and the
author lightened r0006's lens step by step (value 1.10, saturation 0.35 over the photo's white).

``lens_transmission_recommended`` = (photo_lin - A) / backdrop_lin per channel, clipped to what the runtime can pass
(1 - reflectance): the lens_optics transmission that reproduces the photo, ASSUMING the photo's studio reflection equals
the runtime room's A; with no studio reflection at all the photo's lens passes photo_lin / backdrop_lin
(``lens_transmission_upper_bound``), and a stronger studio reflection means a darker target. No transmission is
recommended where the photo cannot determine one: ``reflection_too_bright`` when the runtime's reflection A alone is
brighter than the photo's lens in a channel (the formula clipped to 0.005 there and asked for a black lens, while the
mismatch is the mirror / flash reflectance), ``backdrop_dark`` / ``backdrop_uneven`` when the photo's border is too dark
to see a transmission against or too uneven to stand for what is behind the lens. The photo gives one equation
per channel (T bg + A_photo), so the lens environment intensity (``lens_env_intensity_recommended``, the AR app's
``lensenv``) is only recommended for a near-opaque mirror (T_eff below ``OPAQUE_TRANSMISSION``), whose photo is its
reflection; for a transmissive lens T and the reflection trade one-for-one and it is None.

The lens pixels of a render come from the harness's recorded per-lens ``mesh_to_world`` and the render's camera
matrices (column-major; pixel x = (ndc.x + 1) W / 2, y = (1 - ndc.y) H / 2), so no colour segmentation is involved.
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .paths import AUTOMATION

CORE_FRACTION = 0.6      # the inner part of each lens: no rim, no edge highlight
NEUTRAL_SATURATION = 0.08   # below this HSV saturation a colour has no meaningful hue (grey, clear, near-black lenses)
BASIS = "two_fixture_fit"   # summary.lens_colour's basis; job.py hands a lens env recommendation on only from this basis
# the reading's tolerances (prediction / photo): r0006 read value 1.10 and saturation 0.35 and the owner saw it too light;
# the density-patched lens that matched the photo by eye read 0.99 / 1.02 / hue 0.000
VALUE_TOLERANCE = 0.05
SATURATION_RANGE = (0.8, 1.25)
HUE_TOLERANCE = 0.03
HUELESS_SATURATION = 0.02   # a prediction below this saturation has no hue to compare (a clear lens over white)
OPAQUE_TRANSMISSION = 0.1   # max T_eff channel below which the photo's lens is its reflection (lensenv is determined)
MIN_TRANSMISSION = 0.005
BORDER_FRACTION = 0.03      # the front photo's outer band sampled as its backdrop
# a backdrop that can carry a transmission recommendation: every channel at least this bright (linear; sRGB ~124: over a
# dark backdrop the lens shows mostly its reflection and (photo - A) / backdrop amplifies every level of error) ...
BACKDROP_MIN_LINEAR = 0.2
# ... and a border this even (luma p90 - p10, 8-bit levels): the catalogue's front photos read 0-1.7 (a 255 or 241 white)
BACKDROP_MAX_SPREAD = 24.0
LUMA = np.array([0.2126, 0.7152, 0.0722])


def srgb_to_linear(c) -> np.ndarray:
    """8-bit sRGB (0..255, any shape) -> linear light 0..1."""
    c = np.clip(np.asarray(c, float) / 255.0, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(v) -> np.ndarray:
    """Linear light 0..1 (any shape) -> sRGB 0..255 (float, not rounded)."""
    v = np.clip(np.asarray(v, float), 0.0, 1.0)
    return 255.0 * np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055)


def fit_fixtures(out_a, out_b, bg_a, bg_b) -> tuple[np.ndarray, np.ndarray]:
    """``out = T bg + A`` per channel (linear light) from the same pixels over two fixtures: (T, A), elementwise, so it
    works on core means (3,) and per pixel (N, 3) alike."""
    out_a, out_b = np.asarray(out_a, float), np.asarray(out_b, float)
    bg_a, bg_b = np.asarray(bg_a, float), np.asarray(bg_b, float)
    T = (out_a - out_b) / (bg_a - bg_b)
    return T, out_a - T * bg_a


def photo_backdrop(image: np.ndarray, fraction: float = BORDER_FRACTION) -> tuple[np.ndarray, float]:
    """The photo's backdrop: the per-channel median of its outer band (``fraction`` of the short side, at least 2 px),
    and its spread (luma p90 - p10, 8-bit levels) so a gradient or vignetted backdrop shows."""
    H, W = image.shape[:2]
    b = max(2, int(round(fraction * min(H, W))))
    band = np.concatenate([image[:b].reshape(-1, 3), image[-b:].reshape(-1, 3), image[b:-b, :b].reshape(-1, 3), image[b:-b, -b:].reshape(-1, 3)])
    luma = band @ LUMA
    return np.median(band, 0), float(np.percentile(luma, 90) - np.percentile(luma, 10))


def _mat4(values) -> np.ndarray:
    """A 4x4 from the harness's column-major 16-list, as a row-major numpy matrix."""
    return np.asarray(values, float).reshape(4, 4).T


def project_pixels(points: np.ndarray, matrix: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Homogeneous projection of Nx3 points through a 4x4 (row-major) clip matrix to pixel coordinates; (px, valid)."""
    hom = np.c_[points, np.ones(len(points))] @ matrix.T
    w = hom[:, 3]
    ok = w > 1e-9
    ndc = np.zeros((len(hom), 2))
    ndc[ok] = hom[ok, :2] / w[ok, None]
    return np.c_[(ndc[:, 0] + 1.0) * width / 2.0, (1.0 - ndc[:, 1]) * height / 2.0], ok


def lens_masks_in_render(glb_path: Path, render: dict, shape: tuple[int, int]) -> dict[str, np.ndarray]:
    """Pixel masks of the lens parts in one actual-AR render."""
    from reconstruction.mesh import load_glb_bytes
    spatial = render.get("spatial") or {}
    cam = render.get("camera") or {}
    if not spatial.get("lenses") or not cam.get("projection_matrix"):
        return {}
    mesh = load_glb_bytes(Path(glb_path).read_bytes())
    view = _mat4(cam.get("view_matrix") or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    proj = _mat4(cam["projection_matrix"])
    W, H = int(cam.get("width", shape[1])), int(cam.get("height", shape[0]))
    by_name: dict[str, list] = {}
    for p in mesh.parts or []:
        name = str(p.get("name") or "")
        if name.startswith("lens"):
            by_name.setdefault(name, []).append(p)
    masks = {}
    for lens in spatial["lenses"]:
        parts = by_name.get(lens.get("name"))
        if not parts:
            continue
        px, ok = project_pixels(mesh.vertices, proj @ view @ _mat4(lens["mesh_to_world"]), W, H)
        img = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(img)
        for p in parts:
            for tri in mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]:
                if ok[tri].all():
                    draw.polygon([tuple(px[i]) for i in tri], fill=255)
        masks[lens["name"]] = np.asarray(img) > 0
    return masks


def core_of(mask: np.ndarray, frac: float = CORE_FRACTION) -> np.ndarray:
    """The inner part of a lens mask: eroded away from its edge (12 % of its height, at least 2 px, so no rim or
    edge highlight) and within ``frac`` of the largest radius from the centroid."""
    from scipy import ndimage
    ys, xs = np.nonzero(mask)
    core = np.zeros_like(mask, dtype=bool)
    if len(xs) < 50:
        return core
    margin = max(2, int(round(0.12 * (ys.max() - ys.min() + 1))))
    eroded = ndimage.binary_erosion(mask, iterations=margin)
    cx, cy = xs.mean(), ys.mean()
    r = np.hypot(xs - cx, ys - cy)
    keep = r < frac * r.max()
    core[ys[keep], xs[keep]] = True
    return core & eroded


def front_photo(evidence: dict) -> np.ndarray | None:
    """The full front photo (RGB float), the one the lens outlines were measured on; None without a front input."""
    row = next((r for r in evidence.get("inputs", []) if r.get("view") == "front"), None)
    if row is None:
        return None
    path = Path(row["path"])
    path = path if path.is_absolute() else AUTOMATION / path
    return np.asarray(Image.open(path).convert("RGB")).astype(float)


def photo_lens_core(evidence: dict, image: np.ndarray | None = None) -> tuple[np.ndarray, int] | None:
    """Mean sRGB of the lens cores in the front photo, from the measured lens outlines (mm -> photo pixels)."""
    front = evidence.get("front") or {}
    if not front.get("lenses"):
        return None
    if image is None:
        image = front_photo(evidence)
        if image is None:
            return None
    H, W = image.shape[:2]
    mmpx, ax, ymid = float(front["mm_per_px"]), float(front["axis_x_px"]), float(front["y_mid_px"])
    core_all = np.zeros((H, W), bool)
    for lens in front["lenses"]:
        poly = np.asarray(lens["outline_mm"], float)
        c = poly.mean(0)
        shrunk = c + CORE_FRACTION * (poly - c)
        pts = [(ax + x / mmpx, ymid - y / mmpx) for x, y in shrunk]
        img = Image.new("L", (W, H), 0)
        ImageDraw.Draw(img).polygon(pts, fill=255)
        core_all |= np.asarray(img) > 0
    if core_all.sum() < 20:
        return None
    return image[core_all].mean(0), int(core_all.sum())


def compare(photo_rgb: np.ndarray, render_rgb: np.ndarray, *, mirrored: bool) -> dict:
    """HSV comparison of a lens colour (``render_rgb``: the prediction over the photo's backdrop) with the photo's.
    Neutrality is the PHOTO's alone: a grey or clear photo lens gets no hue error and no saturation ratio (a saturation
    DIFFERENCE instead); a coloured photo lens always gets its ratio, so a pale prediction of it shows (a rule of either
    side hid r0006's 0.35). A prediction too grey to carry a hue gets no hue error; its saturation ratio says it."""
    ph = colorsys.rgb_to_hsv(*(np.asarray(photo_rgb, float) / 255.0))
    rh = colorsys.rgb_to_hsv(*(np.asarray(render_rgb, float) / 255.0))
    neutral = ph[1] < NEUTRAL_SATURATION
    if neutral or rh[1] < HUELESS_SATURATION:
        dh = None
    else:
        dh = abs(ph[0] - rh[0])
        dh = min(dh, 1.0 - dh)
    return {"photo_rgb": [round(float(x), 1) for x in photo_rgb], "render_rgb": [round(float(x), 1) for x in render_rgb],
            "photo_hsv": [round(float(x), 3) for x in ph], "render_hsv": [round(float(x), 3) for x in rh],
            "hue_error": None if dh is None else round(float(dh), 3),
            "saturation_ratio": None if neutral else round(float(rh[1] / max(ph[1], 1e-3)), 2),
            "saturation_difference": round(float(rh[1] - ph[1]), 3),
            "value_ratio": round(float(rh[2] / max(ph[2], 1e-3)), 2), "mirrored": bool(mirrored), "neutral": bool(neutral)}


def reading_flags(d: dict) -> list[str]:
    """The comparison as words, value first: too_light / too_dark, too_pale / too_saturated (tinted for a neutral photo
    lens predicted with colour), hue_off. Empty = a match within the module's tolerances."""
    flags = []
    v = d["value_ratio"]
    if v > 1 + VALUE_TOLERANCE:
        flags.append("too_light")
    elif v < 1 - VALUE_TOLERANCE:
        flags.append("too_dark")
    s = d["saturation_ratio"]
    if s is not None:
        if s < SATURATION_RANGE[0]:
            flags.append("too_pale")
        elif s > SATURATION_RANGE[1]:
            flags.append("too_saturated")
    elif d["saturation_difference"] > NEUTRAL_SATURATION:
        flags.append("tinted")
    if d["hue_error"] is not None and d["hue_error"] > HUE_TOLERANCE:
        flags.append("hue_off")
    return flags


def over_backdrop(img_a: np.ndarray, img_b: np.ndarray, fixture_a, fixture_b, backdrop_rgb) -> np.ndarray:
    """The runtime's front render re-seen over the photo's backdrop, per pixel from the two fixture renders (sRGB float):
    out = T_px backdrop + A_px in linear light; a pixel that shows the bare fixture in both renders becomes the backdrop,
    an opaque one keeps its colour."""
    t, a = fit_fixtures(srgb_to_linear(img_a), srgb_to_linear(img_b), srgb_to_linear(fixture_a), srgb_to_linear(fixture_b))
    return linear_to_srgb(t * srgb_to_linear(backdrop_rgb) + a)


def lens_colour_from_fixtures(photo_rgb, backdrop_rgb, img_a: np.ndarray, img_b: np.ndarray, core: np.ndarray, fixture_a, fixture_b,
                              *, mirrored: bool, reflectance=None, backdrop_spread: float | None = None) -> dict:
    """The lens colour over the photo's backdrop from the lens core of two fixture renders (sRGB images over ``fixture_a``
    and ``fixture_b``), against the photo's lens core ``photo_rgb``. ``reflectance``: the lens material's normal
    reflectance (linear rgb), the runtime's cap on transmission (1 - R); ``backdrop_spread``: ``photo_backdrop``'s spread.
    The recommendation is None (with a flag and a note) where the photo cannot determine it."""
    la, lb = srgb_to_linear(img_a[core]), srgb_to_linear(img_b[core])
    fa, fb, bg = srgb_to_linear(fixture_a), srgb_to_linear(fixture_b), srgb_to_linear(backdrop_rgb)
    T, A = fit_fixtures(la.mean(0), lb.mean(0), fa, fb)
    t_px, a_px = fit_fixtures(la, lb, fa, fb)
    predicted = linear_to_srgb(t_px * bg + a_px).mean(0)       # per pixel, then averaged in sRGB like the photo's core
    d = compare(photo_rgb, predicted, mirrored=mirrored)
    d["predicted_rgb"], d["predicted_hsv"] = d.pop("render_rgb"), d.pop("render_hsv")
    photo_lin = srgb_to_linear(photo_rgb)
    bg_safe = np.maximum(bg, 1e-4)
    cap = 1.0 - np.clip(np.asarray(reflectance if reflectance is not None else (0.0, 0.0, 0.0), float), 0.0, 1.0 - MIN_TRANSMISSION)
    recommended = np.clip((photo_lin - A) / bg_safe, MIN_TRANSMISSION, cap)
    upper = np.clip(photo_lin / bg_safe, 0.0, 1.0)
    guards, notes = [], []
    over = (photo_lin - A) < MIN_TRANSMISSION * bg          # the runtime's reflection alone reaches the photo's lens there
    if over.any():
        ch = ", ".join(c for c, o in zip("RGB", over) if o)
        guards.append("reflection_too_bright")
        notes.append(f"reflection_too_bright: the runtime's reflection alone (lens_reflection, channel {ch}) is brighter than the "
                     "photo's whole lens there, so no transmission reproduces the photo: the mirror / flash reflectance "
                     "(gl.lens_optics mirror_rgb) or its hue is the mismatch, not the transmission; lower it toward the "
                     "photo's lens colour first, then read the recommendation again")
    if float(bg.min()) < BACKDROP_MIN_LINEAR:
        guards.append("backdrop_dark")
        notes.append(f"backdrop_dark: the front photo's border (backdrop_rgb {np.round(backdrop_rgb).astype(int).tolist()}) is too "
                     "dark to see a transmission against; judge the lens colour on the AR sheet by eye, no transmission is "
                     "recommended from it")
    if backdrop_spread is not None and backdrop_spread > BACKDROP_MAX_SPREAD:
        guards.append("backdrop_uneven")
        notes.append(f"backdrop_uneven: the front photo's border varies by {backdrop_spread:.0f} levels (a gradient, vignette or "
                     "scene), so its median need not be what is behind the lens; no transmission is recommended from it")
    backdrop_ok = not ({"backdrop_dark", "backdrop_uneven"} & set(guards))
    env, env_note = None, None
    if not mirrored:
        env_note = "not a mirrored lens"
    elif T.max() >= OPAQUE_TRANSMISSION:
        env_note = (f"not recommended: a transmissive lens (T_eff up to {T.max():.2f}) seen over one photo backdrop gives one equation "
                    "per channel (T x backdrop + the photo's reflection), so the transmission and the reflection trade one-for-one")
    elif float(LUMA @ A) > 1e-3:
        env = round(float(np.clip(LUMA @ (photo_lin - T * bg) / (LUMA @ A), 0.3, 4.0)), 2)
        env_note = "a near-opaque mirror: the photo's lens is its reflection; the runtime's reflection scaled to the photo's luma"
    d.update({"basis": BASIS, "backdrop_rgb": [round(float(x), 1) for x in backdrop_rgb],
              "lens_transmission_effective": [round(float(x), 3) for x in T], "lens_reflection": [round(float(x), 4) for x in A],
              "lens_transmission_recommended": [round(float(x), 3) for x in recommended] if backdrop_ok and not over.any() else None,
              "lens_transmission_upper_bound": [round(float(x), 3) for x in upper] if backdrop_ok else None,
              "lens_transmission_scale": ([round(float(r / max(t, 1e-4)), 3) for r, t in zip(recommended, T)]
                                          if backdrop_ok and not over.any() else None),
              "lens_env_intensity_recommended": env, "lens_env_intensity_note": env_note, "notes": notes})
    d["flags"] = reading_flags(d) + guards
    d["match"] = not d["flags"]
    return d


def actual_ar_render(out_dir: Path, view: str = "front") -> tuple[np.ndarray, dict] | None:
    """The actual-AR render of one view from an archeck output directory: (RGB float image, render row)."""
    rp = Path(out_dir) / "report.json"
    if not rp.exists():
        return None
    report = json.loads(rp.read_text(encoding="utf-8"))
    case = (report.get("cases") or [None])[0]
    render = next((r for r in (case or {}).get("renders", []) if r.get("mode") == "actual-ar" and r.get("view") == view), None)
    if render is None:
        return None
    return np.asarray(Image.open(Path(out_dir) / render["filename"]).convert("RGB")).astype(float), render


def lens_optics_of(materials_json: Path) -> tuple[bool, list[float] | None]:
    """(any lens mirrored, the per-channel maximum normal reflectance over the lens materials, None without one)."""
    mats = json.loads(Path(materials_json).read_text(encoding="utf-8")).get("materials", {})
    lenses = [s.get("lens") or {} for s in mats.values() if s.get("kind") == "lens"]
    refl = [np.asarray(l["reflectance_rgb"], float)[:3] for l in lenses if l.get("reflectance_rgb")]
    return any(bool(l.get("mirror")) for l in lenses), (np.max(refl, 0).tolist() if refl else None)


def lens_colour_metric(glb_path: Path, rendered: dict | None, evidence: dict, materials_json: Path, *, out_png: Path | None = None) -> dict:
    """The metric for one observed candidate from the front view of its two solid-fixture runs (``rendered``: the
    result of ``see_through.render_fixtures``). With ``out_png`` the whole front render re-seen over the photo's backdrop
    is written there (``render_over_backdrop``), for the AR sheet."""
    status = (rendered or {}).get("status")
    if status == "not_applicable":
        return {"status": "not_applicable"}
    if status != "rendered":
        return {"status": "no_fixture_renders", "fixture_status": status, "error": (rendered or {}).get("error")}
    keys = list(rendered["fixtures"])[:2]
    renders = [actual_ar_render(Path(rendered["dirs"][k])) for k in keys]
    if any(r is None for r in renders):
        return {"status": "no_front_render", "fixture": keys[[r is None for r in renders].index(True)]}
    (img_a, row_a), (img_b, _) = renders
    if img_a.shape != img_b.shape:
        return {"status": "render_mismatch"}
    masks = lens_masks_in_render(glb_path, row_a, img_a.shape[:2])
    core = np.zeros(img_a.shape[:2], bool)
    for m in masks.values():
        core |= core_of(m)
    if core.sum() < 50:
        return {"status": "no_lens_pixels", "lenses": sorted(masks)}
    photo = front_photo(evidence)
    pc = photo_lens_core(evidence, image=photo) if photo is not None else None
    if pc is None:
        return {"status": "no_photo_lens"}
    prgb, pn = pc
    backdrop, spread = photo_backdrop(photo)
    fa, fb = (np.array([int(rendered["fixtures"][k].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], float) for k in keys)
    mirrored, reflectance = lens_optics_of(materials_json)
    out = lens_colour_from_fixtures(prgb, backdrop, img_a, img_b, core, fa, fb, mirrored=mirrored, reflectance=reflectance,
                                    backdrop_spread=spread)
    out.update({"status": "measured", "photo_pixels": int(pn), "render_pixels": int(core.sum()), "backdrop_spread": round(spread, 1),
                "fixtures": {k: rendered["fixtures"][k] for k in keys},
                "renders": {k: str(Path(rendered["dirs"][k]) / r[1]["filename"]) for k, r in zip(keys, renders)},
                "note": "the runtime's lens predicted over the front photo's own backdrop (T_eff and the reflection A fitted per channel "
                        "in linear light from the skin and blue fixture renders) vs the photo's lens core; lens_transmission_recommended "
                        "assumes the photo's studio reflection equals the runtime's A (none at all: lens_transmission_upper_bound)"})
    if out_png is not None:
        Image.fromarray(np.round(over_backdrop(img_a, img_b, fa, fb, backdrop)).astype(np.uint8)).save(out_png)
        out["render_over_backdrop"] = str(out_png)
    return out


