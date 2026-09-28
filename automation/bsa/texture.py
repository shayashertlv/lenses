"""S7 `s7_texture`: frame and temple appearance (DESIGN.md S7).

Inputs (read only): S0 mattes and lens proposals, S1 generator (mesh, UVs, basecolor), S2 lens polygons,
S3 cameras, S4 lens surfaces, S5 temples (donor UVs), S6 frame solid (front cap / back cap / walls).

Frame textures, one per S6 frame region, sharing ONE per-corner UV array ``frame_UV`` (M,3,2):

1. Front cap (region 0) -> ``frame_front.png``. Layout = the S6 outline-source pixel plane (``frame_P2``;
   the front photo for every product but INVU, whose source is the mirrored back photo). Each texel is a
   point on the front cap (barycentric in its S6 triangle); it is projected by the S3 FRONT camera into the
   front photo and sampled there. The photo is prepared first: sampling is allowed only inside the front
   cap's own footprint AND the S0 matte, minus the S0 lens proposal, minus backdrop-coloured pixels (dE00 < 10;
   anti-aliased matte edges on INVU's tiny front), minus lens-coloured pixels (Lab histogram likelihood ratio
   > 4 against the lens interior vs the frame far from the lens) that lie within 2.5 mm of the lens AND are
   connected to it (an isolated dark tortoise spot is frame), eroded 2 px (at most 0.75 mm: 1 px on INVU's
   175 px front). Invalid pixels are padded outward from the nearest valid pixel (no backdrop or lens colour
   can enter). Specular highlights (V > 0.97 and saturation < 0.25) are not used for padding and are clamped
   (per channel minimum) to the local median. A texel whose nearest valid pixel is farther than 2.5 mm falls
   back to the generator bake (step 3); 2.5 mm covers VB's outer lens band (S2 1-1.5 mm inside the lens).
2. Back cap (region 1) -> ``frame_back.png``, same layout. Each texel's back-cap point is projected by the
   S3 BACK camera; it takes the back photo (prepared like the front) only when it is VISIBLE there: the
   back-cap face turns toward the camera (cos > 0.25), nothing of the S6 frame lies in front of it (0.5 mm)
   and nothing of the full generator lies more than 3 mm in front of it (temples, nose pads, hinges).
   Otherwise the texel is baked.
3. Walls (region 2) -> ``frame_wall.png``: each boundary loop of the front cap is unrolled into strips
   (arc length x depth, up to 12 px/mm), shelf-packed into a <= 2048 atlas. Every texel is a 3D point on a
   wall; it takes the generator basecolor at its CLOSEST point on the generator, with the generator's lens
   faces removed from the source (face centroid inside the S2 lens polygons dilated 1.5 mm, seen through the
   outline-source camera, and within [-8, +4] mm of the S4 lens surface: the generator's plate is a thick
   slab, and a [-4, +1.5] band left VB's walls 18 % cream). Baked texels whose RAW generator colour is still
   lens-like (histogram ratio against the generator's lens faces vs its colour where the photo shows frame;
   INVU's lateral lens ends lie outside the dilated polygon) take FRAME EVIDENCE instead: the colours of the two
   caps the wall joins at the same outline position, blended along its depth (``cap_extrusion``; Telea-inpainting
   18 % of INVU's wall texels from their neighbours smeared lens colour into light flecks); backdrop-like ones are
   inpainted (Telea) from their good neighbours.
   Walls the FRONT photo sees directly (``front_visible_walls``: turned toward the S3 front camera by cos > 0.15, the
   first delivered surface on the ray, not seen through the lens; a brow's or endpiece's ~1 mm top wall in a pitched
   front photo) take the front cap's colour at their front edge over their whole depth - the front photo's frame
   colour, like the front cap - not the generator bake (rayban: 9 % of the front-view frame pixels at dE00 16) and
   not the photo pixel at their own projection (seen at 75-85 deg a wall shows the studio's specular reflection:
   light grey on rayban's tortoise brow). Not when the front photo is flagged low resolution (INVU, 1.1 px/mm).
4. Colour gain: the generator's baked lighting and saturation differ from the photo (VB navy reads ~2x too
   saturated, walls read pale). Per channel, in linear light, photo = gain * generator + offset between the
   front photo samples on the front cap and the generator colour at the same front-cap points, fitted on
   DISTRIBUTIONS (quantiles 10..90 %), not point pairs (point-wise regression is diluted by misregistered
   texture detail such as tortoise). Median-anchored, offset >= 0, gain in [0.25, 4] (``quantile_gain``
   explains why). Applied to every baked texel.

Matte-edge backdrop mix (front and back photos): pixels within 2 px of the backdrop whose linear colour lies more
than 25 % of the way from the frame colour to the backdrop are not sampled (removed after the erosion). Anti-aliasing
and JPEG blur span ~2 px whatever the px/mm; on INVU's 1.3 px/mm front they padded a white patch into the nose apex.
The same rule next to the LENS (``lens_mix_mask``: part lens colour) applies to sampling and evaluation.

Low-resolution front (flag ``front_low_resolution``) with a mirrored-back outline source (INVU): the front photo measures
the frame's colour but not its detail (padding 1.3 px/mm into a 12 px/mm texture drew streaks - "camouflage"). The front
cap takes the detail of the mirrored back photo (``front_from_back``: sampled where S6 placed each cap point in the
source photo, colour-mapped per channel to the front photo), the front photo's colour at its own resolution
(``detail_transfer``: front low-pass + back detail above one front pixel), then a per-channel gain so the front-view
render has the front photo's mean frame colour (``front_view_gain``, measured like the evaluation). The evaluation
renders integrate each photo pixel over its footprint (``supersampled_base``: >= ``EVAL_MIN_PX_PER_MM``).

Material (roughness, metallic, base-colour gain) is FITTED in the actual AR runtime (``fit_frame_material``):
the assembly as S9 exports it is rendered for every candidate (roughness 0.3..1.0, metallic 0 for acetate /
{0, 0.5, 1} for metal, each at base colour x1 and x0; the gain in [0.25, 1] is synthesised exactly through the
runtime's ACES tone map, then the chosen material is rendered to verify) in the front, back (asset-back) and
yaw +/-80 views, native room lighting, white backdrop. Each render pixel of an opaque part is ray-cast to its 3D
point, projected through the frozen S3 camera of the matching photo and sampled there (occlusion-checked, blurred
to the render's footprint); objective = mean over views of the dE00 between the L*-sorted decile means of the
two pixel sets. The old rule (median generator ORM roughness, gain 1) is rendered in the same batch (before/after).
Why a gain: the photo colour includes the studio's light (clipped white backdrop) so it is an upper bound on the
albedo, and the runtime's bright room renders it 2-3x lighter than the photo; without the gain the roughness fit
is driven by diffuse brightness.

Temples: the donor UVs (S5) and the generator basecolor, downsampled to <= 2048; texels no temple face uses
(dilated 4 px) are filled with the used-texel median so the JPEG stays small. The gain is applied to the
temples only when it lowers the temple colour difference in the two side photos (fit views; the angled view
is only ever measured).

Material CLASSES (``material_classes``): the temple texels (arms + S5 donors) are METAL or DIELECTRIC by the
generator's own metallicRoughness map (metallic >= 0.5), cleaned with its basecolor (a Lab colour bin's majority
class decides its texels; a 5-texel majority filter). When each class covers >= 10 % of the temple surface (miu's
tortoise tips on gold arms: 40 / 60 %; rayban's metal hinge pins, 5 %, do not split) the material fit runs per class
(``_fit_classes``: a dielectric class only at metallic 0; every render pixel's class from its hit UV) and the temples
carry two materials in one: ``temple_mr.png`` (glTF metallicRoughness: B metallic, G roughness per class, factors 1)
and each class's base-colour gain baked into ``temple_basecolor.jpg``; the frame takes its own class's factors (the
class of the generator surface nearest the front cap). Before: one metallic 1 / roughness 1 material for everything
rendered miu's tortoise tips as metallic gold.

Evaluation: the textured assembly (S6 frame + S5 temples, S6 lenses as occluders) is rendered in base colour
through every S3 camera; on the frame pixels (eroded 3 px, inside the S0 matte, outside the S0 lens
proposal) the photo and the render are compared with CIEDE2000: the dE00 of the region's mean colour and the
median / p90 of the per-pixel dE00 (both images blurred sigma 1.5 px). The same numbers for a baseline that
textures the S6 frame with the raw generator bake (no photo, no gain) are reported next to them.

Outputs in data/bsa/runs/<run>/<product>/s7_texture/: the texture images, result.json (``materials`` plan
read by S9: name -> {part, region, texture, uv, factors {metallic, roughness, base_color}[,
metallic_roughness_texture]}, ``material_fit``, ``material_classes``),
arrays.npz (``frame_UV`` and the temple UVs, texel provenance maps), sheet.png and ar_fit/ (before and verified
after renders, sheet.png).
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab

from reconstruction.camera import Camera

from . import core, depth, raster
from .core import NormFrame, stage_dir

STAGE = "s7_texture"

ERODE_PX = 2                     # DESIGN: frame region eroded 2 px before sampling ...
ERODE_MAX_MM = 0.75              # ... but never more than 0.75 mm (INVU's front photo is 1.3 px/mm -> 1 px)
PAD_MAX_MM = 2.5                 # photo padding reach; farther texels fall back to the generator bake
PAD_MIN_PX = 3.0
HIGHLIGHT_V = 0.97               # plan: V > 0.97 and low saturation
HIGHLIGHT_S = 0.25
MEDIAN_MM = 1.0                  # local-median window for the highlight clamp
LENSLIKE_RATIO = 4.0             # p(lens colour) / p(frame colour) above which a cap pixel is lens ...
LENSLIKE_BAND_MM = 2.5           # ... when within 2.5 mm of the lens and connected to it through lens-like pixels
LENS_REF_ERODE_MM = 1.0          # lens colour reference: S2 lens polygons eroded 1 mm (plus the S0 proposal)
FRAME_REF_AWAY_MM = 2.0          # frame colour reference: cap pixels farther than 2 mm from any lens
LENS_DILATE_MM = 1.5             # generator lens faces: inside the lens polygons dilated 1.5 mm ...
PLATE_BAND_MM = (-8.0, 4.0)      # ... and within this band of the S4 lens (plate) surface (signed, + = front)
BACK_MIN_COS = 0.25
BACK_TOL_SELF_MM = 0.5
BACK_TOL_GEN_MM = 3.0
CAP_MARGIN_PX = 8
ATLAS_MAX = 2048
WALL_PX_PER_MM = 12.0
WALL_PAD = 3
WALL_FAR_MM = 5.0                # flag when the wall bake's p95 closest-point distance exceeds this
METAL_TEXEL = 0.5                # generator metallic (glTF B channel) at or above this = a metal texel (``material_classes``)
CLASS_BIN_PURITY = 0.55          # ... a colour bin this pure in one class decides its texels (cleans the generator map)
CLASS_MIN_SHARE = 0.1            # two materials on the temples only when each class covers this share of their surface
WALL_MIN_COS = 0.15              # a wall the front photo sees (``front_visible_walls``): turned toward its camera by more
WALL_VIS_TOL_MM = 0.5            # ... than this, and the first delivered surface on its ray within this
GAIN_BOUNDS = (0.25, 4.0)
GAIN_QUANTILES = np.arange(10, 91, 10)
GAIN_MIN_PAIRS = 200
INPAINT_DE = 10.0
TEMPLE_TEX_MAX = 2048
DE_FLAG = 8.0
EVAL_ERODE_PX = 3
EVAL_BLUR = 1.5
EVAL_MIN_PX_PER_MM = 4.0         # the evaluation render integrates each photo pixel over >= this many samples per mm
EVAL_MAX_SUPERSAMPLE = 4
LENS_TINT = (190, 200, 210)


# =========================================================================== colour helpers
def srgb_to_linear(c) -> np.ndarray:
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c) -> np.ndarray:
    c = np.clip(np.asarray(c, np.float64), 0.0, 1.0)
    return 255.0 * np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def lab_of(rgb) -> np.ndarray:
    """CIELAB (D65) of sRGB 0..255 values, any leading shape."""
    a = np.asarray(rgb, np.float64)
    return rgb2lab((a.reshape(-1, 1, 3) / 255.0)).reshape(a.shape)


def de00(rgb_a, rgb_b) -> np.ndarray:
    return deltaE_ciede2000(lab_of(rgb_a), lab_of(rgb_b))


def mean_colour_de00(rgb_a: np.ndarray, rgb_b: np.ndarray) -> float:
    """dE00 between the mean Lab colours of two pixel sets."""
    return float(deltaE_ciede2000(lab_of(rgb_a).reshape(-1, 3).mean(0), lab_of(rgb_b).reshape(-1, 3).mean(0)))


def quantile_gain(gen_rgb: np.ndarray, photo_rgb: np.ndarray, bounds=GAIN_BOUNDS) -> tuple[np.ndarray, np.ndarray, dict]:
    """Per-channel linear-light photo = gain * gen + offset, MEDIAN-ANCHORED (gain * gen_q50 + offset =
    photo_q50 exactly) with a NON-NEGATIVE offset; the one free parameter (the offset) is fitted to the
    matched 10..90 % quantiles. Why constrained: the photo's spread is lighting (sheen, speculars, shading)
    as much as albedo, and on every product it exceeds the generator's; an unconstrained fit then drives the
    gain to its bound with a large negative offset that maps darker generator colours (tortoise tips on
    Miu's gold temples) to black. With offset >= 0 the map is gain-only unless the photo's black level is
    lifted, and it never produces negative light."""
    g_lin = srgb_to_linear(gen_rgb).reshape(-1, 3)
    p_lin = srgb_to_linear(photo_rgb).reshape(-1, 3)
    gain, off, info = np.ones(3), np.zeros(3), {"pairs": int(len(g_lin)), "channels": [],
                                                "model": "median-anchored gain + offset >= 0, quantiles 10..90 %",
                                                "gain_bounds": list(bounds)}
    if len(g_lin) < GAIN_MIN_PAIRS:
        info["skipped"] = "too_few_pairs"
        return gain, off, info
    for c in range(3):
        qg = np.percentile(g_lin[:, c], GAIN_QUANTILES)
        qp = np.percentile(p_lin[:, c], GAIN_QUANTILES)
        mg, mp = float(np.median(g_lin[:, c])), float(np.median(p_lin[:, c]))
        if mg < 1e-6:
            g, o = 1.0, max(mp - mg, 0.0)
        else:
            # offset o in [0, mp): gain(o) = (mp - o) / mg, least squares on the central quantile pairs
            dq = qg - mg
            den = float(np.sum(dq * dq))
            if den < 1e-12:
                o = 0.0
            else:
                # residual r(o) = qp - (mp - o) (qg / mg) - o = (qp - mp qg/mg) + o (qg/mg - 1); linear in o
                a = qg / mg - 1.0
                bq = qp - mp * qg / mg
                o = float(-np.sum(a * bq) / max(np.sum(a * a), 1e-12))
            o = float(np.clip(o, 0.0, max(mp - bounds[0] * mg, 0.0)))     # keeps gain >= the lower bound
            g = (mp - o) / mg
            if not (bounds[0] <= g <= bounds[1]):
                g = float(np.clip(g, *bounds))
                o = max(mp - g * mg, 0.0)
        gain[c], off[c] = g, o
        info["channels"].append({"gen_q10_q50_q90": [round(float(x), 5) for x in (qg[0], mg, qg[-1])],
                                 "photo_q10_q50_q90": [round(float(x), 5) for x in (qp[0], mp, qp[-1])],
                                 "gain": round(float(g), 4), "offset": round(float(o), 5)})
    return gain, off, info


def apply_gain(rgb, gain, offset) -> np.ndarray:
    """sRGB 0..255 (float or uint8) -> gained sRGB float 0..255 (linear-light affine per channel)."""
    lin = srgb_to_linear(rgb) * np.asarray(gain) + np.asarray(offset)
    return linear_to_srgb(lin)


# =========================================================================== raster helpers
def rasterize_triangles(tri: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Texel-centre rasterisation of triangles given in TEXEL coordinates (texel (i, j) centre = (j+.5, i+.5)).
    Returns face id (H,W) int32 (-1 empty; later triangles win) and barycentrics (H,W,3) float32."""
    H, W = shape
    fid = np.full((H, W), -1, np.int32)
    bary = np.zeros((H, W, 3), np.float32)
    tri = np.asarray(tri, np.float64)
    lo = np.floor(tri.min(1) - 0.5).astype(int)
    hi = np.ceil(tri.max(1) - 0.5).astype(int)
    for k in range(len(tri)):
        x0, y0 = max(lo[k, 0], 0), max(lo[k, 1], 0)
        x1, y1 = min(hi[k, 0], W - 1), min(hi[k, 1], H - 1)
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = tri[k]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        xs = np.arange(x0, x1 + 1) + 0.5
        ys = np.arange(y0, y1 + 1) + 0.5
        X, Y = np.meshgrid(xs, ys)
        l0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        l1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if not m.any():
            continue
        sub = (slice(y0, y1 + 1), slice(x0, x1 + 1))
        fid[sub][m] = k
        bary[sub][m] = np.stack([l0[m], l1[m], l2[m]], -1)
    return fid, bary


def nearest_fill(img: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Every pixel takes the value of its nearest valid pixel; also returns that distance (px)."""
    if not valid.any():
        return img.copy(), np.full(valid.shape, np.inf)
    dist, (iy, ix) = ndimage.distance_transform_edt(~valid, return_indices=True)
    return img[iy, ix], dist


def erode_px(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.erode(mask.astype(np.uint8), se, borderValue=0).astype(bool)


def erode_by_component(mask: np.ndarray, r: int) -> np.ndarray:
    """Erode by r px; a connected component that would vanish keeps its largest non-empty erosion (r-1..0)."""
    out = erode_px(mask, r)
    lab, n = ndimage.label(mask)
    if n == 0:
        return out
    kept = np.bincount(lab[out], minlength=n + 1)
    boxes = ndimage.find_objects(lab)
    for comp in np.nonzero(kept[1:] == 0)[0] + 1:
        sl = boxes[comp - 1]
        # pad the box so the erosion border (value 0) is outside the component
        y0, y1 = max(sl[0].start - 1, 0), min(sl[0].stop + 1, mask.shape[0])
        x0, x1 = max(sl[1].start - 1, 0), min(sl[1].stop + 1, mask.shape[1])
        cm = lab[y0:y1, x0:x1] == comp
        for rr in range(r - 1, -1, -1):
            e = erode_px(cm, rr)
            if e.any():
                out[y0:y1, x0:x1] |= e
                break
    return out


def poly_raster(polys: list[np.ndarray], shape) -> np.ndarray:
    return depth.poly_mask(polys, shape)


_LAB_EDGES = (np.linspace(0, 100, 21), np.linspace(-90, 90, 31), np.linspace(-90, 90, 31))


def lab_hist(rgb: np.ndarray) -> np.ndarray:
    """Normalised 3D Lab histogram (5 L x 6 a/b units), Gaussian-smoothed one bin."""
    h, _ = np.histogramdd(lab_of(np.asarray(rgb, float).reshape(-1, 3)), bins=_LAB_EDGES)
    h = ndimage.gaussian_filter(h, 1.0, mode="constant")
    return h / max(h.sum(), 1e-12)


def hist_lookup(h: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    lab = lab_of(np.asarray(rgb, float).reshape(-1, 3))
    idx = [np.clip(np.searchsorted(e, lab[:, i], side="right") - 1, 0, len(e) - 2) for i, e in enumerate(_LAB_EDGES)]
    return h[idx[0], idx[1], idx[2]]


def colour_is_lens(rgb: np.ndarray, h_lens: np.ndarray, h_frame: np.ndarray, ratio: float = LENSLIKE_RATIO) -> np.ndarray:
    """True where a colour is ``ratio`` x more frequent among lens colours than among frame colours."""
    pl, pf = hist_lookup(h_lens, rgb), hist_lookup(h_frame, rgb)
    return (pl > ratio * pf) & (pl > 1e-5)


def lens_like_mask(photo: np.ndarray, cap: np.ndarray, lens_ref: np.ndarray, frame_ref: np.ndarray,
                   ratio: float = LENSLIKE_RATIO, lens_region: np.ndarray | None = None,
                   band_px: float | None = None) -> tuple[np.ndarray, dict]:
    """Cap pixels whose Lab colour is ``ratio`` x more frequent in the lens reference than in the frame
    reference (``colour_is_lens``). With ``lens_region``/``band_px`` only lens-like pixels within ``band_px``
    of the lens region AND 8-connected to it (through lens-like pixels) count: lens colour leaks in from the
    lens edge; an isolated dark tortoise spot in the middle of a rim is frame even when its colour resembles
    a dark lens."""
    info = {"lens_ref_px": int(lens_ref.sum()), "frame_ref_px": int(frame_ref.sum())}
    out = np.zeros(cap.shape, bool)
    if lens_ref.sum() < 50 or frame_ref.sum() < 50 or not cap.any():
        info["skipped"] = True
        return out, info
    lensy = colour_is_lens(photo[cap], lab_hist(photo[lens_ref]), lab_hist(photo[frame_ref]), ratio)
    out[cap] = lensy
    info["colour_lens_like_px"] = int(lensy.sum())
    if lens_region is not None and band_px is not None:
        near = ndimage.distance_transform_edt(~lens_region) <= band_px
        seed = ndimage.binary_dilation(lens_region, iterations=2)
        cand = out & near
        lab_c, _ = ndimage.label(cand | seed, structure=np.ones((3, 3), bool))
        touching = np.unique(lab_c[seed & (lab_c > 0)])
        out = cand & np.isin(lab_c, touching)
        info["band_px"] = round(float(band_px), 2)
    info["lens_like_px"] = int(out.sum())
    info["lens_like_share_of_cap"] = round(float(out.sum() / max(cap.sum(), 1)), 4)
    return out, info


def prepare_photo(photo: np.ndarray, allowed: np.ndarray, lens_like: np.ndarray, px_per_mm: float,
                  exclude: np.ndarray | None = None, erode: int | None = None) -> dict:
    """Valid sampling region (allowed minus lens-like, eroded, minus ``exclude``), padded photo, distance to
    valid, highlights. ``exclude`` (matte-edge backdrop mix) is removed AFTER the erosion: removed before it, it
    would cut thin rims into small components that ``erode_by_component`` then keeps un-eroded."""
    r = int(max(1, min(ERODE_PX, math.floor(ERODE_MAX_MM * px_per_mm)))) if erode is None else int(erode)
    valid = erode_by_component(allowed & ~lens_like, r) if r > 0 else (allowed & ~lens_like)
    if exclude is not None:
        valid &= ~exclude
    hsv = cv2.cvtColor(photo, cv2.COLOR_RGB2HSV)
    hl = valid & (hsv[..., 2] > HIGHLIGHT_V * 255) & (hsv[..., 1] < HIGHLIGHT_S * 255)
    src = valid & ~hl
    filled, dist = nearest_fill(photo, src if src.any() else valid)
    k = int(max(5, min(15, 2 * round(MEDIAN_MM * px_per_mm / 2) + 1)))
    med = cv2.medianBlur(np.ascontiguousarray(filled), k)
    out = filled.copy()
    out[hl] = np.minimum(photo[hl], med[hl])
    return {"img": out, "dist": dist, "valid": valid, "highlight": hl, "erode_px": r, "median_px": k,
            "pad_max_px": max(PAD_MIN_PX, PAD_MAX_MM * px_per_mm)}


def sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear sample at native pixel coordinates (integer = pixel centre), clamped at the border."""
    H, W = img.shape[:2]
    u = np.clip(np.asarray(u, float), 0, W - 1)
    v = np.clip(np.asarray(v, float), 0, H - 1)
    x0 = np.minimum(np.floor(u).astype(int), W - 2)
    y0 = np.minimum(np.floor(v).astype(int), H - 2)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    t = img.reshape(H, W, -1).astype(np.float32)
    return (t[y0, x0] * (1 - fx) * (1 - fy) + t[y0, x0 + 1] * fx * (1 - fy)
            + t[y0 + 1, x0] * (1 - fx) * fy + t[y0 + 1, x0 + 1] * fx * fy)


def sample_nearest(img: np.ndarray, u, v) -> np.ndarray:
    H, W = img.shape[:2]
    ui = np.clip(np.round(u).astype(int), 0, W - 1)
    vi = np.clip(np.round(v).astype(int), 0, H - 1)
    inb = (u > -0.5) & (u < W - 0.5) & (v > -0.5) & (v < H - 0.5)
    return img[vi, ui], inb


# =========================================================================== generator bake
def generator_lens_faces(gen, a2: dict, src: depth.SourceView, df: depth.DepthField, ppm: float,
                         dilate_mm: float = LENS_DILATE_MM, band=PLATE_BAND_MM) -> tuple[np.ndarray, dict]:
    """Full-mesh generator faces that are lens plate: centroid inside the S2 lens polygon (source px) dilated
    ``dilate_mm`` and within ``band`` of that lens's S4 surface."""
    polys = depth.lens_polys(a2)
    H, W = src.shape
    lab = np.zeros((H, W), np.uint8)
    r = int(round(dilate_mm * ppm))
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    for i, p in enumerate(polys, start=1):
        m = np.zeros((H, W), np.uint8)
        cv2.fillPoly(m, [np.round(np.asarray(p) * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
        m = cv2.dilate(m, se)
        lab[(m > 0) & (lab == 0)] = i
    C = gen.V[gen.F].mean(axis=1).astype(np.float64)
    uv = src.project(C, gen.frame)
    ui, vi = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
    inb = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    li = np.zeros(len(C), np.int8)
    li[inb] = lab[vi[inb], ui[inb]]
    out = np.zeros(len(C), bool)
    per = []
    for i in range(1, len(polys) + 1):
        sel = np.nonzero(li == i)[0]
        if not len(sel):
            per.append(0)
            continue
        s = df.lens_surface(i).signed(C[sel])
        ok = np.isfinite(s) & (s >= band[0]) & (s <= band[1])
        out[sel[ok]] = True
        per.append(int(ok.sum()))
    return out, {"lens_faces": int(out.sum()), "per_lens": per, "dilate_mm": dilate_mm, "band_mm": list(band),
                 "candidates_in_polygon": int((li > 0).sum())}


class Baker:
    """Closest-point colour lookup on the generator with its lens faces removed."""

    def __init__(self, gen, exclude: np.ndarray, tex: np.ndarray):
        keep = np.nonzero(~exclude)[0]
        self.face_ids = keep
        self.gen = gen
        self.tex = tex
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(gen.V, np.float32)),
                                 o3d.core.Tensor(np.ascontiguousarray(gen.F[keep], np.uint32)))

    def closest(self, P: np.ndarray) -> dict:
        P = np.ascontiguousarray(np.asarray(P, np.float32).reshape(-1, 3))
        if not len(P):
            return {"face": np.zeros(0, np.int64), "bary": np.zeros((0, 2)), "dist": np.zeros(0)}
        ans = self.scene.compute_closest_points(o3d.core.Tensor(P))
        prim = ans["primitive_ids"].numpy().astype(np.int64)
        pts = ans["points"].numpy().astype(np.float64)
        return {"face": self.face_ids[prim], "bary": ans["primitive_uvs"].numpy().astype(np.float64),
                "dist": np.linalg.norm(pts - P, axis=1)}

    def colour(self, P: np.ndarray, chunk: int = 400_000) -> tuple[np.ndarray, np.ndarray]:
        """Raw generator basecolor (float32 sRGB 0..255) at the closest generator point, and that distance."""
        from . import generator
        cols, dists = [], []
        P = np.asarray(P, np.float64).reshape(-1, 3)
        for s in range(0, len(P), chunk):
            c = self.closest(P[s:s + chunk])
            uv = generator.interpolate_uv(self.gen.UV, self.gen.F, c["face"], c["bary"])
            cols.append(generator.sample_texture(self.tex, uv))
            dists.append(c["dist"])
        if not cols:
            return np.zeros((0, 3), np.float32), np.zeros(0)
        return np.concatenate(cols), np.concatenate(dists)


# =========================================================================== layouts
def cap_layout(P2: np.ndarray, margin: int = CAP_MARGIN_PX, max_px: int = ATLAS_MAX) -> dict:
    """Source-pixel crop for the cap textures: texel coord = (px - origin + 0.5) * k."""
    lo = np.floor(P2.min(0)) - margin
    hi = np.ceil(P2.max(0)) + margin
    size = hi - lo + 1
    k = min(1.0, max_px / float(size.max()))
    W, H = int(math.ceil(size[0] * k)), int(math.ceil(size[1] * k))
    return {"origin": lo, "k": k, "shape": (H, W)}


def cap_texel(P2: np.ndarray, lay: dict) -> np.ndarray:
    return (np.asarray(P2, float) - lay["origin"] + 0.5) * lay["k"]


def boundary_loops(T: np.ndarray) -> list[list[tuple[int, int]]]:
    """Directed boundary edges of a triangle set chained into loops (deterministic)."""
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]).astype(np.int64)
    key = np.minimum(e[:, 0], e[:, 1]) * (int(T.max()) + 1) + np.maximum(e[:, 0], e[:, 1])
    uk, cnt = np.unique(key, return_counts=True)
    bnd = e[np.isin(key, uk[cnt == 1])]
    bnd = bnd[np.lexsort((bnd[:, 1], bnd[:, 0]))]
    out_of: dict[int, list[int]] = {}
    for i, (a, _) in enumerate(bnd):
        out_of.setdefault(int(a), []).append(i)
    used = np.zeros(len(bnd), bool)
    loops = []
    for start in range(len(bnd)):
        if used[start]:
            continue
        loop, cur = [], start
        while cur is not None and not used[cur]:
            used[cur] = True
            a, b = int(bnd[cur, 0]), int(bnd[cur, 1])
            loop.append((a, b))
            nxt = [j for j in out_of.get(b, []) if not used[j]]
            cur = nxt[0] if nxt else None
        loops.append(loop)
    return loops


def wall_layout(V: np.ndarray, T2: np.ndarray, n_front: int, max_px: int = ATLAS_MAX,
                px_per_mm: float = WALL_PX_PER_MM, pad: int = WALL_PAD) -> dict:
    """Unroll every front-cap boundary loop into strips (arc length x depth), shelf-packed.
    Returns per directed boundary edge (a, b) -> texel coords of (a front, b front, a back, b back)."""
    loops = boundary_loops(T2)
    thick = np.linalg.norm(V[n_front:2 * n_front] - V[:n_front], axis=1)
    d = float(px_per_mm)
    for _ in range(40):
        usable = (max_px - 2 * pad - 1) / d
        chunks = []
        for li, loop in enumerate(loops):
            cur, s = [], 0.0
            for a, b in loop:
                ln = float(np.linalg.norm(V[b] - V[a]))
                if cur and s + ln > usable:
                    chunks.append((li, cur))
                    cur, s = [], 0.0
                cur.append((a, b, ln))
                s += ln
            if cur:
                chunks.append((li, cur))
        x, y, shelf_h, place = 0, 0, 0, []
        for li, ch in chunks:
            w = int(math.ceil(sum(c[2] for c in ch) * d)) + 2 * pad + 1
            h = int(math.ceil(max(max(thick[a], thick[b]) for a, b, _ in ch) * d)) + 2 * pad + 1
            if x + w > max_px:
                x, y, shelf_h = 0, y + shelf_h, 0
            place.append((x, y, w, h))
            x += w
            shelf_h = max(shelf_h, h)
        total_h = y + shelf_h
        if total_h <= max_px:
            break
        d *= 0.85
    H = int(4 * math.ceil(total_h / 4))
    edges = {}
    for (li, ch), (x0, y0, _, _) in zip(chunks, place):
        s = 0.0
        for a, b, ln in ch:
            xa, xb = x0 + pad + s * d, x0 + pad + (s + ln) * d
            edges[(a, b)] = np.array([[xa, y0 + pad], [xb, y0 + pad],
                                      [xa, y0 + pad + thick[a] * d], [xb, y0 + pad + thick[b] * d]])
            s += ln
    return {"edges": edges, "shape": (H, max_px), "px_per_mm": d, "loops": len(loops), "chunks": len(chunks),
            "loop_lengths_mm": [round(float(sum(np.linalg.norm(V[b] - V[a]) for a, b in lp)), 2) for lp in loops]}


def wall_corner_texels(F: np.ndarray, wall_faces: np.ndarray, n_front: int, layout: dict) -> tuple[np.ndarray, int]:
    """(K,3,2) texel coords of the wall faces' corners; faces not matching a boundary edge get NaN."""
    out = np.full((len(wall_faces), 3, 2), np.nan)
    bad = 0
    E = layout["edges"]
    for k, f in enumerate(wall_faces):
        vs = F[f]
        fr = vs % n_front
        u = sorted(set(int(x) for x in fr))
        if len(u) != 2:
            bad += 1
            continue
        a, b = u
        key = (a, b) if (a, b) in E else ((b, a) if (b, a) in E else None)
        if key is None:
            bad += 1
            continue
        q = E[key]
        for c, v in enumerate(vs):
            back = v >= n_front
            first = int(v % n_front) == key[0]
            out[k, c] = q[(0 if first else 1) + (2 if back else 0)]
    return out, bad


# =========================================================================== rendering
def render_parts(parts: list[dict], cam: Camera, frame: NormFrame, shape: tuple[int, int], roi=None,
                 stride: int = 1) -> dict:
    """Base colour + Lambert of textured parts. part: {V, F, UVc (M,3,2)|None, tex [imgs]|None, tex_id (M,)|None,
    rgb, lens bool}. Returns base (gh,gw,3) float, label (-1 bg), shade, lens_front (bool), grid."""
    opaque = [p for p in parts if not p.get("lens")]
    lenses = [p for p in parts if p.get("lens")]
    out = None
    toward = raster.camera_basis(cam)[2]
    for li, p in enumerate(opaque):
        r = raster.render(p["V"], p["F"], cam, frame, shape, stride, roi, want_bary=True, want_normal=True)
        if out is None:
            gh, gw = r["mask"].shape
            out = {"base": np.full((gh, gw, 3), 255.0, np.float32), "label": np.full((gh, gw), -1, np.int8),
                   "depth": np.full((gh, gw), np.inf, np.float32), "shade": np.ones((gh, gw), np.float32),
                   "grid": r["grid"]}
        m = r["mask"] & (r["depth"] < out["depth"])
        if not m.any():
            continue
        f = r["face_id"][m]
        b = r["bary"][m].astype(np.float64)
        w = np.column_stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]])
        if p.get("UVc") is not None and p.get("tex") is not None:
            uv = np.einsum("kc,kcd->kd", w, p["UVc"][f].astype(np.float64))
            tid = p["tex_id"][f] if p.get("tex_id") is not None else np.zeros(len(f), int)
            col = np.zeros((len(f), 3), np.float32)
            from . import generator
            for t in np.unique(tid):
                s = tid == t
                col[s] = generator.sample_texture(p["tex"][t], uv[s])
        else:
            col = np.tile(np.asarray(p.get("rgb", (180, 180, 180)), np.float32), (len(f), 1))
        out["base"][m] = col
        out["label"][m] = li
        out["depth"][m] = r["depth"][m]
        out["shade"][m] = 0.45 + 0.55 * np.abs(r["normal"][m] @ toward)
    if out is None:
        raise ValueError("no opaque parts")
    out["lens_front"] = np.zeros(out["label"].shape, bool)
    for p in lenses:
        r = raster.render(p["V"], p["F"], cam, frame, shape, stride, roi)
        out["lens_front"] |= r["mask"] & (r["depth"] < out["depth"])
    return out


def eval_supersample(cam: Camera, frame: NormFrame, gen) -> int:
    """Supersampling of an evaluation render so each photo pixel integrates >= ``EVAL_MIN_PX_PER_MM`` samples/mm."""
    from . import cameras
    ppm = cameras.px_per_mm_at(cam, frame, cameras.front_piece_centre(gen))
    return int(min(EVAL_MAX_SUPERSAMPLE, max(1, math.ceil(EVAL_MIN_PX_PER_MM / max(ppm, 1e-6)))))


def eval_frame_mask(r: dict, photo_c: np.ndarray, fg_c: np.ndarray, lens0_c: np.ndarray, h_lens, h_frame) -> tuple:
    """The comparison masks of the S7 evaluation (frame, temples; eroded ``EVAL_ERODE_PX``): the photo's matte, not
    its lens proposal, not a lens in front in the render, not lens-coloured (the front photo's lens vs frame colour
    model), not part lens colour next to the lens (``lens_mix_mask``). Returns (frame mask, temple mask, info with
    the un-eroded comparable mask as ``_ok``)."""
    info = {}
    ok = fg_c & ~lens0_c & ~r["lens_front"]
    if h_lens is not None and h_frame is not None:
        cand = ok & (r["label"] >= 0)
        lc = np.zeros_like(ok)
        lc[cand] = colour_is_lens(photo_c[cand], h_lens, h_frame)
        ok &= ~lc
        info["lens_coloured_px_excluded"] = int(lc.sum())
    if lens0_c.any():
        reg_ = ok & (r["label"] >= 0)
        lm_, _ = lens_mix_mask(photo_c, reg_, lens0_c, np.median(photo_c[reg_], axis=0) if reg_.any() else (0, 0, 0))
        ok &= ~lm_
        info["lens_mix_px_excluded"] = int(lm_.sum())
    info["_ok"] = ok
    return erode_px((r["label"] == 0), EVAL_ERODE_PX) & ok, erode_px((r["label"] >= 1), EVAL_ERODE_PX) & ok, info


def eval_roi(fg: np.ndarray) -> tuple[int, int, int, int]:
    H, W = fg.shape
    ys, xs = np.nonzero(fg)
    m = int(0.08 * max(xs.max() - xs.min(), 1))
    return max(0, xs.min() - m), max(0, ys.min() - m), min(W, xs.max() + m + 1), min(H, ys.max() + m + 1)


def front_view_gain(parts: list[dict], cam: Camera, frame: NormFrame, photo: np.ndarray, fg: np.ndarray,
                    lens0: np.ndarray, gen, h_lens, h_frame) -> tuple[np.ndarray | None, dict]:
    """Per-channel linear gain that makes the FRONT-view render of the frame (the S7 evaluation's own render and mask,
    each photo pixel integrated over its footprint) have the front photo's mean frame colour."""
    H, W = photo.shape[:2]
    roi = eval_roi(fg)
    x0, y0, x1, y1 = roi
    r = render_parts(parts, cam, frame, (H, W), roi)
    ss = eval_supersample(cam, frame, gen)
    base = supersampled_base(parts, cam, frame, (H, W), roi, ss) if ss > 1 else r["base"]
    photo_c = photo[y0:y1, x0:x1]
    fm, _, _ = eval_frame_mask(r, photo_c, fg[y0:y1, x0:x1], lens0[y0:y1, x0:x1], h_lens, h_frame)
    if fm.sum() < 50:
        return None, {"skipped": "fewer than 50 comparable front pixels", "pixels": int(fm.sum())}
    p = srgb_to_linear(photo_c[fm].astype(float)).mean(0)
    q = srgb_to_linear(base[fm].astype(float)).mean(0)
    g = np.clip(p / np.maximum(q, 1e-6), 0.5, 2.0)
    return g, {"pixels": int(fm.sum()), "supersample": ss, "linear_gain": np.round(g, 4).tolist(),
               "de00_mean_colour_before": round(mean_colour_de00(photo_c[fm], base[fm]), 2)}


def supersampled_base(parts: list[dict], cam: Camera, frame: NormFrame, shape: tuple[int, int], roi, ss: int
                      ) -> np.ndarray:
    """Base colour of ``render_parts`` integrated over each photo pixel: rendered ``ss`` x ``ss`` finer (the same camera,
    scale x ss, pixel centres kept) and box-averaged, the backdrop white where nothing is hit - what a camera pixel of a
    LOW-resolution photo records (INVU's front: 1.3 px/mm, a pixel is 0.75 mm of frame blended with its neighbours);
    a point sample there compares one texel with a blend."""
    if ss <= 1:
        return render_parts(parts, cam, frame, shape, roi)["base"]
    cs = Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale * ss, cam.center_x * ss + (ss - 1) / 2.0,
                cam.center_y * ss + (ss - 1) / 2.0)
    x0, y0, x1, y1 = roi
    r = render_parts(parts, cs, frame, (shape[0] * ss, shape[1] * ss), (x0 * ss, y0 * ss, x1 * ss, y1 * ss))
    b = r["base"]
    gh, gw = (y1 - y0), (x1 - x0)
    return b[:gh * ss, :gw * ss].reshape(gh, ss, gw, ss, 3).mean(axis=(1, 3))


def shaded_image(r: dict) -> np.ndarray:
    img = r["base"] * r["shade"][..., None]
    img[r["label"] < 0] = 255.0
    lf = r["lens_front"]
    img[lf] = 0.7 * img[lf] + 0.3 * np.asarray(LENS_TINT, np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def colour_metrics(photo: np.ndarray, render: np.ndarray, mask: np.ndarray) -> dict | None:
    if mask.sum() < 50:
        return None
    pb = cv2.GaussianBlur(photo.astype(np.float32), (0, 0), EVAL_BLUR)
    rb = cv2.GaussianBlur(render.astype(np.float32), (0, 0), EVAL_BLUR)
    dp = de00(pb[mask], rb[mask])
    return {"pixels": int(mask.sum()), "de00_mean_colour": round(mean_colour_de00(photo[mask], render[mask]), 2),
            "de00_pixel_median": round(float(np.median(dp)), 2), "de00_pixel_p90": round(float(np.percentile(dp, 90)), 2),
            "photo_mean_rgb": [round(float(x), 1) for x in photo[mask].mean(0)],
            "render_mean_rgb": [round(float(x), 1) for x in render[mask].mean(0)]}


# =========================================================================== AR appearance calibration
# Shared by S7 (frame material fit) and S8 (mirror lens fit). Every candidate material is rendered in the
# ACTUAL AR runtime (TryOnRenderer through ``python -m qa.provider_comparison --ar-check``, the runtime's native
# room lighting, a solid backdrop in the photo's own backdrop colour, shadows off so the backdrop stays uniform)
# and compared with the photo on the same part of the glasses. The runtime reports each render's exact camera
# (projection, view, asset_to_world): the exported geometry is ray-cast through it to label every render pixel
# (which part / primitive is hit first and what lies behind it), so no silhouette guessing is involved.
AR_FIT_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "back", "type": "asset-back"},
                {"id": "side_pos", "yaw_degrees": 80}, {"id": "side_neg", "yaw_degrees": -80})
AR_FIT_ROUGHNESS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
AR_FIT_METALLIC = {"acetate": (0.0,), "metal": (0.0, 0.5, 1.0)}   # dielectric frames are never metallic
AR_FIT_GAIN = (GAIN_BOUNDS[0], 1.0)      # base-colour gain: the photo colour is an UPPER bound on the albedo
AR_FIT_GAIN_STEP = 0.0125
AR_FIT_SLABS = 10
AR_FIT_MIN_PX = 150
AR_BG_DIFF = 6.0                         # render pixel differs from the solid backdrop by more than this (0..255)
EDGE_MIX_PX = 2                          # matte-edge band (native px) where backdrop light mixes into the frame
EDGE_MIX_ALPHA = 0.25                    # ... a pixel more than 25 % of the way from the frame colour to the backdrop
EDGE_MIX_MIN_DE = 20.0                   # ... only when frame and backdrop colours differ (dE00) by at least this

# three.js r185 ACESFilmicToneMapping (exposure 1, the runtime's setting), applied to frame/temple materials;
# lens materials are toneMapped = false in the runtime (renderer.ts configure()).
_ACES_IN = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]])
_ACES_OUT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]])
_ACES_IN_INV = np.linalg.inv(_ACES_IN)
_ACES_OUT_INV = np.linalg.inv(_ACES_OUT)


def aces_filmic(c) -> np.ndarray:
    """three.js ACESFilmicToneMapping of linear scene radiance (..., 3) -> display-linear [0, 1]."""
    v = (np.asarray(c, float) / 0.6) @ _ACES_IN.T
    a = v * (v + 0.0245786) - 0.000090537
    b = v * (0.983729 * v + 0.4329510) + 0.238081
    return np.clip((a / b) @ _ACES_OUT.T, 0.0, 1.0)


def aces_filmic_inverse(o) -> np.ndarray:
    """Exact inverse of ``aces_filmic`` below the clip (display-linear -> scene radiance)."""
    y = np.asarray(o, float) @ _ACES_OUT_INV.T
    A = 1.0 - 0.983729 * y
    B = 0.0245786 - 0.4329510 * y
    C = -(0.000090537 + 0.238081 * y)
    v = (-B + np.sqrt(np.maximum(B * B - 4 * A * C, 0.0))) / (2 * A)
    return (v @ _ACES_IN_INV.T) * 0.6


def glb_split(data: bytes) -> tuple[dict, bytes]:
    """(JSON document, BIN chunk) of a GLB."""
    import json
    import struct
    jl, = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20:20 + jl])
    off = 20 + jl
    bl, = struct.unpack_from("<I", data, off)
    return doc, data[off + 8:off + 8 + bl]


def glb_pack(doc: dict, binary: bytes) -> bytes:
    import json
    import struct
    js = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")
    js += b" " * (-len(js) % 4)
    binary = bytes(binary) + b"\0" * (-len(binary) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def glb_patch_materials(data: bytes, patch: dict[str, dict]) -> bytes:
    """Copy of a GLB with material fields replaced by name: ``{material name: {"pbr": {...}, "ext": {...},
    "top": {...}}}`` (pbrMetallicRoughness keys / extensions / top-level keys). Geometry and texture bytes are
    untouched, so every candidate of a sweep renders the exact same asset apart from its factors."""
    import copy as _copy
    doc, binary = glb_split(data)
    doc = _copy.deepcopy(doc)
    for m in doc.get("materials", []):
        p = patch.get(m.get("name"))
        if not p:
            continue
        m.setdefault("pbrMetallicRoughness", {}).update(_copy.deepcopy(p.get("pbr", {})))
        if p.get("ext"):
            ext = m.setdefault("extensions", {})
            for k, v in p["ext"].items():
                if v is None:
                    ext.pop(k, None)
                else:
                    ext[k] = _copy.deepcopy(v)
                    if k not in doc.setdefault("extensionsUsed", []):
                        doc["extensionsUsed"].append(k)
                        doc["extensionsUsed"].sort()
            if not ext:
                m.pop("extensions")
        m.update(_copy.deepcopy(p.get("top", {})))
    return glb_pack(doc, binary)


def glb_primitives(data: bytes) -> list[dict]:
    """Per primitive of a GLB written by ``bsa.export``: node name, material name, V (asset metres), F, and UV
    (TEXCOORD_0, glTF v down) when present."""
    doc, binary = glb_split(data)

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        dt = np.float32 if a["componentType"] == 5126 else np.uint32
        o = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        arr = np.frombuffer(binary, dt, a["count"] * n, o)
        return arr.reshape(a["count"], n) if n > 1 else arr
    out = []
    for node in doc["nodes"]:
        if "mesh" not in node:
            continue
        for p in doc["meshes"][node["mesh"]]["primitives"]:
            prim = {"node": node.get("name"), "material": doc["materials"][p["material"]].get("name"),
                    "V": acc(p["attributes"]["POSITION"]).astype(np.float64),
                    "F": acc(p["indices"]).reshape(-1, 3).astype(np.int64)}
            if "TEXCOORD_0" in p["attributes"]:
                prim["UV"] = acc(p["attributes"]["TEXCOORD_0"]).astype(np.float64)
            out.append(prim)
    return out


def hit_uv(prim: dict, face: np.ndarray, points: np.ndarray) -> np.ndarray:
    """TEXCOORD_0 of a primitive at ray hits (its local face ids, hit points in asset metres): the barycentric
    interpolation of the hit face's corner UVs."""
    F = prim["F"][np.asarray(face, np.int64)]
    A, B, C = (prim["V"][F[:, k]] for k in range(3))
    P = np.asarray(points, float)
    v0, v1, v2 = B - A, C - A, P - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-30, 1e-30, d00 * d11 - d01 * d01)
    b1 = (d11 * d20 - d01 * d21) / den
    b2 = (d00 * d21 - d01 * d20) / den
    b0 = 1.0 - b1 - b2
    UV = prim["UV"]
    return b0[:, None] * UV[F[:, 0]] + b1[:, None] * UV[F[:, 1]] + b2[:, None] * UV[F[:, 2]]


def _m4(a) -> np.ndarray:
    return np.asarray(a, float).reshape(4, 4).T          # three.js column-major -> row-major


def ar_render(glbs: dict[str, bytes], out_dir, views=AR_FIT_VIEWS, background_rgb=(255, 255, 255),
              shadows: bool = False, width_mm: float | None = None, timeout_s: int = 1800) -> dict:
    """Write the GLBs, render them in the actual AR runtime and return per model/view the render path and its
    exact camera: {"models": {name: {"status", "error", "optical_meshes", "views": {view: {png, P, V, A2W, W, H,
    camera_in_asset}}}}, "harness_status", "harness_errors", "source_snapshot_stable", "returncode",
    "validation"}. ``validation`` is ``archeck.validate_ar_result`` over the same report (every model, the bytes
    written here, exactly ``views``): a consumer that scores a render without its ``ok`` (or ``harness_ok`` plus
    its own row's ok) is reading a failed run. The GLB files are removed afterwards (the renders, manifest and
    report stay)."""
    import hashlib
    import json
    import subprocess
    import sys
    from pathlib import Path
    from . import archeck
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob("*.png")) + list(out_dir.glob("*.glb")) + [out_dir / "report.json"]:
        if old.exists():
            old.unlink()
    paths = {}
    for name, data in glbs.items():
        p = out_dir / f"{archeck.safe_id(name)}.glb"
        p.write_bytes(data)
        paths[name] = p
    if width_mm is None:
        width_mm = archeck.front_width_mm(next(iter(paths.values())))
    manifest, ids = archeck.write_manifest(paths, out_dir, ar_views=views, shadows=shadows,
                                           background="checker" if background_rgb is None else "solid",
                                           width_mm={n: width_mm for n in paths},
                                           description="BSA appearance calibration (candidate materials, same geometry)")
    doc = json.loads(manifest.read_text())
    if background_rgb is not None:                        # None: the harness's checker fixture (review renders)
        doc["background_color"] = "#%02x%02x%02x" % tuple(int(round(float(c))) for c in background_rgb)
    manifest.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "qa.provider_comparison", "--manifest", str(manifest), "--output", str(out_dir), "--ar-check"]
    try:
        proc = subprocess.run(cmd, cwd=str(core.AUTOMATION), capture_output=True, text=True, timeout=timeout_s,
                              encoding="utf-8", errors="replace")
        rc, err = proc.returncode, proc.stderr[-1500:]
    except subprocess.TimeoutExpired:
        rc, err = None, f"timeout after {timeout_s} s"
    for p in paths.values():
        if p.exists():
            p.unlink()
    parsed = archeck.parse_report(out_dir, ids)
    out = {"returncode": rc, "stderr_tail": err, "models": {}, "manifest": str(manifest),
           "background_hex": doc.get("background_color", "checker"), "harness_status": parsed.get("harness_status"),
           "harness_errors": parsed.get("harness_errors", []), "source_snapshot_stable": parsed.get("source_snapshot_stable")}
    out["validation"] = archeck.validate_ar_result(
        {**parsed, "returncode": rc}, expected_models={n: hashlib.sha256(d).hexdigest() for n, d in glbs.items()},
        expected_views=[v["id"] for v in views])
    rp = out_dir / "report.json"
    if not rp.exists():
        return out
    report = json.loads(rp.read_text(encoding="utf-8"))
    for row in report.get("cases", []):
        name = ids.get(row.get("id"), row.get("id"))
        vw = {}
        for r in row.get("renders", []):
            if r.get("mode") != "actual-ar" or "camera" not in r or "spatial" not in r:
                continue
            vw[r["view"]] = {"png": str(out_dir / r["filename"]), "P": _m4(r["camera"]["projection_matrix"]),
                             "V": _m4(r["camera"]["view_matrix"]), "A2W": _m4(r["spatial"]["asset_to_world"]),
                             "W": int(r["camera"]["width"]), "H": int(r["camera"]["height"]),
                             "camera_in_asset": np.asarray(r["spatial"].get("camera_origin_in_asset", [0, 0, 0]), float)}
        out["models"][name] = {"status": row.get("status"), "optical_meshes": row.get("optical_meshes_detected"),
                               "error": (row.get("error") or "").splitlines()[0] if row.get("error") else None,
                               "views": vw}
    return out


def ar_project(meta: dict, X: np.ndarray) -> np.ndarray:
    """Asset-frame points (metres) -> render pixel coordinates (x right, y down; pixel centre = integer + 0.5)."""
    M = meta["P"] @ meta["V"] @ meta["A2W"]
    h = np.c_[np.asarray(X, float), np.ones(len(X))] @ M.T
    ndc = h[:, :2] / h[:, 3:4]
    return np.c_[(ndc[:, 0] + 1) * meta["W"] / 2, (1 - ndc[:, 1]) * meta["H"] / 2]


def ar_px_per_mm(meta: dict) -> float:
    """Render px per model mm at the asset origin (the bridge), along the image's horizontal axis."""
    d = np.linalg.inv(meta["A2W"])[:3, :3] @ np.array([1.0, 0.0, 0.0])
    d /= max(np.linalg.norm(d), 1e-12)
    uv = ar_project(meta, np.vstack([np.zeros(3), 0.01 * d]))
    return float(np.linalg.norm(uv[1] - uv[0]) / 10.0)


def ar_label(meta: dict, prims: list[dict]) -> dict:
    """Ray-cast the exported geometry through the render's camera: per pixel the first-hit primitive index
    (-1 none), the primitive hit next behind it (-1 none) and the first-hit face id (local to its primitive)."""
    W, H = meta["W"], meta["H"]
    Mi = np.linalg.inv(meta["P"] @ meta["V"] @ meta["A2W"])
    xs, ys = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    nx, ny = (xs / W * 2 - 1).ravel(), (1 - ys / H * 2).ravel()

    def unproject(z):
        h = np.stack([nx, ny, np.full_like(nx, z), np.ones_like(nx)], -1) @ Mi.T
        return h[:, :3] / h[:, 3:4]
    a, b = unproject(-1.0), unproject(1.0)
    d = b - a
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    Vs, Fs, pid, lid = [], [], [], []
    base = 0
    for i, p in enumerate(prims):
        Vs.append(p["V"])
        Fs.append(p["F"] + base)
        pid.append(np.full(len(p["F"]), i))
        lid.append(np.arange(len(p["F"])))
        base += len(p["V"])
    pid, lid = np.concatenate(pid), np.concatenate(lid)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.vstack(Vs).astype(np.float32)), o3d.core.Tensor(np.vstack(Fs).astype(np.uint32)))
    ans = scene.cast_rays(o3d.core.Tensor(np.c_[a, d].astype(np.float32)))
    t = ans["t_hit"].numpy().astype(np.float64)
    g = ans["primitive_ids"].numpy().astype(np.int64)
    hit = np.isfinite(t)
    first = np.full(len(t), -1, np.int64)
    first[hit] = pid[g[hit]]
    face = np.full(len(t), -1, np.int64)
    face[hit] = lid[g[hit]]
    points = np.full((len(t), 3), np.nan)
    points[hit] = a[hit] + d[hit] * t[hit, None]
    a2 = a + d * np.where(hit, t + 1e-5, 0.0)[:, None]
    ans2 = scene.cast_rays(o3d.core.Tensor(np.c_[a2, d].astype(np.float32)))
    t2 = ans2["t_hit"].numpy()
    g2 = ans2["primitive_ids"].numpy().astype(np.int64)
    hit2 = hit & np.isfinite(t2)
    second = np.full(len(t), -1, np.int64)
    second[hit2] = pid[g2[hit2]]
    return {"first": first.reshape(H, W), "second": second.reshape(H, W), "face": face.reshape(H, W),
            "points": points.reshape(H, W, 3)}


def lab_slabs(lab: np.ndarray, n: int = AR_FIT_SLABS) -> np.ndarray:
    """Mean Lab of the n equal-count slabs of a pixel set sorted by L* (a tone-and-colour distribution)."""
    o = np.argsort(lab[:, 0], kind="stable")
    return np.array([s.mean(0) for s in np.array_split(lab[o], n)])


def slab_distance(lab_a: np.ndarray, lab_b: np.ndarray, n: int = AR_FIT_SLABS) -> float:
    """Mean dE00 between the L*-sorted slab means of two pixel sets: 0 when the brightness distributions and
    the colour at every brightness agree. Registration-free (speculars sit elsewhere under other lighting)."""
    return float(deltaE_ciede2000(lab_slabs(lab_a, n), lab_slabs(lab_b, n)).mean())


def backdrop_mix_mask(photo: np.ndarray, region: np.ndarray, outside: np.ndarray, backdrop_rgb,
                      frame_rgb, band_px: int = EDGE_MIX_PX, alpha_max: float = EDGE_MIX_ALPHA) -> tuple[np.ndarray, dict]:
    """Region pixels within ``band_px`` of ``outside`` (backdrop) whose linear colour lies more than
    ``alpha_max`` of the way from the frame colour to the backdrop: matte-edge pixels that are part backdrop
    (anti-aliasing and JPEG blur span ~2 px whatever the photo's px/mm; on INVU's 1.3 px/mm front that is 1.5 mm
    of frame). Skipped when the frame's colour is close to the backdrop's (no mixing axis)."""
    out = np.zeros(region.shape, bool)
    info = {"band_px": band_px, "alpha_max": alpha_max}
    f = srgb_to_linear(np.asarray(frame_rgb, float))
    b = srgb_to_linear(np.asarray(backdrop_rgb, float))
    if float(deltaE_ciede2000(lab_of(np.asarray(frame_rgb, float)), lab_of(np.asarray(backdrop_rgb, float)))) < EDGE_MIX_MIN_DE:
        info["skipped_frame_near_backdrop"] = True
        return out, info
    near = region & (ndimage.distance_transform_edt(~outside) <= band_px)
    if near.any():
        p = srgb_to_linear(photo[near].astype(float))
        alpha = ((p - f) @ (b - f)) / max(float((b - f) @ (b - f)), 1e-12)
        out[near] = alpha > alpha_max
    info["mixed_px"] = int(out.sum())
    return out, info


def lens_mix_mask(photo: np.ndarray, region: np.ndarray, lens: np.ndarray, frame_rgb) -> tuple[np.ndarray, dict]:
    """Frame pixels next to the LENS that are part lens colour: the matte-edge backdrop-mix rule
    (``backdrop_mix_mask``) with the lens proposal as the outside and the lens's median colour (its interior, eroded
    ``EDGE_MIX_PX``) as the colour mixed in. Anti-aliasing and JPEG blur span ~2 px whatever the px/mm: on INVU's
    1.3 px/mm front that is 1.5 mm of frame carrying the cyan lens (47 of its 124 evaluated frame pixels read
    (31, 57, 83) against the frame's (33, 41, 56))."""
    lens = np.asarray(lens, bool)
    core_ = erode_px(lens, EDGE_MIX_PX)
    if core_.sum() < 50 or not region.any():
        return np.zeros(region.shape, bool), {"skipped": "no lens interior"}
    lens_rgb = np.median(photo[core_], axis=0)
    out, info = backdrop_mix_mask(photo, region, lens, lens_rgb, frame_rgb)
    info["lens_rgb"] = [round(float(x), 1) for x in lens_rgb]
    return out, info


def _render_region(img: np.ndarray, lab: dict, opaque: list[int], bg_rgb) -> np.ndarray:
    """Render pixels showing an opaque part: first hit is a frame/temple primitive, the pixel differs from the
    backdrop (the runtime clips/drops temple ends our static geometry still has), eroded 1 px (anti-aliasing)."""
    diff = np.abs(img.astype(float) - np.asarray(bg_rgb, float)[None, None]).max(-1) > AR_BG_DIFF
    return erode_px(np.isin(lab["first"], opaque) & diff, 1)


OCCLUSION_TOL_MM = 1.0


def photo_samplers(photos: dict, a0: dict, s0: dict, cams: dict, frame: NormFrame, centre_mm: np.ndarray,
                   lens_polys_front: list[np.ndarray], occ_V: np.ndarray, occ_F: np.ndarray,
                   views=core.FIT_VIEWS) -> dict:
    """Per fit view, a sampler X_mm (model-frame surface points seen in an AR render) -> the photo's colour
    there: projected through the frozen S3 camera, kept only where the point is the front-most surface of the
    assembly (``occ_V``/``occ_F``, model mm, within 1 mm) and the photo pixel is an OPAQUE glasses pixel (S0
    matte minus S0 lens proposal (front: also the S2 lens polygons), minus backdrop-like and matte-edge
    backdrop-mix pixels, minus lens-coloured pixels), sampled after a blur to the render's pixel footprint.
    The render and the photo are thus compared on the same surface points: parts the runtime hides (temple
    ends behind the synthetic head) are not compared. The held-out view is never included."""
    out = {}
    fg_f, l0_f = np.asarray(a0["fg_front"], bool), np.asarray(a0["lens_front"], bool)
    ph_f = photos["front"]
    lp_f = poly_raster(lens_polys_front, ph_f.shape[:2])
    ppm_f = cameras_px_per_mm(cams["front"], frame, centre_mm)
    lens_ref_f = (depth.erode_mm(lp_f, LENS_REF_ERODE_MM, ppm_f) | l0_f) & fg_f
    h_lens = lab_hist(ph_f[lens_ref_f]) if lens_ref_f.sum() >= 50 else None
    scene = raster.get_scene(np.asarray(occ_V, float), np.asarray(occ_F, np.int64), frame)
    for view in views:
        if view not in cams or view in core.HELD_OUT_VIEWS:
            continue
        ph = photos[view]
        fg = np.asarray(a0[f"fg_{view}"], bool)
        l0 = np.asarray(a0[f"lens_{view}"], bool)
        ppm = cameras_px_per_mm(cams[view], frame, centre_mm)
        lensreg = l0 | (lp_f if view == "front" else np.zeros_like(l0))
        reg = fg & ~lensreg
        bd, _ = backdrop_like(ph, reg, s0["views"][view]["backdrop_rgb"])
        reg &= ~bd
        info = {"px_per_mm": round(ppm, 4)}
        if view in ("front", "back"):
            away = ndimage.distance_transform_edt(~lensreg) > FRAME_REF_AWAY_MM * ppm
            lref = (depth.erode_mm(lensreg, LENS_REF_ERODE_MM, ppm) | l0) & fg
            ll, _ = lens_like_mask(ph, reg, lref, reg & away, lens_region=lensreg, band_px=LENSLIKE_BAND_MM * ppm)
        else:
            ll = np.zeros_like(reg)
            if h_lens is not None and reg.sum() >= 50:
                ll[reg] = colour_is_lens(ph[reg], h_lens, lab_hist(ph_f[fg_f & ~l0_f & ~lp_f]))
        reg &= ~ll
        if reg.sum() >= 50:
            mix, minfo = backdrop_mix_mask(ph, reg, ~fg | bd, s0["views"][view]["backdrop_rgb"], np.median(ph[reg], axis=0))
            reg &= ~mix
            info["edge_mix"] = minfo
        info.update({"pixels": int(reg.sum()), "lens_like_px": int(ll.sum()), "backdrop_like_px": int(bd.sum())})
        side = float(np.sign(raster.camera_basis(cams[view])[2][0])) if view in ("left", "right") else 0.0
        out[view] = {"sample": _make_sampler(ph, reg, ppm, cams[view], frame, scene), "px_per_mm": ppm,
                     "info": info, "side_sign": side, "photo": ph}
    return out


def _make_sampler(ph, reg, ppm, cam, frame, scene):
    cache = {}

    def sample(X_mm: np.ndarray, render_ppm: float):
        k = max(ppm / max(render_ppm, 1e-6), 1.0)           # photo px per render px
        key = round(k, 3)
        if key not in cache:
            blur = cv2.GaussianBlur(ph.astype(np.float32), (0, 0), 0.5 * k) if k > 1.01 else ph.astype(np.float32)
            cache[key] = (blur, erode_px(reg, int(math.ceil(k / 2))))
        blur, mask = cache[key]
        H, W = mask.shape
        uv = core.project_mm(X_mm, cam, frame)
        ok = (uv[:, 0] > 0) & (uv[:, 0] < W - 1) & (uv[:, 1] > 0) & (uv[:, 1] < H - 1)
        ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
        vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
        ok &= mask[vi, ui]
        if ok.any():
            toward = raster.camera_basis(cam)[2]
            d = -(frame.to_norm(X_mm[ok]) @ toward) * frame.extent
            hit = scene.cast(cam, uv[ok, 0], uv[ok, 1])
            vis = ~hit["hit"] | (hit["depth"] >= d - OCCLUSION_TOL_MM)
            idx = np.nonzero(ok)[0]
            ok[idx[~vis]] = False
        rgb = sample_bilinear(blur, uv[:, 0], uv[:, 1])
        return rgb, ok, uv
    return sample


def cameras_px_per_mm(cam: Camera, frame: NormFrame, point_mm: np.ndarray) -> float:
    from . import cameras
    return cameras.px_per_mm_at(cam, frame, point_mm)


class HarnessError(RuntimeError):
    """The AR harness (an external process) produced no usable renders - as opposed to a defect in this code."""


VERIFICATION_FAILED = "verification_render_failed"


def complete_verifications(ver: dict, names: dict, planned) -> dict:
    """The verification candidates whose renders may be scored. ``names`` maps a candidate key to its model name in
    the ``ar_render`` result ``ver``; a candidate is kept only when the run validated (``ver["validation"]``: the
    harness ok and this row ok) and its views hold an existing PNG for EVERY planned view. A candidate missing one
    view is excluded - its objective would average fewer views than the others' and a synthesised value is no
    substitute for the render. {} means nothing was verified (``VERIFICATION_FAILED``), never a score from a subset."""
    from pathlib import Path
    val = ver.get("validation") or {}
    rows = val.get("models") or {}
    out = {}
    for key, name in names.items():
        if not val.get("harness_ok") or not (rows.get(name) or {}).get("ok"):
            continue
        vm = ((ver.get("models") or {}).get(name) or {}).get("views") or {}
        if all(v in vm and vm[v].get("png") and Path(vm[v]["png"]).is_file() for v in planned):
            out[key] = {v: vm[v] for v in planned}
    return out


def verification_status(complete: dict) -> str:
    """``chosen_rendered.status``: the runtime's word when at least one candidate was fully rendered, else the failure."""
    return "runtime_compatible" if complete else VERIFICATION_FAILED


def fit_frame_material(base_glb: bytes, materials: list[str], regions: dict, out_dir, mclass: str,
                       old: dict, origin_mm, log=print, classes: dict | None = None) -> dict:
    """Choose (metallic, roughness, base-colour gain g) for the frame/temple materials by rendering candidates
    in the actual AR runtime and matching the photo's opaque-part colour distribution in every fit view.

    Each (metallic, roughness) candidate is rendered twice, with base colour x1 and x0. Scene radiance is
    affine in the base colour for any metallic/roughness (diffuse ~ base, F0 = mix(0.04, base, metallic)), so
    after inverting the runtime's ACES tone map the render for any gain g is synthesised exactly:
    L(g) = L0 + g (L1 - L0). g lives in [0.25, 1]: the photo colour includes the studio's light (the backdrop is
    clipped white), so it is an upper bound on the albedo; the runtime's bright room then renders it lighter
    than the photo. Objective = mean over views of ``slab_distance`` (render vs photo at the same px/mm).
    ``old`` = {"metallic", "roughness"} of the previous rule, rendered in the same batch (before/after).

    ``classes`` (two materials on the temples, ``material_classes``): {"texture_class" (H,W) uint8 1 metal / 0
    dielectric in the temples' UV layout, "textured": temple material names, "frame_class": 0 | 1, "rebuild":
    fn(frame (m, r, gain_rgb), {class: (m, r, gain_rgb)}, scale) -> GLB bytes}. The candidates are rendered with the
    base GLB's metallicRoughness texture (metallic 1 on metal texels, 0 on dielectric ones), so every render pixel
    belongs to one class (``hit_uv`` into the class map) and each class is fitted on its own pixels (``_fit_classes``;
    a dielectric class only at metallic 0)."""
    from pathlib import Path
    out_dir = Path(out_dir)
    ms = AR_FIT_METALLIC.get(mclass, (0.0,)) if classes is None else AR_FIT_METALLIC["metal"]
    grid = [(m, r) for m in ms for r in AR_FIT_ROUGHNESS]
    cand = list(grid)
    old_key = (float(old["metallic"]), float(old["roughness"]))
    if old_key not in cand:
        cand.append(old_key)
    glbs = {}
    for m, r in cand:
        for g in (0, 1):
            glbs[f"m{int(round(m * 100)):03d}-r{int(round(r * 1000)):04d}-g{g}"] = glb_patch_materials(
                base_glb, {n: {"pbr": {"metallicFactor": float(m), "roughnessFactor": float(r),
                                       "baseColorFactor": [float(g), float(g), float(g), 1.0]}} for n in materials})
    bg = (255, 255, 255)
    t0 = time.time()
    res = ar_render(glbs, out_dir, views=AR_FIT_VIEWS, background_rgb=bg)
    log(f"[s7 fit] {len(glbs)} candidates x {len(AR_FIT_VIEWS)} views rendered in {time.time() - t0:.1f} s "
        f"(harness {res.get('harness_status')}, rc {res.get('returncode')})")
    bad = [n for n, m in res["models"].items() if m.get("status") != "runtime_compatible"]
    val = res.get("validation") or {}
    if not res["models"] or bad or not val.get("ok"):
        raise HarnessError(f"harness: {res.get('harness_status')} rc {res.get('returncode')}; not compatible: "
                           f"{bad[:4]}; validation {val.get('reasons', ['absent'])[:3]}; {(res.get('stderr_tail') or '')[-300:]}")
    prims = glb_primitives(base_glb)
    opaque = [i for i, p in enumerate(prims) if not str(p["node"]).startswith("lens")]
    first = next(iter(res["models"].values()))["views"]
    # views: label once (geometry and pose are identical across candidates); the photo is sampled on the
    # render's own visible surface points
    view_data = {}
    ref_name = f"m{int(round(cand[0][0] * 100)):03d}-r{int(round(cand[0][1] * 1000)):04d}-g1"
    for v, meta in first.items():
        pv = v if v in ("front", "back") else None
        if pv is None:
            sx = float(np.sign(meta["camera_in_asset"][0]))
            pv = next((pvv for pvv in ("left", "right") if pvv in regions and regions[pvv]["side_sign"] == sx), None)
        if pv is None or pv not in regions:
            continue
        lab = ar_label(meta, prims)
        rppm = ar_px_per_mm(meta)
        img1 = np.asarray(Image.open(res["models"][ref_name]["views"][v]["png"]).convert("RGB"))
        rmask = _render_region(img1, lab, opaque, bg)
        X_mm = lab["points"][rmask] * 1000.0 + np.asarray(origin_mm, float)
        rgb_p, ok, uv = regions[pv]["sample"](X_mm, rppm)
        use = np.zeros_like(rmask)
        use[rmask] = ok
        if use.sum() < AR_FIT_MIN_PX:
            continue
        ph = regions[pv]["photo"]
        u = uv[ok]
        x0, x1 = int(max(0, u[:, 0].min() - 20)), int(min(ph.shape[1], u[:, 0].max() + 20))
        y0, y1 = int(max(0, u[:, 1].min() - 20)), int(min(ph.shape[0], u[:, 1].max() + 20))
        view_data[v] = {"photo_view": pv, "photo_lab": rgb2lab(np.clip(rgb_p[ok], 0, 255)[None].astype(np.float64) / 255.0)[0],
                        "mask": use, "render_px_per_mm": rppm, "photo_px": int(ok.sum()), "render_px": int(use.sum()),
                        "render_region_px": int(rmask.sum()), "photo_crop": ph[y0:y1, x0:x1]}
        if classes is not None:
            view_data[v]["cls"] = pixel_classes(lab, use, prims, classes)
    # Integration fix (2026-09-24): a view with fewer than AR_FIT_MIN_PX comparable pixels is left out; the fit
    # needs at least one usable view, not specifically the front. Miu's rimless front (hardware behind the lens
    # sheet, 2.4 render px/mm) has 119 comparable pixels after the S5/S6 donor rework; requiring the front made
    # the whole fit fall back to the old ORM rule (the pale specular wash) although both side views had ~900.
    if not view_data:
        return {"ok": False, "reason": "no usable fit view"}
    unusable = sorted(v for v in first if v not in view_data)
    if classes is not None:
        return _fit_classes(res, view_data, grid, old_key, classes, out_dir, bg, log, unusable, regions, len(glbs))
    gains = np.round(np.arange(AR_FIT_GAIN[0], AR_FIT_GAIN[1] + 1e-9, AR_FIT_GAIN_STEP), 5)
    table = {}
    for m, r in cand:
        key = f"m{int(round(m * 100)):03d}-r{int(round(r * 1000)):04d}"
        per_view = {}
        for v, d in view_data.items():
            ims = [np.asarray(Image.open(res["models"][f"{key}-g{g}"]["views"][v]["png"]).convert("RGB"))[d["mask"]]
                   for g in (0, 1)]
            L0, L1 = (aces_filmic_inverse(srgb_to_linear(x.astype(float))) for x in ims)
            dists, means = [], []
            for g in gains:
                rgb = linear_to_srgb(aces_filmic(L0 + g * (L1 - L0)))
                lab_r = rgb2lab(rgb[None] / 255.0)[0]
                dists.append(slab_distance(d["photo_lab"], lab_r))
                means.append(float(deltaE_ciede2000(d["photo_lab"].mean(0), lab_r.mean(0))))
            per_view[v] = (np.asarray(dists), np.asarray(means))
        J = np.mean([pv[0] for pv in per_view.values()], axis=0)
        table[(m, r)] = {"J": J, "views": per_view}
    in_grid = [(m, r) for m, r in grid]
    best = min(((table[k]["J"].min(), i, k) for i, k in enumerate(in_grid)))
    (m_b, r_b), gi = best[2], int(np.argmin(table[best[2]]["J"]))
    g_b = float(gains[gi])
    g1 = len(gains) - 1

    def summary(k, i):
        t = table[k]
        return {"metallic": k[0], "roughness": k[1], "gain": float(gains[i]), "objective": round(float(t["J"][i]), 3),
                "views": {v: {"slab_dE00": round(float(t["views"][v][0][i]), 2),
                              "mean_colour_dE00": round(float(t["views"][v][1][i]), 2)} for v in t["views"]}}
    rough_only = min(((table[k]["J"][g1], i, k) for i, k in enumerate(in_grid)))
    # per-channel base-colour gain at the chosen (metallic, roughness): scene radiance is affine in each channel of
    # the base colour separately (diffuse ~ base_c; F0_c = mix(0.04, base_c, metallic)), so L(g) = L0 + g (.) (L1 - L0)
    # per channel stays exact. One scalar gain darkens every channel alike and the tone map then desaturates the
    # texture (rayban: photo chroma 15.0 vs render 11.6); the channels are refined from the scalar optimum, each in
    # the same [0.25, 1] box (the photo colour is an upper bound on the albedo in every channel)
    key_b = f"m{int(round(m_b * 100)):03d}-r{int(round(r_b * 1000)):04d}"
    lin = {}
    for v, d in view_data.items():
        ims = [np.asarray(Image.open(res["models"][f"{key_b}-g{g}"]["views"][v]["png"]).convert("RGB"))[d["mask"]]
               for g in (0, 1)]
        lin[v] = tuple(aces_filmic_inverse(srgb_to_linear(x.astype(float))) for x in ims)

    def J_rgb(gv):
        gv = np.clip(np.asarray(gv, float), AR_FIT_GAIN[0], AR_FIT_GAIN[1])
        return float(np.mean([slab_distance(view_data[v]["photo_lab"],
                                            rgb2lab(linear_to_srgb(aces_filmic(L0 + gv[None] * (L1 - L0)))[None] / 255.0)[0])
                              for v, (L0, L1) in lin.items()]))
    from scipy import optimize as _opt
    x0 = np.full(3, g_b)
    opt = _opt.minimize(J_rgb, x0, method="Powell", bounds=[AR_FIT_GAIN] * 3,
                        options={"xtol": 0.005, "ftol": 1e-4, "maxfev": 400})
    g_rgb = np.round(np.clip(opt.x, AR_FIT_GAIN[0], AR_FIT_GAIN[1]), 4)
    J_scalar, J_chan = float(table[(m_b, r_b)]["J"][gi]), J_rgb(g_rgb)
    if not J_chan < J_scalar - 1e-6:
        g_rgb = np.full(3, g_b)
        J_chan = J_scalar
    out = {"ok": True, "chosen": summary((m_b, r_b), gi), "before_old_rule": summary(old_key, g1),
           "per_channel_gain": {"gain_rgb": g_rgb.tolist(), "objective": round(J_chan, 3),
                                "objective_scalar_gain": round(J_scalar, 3)},
           "best_without_gain": summary(rough_only[2], g1),
           "grid": {"metallic": list(ms), "roughness": list(AR_FIT_ROUGHNESS), "gain": [float(gains[0]), float(gains[-1]),
                                                                                     AR_FIT_GAIN_STEP]},
           "table": {f"m{k[0]}_r{k[1]}": {"best_gain": float(gains[int(np.argmin(t['J']))]),
                                           "objective_at_best_gain": round(float(t["J"].min()), 3),
                                           "objective_at_gain_1": round(float(t["J"][g1]), 3)} for k, t in table.items()},
           "views": {v: {"photo_view": d["photo_view"], "compared_px": d["render_px"],
                         "render_opaque_px": d["render_region_px"],
                         "render_px_per_mm": round(d["render_px_per_mm"], 3)} for v, d in view_data.items()},
           "views_unusable": unusable, "min_compared_px": AR_FIT_MIN_PX,
           "photo_regions": {v: r["info"] for v, r in regions.items()},
           "harness": {"status": res.get("harness_status"), "renders": len(glbs) * len(AR_FIT_VIEWS),
                       "background_hex": res.get("background_hex")},
           "objective": "mean over fit views of the mean dE00 between the 10 L*-sorted slab means of the photo's and "
                        "the render's opaque-part pixels (both at the render's px/mm); lower is better"}
    # verification: render the chosen material itself at the synthesised gain and at +/- 15 % (the synthesis
    # is exact for dielectrics; three.js's multiple-scattering term makes it approximate for rough metals), and
    # keep the gain whose ACTUAL render scores best
    gv = sorted({float(round(f, 4)) for f in (0.85, 1.0, 1.15)})          # x the per-channel gain

    def gvec(f):
        return [float(np.clip(round(float(c) * f, 4), gains[0], gains[-1])) for c in g_rgb]
    vglbs = {f"g{int(round(g * 10000)):05d}": glb_patch_materials(
        base_glb, {n: {"pbr": {"metallicFactor": float(m_b), "roughnessFactor": float(r_b),
                               "baseColorFactor": gvec(g) + [1.0]}} for n in materials}) for g in gv}
    ver = ar_render(vglbs, out_dir / "verify", views=AR_FIT_VIEWS, background_rgb=bg)
    complete = complete_verifications(ver, {g: f"g{int(round(g * 10000)):05d}" for g in gv}, list(view_data))
    rendered = []
    for g, vm in complete.items():
        vv = {}
        for v, d in view_data.items():
            lab_r = rgb2lab(np.asarray(Image.open(vm[v]["png"]).convert("RGB"))[d["mask"]][None].astype(np.float64) / 255.0)[0]
            vv[v] = {"slab_dE00": round(slab_distance(d["photo_lab"], lab_r), 2),
                     "mean_colour_dE00": round(float(deltaE_ciede2000(d["photo_lab"].mean(0), lab_r.mean(0))), 2)}
        rendered.append({"gain": g, "gain_rgb": gvec(g), "views": vv,
                         "objective": round(float(np.mean([x["slab_dE00"] for x in vv.values()])), 3),
                         "renders": {v: m["png"] for v, m in vm.items()}})
    if rendered:
        best_r = min(rendered, key=lambda x: (x["objective"], abs(x["gain"] - 1.0)))
        out["chosen_rendered"] = {"views": best_r["views"], "objective": best_r["objective"], "gain": best_r["gain_rgb"],
                                  "synthesised_gain": g_rgb.tolist(), "status": verification_status(complete),
                                  "gain_candidates": [{"scale": x["gain"], "gain_rgb": x["gain_rgb"],
                                                       "objective": x["objective"]} for x in rendered]}
        out["chosen"]["gain"] = best_r["gain_rgb"]
        vm = best_r["renders"]
        keep_ver = set(Path(x).name for x in vm.values())
        for f in (out_dir / "verify").glob("*.png"):
            if f.name not in keep_ver:
                f.unlink()
    else:
        # no candidate was seen rendered in every fit view: the sweep's synthesised gain is a prediction, not a fit
        out["chosen_rendered"] = {"views": {}, "objective": None, "status": verification_status(complete),
                                  "harness_status": ver.get("harness_status"),
                                  "reasons": (ver.get("validation") or {}).get("reasons", [])[:4]}
        out["ok"] = False
        out["reason"] = f"{VERIFICATION_FAILED}: harness {ver.get('harness_status')} rc {ver.get('returncode')}"
        vm = {}
    # keep the before (old rule) and verification renders; drop the sweep's other images
    old_name = f"m{int(round(old_key[0] * 100)):03d}-r{int(round(old_key[1] * 1000)):04d}-g1"
    keep = {Path(p["png"]).name for p in res["models"][old_name]["views"].values()}
    for f in out_dir.glob("*.png"):
        if f.name not in keep:
            f.unlink()
    for f in list(out_dir.glob("*cards*.json")) + list(out_dir.glob("contact-sheets.json")) + list((out_dir / "verify").glob("*card*")):
        f.unlink()
    for f in (out_dir / "verify").glob("*comparison*.png"):
        f.unlink()
    out["_renders"] = {"before": {v: p["png"] for v, p in res["models"][old_name]["views"].items()},
                       "after": {v: (p["png"] if isinstance(p, dict) else p) for v, p in vm.items()}}
    out["_view_data"] = view_data
    return out


CLASS_NAMES = ("dielectric", "metal")


def pixel_classes(lab: dict, use: np.ndarray, prims: list[dict], classes: dict) -> np.ndarray:
    """Material class (0 dielectric, 1 metal) of every ``use`` render pixel (row-major order): a textured (temple)
    primitive's class map at the hit's UV, the frame's class elsewhere."""
    first, face, pts = lab["first"][use], lab["face"][use], lab["points"][use]
    out = np.full(len(first), int(classes["frame_class"]), np.int8)
    cm = np.asarray(classes["texture_class"])
    Hc, Wc = cm.shape
    for i, p in enumerate(prims):
        if p.get("material") not in classes["textured"] or "UV" not in p:
            continue
        sel = first == i
        if sel.any():
            uv = hit_uv(p, face[sel], pts[sel])
            ui = np.clip((np.mod(uv[:, 0], 1.0) * Wc).astype(int), 0, Wc - 1)
            vi = np.clip((np.mod(uv[:, 1], 1.0) * Hc).astype(int), 0, Hc - 1)
            out[sel] = cm[vi, ui]
    return out


def _fit_classes(res: dict, view_data: dict, grid: list, old_key: tuple, classes: dict, out_dir, bg, log,
                 unusable: list, regions: dict, n_glbs: int) -> dict:
    """The per-class fit of ``fit_frame_material``: for each class, the (metallic, roughness, gain) whose render
    matches the photo best on that class's own pixels (a dielectric class only at metallic 0), then the per-channel
    gain; the combined objective (every pixel with its class's material) is reported next to the old rule's, and the
    chosen materials are rendered for real (``classes["rebuild"]``) at 0.85 / 1 / 1.15 x the gains."""
    from pathlib import Path
    from scipy import optimize as _opt
    gains = np.round(np.arange(AR_FIT_GAIN[0], AR_FIT_GAIN[1] + 1e-9, AR_FIT_GAIN_STEP), 5)
    cache: dict = {}

    def lin(key, v, g):
        if (key, v, g) not in cache:
            x = np.asarray(Image.open(res["models"][f"{key}-g{g}"]["views"][v]["png"]).convert("RGB"))[view_data[v]["mask"]]
            cache[(key, v, g)] = aces_filmic_inverse(srgb_to_linear(x.astype(float)))
        return cache[(key, v, g)]

    def key_of(m, r):
        return f"m{int(round(m * 100)):03d}-r{int(round(r * 1000)):04d}"

    def lab_of_lin(L):
        return rgb2lab(linear_to_srgb(aces_filmic(L))[None] / 255.0)[0]
    per_class, chosen = {}, {}
    for k, name in enumerate(CLASS_NAMES):
        views = [v for v, d in view_data.items() if int((d["cls"] == k).sum()) >= AR_FIT_MIN_PX]
        allowed = [(m, r) for m, r in grid if (m == 0.0 or k == 1)]
        if not views:
            per_class[name] = {"ok": False, "reason": "no fit view with enough pixels of this class"}
            continue
        table = {}
        for m, r in allowed:
            J = []
            for v in views:
                sel = view_data[v]["cls"] == k
                L0, L1 = lin(key_of(m, r), v, 0)[sel], lin(key_of(m, r), v, 1)[sel]
                J.append([slab_distance(view_data[v]["photo_lab"][sel], lab_of_lin(L0 + g * (L1 - L0))) for g in gains])
            table[(m, r)] = np.mean(np.asarray(J), axis=0)
        (m_b, r_b) = min(table, key=lambda q: (float(table[q].min()), q))
        gi = int(np.argmin(table[(m_b, r_b)]))
        sels = {v: view_data[v]["cls"] == k for v in views}
        lins = {v: (lin(key_of(m_b, r_b), v, 0)[sels[v]], lin(key_of(m_b, r_b), v, 1)[sels[v]]) for v in views}

        def J_rgb(gv, lins=lins, sels=sels):
            gv = np.clip(np.asarray(gv, float), AR_FIT_GAIN[0], AR_FIT_GAIN[1])
            return float(np.mean([slab_distance(view_data[v]["photo_lab"][sels[v]], lab_of_lin(L0 + gv[None] * (L1 - L0)))
                                  for v, (L0, L1) in lins.items()]))
        opt = _opt.minimize(J_rgb, np.full(3, float(gains[gi])), method="Powell", bounds=[AR_FIT_GAIN] * 3,
                            options={"xtol": 0.005, "ftol": 1e-4, "maxfev": 400})
        g_rgb = np.round(np.clip(opt.x, AR_FIT_GAIN[0], AR_FIT_GAIN[1]), 4)
        J_scalar, J_chan = float(table[(m_b, r_b)][gi]), J_rgb(g_rgb)
        if not J_chan < J_scalar - 1e-6:
            g_rgb, J_chan = np.full(3, float(gains[gi])), J_scalar
        chosen[k] = (float(m_b), float(r_b), g_rgb)
        per_class[name] = {"ok": True, "metallic": float(m_b), "roughness": float(r_b), "gain_scalar": float(gains[gi]),
                           "gain_rgb": g_rgb.tolist(), "objective_scalar_gain": round(J_scalar, 3),
                           "objective": round(J_chan, 3), "views": {v: int(sels[v].sum()) for v in views},
                           "table": {f"m{q[0]}_r{q[1]}": round(float(t.min()), 3) for q, t in table.items()}}
    if not chosen:
        return {"ok": False, "reason": "no class has a usable fit view"}
    for k in (0, 1):                       # a class no view shows well enough takes the other's roughness and gain
        if k not in chosen:
            m_o, r_o, g_o = chosen[1 - k]
            chosen[k] = (0.0 if k == 0 else m_o, r_o, g_o)
            per_class[CLASS_NAMES[k]]["fallback"] = f"the {CLASS_NAMES[1 - k]} class's roughness and gain"

    def combined(pick):
        """Mean over views of the slab dE00 of ALL compared pixels, each rendered with its class's material."""
        vals = {}
        for v, d in view_data.items():
            L = np.zeros((int(d["mask"].sum()), 3))
            for k in (0, 1):
                sel = d["cls"] == k
                if sel.any():
                    m, r, g = pick(k)
                    L0, L1 = lin(key_of(m, r), v, 0)[sel], lin(key_of(m, r), v, 1)[sel]
                    L[sel] = L0 + np.asarray(g, float)[None] * (L1 - L0)
            vals[v] = round(slab_distance(d["photo_lab"], lab_of_lin(L)), 2)
        return {"objective": round(float(np.mean(list(vals.values()))), 3), "views": vals}
    after = combined(lambda k: chosen[k])
    before = combined(lambda k: (old_key[0], old_key[1], np.ones(3)))
    fk = int(classes["frame_class"])
    out = {"ok": True, "split": True, "classes": per_class, "frame_class": CLASS_NAMES[fk],
           "chosen": {"metallic": chosen[fk][0], "roughness": chosen[fk][1], "gain": chosen[fk][2].tolist(),
                      "objective": after["objective"], "views": {v: {"slab_dE00": x} for v, x in after["views"].items()}},
           "before_old_rule": {"metallic": old_key[0], "roughness": old_key[1], "gain": 1.0, **before,
                               "views": {v: {"slab_dE00": x} for v, x in before["views"].items()}},
           "per_channel_gain": {"gain_rgb": chosen[fk][2].tolist(), "objective": after["objective"],
                                "objective_scalar_gain": after["objective"]},
           "best_without_gain": {"roughness": old_key[1], "objective": before["objective"]},
           "grid": {"metallic": sorted({m for m, _ in grid}), "roughness": list(AR_FIT_ROUGHNESS),
                    "gain": [float(gains[0]), float(gains[-1]), AR_FIT_GAIN_STEP]},
           "views": {v: {"photo_view": d["photo_view"], "compared_px": d["render_px"],
                         "render_opaque_px": d["render_region_px"], "render_px_per_mm": round(d["render_px_per_mm"], 3),
                         "class_px": {CLASS_NAMES[k]: int((d["cls"] == k).sum()) for k in (0, 1)}}
                     for v, d in view_data.items()},
           "views_unusable": unusable, "min_compared_px": AR_FIT_MIN_PX,
           "photo_regions": {v: r["info"] for v, r in regions.items()},
           "harness": {"status": res.get("harness_status"), "renders": n_glbs * len(AR_FIT_VIEWS),
                       "background_hex": res.get("background_hex")},
           "objective": "per class: mean over fit views of the slab dE00 of that class's pixels; combined: every "
                        "compared pixel rendered with its own class's material"}
    # verification: the chosen materials rendered for real at 0.85 / 1 / 1.15 x the gains
    scales = (0.85, 1.0, 1.15)
    vglbs = {f"s{int(round(sc * 100)):03d}": classes["rebuild"](chosen[fk], chosen, sc) for sc in scales}
    ver = ar_render(vglbs, out_dir / "verify", views=AR_FIT_VIEWS, background_rgb=bg)
    complete = complete_verifications(ver, {sc: f"s{int(round(sc * 100)):03d}" for sc in scales}, list(view_data))
    rendered = []
    for sc, vm in complete.items():
        vv = {}
        for v, d in view_data.items():
            lab_r = rgb2lab(np.asarray(Image.open(vm[v]["png"]).convert("RGB"))[d["mask"]][None].astype(np.float64) / 255.0)[0]
            vv[v] = {"slab_dE00": round(slab_distance(d["photo_lab"], lab_r), 2),
                     "mean_colour_dE00": round(float(deltaE_ciede2000(d["photo_lab"].mean(0), lab_r.mean(0))), 2)}
        rendered.append({"scale": sc, "views": vv, "renders": {v: m["png"] for v, m in vm.items()},
                         "objective": round(float(np.mean([x["slab_dE00"] for x in vv.values()])), 3)})
    if rendered:
        best_r = min(rendered, key=lambda x: (x["objective"], abs(x["scale"] - 1.0)))
        sc = best_r["scale"]
        out["scale"] = sc
        out["chosen_rendered"] = {"views": best_r["views"], "objective": best_r["objective"], "scale": sc,
                                  "status": verification_status(complete),
                                  "scale_candidates": [{"scale": x["scale"], "objective": x["objective"]} for x in rendered]}
        keep_ver = set(Path(x).name for x in best_r["renders"].values())
        for f_ in (out_dir / "verify").glob("*.png"):
            if f_.name not in keep_ver:
                f_.unlink()
        vm = best_r["renders"]
    else:
        # no candidate was seen rendered in every fit view: the class gains are a prediction, not a fit
        out["scale"] = 1.0
        out["chosen_rendered"] = {"views": {}, "objective": None, "status": verification_status(complete),
                                  "harness_status": ver.get("harness_status"),
                                  "reasons": (ver.get("validation") or {}).get("reasons", [])[:4]}
        out["ok"] = False
        out["reason"] = f"{VERIFICATION_FAILED}: harness {ver.get('harness_status')} rc {ver.get('returncode')}"
        vm = {}
    # the gains as rendered: x the verified scale, clipped to 1 (a base colour is at most the texture's own colour)
    out["class_factors"] = {CLASS_NAMES[k]: {"metallic": chosen[k][0], "roughness": chosen[k][1],
                                             "gain_rgb": [round(float(min(c * out["scale"], 1.0)), 4) for c in chosen[k][2]]}
                            for k in (0, 1)}
    out["chosen"]["gain"] = out["class_factors"][CLASS_NAMES[fk]]["gain_rgb"]
    old_name = f"{key_of(*old_key)}-g1"
    keep = {Path(p["png"]).name for p in res["models"][old_name]["views"].values()}
    for f_ in out_dir.glob("*.png"):
        if f_.name not in keep:
            f_.unlink()
    for f_ in list(out_dir.glob("*cards*.json")) + list(out_dir.glob("contact-sheets.json")) + list((out_dir / "verify").glob("*card*")):
        f_.unlink()
    for f_ in (out_dir / "verify").glob("*comparison*.png"):
        f_.unlink()
    out["_renders"] = {"before": {v: p["png"] for v, p in res["models"][old_name]["views"].items()}, "after": dict(vm)}
    out["_view_data"] = view_data
    log(f"[s7 fit] classes: " + "; ".join(f"{n} m {per_class[n].get('metallic')} r {per_class[n].get('roughness')} "
                                          f"g {per_class[n].get('gain_rgb')}" for n in CLASS_NAMES)
        + f"; combined {before['objective']} -> {after['objective']} (rendered {out['chosen_rendered']['objective']})")
    return out


def calibration_glb(product: str, run: str, V, F, UVc, tex_id, frame_textures, temple_parts: dict, s5: dict,
                    temple_tex, a6: dict, s2: dict, s6: dict, factors: dict, a5: dict | None = None,
                    temple_factors: dict | None = None, temple_mr: np.ndarray | None = None) -> tuple[bytes, list[str]]:
    """The assembly exactly as S9 will export it (S6 frame with the S7 textures and per-corner UVs, accepted
    S5 temples, S6 lens sheets, S6 bridge origin) with a neutral clear lens (S8 is not an input of S7: lens
    pixels are excluded from the fit). Returns (GLB bytes, the frame/temple material names, origin mm)."""
    import tempfile
    from pathlib import Path
    from . import export
    if a5 is None:
        a5 = {f"temple_{s}_{k}": v for s, (TV, TF, TUV) in temple_parts.items() for k, v in (("V", TV), ("F", TF), ("UV", TUV))}
    u8 = lambda t: np.clip(np.round(t), 0, 255).astype(np.uint8)   # noqa: E731
    fac = {"metallic": float(factors["metallic"]), "roughness": float(factors["roughness"])}
    if factors.get("base_color") is not None:
        fac["base_color"] = [float(x) for x in factors["base_color"]]
    tfac = dict(fac) if temple_factors is None else {k: (list(v) if isinstance(v, (list, tuple)) else float(v))
                                                      for k, v in temple_factors.items()}
    if temple_mr is not None:
        tfac["metallic_roughness_texture"] = u8(temple_mr)
    parts = {"frame": {"V": V, "F": F, "UV": UVc, "material": ["frame_front", "frame_back", "frame_wall"],
                       "face_material": np.asarray(tex_id, np.int64)}}
    materials = {n: {"base_color_texture": u8(t), **fac}
                 for n, t in zip(("frame_front", "frame_back", "frame_wall"), frame_textures)}
    for s in temple_parts:
        ta = export.temple_arrays(a5, s5, s, a6)        # exactly what S9 exports (a rejected arm: its donors only)
        if ta is None:
            continue
        TV, TF, TUV, _ = ta
        parts[f"temple_{s}"] = {"V": TV, "F": TF, "UV": TUV, "material": f"temple_{s}"}
        materials[f"temple_{s}"] = {"base_color_texture": u8(temple_tex), **tfac}
    sides = [l.get("side") for l in s2.get("lenses", [])]
    i = 1
    while f"lens{i}_V" in a6:
        side = sides[i - 1] if i - 1 < len(sides) and sides[i - 1] in ("R", "L", "C") else (
            "C" if (i == 1 and f"lens{i + 1}_V" not in a6) else ("R" if a6[f"lens{i}_V"][:, 0].mean() > 0 else "L"))
        parts[f"lens_{side}"] = {"V": a6[f"lens{i}_V"], "F": a6[f"lens{i}_F"], "material": "calibration_lens"}
        if f"lens{i}_uv" in a6:
            parts[f"lens_{side}"]["UV"] = a6[f"lens{i}_uv"]
        i += 1
    materials["calibration_lens"] = {"base_color": [1.0, 1.0, 1.0, 1.0], "metallic": 0.0, "roughness": 0.05,
                                     "transmission": 1.0, "ior": 1.5}
    origin = export._find_vec3(s6, "bridge")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "calibration.glb"
        export.write_glb(parts, materials, path, origin_mm=origin, extras={"product": product, "run": run,
                                                                            "purpose": "S7 material calibration"})
        data = path.read_bytes()
    if origin is None:
        origin = export.bridge_underside_mm(parts)[0]
    return data, [n for n in materials if n != "calibration_lens"], np.asarray(origin, float)


def fit_sheet(fit: dict, path, title: str) -> str:
    """Rows = fit views; columns: photo (render scale) | before (old rule) | after (chosen), numbers on top."""
    font = _font(15)
    rows = []
    for v, d in fit["_view_data"].items():
        tiles = [_label(_fit(d["photo_crop"], 360, 170), f"photo {d['photo_view']}", font)]
        for tag, label in (("before", "before (old rule)"), ("after", "after (fitted)")):
            p = fit["_renders"][tag].get(v)
            if p:
                im = np.asarray(Image.open(p).convert("RGB"))
                im = _crop_content(im, 8)
                s = (fit["before_old_rule"] if tag == "before" else fit["chosen_rendered"])["views"].get(v, {})
                tiles.append(_label(_fit(im, 360, 170), f"{label} {v}: slab dE {s.get('slab_dE00')}", font))
        rows.append(np.hstack(tiles + [np.full((170, 360, 3), 255, np.uint8)] * (3 - len(tiles))))
    body = np.vstack(rows)
    c, b = fit["chosen"], fit["before_old_rule"]
    head = [title,
            f"before: metallic {b['metallic']} roughness {b['roughness']} gain 1 -> objective {b['objective']}; "
            f"roughness-only best: r {fit['best_without_gain']['roughness']} -> {fit['best_without_gain']['objective']}",
            f"after: metallic {c['metallic']} roughness {c['roughness']} gain {c['gain']} -> objective {c['objective']} "
            f"(synthesised), {fit['chosen_rendered']['objective']} (rendered)"]
    top = Image.new("RGB", (body.shape[1], 24 * len(head) + 8), "white")
    dr = ImageDraw.Draw(top)
    for i, t in enumerate(head):
        dr.text((6, 4 + 24 * i), t, fill=(0, 0, 0), font=font)
    Image.fromarray(np.vstack([np.asarray(top), body])).save(path)
    return str(path)


def _crop_box(img: np.ndarray, mask: np.ndarray, margin: int = 6) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return img
    return img[max(0, ys.min() - margin):ys.max() + margin + 1, max(0, xs.min() - margin):xs.max() + margin + 1]


# =========================================================================== stage
def material_classes(orm: np.ndarray, tex: np.ndarray, used: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per temple texel (the generator's UV layout, same size as ``tex``): 1 METAL, 0 DIELECTRIC, from the generator's
    own metallicRoughness map (glTF: metallic in B, >= ``METAL_TEXEL``), cleaned with its basecolor: a texel whose Lab
    colour bin (``_LAB_EDGES``) is >= ``CLASS_BIN_PURITY`` of the other class among the used texels takes that class
    (the map carries dielectric streaks along miu's gold arm; the tortoise tips' light spots share bins with the arm's
    shaded gold and keep the map's class), then a 5 texel majority filter. Only ``used`` texels are classified."""
    info = {"metal_texel": METAL_TEXEL, "bin_purity": CLASS_BIN_PURITY}
    cls = np.zeros(used.shape, np.uint8)
    if orm is None or not used.any():
        return cls, info
    metal = (np.asarray(orm, float)[..., 2] / 255.0 >= METAL_TEXEL) & used
    lab = lab_of(tex[used].astype(float))
    idx = [np.clip(np.searchsorted(e, lab[:, i], side="right") - 1, 0, len(e) - 2) for i, e in enumerate(_LAB_EDGES)]
    b = (idx[0] * (len(_LAB_EDGES[1]) - 1) + idx[1]) * (len(_LAB_EDGES[2]) - 1) + idx[2]
    nb = (len(_LAB_EDGES[0]) - 1) * (len(_LAB_EDGES[1]) - 1) * (len(_LAB_EDGES[2]) - 1)
    m_used = metal[used].astype(float)
    share = np.bincount(b, weights=m_used, minlength=nb) / np.maximum(np.bincount(b, minlength=nb), 1)
    c = m_used.copy()
    c[share[b] >= CLASS_BIN_PURITY] = 1.0
    c[share[b] <= 1.0 - CLASS_BIN_PURITY] = 0.0
    cls[used] = c.astype(np.uint8)
    # majority filter inside the used texels (the unused ones do not vote)
    num = cv2.boxFilter(cls.astype(np.float32), -1, (5, 5), normalize=False)
    den = cv2.boxFilter(used.astype(np.float32), -1, (5, 5), normalize=False)
    cls = ((num >= 0.5 * np.maximum(den, 1)) & used).astype(np.uint8)
    info.update({"map_metal_share_of_used": round(float(m_used.mean()), 4),
                 "metal_share_of_used": round(float(cls[used].mean()), 4),
                 "flipped_by_colour": int((cls[used] != metal[used]).sum())})
    return cls, info


def class_area_shares(cls: np.ndarray, parts: list[tuple]) -> dict:
    """Area share of each class over the temple faces (class at each face's centroid UV)."""
    from . import generator
    area = np.zeros(2)
    for V, F, UV in parts:
        V, F = np.asarray(V, float), np.asarray(F, np.int64)
        A = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
        c = generator.sample_texture(cls[..., None].astype(np.float32), np.asarray(UV, float)[F].mean(axis=1))[:, 0] >= 0.5
        area += [A[~c].sum(), A[c].sum()]
    tot = max(area.sum(), 1e-12)
    return {"dielectric_mm2": round(float(area[0]), 1), "metal_mm2": round(float(area[1]), 1),
            "dielectric_share": round(float(area[0] / tot), 4), "metal_share": round(float(area[1] / tot), 4)}


def mr_texture(cls: np.ndarray, metallic: tuple, roughness: tuple) -> np.ndarray:
    """glTF metallicRoughness texture of a class map: G = roughness, B = metallic of each texel's class (R unused)."""
    c = np.asarray(cls, np.int64)
    out = np.full(c.shape + (3,), 255, np.uint8)
    out[..., 1] = np.round(255 * np.asarray(roughness, float)[c]).astype(np.uint8)
    out[..., 2] = np.round(255 * np.asarray(metallic, float)[c]).astype(np.uint8)
    return out


def bake_class_gain(tex: np.ndarray, cls: np.ndarray, gains: dict) -> np.ndarray:
    """A base-colour texture times each texel's class gain (linear light, per channel, <= 1): the per-class
    base-colour factor carried by the texture, so one temple material holds two materials."""
    lin = srgb_to_linear(np.asarray(tex, float))
    g = np.stack([np.asarray(gains[k], float) for k in sorted(gains)])[np.asarray(cls, np.int64)]
    return linear_to_srgb(np.clip(lin * g, 0.0, 1.0)).astype(np.float32)


def cap_extrusion(Fw: np.ndarray, bary: np.ndarray, n_front: int, capuv: np.ndarray, front_tex: np.ndarray,
                  back_tex: np.ndarray) -> np.ndarray:
    """Per wall texel (its wall face's vertex ids ``Fw`` (K,3): < n_front front cap, >= n_front back cap; barycentrics
    ``bary`` (K,3)) the colour of the caps it joins: the front and back cap textures at the same cap position, blended
    by the texel's depth fraction (0 at the front edge, 1 at the back edge). The wall's own material seen by the
    photos, carried over the ~1-5 mm of wall; frame evidence where the generator bake is not (``_inpaint_suspects``)."""
    from . import generator
    Fw = np.asarray(Fw, np.int64)
    b = np.asarray(bary, np.float64)
    t = (b * (Fw >= n_front)).sum(axis=1)
    uv = np.einsum("kc,kcd->kd", b, np.asarray(capuv, np.float64)[Fw % n_front])
    cf = generator.sample_texture(front_tex, uv)
    cb = generator.sample_texture(back_tex, uv)
    return ((1.0 - t)[:, None] * cf + t[:, None] * cb).astype(np.float32)


def visible_points(P: np.ndarray, N: np.ndarray, cam: Camera, frame: NormFrame, scene, shape: tuple[int, int],
                   min_cos: float = WALL_MIN_COS, tol_mm: float = WALL_VIS_TOL_MM) -> tuple[np.ndarray, np.ndarray]:
    """Surface points (outward unit normals ``N``) the camera sees DIRECTLY: inside the image, turned toward the
    camera (cos > ``min_cos``) and the first surface of ``scene`` on their ray (within ``tol_mm``). Returns (visible,
    projected native px)."""
    P = np.asarray(P, float).reshape(-1, 3)
    uv = core.project_mm(P, cam, frame)
    if not len(P):
        return np.zeros(0, bool), uv
    H, W = shape
    toward = raster.camera_basis(cam)[2]
    inb = (uv[:, 0] > -0.5) & (uv[:, 0] < W - 0.5) & (uv[:, 1] > -0.5) & (uv[:, 1] < H - 0.5)
    d = -(frame.to_norm(P) @ toward) * frame.extent
    hit = scene.cast(cam, uv[:, 0], uv[:, 1])
    first = ~hit["hit"] | (hit["depth"] >= d - tol_mm)
    return inb & (np.asarray(N, float) @ toward > min_cos) & first, uv


def front_visible_walls(Pw: np.ndarray, Nw: np.ndarray, cam: Camera, frame: NormFrame, scene, shape: tuple[int, int],
                        lens_region: np.ndarray) -> tuple[np.ndarray, dict]:
    """Wall texels the FRONT photo sees directly (``visible_points`` through the delivered frame + temples: a brow's or
    an endpiece's ~1 mm top wall in a pitched front photo), except those seen through the lens (the lens region: a
    lens-hole wall behind the lens is not photographed directly). Baked from the generator they were 9 % of rayban's
    front-view frame pixels at dE00 16 and set its front-cap region colour off by 1.5; S7 gives them the front cap's
    colour at their front edge (``cap_extrusion`` of the front texture), not the photo pixel at their own projection:
    seen at 75-85 deg a wall shows the studio's specular reflection (rayban's sampled brow-top pixels were a light grey
    [118, 112, 110] against a [92, 76, 71] tortoise front)."""
    vis, uv = visible_points(Pw, Nw, cam, frame, scene, shape)
    H, W = shape
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    vis &= ~np.asarray(lens_region, bool)[vi, ui]
    return vis, {"front_visible_texels": int(vis.sum()), "min_cos": WALL_MIN_COS}


def _texture_files(root, textures: dict[str, np.ndarray]) -> None:
    for name, img in textures.items():
        p = root / name
        im = Image.fromarray(np.clip(np.round(img), 0, 255).astype(np.uint8))
        if name.endswith(".jpg"):
            im.save(p, "JPEG", quality=95, subsampling=0, optimize=False)
        else:
            im.save(p, "PNG", optimize=False, compress_level=6)


def _inpaint_suspects(tex: np.ndarray, cand: np.ndarray, raw: np.ndarray, h_lens: np.ndarray | None,
                      h_frame: np.ndarray, backdrop_rgb, frame_rgb: np.ndarray,
                      covered: np.ndarray, evidence: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Inpaint (Telea) baked texels (``cand``) whose RAW generator colour is lens-like (``colour_is_lens``
    against the generator's lens-face and front-cap colours) or whose final colour is backdrop-like (dE00 < 10
    to the photo backdrop, farther than 10 from the frame's reference colour). With ``evidence`` (a per-texel
    colour from the frame itself: ``cap_extrusion`` for the walls) the lens-like texels take it instead: inpainted
    from their neighbours, a wall whose bake is 18 % lens-coloured (INVU) smeared lens colour into light flecks."""
    info = {}
    sus = np.zeros(tex.shape[:2], bool)
    if cand.any():
        lensy = colour_is_lens(raw[cand], h_lens, h_frame) if h_lens is not None else np.zeros(int(cand.sum()), bool)
        lab = lab_of(tex[cand])
        dfr = deltaE_ciede2000(lab, lab_of(np.asarray(frame_rgb, float).reshape(1, 3))[0][None, :])
        dbd = deltaE_ciede2000(lab, lab_of(np.asarray(backdrop_rgb, float).reshape(1, 3))[0][None, :])
        backy = (dbd < INPAINT_DE) & (dfr > INPAINT_DE) & (dbd < dfr)
        info["lens_like_texels"] = int(lensy.sum())
        info["backdrop_like_texels"] = int(backy.sum())
        sus[cand] = lensy | backy
    if evidence is not None and cand.any():
        lens_sus = np.zeros(tex.shape[:2], bool)
        lens_sus[cand] = lensy
        tex = tex.copy()
        tex[lens_sus] = evidence[lens_sus]
        sus &= ~lens_sus
        info["frame_evidence_texels"] = int(lens_sus.sum())
    info["inpainted_texels"] = int(sus.sum())
    info["inpainted_share_of_baked"] = round(float(sus.sum() / max(cand.sum(), 1)), 5)
    if sus.any():
        # neighbours first: uncovered texels are still empty (black) here and must not bleed in
        good = covered & ~sus
        img = nearest_fill(tex, good)[0] if good.any() else tex
        img = np.clip(np.round(img), 0, 255).astype(np.uint8)
        filled = cv2.inpaint(img, sus.astype(np.uint8), 3, cv2.INPAINT_TELEA).astype(np.float32)
        tex = tex.copy()
        tex[sus] = filled[sus]
    return tex, info


def backdrop_like(photo: np.ndarray, region: np.ndarray, backdrop_rgb, max_share: float = 0.5) -> tuple[np.ndarray, dict]:
    """Pixels of ``region`` within dE00 10 of the photo backdrop (anti-aliased matte edges on tiny photos).
    Not applied when more than ``max_share`` of the region is backdrop-coloured (a frame in the backdrop's
    colour): then nothing is removed and the caller flags it."""
    out = np.zeros(region.shape, bool)
    if not region.any():
        return out, {"backdrop_like_px": 0}
    d = deltaE_ciede2000(lab_of(photo[region]), lab_of(np.asarray(backdrop_rgb, float).reshape(1, 3))[0][None, :])
    hit = d < INPAINT_DE
    share = float(hit.mean())
    info = {"backdrop_like_px": int(hit.sum()), "backdrop_like_share": round(share, 4)}
    if share > max_share:
        info["skipped_frame_is_backdrop_coloured"] = True
        return out, info
    out[region] = hit
    return out, info


def _nconv_texels(vals: np.ndarray, ok: np.ndarray, cov: np.ndarray, sigma: float) -> np.ndarray:
    """Normalized Gaussian convolution (``sigma`` texels) of per-texel values defined where ``ok`` (texels of ``cov``)."""
    H, W = cov.shape
    img = np.zeros((H, W, vals.shape[1]))
    m = np.zeros((H, W))
    idx = np.nonzero(cov)
    img[idx[0][ok], idx[1][ok]] = vals[ok]
    m[idx[0][ok], idx[1][ok]] = 1.0
    wsum = ndimage.gaussian_filter(m, sigma)
    out = np.stack([ndimage.gaussian_filter(img[..., c], sigma) for c in range(vals.shape[1])], -1)
    return (out / np.maximum(wsum, 1e-6)[..., None])[cov]


def detail_transfer(base: np.ndarray, ok_base: np.ndarray, detail: np.ndarray, ok_detail: np.ndarray, cov: np.ndarray,
                    sigma: float) -> np.ndarray:
    """``base`` (sRGB 0..255 per texel: the low-resolution front photo) with the detail of ``detail`` (the colour-mapped
    back photo) above ``sigma`` texels (one front-photo pixel), in linear light and multiplicative (shading and texture
    are ratios; an additive detail clipped at black on a dark frame and brightened it): base_lowpass x detail /
    detail_lowpass where the base exists, the detail alone elsewhere. At the front photo's resolution the texture is the
    front photo."""
    B = srgb_to_linear(np.asarray(base, float))
    D = srgb_to_linear(np.asarray(detail, float))
    lpB = _nconv_texels(B, ok_base, cov, sigma)
    lpD = _nconv_texels(D, ok_detail, cov, sigma)
    out = np.where(ok_base[:, None], lpB * D / np.maximum(lpD, 1e-4), D)
    return linear_to_srgb(np.clip(out, 0.0, 1.0))


def front_from_back(back_photo: np.ndarray, a0: dict, s0: dict, a6: dict, P2: np.ndarray, T2: np.ndarray,
                    fid: np.ndarray, bary: np.ndarray, cov: np.ndarray, ppm_src: float, col_f: np.ndarray,
                    pair_f: np.ndarray) -> tuple[np.ndarray, np.ndarray | None, dict]:
    """Front-cap texel colours from the MIRRORED BACK photo (the outline source of a low-resolution front, S2): each
    texel's point on the front cap is where S6 placed it in the source photo (``frame_uv_src_px``), sampled from the
    mirrored back photo prepared like every photo here (the cap footprint in the S0 matte, minus the lens proposal,
    backdrop- and lens-coloured pixels and matte-edge backdrop mix; eroded, padded outward, highlights clamped), then
    colour-mapped per channel to the FRONT photo (``quantile_gain`` on the texels both photos see directly): the back
    photo gives the detail (5 px/mm on INVU against the front's 1.3), the front photo the colour it measures. Returns
    (colours (n_texels, 3), texels within the padding reach or None, info)."""
    n = len(P2)
    Q = np.asarray(a6["frame_uv_src_px"], float)[:n]
    ph = np.ascontiguousarray(back_photo[:, ::-1])
    fg = np.asarray(a0["fg_back"], bool)[:, ::-1]
    lens0 = np.asarray(a0["lens_back"], bool)[:, ::-1]
    H, W = fg.shape
    info: dict = {"source": "back photo, mirrored (the S2 outline source)", "px_per_mm": round(float(ppm_src), 3)}
    if not (np.isfinite(Q).all() and (Q >= 0).all()):
        info["skipped"] = "front cap vertices without a source position"
        return np.zeros((int(cov.sum()), 3)), None, info
    cap = np.zeros((H, W), np.uint8)
    for t in T2:
        cv2.fillPoly(cap, [np.round(Q[t] * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    cap = cap.astype(bool)
    bd, bd_info = backdrop_like(ph, cap & fg & ~lens0, s0["views"]["back"]["backdrop_rgb"])
    allowed = cap & fg & ~lens0 & ~bd
    lens_ref = depth.erode_mm(lens0, LENS_REF_ERODE_MM, ppm_src) & fg
    away = ndimage.distance_transform_edt(~lens0) > FRAME_REF_AWAY_MM * ppm_src
    lenslike, ll_info = lens_like_mask(ph, allowed, lens_ref, allowed & away, lens_region=lens0,
                                       band_px=LENSLIKE_BAND_MM * ppm_src)
    ref = allowed & ~lenslike & away
    mix, mix_info = backdrop_mix_mask(ph, allowed & ~lenslike, ~fg | bd, s0["views"]["back"]["backdrop_rgb"],
                                      np.median(ph[ref if ref.sum() >= 50 else allowed], axis=0) if allowed.any()
                                      else s0["views"]["back"]["backdrop_rgb"])
    prep = prepare_photo(ph, allowed, lenslike, ppm_src, exclude=mix)
    uvq = np.einsum("kc,kcd->kd", bary[cov].astype(np.float64), Q[T2[fid[cov]]])
    col = sample_bilinear(prep["img"], uvq[:, 0], uvq[:, 1])
    dist, inb = sample_nearest(prep["dist"], uvq[:, 0], uvq[:, 1])
    ok = inb & (dist <= prep["pad_max_px"])
    pair = pair_f & ok & (dist < 0.5)
    gain, off, ginfo = quantile_gain(col[pair], col_f[pair])
    info.update({"valid_px": int(prep["valid"].sum()), "texels_used": int(ok.sum()), "pairs_with_front": int(pair.sum()),
                 "colour_map": {"linear_gain": np.round(gain, 4).tolist(), "linear_offset": np.round(off, 5).tolist(),
                                **{k: v for k, v in ginfo.items() if k != "channels"}},
                 "backdrop_like": bd_info, "lens_like": ll_info, "edge_mix": mix_info})
    if "skipped" in ginfo:
        info["skipped"] = f"colour map: {ginfo['skipped']}"
        return col, None, info
    mapped = apply_gain(col, gain, off)
    info["front_de00_mean_colour"] = {"raw": round(mean_colour_de00(col[pair], col_f[pair]), 2),
                                      "mapped": round(mean_colour_de00(mapped[pair], col_f[pair]), 2)}
    return mapped, ok, info


def material_class(s4: dict) -> tuple[str, dict]:
    share = float(((s4.get("thickness") or {}).get("metal_share_of_frame")) or 0.0)
    cls = "metal" if share >= 0.15 else "acetate"
    return cls, {"rule": "metal when the S4 metal-stroke share of the frame >= 0.15", "metal_share_of_frame": share}


def run(product: str, run: str = "m1", force: bool = False, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    from . import cameras, generator
    t0 = time.time()
    flags: list[str] = []
    gen = generator.load(product, run)
    frame = gen.frame
    _, cams, s3 = cameras.load_cameras(product, run)
    s0, a0 = stage_dir(run, product, "s0_intake").load()
    s2, a2 = stage_dir(run, product, "s2_front").load()
    s4 = stage_dir(run, product, "s4_depth").load()[0]
    s5, a5 = stage_dir(run, product, "s5_temples").load()
    s6, a6 = stage_dir(run, product, "s6_assembly").load()
    df = depth.load_depth(product, run)
    src = depth.source_view(product, run)
    P = core.PRODUCTS[product]
    photos = {v: core.load_photo(P, v) for v in core.FIT_VIEWS}   # the held-out view is never read here
    tex_gen = gen.texture("basecolor")
    if tex_gen is None or gen.UV is None:
        raise RuntimeError(f"{product}: the generator has no basecolor/UV; S7 needs them")

    V = np.asarray(a6["frame_V"], np.float64)
    F = np.asarray(a6["frame_F"], np.int64)
    region = np.asarray(a6["frame_region"], np.int8)
    P2 = np.asarray(a6["frame_P2"], np.float64)
    T2 = np.asarray(a6["frame_T2"], np.int64)
    n = len(P2)
    if len(V) != 2 * n:
        raise RuntimeError(f"{product}: S6 frame has {len(V)} vertices, expected 2 x {n} (front + back caps)")
    ppm_src = cameras.px_per_mm_at(src.camera, frame, cameras.front_piece_centre(gen))
    ppm_front = cameras.px_per_mm_at(cams["front"], frame, cameras.front_piece_centre(gen))
    ppm_back = cameras.px_per_mm_at(cams["back"], frame, cameras.front_piece_centre(gen))
    if "low_resolution" in s0["views"]["front"].get("flags", []) or ppm_front < 3.0:
        flags.append("front_low_resolution")

    # ---- generator lens faces and the closest-point baker
    lens_f, lens_info = generator_lens_faces(gen, a2, src, df, ppm_src)
    baker = Baker(gen, lens_f, tex_gen)
    lens_ids = np.nonzero(lens_f)[0]
    lens_ids = lens_ids[::max(1, len(lens_ids) // 200_000)]      # deterministic subsample for the colour model
    gen_lens_cols = (generator.sample_texture(tex_gen, gen.UV[gen.F[lens_ids]].mean(1).astype(np.float64))
                     if len(lens_ids) else None)
    gen_lens_rgb = np.median(gen_lens_cols, axis=0) if gen_lens_cols is not None else None
    log(f"[s7 {product}] generator lens faces {lens_info['lens_faces']} ({time.time() - t0:.1f} s)")

    # ---- cap layout (source px) and texel -> 3D points
    lay = cap_layout(P2)
    Hc, Wc = lay["shape"]
    tri_t = cap_texel(P2, lay)[T2]
    fid, bary = rasterize_triangles(tri_t, (Hc, Wc))
    cov = fid >= 0
    b = bary[cov].astype(np.float64)
    Pf = np.einsum("kc,kcd->kd", b, V[T2[fid[cov]]])
    Pb = np.einsum("kc,kcd->kd", b, V[T2[fid[cov]] + n])

    # ---- front photo preparation
    ph_f = photos["front"]
    Hf, Wf = ph_f.shape[:2]
    uv_front_v = core.project_mm(V[:n], cams["front"], frame)
    cap_f = np.zeros((Hf, Wf), np.uint8)
    for t in T2:
        cv2.fillPoly(cap_f, [np.round(uv_front_v[t] * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    cap_f = cap_f.astype(bool)
    fg_f, lens_f0 = np.asarray(a0["fg_front"], bool), np.asarray(a0["lens_front"], bool)
    if src.mirrored:
        # S2 polygons live in the mirrored back photo; map them into the front photo through S2's affine
        A = np.asarray(s2["source_to_front"], float)
        polys_f = [np.asarray(p) @ A[:, :2].T + A[:, 2] for p in depth.lens_polys(a2)]
    else:
        polys_f = depth.lens_polys(a2)
    lens_poly_f = poly_raster(polys_f, (Hf, Wf))
    lens_ref_f = (depth.erode_mm(lens_poly_f, LENS_REF_ERODE_MM, ppm_front) | lens_f0) & fg_f
    away = ndimage.distance_transform_edt(~(lens_poly_f | lens_f0)) > FRAME_REF_AWAY_MM * ppm_front
    bd_f, bd_info_f = backdrop_like(ph_f, cap_f & fg_f & ~lens_f0, s0["views"]["front"]["backdrop_rgb"])
    allowed_f = cap_f & fg_f & ~lens_f0 & ~bd_f
    if bd_info_f.get("skipped_frame_is_backdrop_coloured"):
        flags.append("frame_is_backdrop_coloured")
    lenslike_f, ll_info_f = lens_like_mask(ph_f, allowed_f, lens_ref_f, allowed_f & away,
                                           lens_region=lens_poly_f | lens_f0, band_px=LENSLIKE_BAND_MM * ppm_front)
    # matte-edge pixels that are part backdrop (INVU's 1.3 px/mm front: a light rim padded into the nose apex)
    ref_f = allowed_f & ~lenslike_f & away
    mix_f, mix_info_f = backdrop_mix_mask(ph_f, allowed_f & ~lenslike_f, ~fg_f | bd_f, s0["views"]["front"]["backdrop_rgb"],
                                          np.median(ph_f[ref_f if ref_f.sum() >= 50 else allowed_f], axis=0)
                                          if allowed_f.any() else s0["views"]["front"]["backdrop_rgb"])
    lmix_f, lmix_info_f = lens_mix_mask(ph_f, allowed_f & ~lenslike_f & ~mix_f, lens_poly_f | lens_f0,
                                        np.median(ph_f[ref_f if ref_f.sum() >= 50 else allowed_f], axis=0)
                                        if allowed_f.any() else s0["views"]["front"]["backdrop_rgb"])
    mix_info_f["lens_mix"] = lmix_info_f
    mix_f = mix_f | lmix_f
    prep_f = prepare_photo(ph_f, allowed_f, lenslike_f, ppm_front, exclude=mix_f)

    uvf = core.project_mm(Pf, cams["front"], frame)
    col_f = sample_bilinear(prep_f["img"], uvf[:, 0], uvf[:, 1])
    dist_f, inb_f = sample_nearest(prep_f["dist"], uvf[:, 0], uvf[:, 1])
    photo_ok_f = inb_f & (dist_f <= prep_f["pad_max_px"])
    direct_f = inb_f & (dist_f < 0.5)
    hl_at, _ = sample_nearest(prep_f["highlight"], uvf[:, 0], uvf[:, 1])

    # ---- generator colour at every cap point (front and back) + gain
    gen_f, dgen_f = baker.colour(Pf)
    gen_b, dgen_b = baker.colour(Pb)
    pair = direct_f & ~hl_at
    gain, offset, gain_info = quantile_gain(gen_f[pair], col_f[pair])
    if "skipped" in gain_info:
        flags.append("gain_not_fitted")
    gained_f = apply_gain(gen_f, gain, offset)
    gain_info["front_cap_de00_mean_colour_raw"] = round(mean_colour_de00(gen_f[pair], col_f[pair]), 2) if pair.sum() else None
    gain_info["front_cap_de00_mean_colour_gained"] = round(mean_colour_de00(gained_f[pair], col_f[pair]), 2) if pair.sum() else None
    gain_info["space"] = "linear light, per channel"
    log(f"[s7 {product}] gain {np.round(gain, 3).tolist()} offset {np.round(offset, 4).tolist()} "
        f"dE {gain_info['front_cap_de00_mean_colour_raw']} -> {gain_info['front_cap_de00_mean_colour_gained']}")

    front_tex = np.zeros((Hc, Wc, 3), np.float32)
    front_src = np.zeros((Hc, Wc), np.uint8)       # 0 empty, 1 photo direct, 2 photo padded, 3 bake, 4 mirrored back
    tf = np.where(photo_ok_f[:, None], col_f, gained_f)
    front_src_c = np.where(direct_f, 1, np.where(photo_ok_f, 2, 3)).astype(np.uint8)
    frame_rgb = np.median(col_f[pair], axis=0) if pair.any() else np.median(gained_f, axis=0)
    back_front_info = None
    if "front_low_resolution" in flags and src.mirrored:
        # a front photo below the resolution floor measures the frame's COLOUR (a mean over hundreds of pixels) but not
        # its detail: padding 1.3 px/mm into a 12 px/mm texture drew streaks (INVU's camouflage). The outline source -
        # the mirrored back photo S2 registered to the front - supplies the detail, colour-mapped per channel (linear
        # light, ``quantile_gain``) to the front photo on the texels both see directly (``front_from_back``)
        tb, okb, back_front_info = front_from_back(photos["back"], a0, s0, a6, P2, T2, fid, bary, cov, ppm_src,
                                                   col_f, direct_f & ~hl_at)
        if okb is not None:
            # the front photo's own colour at its own resolution, the back photo's detail above it (``detail_transfer``)
            sigma = lay["k"] * ppm_src / max(ppm_front, 1e-6)
            # the base: the front photo prepared WITHOUT the erosion (its lens- and backdrop-mix pixels are excluded by
            # colour): at 1.3 px/mm one eroded pixel is a third of a brow, and the rows it removed carry the frame's
            # front-view colour (INVU's dark blue lower brow next to its lens)
            prep0 = prepare_photo(ph_f, allowed_f, lenslike_f, ppm_front, exclude=mix_f, erode=0)
            base0 = sample_bilinear(prep0["img"], uvf[:, 0], uvf[:, 1])
            d0_, inb0 = sample_nearest(prep0["dist"], uvf[:, 0], uvf[:, 1])
            tb = detail_transfer(base0, inb0 & (d0_ <= prep0["pad_max_px"]), tb, okb, cov, sigma)
            back_front_info["detail_sigma_texels"] = round(float(sigma), 2)
            tf = np.where(okb[:, None], tb, tf)
            front_src_c = np.where(okb, 4, front_src_c).astype(np.uint8)
    front_tex[cov] = tf
    front_src[cov] = front_src_c

    # ---- back cap: visibility-checked projection into the back photo
    ph_b = photos["back"]
    Hb, Wb = ph_b.shape[:2]
    cb = cams["back"]
    toward_b = raster.camera_basis(cb)[2]
    uvb = core.project_mm(Pb, cb, frame)
    d_pt = -(frame.to_norm(Pb) @ toward_b) * frame.extent
    FN = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    FN /= np.maximum(np.linalg.norm(FN, axis=1, keepdims=True), 1e-12)
    back_faces = np.nonzero(region == 1)[0]
    # map back-cap triangles to T2 rows by vertex set (the back cap is the front cap offset, same triangles)
    keyT = {tuple(sorted(t)): i for i, t in enumerate(T2.tolist())}
    back_of_T = np.full(len(T2), -1, np.int64)
    for f in back_faces:
        k = tuple(sorted(int(v - n) for v in F[f]))
        if k in keyT:
            back_of_T[keyT[k]] = f
    fT = fid[cov]
    has_face = back_of_T[fT] >= 0
    cosv = np.where(has_face, FN[np.maximum(back_of_T[fT], 0)] @ toward_b, 0.0)
    inb_b = (uvb[:, 0] > -0.5) & (uvb[:, 0] < Wb - 0.5) & (uvb[:, 1] > -0.5) & (uvb[:, 1] < Hb - 0.5)
    self_hit = raster.get_scene(V, F, frame).cast(cb, uvb[:, 0], uvb[:, 1])
    gen_hit = gen.scene(decimated=False).cast(cb, uvb[:, 0], uvb[:, 1])
    vis_self = ~self_hit["hit"] | (self_hit["depth"] >= d_pt - BACK_TOL_SELF_MM)
    vis_gen = ~gen_hit["hit"] | (gen_hit["depth"] >= d_pt - BACK_TOL_GEN_MM)
    visible = inb_b & (cosv > BACK_MIN_COS) & vis_self & vis_gen
    # back photo preparation, on the footprint of the visible back cap
    cap_b = np.zeros((Hb, Wb), bool)
    vb_i = np.round(uvb[visible]).astype(int)
    if len(vb_i):
        cap_b[np.clip(vb_i[:, 1], 0, Hb - 1), np.clip(vb_i[:, 0], 0, Wb - 1)] = True
        cap_b = ndimage.binary_closing(cap_b, iterations=2) | cap_b
    fg_b, lens_b0 = np.asarray(a0["fg_back"], bool), np.asarray(a0["lens_back"], bool)
    bd_b, bd_info_b = backdrop_like(ph_b, cap_b & fg_b & ~lens_b0, s0["views"]["back"]["backdrop_rgb"])
    allowed_b = cap_b & fg_b & ~lens_b0 & ~bd_b
    lens_ref_b = depth.erode_mm(lens_b0, LENS_REF_ERODE_MM, ppm_back) & fg_b
    away_b = ndimage.distance_transform_edt(~lens_b0) > FRAME_REF_AWAY_MM * ppm_back
    lenslike_b, ll_info_b = lens_like_mask(ph_b, allowed_b, lens_ref_b, allowed_b & away_b,
                                           lens_region=lens_b0, band_px=LENSLIKE_BAND_MM * ppm_back)
    ref_b = allowed_b & ~lenslike_b & away_b
    mix_b, mix_info_b = backdrop_mix_mask(ph_b, allowed_b & ~lenslike_b, ~fg_b | bd_b, s0["views"]["back"]["backdrop_rgb"],
                                          np.median(ph_b[ref_b if ref_b.sum() >= 50 else allowed_b], axis=0)
                                          if allowed_b.any() else s0["views"]["back"]["backdrop_rgb"])
    lmix_b, lmix_info_b = lens_mix_mask(ph_b, allowed_b & ~lenslike_b & ~mix_b, lens_b0,
                                        np.median(ph_b[ref_b if ref_b.sum() >= 50 else allowed_b], axis=0)
                                        if allowed_b.any() else s0["views"]["back"]["backdrop_rgb"])
    mix_info_b["lens_mix"] = lmix_info_b
    mix_b = mix_b | lmix_b
    prep_b = prepare_photo(ph_b, allowed_b, lenslike_b, ppm_back, exclude=mix_b)
    col_b = sample_bilinear(prep_b["img"], uvb[:, 0], uvb[:, 1])
    dist_b, _ = sample_nearest(prep_b["dist"], uvb[:, 0], uvb[:, 1])
    photo_ok_b = visible & (dist_b <= prep_b["pad_max_px"])
    gained_b = apply_gain(gen_b, gain, offset)
    back_tex = np.zeros((Hc, Wc, 3), np.float32)
    back_src = np.zeros((Hc, Wc), np.uint8)        # 0 empty, 1 photo direct, 2 photo padded, 3 bake occluded, 4 bake off-matte
    back_tex[cov] = np.where(photo_ok_b[:, None], col_b, gained_b)
    back_src[cov] = np.where(photo_ok_b, np.where(dist_b < 0.5, 1, 2), np.where(visible, 4, 3))

    # ---- walls
    wl = wall_layout(V, T2, n)
    Hw, Ww = wl["shape"]
    wall_faces = np.nonzero(region == 2)[0]
    wtri, bad_wall = wall_corner_texels(F, wall_faces, n, wl)
    if bad_wall:
        flags.append("wall_faces_unmatched")
    okw = np.isfinite(wtri).all(axis=(1, 2))
    wfid, wbary = rasterize_triangles(np.where(okw[:, None, None], wtri, -10.0), (Hw, Ww))
    wcov = wfid >= 0
    Pw = np.einsum("kc,kcd->kd", wbary[wcov].astype(np.float64), V[F[wall_faces[wfid[wcov]]]])
    gen_w, dgen_w = baker.colour(Pw)
    if len(dgen_w) and np.percentile(dgen_w, 95) > WALL_FAR_MM:
        # the S6 frame has walls where the generator (lens faces removed) has no surface nearby (Miu's drill
        # mounts sit on the lens plate): those texels take the colour of whatever frame part is nearest
        flags.append("wall_bake_far_from_generator")
    wall_tex = np.zeros((Hw, Ww, 3), np.float32)
    wall_tex[wcov] = apply_gain(gen_w, gain, offset)
    # walls the FRONT photo sees directly take the front cap's colour at their front edge (``front_visible_walls``),
    # applied below once the front texture is final; not on a front photo too coarse to resolve them (INVU's 1.1 px/mm:
    # a 1 mm wall is a fraction of a blended pixel). Occluders = the delivered frame + S5 temples.
    occ_parts = [(V, F)] + [(np.asarray(a5[f"temple_{s}_V"], float), np.asarray(a5[f"temple_{s}_F"], np.int64))
                            for s in ("R", "L") if f"temple_{s}_V" in a5]
    occ_scene = raster.get_scene(np.vstack([o[0] for o in occ_parts]),
                                 np.vstack([o[1] + off for o, off in zip(occ_parts, np.cumsum([0] + [len(o[0]) for o in occ_parts[:-1]]))]),
                                 frame)
    if "front_low_resolution" in flags:
        use_wf, winfo_f = np.zeros(len(Pw), bool), {"skipped": "front_low_resolution"}
    else:
        use_wf, winfo_f = front_visible_walls(Pw, FN[wall_faces[wfid[wcov]]], cams["front"], frame, occ_scene, (Hf, Wf),
                                              lens_poly_f | lens_f0)
    wall_src = np.zeros((Hw, Ww), np.uint8)          # 0 empty, 1 front cap colour (the front photo sees it), 3 bake
    wall_src[wcov] = np.where(use_wf, 1, 3)

    # ---- suspects (lens / backdrop colour in baked texels) -> inpaint
    h_lens = lab_hist(gen_lens_cols) if gen_lens_cols is not None and len(gen_lens_cols) >= 50 else None
    h_frame = lab_hist(gen_f[pair] if pair.sum() >= 50 else gen_f)      # generator colour where the photo shows frame
    bd_rgb = np.asarray(s0["views"]["front"]["backdrop_rgb"], float)
    raw_front = np.zeros((Hc, Wc, 3), np.float32)
    raw_front[cov] = gen_f
    raw_back = np.zeros((Hc, Wc, 3), np.float32)
    raw_back[cov] = gen_b
    raw_wall = np.zeros((Hw, Ww, 3), np.float32)
    raw_wall[wcov] = gen_w
    front_tex, inp_f = _inpaint_suspects(front_tex, front_src == 3, raw_front, h_lens, h_frame, bd_rgb, frame_rgb, cov)
    back_tex, inp_b = _inpaint_suspects(back_tex, back_src >= 3, raw_back, h_lens, h_frame, bd_rgb, frame_rgb, cov)
    # frame evidence for the walls: the colours of the two caps a wall joins, along its depth; the walls the front
    # photo sees take the front cap's colour over their whole depth
    capuv = cap_texel(P2, lay) / np.array([Wc, Hc], float)
    front_full, back_full = nearest_fill(front_tex, cov)[0], nearest_fill(back_tex, cov)[0]
    Fw_tex, Bw_tex = F[wall_faces[wfid[wcov]]], wbary[wcov]
    ev_w = np.zeros((Hw, Ww, 3), np.float32)
    ev_w[wcov] = cap_extrusion(Fw_tex, Bw_tex, n, capuv, front_full, back_full)
    wt = wall_tex[wcov]
    wt[use_wf] = cap_extrusion(Fw_tex[use_wf], Bw_tex[use_wf], n, capuv, front_full, front_full)
    wall_tex[wcov] = wt
    wall_tex, inp_w = _inpaint_suspects(wall_tex, wall_src == 3, raw_wall, h_lens, h_frame, bd_rgb, frame_rgb, wcov,
                                        evidence=ev_w)

    # ---- raw-bake baseline textures (for the before/after measurement only)
    base_front, base_back, base_wall = raw_front, raw_back, raw_wall

    def pad_all(t, m):
        return nearest_fill(t, m)[0] if m.any() else t
    front_tex, back_tex, wall_tex = pad_all(front_tex, cov), pad_all(back_tex, cov), pad_all(wall_tex, wcov)
    base_front, base_back, base_wall = pad_all(base_front, cov), pad_all(base_back, cov), pad_all(base_wall, wcov)

    # ---- per-corner frame UV
    UVc = np.zeros((len(F), 3, 2), np.float64)
    capuv = cap_texel(P2, lay) / np.array([Wc, Hc], float)
    fr0 = np.nonzero(region == 0)[0]
    UVc[fr0] = capuv[F[fr0]]
    fr1 = np.nonzero(region == 1)[0]
    UVc[fr1] = capuv[F[fr1] - n]
    wt = np.where(np.isfinite(wtri), wtri, 0.5) / np.array([Ww, Hw], float)
    UVc[wall_faces] = wt
    tex_id = np.clip(region.astype(np.int64), 0, 2)

    # ---- temples
    mclass, minfo = material_class(s4)
    k_t = min(1.0, TEMPLE_TEX_MAX / max(tex_gen.shape[:2]))
    tH, tW = int(round(tex_gen.shape[0] * k_t)), int(round(tex_gen.shape[1] * k_t))
    tex_small = cv2.resize(tex_gen, (tW, tH), interpolation=cv2.INTER_AREA) if k_t < 1 else tex_gen.copy()
    used = np.zeros((tH, tW), np.uint8)
    temple_parts = {}
    for s in ("R", "L"):
        if f"temple_{s}_V" not in a5:
            continue
        acc = (s5.get(s) or {}).get("accepted", True)
        if not acc:
            flags.append(f"temple_{s}_rejected_in_s5")
        TV, TF, TUV = a5[f"temple_{s}_V"], a5[f"temple_{s}_F"], a5[f"temple_{s}_UV"]
        temple_parts[s] = (TV, TF, TUV)
        for tri in (TUV[TF] * np.array([tW, tH]) - 0.5):        # glTF uv -> pixel-centre coordinates
            cv2.fillPoly(used, [np.round(tri * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    used = cv2.dilate(used, np.ones((9, 9), np.uint8)).astype(bool)
    fill_rgb = np.median(tex_small[used], axis=0) if used.any() else np.array([128, 128, 128])
    temple_raw = tex_small.astype(np.float32)
    temple_raw[~used] = fill_rgb
    temple_gained = temple_raw.copy()
    temple_gained[used] = apply_gain(temple_raw[used], gain, offset)
    # roughness: median generator ORM roughness (G) over the texels the temples use (the frame's material)
    orm = gen.texture("metallic_roughness")
    if orm is not None and used.any():
        orm_s = cv2.resize(orm, (tW, tH), interpolation=cv2.INTER_AREA) if orm.shape[:2] != (tH, tW) else orm
        rough = float(np.clip(np.median(orm_s[..., 1][used]) / 255.0, 0.25, 0.6))
    else:
        rough = 0.45
    factors = {"metallic": 0.8 if mclass == "metal" else 0.0, "roughness": 0.3 if mclass == "metal" else round(rough, 3)}
    # material CLASSES on the temples (metal / dielectric: miu's tortoise tips on gold arms): the generator's own
    # metallicRoughness map cleaned with its colours (material_classes); two materials only when each class covers
    # >= CLASS_MIN_SHARE of the temple surface (rayban's metal hinge pins, 5 %, stay in the one material)
    tclass, split_info = None, {"split": False, "min_share": CLASS_MIN_SHARE}
    if orm is not None and used.any() and temple_parts:
        cls_map, cls_info = material_classes(orm_s, tex_small, used)
        shares = class_area_shares(cls_map, list(temple_parts.values()))
        split_info.update({**cls_info, **shares})
        if min(shares["metal_share"], shares["dielectric_share"]) >= CLASS_MIN_SHARE:
            # the frame's class: the generator's metallic at the front cap's closest generator points
            cc = baker.closest(Pf[::max(1, len(Pf) // 20000)])
            mf = generator.sample_texture(orm, generator.interpolate_uv(gen.UV, gen.F, cc["face"], cc["bary"]))[:, 2]
            frame_class = int(float((mf / 255.0 >= METAL_TEXEL).mean()) >= 0.5)
            tclass = {"texture_class": cls_map, "textured": [f"temple_{s}" for s in temple_parts],
                      "frame_class": frame_class}
            split_info.update({"split": True, "frame_class": CLASS_NAMES[frame_class],
                               "frame_metal_share": round(float((mf / 255.0 >= METAL_TEXEL).mean()), 4)})

    # ---- evaluation renders through every S3 camera
    lens_parts = []
    i = 1
    while f"lens{i}_V" in a6:
        lens_parts.append({"V": a6[f"lens{i}_V"], "F": a6[f"lens{i}_F"], "lens": True})
        i += 1
    frame_textures = [front_tex, back_tex, wall_tex]
    base_textures = [base_front, base_back, base_wall]

    def assembly(ftex, ttex):
        parts = [{"V": V, "F": F, "UVc": UVc, "tex": ftex, "tex_id": tex_id}]
        for s, (TV, TF, TUV) in temple_parts.items():
            parts.append({"V": TV, "F": TF, "UVc": TUV[TF].astype(np.float64), "tex": [ttex], "tex_id": None})
        return parts + lens_parts

    # evaluation guard: side views have no S0 lens proposal, so photo pixels whose colour is lens-like (the
    # front photo's lens vs frame colour model) are left out of every view's comparison mask
    h_lens_ph = lab_hist(ph_f[lens_ref_f]) if lens_ref_f.sum() >= 50 else None
    h_frame_ph = lab_hist(ph_f[prep_f["valid"]]) if prep_f["valid"].sum() >= 50 else None
    if back_front_info is not None and (front_src == 4).any():
        # the mirrored back photo's detail carries the colour of the frame's BACK, lit from behind: the front photo
        # sets the colour, measured the way it is measured - through the front camera, each photo pixel integrated
        # over its footprint (``front_view_gain``) - by a per-channel linear gain on those texels
        g_front, cal = front_view_gain(assembly(frame_textures, temple_raw), cams["front"], frame, ph_f,
                                       np.asarray(a0["fg_front"], bool), np.asarray(a0["lens_front"], bool), gen,
                                       h_lens_ph, h_frame_ph)
        back_front_info["front_view_calibration"] = cal
        if g_front is not None:
            sel = (front_src == 4) | (front_src == 0)          # and the padding around them
            front_tex[sel] = linear_to_srgb(srgb_to_linear(front_tex[sel]) * g_front[None])
            frame_textures = [front_tex, back_tex, wall_tex]
    variants = {"s7": assembly(frame_textures, temple_raw), "raw_bake": assembly(base_textures, temple_raw)}
    colour = {}
    renders = {}
    for view in core.FIT_VIEWS:
        cam = cams[view]
        ph = photos[view]
        H, W = ph.shape[:2]
        fg = np.asarray(a0[f"fg_{view}"], bool)
        lens0 = np.asarray(a0[f"lens_{view}"], bool)
        roi = eval_roi(fg)
        x0, y0, x1, y1 = roi
        entry = {}
        ss = eval_supersample(cam, frame, gen)
        entry["supersample"] = ss
        for vname in ("s7", "raw_bake"):
            r = render_parts(variants[vname], cam, frame, (H, W), roi)
            base_eval = supersampled_base(variants[vname], cam, frame, (H, W), roi, ss) if ss > 1 else r["base"]
            if vname == "s7":
                renders[view] = (r, roi)
            photo_c = ph[y0:y1, x0:x1]
            fm, tm, minfo = eval_frame_mask(r, photo_c, fg[y0:y1, x0:x1], lens0[y0:y1, x0:x1], h_lens_ph, h_frame_ph)
            ok = minfo.pop("_ok")
            for k_, v_ in minfo.items():
                entry.setdefault(k_, v_)
            entry[vname] = {"frame": colour_metrics(photo_c, base_eval, fm), "temples": colour_metrics(photo_c, base_eval, tm)}
            if vname == "s7" and view in ("left", "right", "back"):
                # temple gain decision evidence: re-sample the temple pixels with the gained temple texture
                if tm.sum() >= 50:
                    rg = render_parts(assembly(frame_textures, temple_gained), cam, frame, (H, W), roi)
                    tm2 = erode_px((rg["label"] >= 1), EVAL_ERODE_PX) & ok
                    bg_ = supersampled_base(assembly(frame_textures, temple_gained), cam, frame, (H, W), roi, ss) \
                        if ss > 1 else rg["base"]
                    entry["s7_temple_gain"] = {"temples": colour_metrics(photo_c, bg_, tm2)}
        colour[view] = entry
    side_raw = [colour[v]["s7"]["temples"]["de00_mean_colour"] for v in ("left", "right")
                if colour[v]["s7"].get("temples")]
    side_gain = [colour[v]["s7_temple_gain"]["temples"]["de00_mean_colour"] for v in ("left", "right")
                 if colour[v].get("s7_temple_gain", {}).get("temples")]
    temple_gain = bool(side_raw and side_gain and len(side_raw) == len(side_gain) and np.mean(side_gain) < np.mean(side_raw))
    temple_tex = temple_gained if temple_gain else temple_raw
    for view in core.FIT_VIEWS:
        fr = colour[view]["s7"]["frame"]
        if fr and fr["de00_mean_colour"] > DE_FLAG:
            flags.append(f"frame_de00_high:{view}")

    # ---- frame/temple material: fitted in the actual AR runtime against the fit-view photos
    old_factors = dict(factors)
    ar_fit = None
    # a HARNESS failure is flagged (S10 turns ar_fit_failed into REVIEW) and keeps the old rule; any other exception
    # is a defect and fails the stage
    temple_mr, temple_factors = None, None
    try:
        fit_mr = None if tclass is None else mr_texture(tclass["texture_class"], (0.0, 1.0), (1.0, 1.0))
        base_glb, mat_names, origin_mm = calibration_glb(product, run, V, F, UVc, tex_id, [front_tex, back_tex, wall_tex],
                                                         temple_parts, s5, temple_tex, a6, s2, s6, old_factors, a5,
                                                         temple_mr=fit_mr)
        if tclass is not None:
            def _rebuild(fr, ch, sc, _cls=tclass["texture_class"], _tt=temple_tex):
                gk = {k: np.clip(np.asarray(ch[k][2], float) * sc, 0.0, 1.0) for k in (0, 1)}
                return calibration_glb(product, run, V, F, UVc, tex_id, [front_tex, back_tex, wall_tex], temple_parts,
                                       s5, bake_class_gain(_tt, _cls, gk), a6, s2, s6,
                                       {"metallic": fr[0], "roughness": fr[1],
                                        "base_color": list(np.clip(np.asarray(fr[2], float) * sc, AR_FIT_GAIN[0], 1.0)) + [1.0]},
                                       a5, temple_factors={"metallic": 1.0, "roughness": 1.0, "base_color": [1.0, 1.0, 1.0, 1.0]},
                                       temple_mr=mr_texture(_cls, (ch[0][0], ch[1][0]), (ch[0][1], ch[1][1])))[0]
            tclass["rebuild"] = _rebuild
        occ = [(V, F)] + [(TV, TF) for (TV, TF, _) in temple_parts.values()] + [(p["V"], p["F"]) for p in lens_parts]
        occ_V = np.vstack([o[0] for o in occ])
        occ_F = np.vstack([np.asarray(o[1], np.int64) + off for o, off in
                           zip(occ, np.cumsum([0] + [len(o[0]) for o in occ[:-1]]))])
        regions = photo_samplers(photos, a0, s0, cams, frame, cameras.front_piece_centre(gen), polys_f, occ_V, occ_F)
        ar_fit = fit_frame_material(base_glb, mat_names, regions, sd.root / "ar_fit", mclass, old_factors, origin_mm,
                                    log=lambda *a: log(f"[s7 {product}]", *a), classes=tclass)
    except HarnessError as e:
        ar_fit = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    if ar_fit.get("ok") and (ar_fit.get("chosen_rendered") or {}).get("status") == VERIFICATION_FAILED:
        # the chosen material was never seen rendered: applying its synthesised gain would ship a prediction
        ar_fit = {**ar_fit, "ok": False, "reason": ar_fit.get("reason") or VERIFICATION_FAILED}
    if ar_fit.get("ok"):
        c = ar_fit["chosen"]
        g = [round(float(x), 4) for x in np.broadcast_to(np.asarray(c["gain"], float), (3,))]
        if "front" in ar_fit.get("views_unusable", []):
            flags.append("ar_fit_front_view_unusable")
        factors = {"metallic": float(c["metallic"]), "roughness": float(c["roughness"]), "base_color": g + [1.0]}
        if ar_fit.get("split"):
            # two materials on the temples: each class's gain carried by the texture, its metallic/roughness by the
            # metallicRoughness texture; the frame takes its own class's factors
            cf = ar_fit["class_factors"]
            factors["base_color"] = [round(float(np.clip(x, AR_FIT_GAIN[0], 1.0)), 4) for x in g] + [1.0]
            temple_tex = bake_class_gain(temple_tex, tclass["texture_class"],
                                         {k: np.clip(cf[CLASS_NAMES[k]]["gain_rgb"], 0.0, 1.0) for k in (0, 1)})
            temple_mr = mr_texture(tclass["texture_class"], tuple(cf[n]["metallic"] for n in CLASS_NAMES),
                                   tuple(cf[n]["roughness"] for n in CLASS_NAMES))
            temple_factors = {"metallic": 1.0, "roughness": 1.0, "base_color": [1.0, 1.0, 1.0, 1.0]}
        try:
            ar_fit["sheet"] = fit_sheet(ar_fit, sd.root / "ar_fit" / "sheet.png",
                                        f"S7 AR material fit {product} ({run}): actual TryOnRenderer, room lighting, "
                                        f"white backdrop; photo | before | after per fit view")
        except (OSError, ValueError, KeyError) as e:
            ar_fit["sheet_error"] = repr(e)
        log(f"[s7 {product}] AR fit: metallic {c['metallic']} roughness {c['roughness']} gain {c['gain']} objective "
            f"{ar_fit['before_old_rule']['objective']} -> {c['objective']} (rendered {ar_fit['chosen_rendered']['objective']})")
    else:
        flags.append("ar_fit_failed")
        log(f"[s7 {product}] AR fit failed: {ar_fit.get('reason')}; keeping the ORM-median rule")
    ar_fit_public = {k: v for k, v in ar_fit.items() if not k.startswith("_")}

    # ---- write
    textures = {"frame_front.png": front_tex, "frame_back.png": back_tex, "frame_wall.png": wall_tex,
                "temple_basecolor.jpg": temple_tex}
    if temple_mr is not None:
        textures["temple_mr.png"] = temple_mr
    _texture_files(sd.root, textures)
    materials = {
        "frame_front": {"part": "frame", "region": [0], "texture": "frame_front.png", "uv": "frame_UV",
                        "factors": dict(factors)},
        "frame_back": {"part": "frame", "region": [1], "texture": "frame_back.png", "uv": "frame_UV",
                       "factors": dict(factors)},
        "frame_wall": {"part": "frame", "region": [2], "texture": "frame_wall.png", "uv": "frame_UV",
                       "factors": dict(factors)},
    }
    for s in temple_parts:
        materials[f"temple_{s}"] = {"part": f"temple_{s}", "region": None, "texture": "temple_basecolor.jpg",
                                    "uv": f"temple_{s}_UV", "factors": dict(factors if temple_factors is None else temple_factors)}
        if temple_mr is not None:
            materials[f"temple_{s}"]["metallic_roughness_texture"] = "temple_mr.png"
    nc = int(cov.sum())
    result = {
        "stage": STAGE, "product": product, "run": run,
        "materials": materials,
        "material_class": {"class": mclass, **minfo, "roughness_from_generator_orm_median": round(rough, 3)},
        "material_factors": {"chosen": factors, "old_rule": old_factors,
                             "old_rule_text": "roughness = median generator ORM G over the temple texels clamped to "
                                              "[0.25, 0.6] (metal: 0.3, metallic 0.8)",
                             "rule": "fitted in the actual AR runtime (material_fit)" if ar_fit.get("ok")
                             else "old rule (AR fit failed)"},
        "material_fit": ar_fit_public,
        "material_classes": split_info,
        "gain": {"linear_gain": np.round(gain, 5).tolist(), "linear_offset": np.round(offset, 6).tolist(), **gain_info},
        "front": {"texture": "frame_front.png", "shape": [Hc, Wc], "layout": "outline-source px crop",
                  "source_to_texel": {"origin_px": lay["origin"].tolist(), "k": lay["k"]},
                  "photo_px_per_mm": round(ppm_front, 3), "erode_px": prep_f["erode_px"],
                  "median_px": prep_f["median_px"], "pad_max_px": round(prep_f["pad_max_px"], 2),
                  "texels": nc, "share_photo_direct": round(float(direct_f.mean()), 4),
                  "share_photo_padded": round(float((photo_ok_f & ~direct_f).mean()), 4),
                  "share_bake": round(float((~photo_ok_f).mean()), 4),
                  "highlight_px": int(prep_f["highlight"].sum()), "valid_px": int(prep_f["valid"].sum()),
                  "allowed_px": int(allowed_f.sum()), "lens_like": ll_info_f, "backdrop_like": bd_info_f,
                  "edge_mix": mix_info_f, "inpaint": inp_f,
                  "share_mirrored_back": round(float((front_src[cov] == 4).mean()), 4) if nc else 0.0,
                  "mirrored_back": back_front_info},
        "back": {"texture": "frame_back.png", "shape": [Hc, Wc], "photo_px_per_mm": round(ppm_back, 3),
                 "share_visible": round(float(visible.mean()), 4), "share_photo": round(float(photo_ok_b.mean()), 4),
                 "share_bake_occluded": round(float((~visible).mean()), 4),
                 "share_bake_off_matte": round(float((visible & ~photo_ok_b).mean()), 4),
                 "occlusion": {"grazing_or_facing_away": int((cosv <= BACK_MIN_COS).sum()),
                               "self": int((~vis_self).sum()), "generator": int((~vis_gen).sum()),
                               "outside_photo": int((~inb_b).sum())},
                 "back_faces_matched": int((back_of_T >= 0).sum()), "front_triangles": int(len(T2)),
                 "lens_like": ll_info_b, "backdrop_like": bd_info_b, "edge_mix": mix_info_b, "inpaint": inp_b},
        "walls": {"texture": "frame_wall.png", "shape": [Hw, Ww], "px_per_mm": round(wl["px_per_mm"], 3),
                  "loops": wl["loops"], "chunks": wl["chunks"], "loop_lengths_mm": wl["loop_lengths_mm"],
                  "texels": int(wcov.sum()), "faces": int(len(wall_faces)), "faces_unmatched": int(bad_wall),
                  "closest_point_distance_mm": {"median": round(float(np.median(dgen_w)), 3),
                                                "p95": round(float(np.percentile(dgen_w, 95)), 3)} if len(dgen_w) else None,
                  "share_front_visible": round(float(use_wf.mean()), 4) if len(use_wf) else 0.0,
                  "front_visible": winfo_f,
                  "inpaint": inp_w},
        "generator_lens_exclusion": lens_info,
        "generator_lens_rgb": None if gen_lens_rgb is None else np.round(gen_lens_rgb, 1).tolist(),
        "frame_reference_rgb": np.round(frame_rgb, 1).tolist(),
        "temples": {"texture": "temple_basecolor.jpg", "shape": [tH, tW], "source_shape": list(tex_gen.shape[:2]),
                    "uv": "S5 donor UVs (unchanged; copied as temple_<s>_UV)",
                    "gain_applied": temple_gain,
                    "gain_rule": "apply the front-cap gain to the temples only if it lowers the mean temple dE00 "
                                 "(region mean colour) over left.jpg and right.jpg",
                    "side_de00_raw": side_raw, "side_de00_gained": side_gain,
                    "used_texel_share": round(float(used.mean()), 4)},
        "colour_difference": colour,
        "conventions": {"frame_UV": "(M,3,2) per-corner glTF uv (u right, v down, 0..1) into the texture of the "
                                    "face's region material (0 front, 1 back, 2 wall)",
                        "texel_provenance": "front_source: 1 photo, 2 photo padded, 3 bake; back_source: 1 photo, "
                                            "2 photo padded, 3 bake (occluded), 4 bake (visible but off the matte)",
                        "colour_difference": "base-colour render through the S3 camera vs photo on frame pixels eroded "
                                             f"{EVAL_ERODE_PX} px inside the S0 matte, outside the S0 lens proposal and "
                                             "outside lens-coloured photo pixels (front-photo lens/frame colour model); "
                                             "de00_mean_colour = dE00 of the region means; pixel stats after a "
                                             f"{EVAL_BLUR} px blur. Fit views only: the held-out angled view is "
                                             "never rendered or measured here (S10 evaluates it)."},
        "flags": sorted(set(flags)),
    }
    arrays = {"frame_UV": UVc.astype(np.float32), "frame_tex_id": tex_id.astype(np.int8),
              "front_source": front_src, "back_source": back_src, "wall_source": wall_src}
    for s, (TV, TF, TUV) in temple_parts.items():
        arrays[f"temple_{s}_UV"] = np.asarray(TUV, np.float32)
    result["seconds"] = round(time.time() - t0, 1)
    sd.save(result, arrays)
    try:
        make_sheet(product, run, result, photos, renders, cams, frame, V, F, UVc, tex_id,
                   [front_tex, back_tex, wall_tex], temple_parts, temple_tex, lens_parts, front_src, back_src)
    except Exception as e:  # the sheet is diagnostics; never lose the stage for it
        result["flags"] = sorted(set(result["flags"] + ["sheet_failed"]))
        result["sheet_error"] = repr(e)
        sd.save(result)
    fr = {v: (colour[v]["s7"]["frame"] or {}).get("de00_mean_colour") for v in core.FIT_VIEWS}
    log(f"[s7 {product}] {result['seconds']} s  frame dE00 {fr}  flags {result['flags']}")
    return result


# =========================================================================== sheets
def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    k = min(w / img.shape[1], h / img.shape[0])
    im = cv2.resize(img, (max(1, int(img.shape[1] * k)), max(1, int(img.shape[0] * k))),
                    interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    out = np.full((h, w, 3), 255, np.uint8)
    y, x = (h - im.shape[0]) // 2, (w - im.shape[1]) // 2
    out[y:y + im.shape[0], x:x + im.shape[1]] = im
    return out


def _crop_content(img: np.ndarray, margin: int = 10) -> np.ndarray:
    m = (img < 250).any(-1)
    if not m.any():
        return img
    ys, xs = np.nonzero(m)
    return img[max(0, ys.min() - margin):ys.max() + margin + 1, max(0, xs.min() - margin):xs.max() + margin + 1]


def _label(img: np.ndarray, text: str, font) -> np.ndarray:
    im = Image.fromarray(img)
    ImageDraw.Draw(im).text((6, 4), text, fill=(0, 0, 0), font=font)
    return np.asarray(im)


def synthetic_views(parts: list[dict], frame: NormFrame, size: int = 640, ppm: float = 4.2) -> dict:
    out = {}
    for name, yaw, pitch in (("front", 0.0, 0.0), ("yaw 30", 30.0, 8.0), ("side (yaw 90)", 90.0, 0.0),
                             ("top (pitch 90)", 0.0, 90.0)):
        cam = raster.view_camera(frame, yaw, pitch, ppm, (size, size))
        r = render_parts(parts, cam, frame, (size, size))
        out[name] = _crop_content(shaded_image(r))
    return out


def make_sheet(product, run, result, photos, renders, cams, frame, V, F, UVc, tex_id, frame_textures,
               temple_parts, temple_tex, lens_parts, front_src, back_src) -> str:
    font, small = _font(22), _font(17)
    TW, TH = 560, 330
    parts = [{"V": V, "F": F, "UVc": UVc, "tex": frame_textures, "tex_id": tex_id}]
    for s, (TV, TF, TUV) in temple_parts.items():
        parts.append({"V": TV, "F": TF, "UVc": TUV[TF].astype(np.float64), "tex": [temple_tex], "tex_id": None})
    parts += lens_parts
    rows = []
    col = result["colour_difference"]
    # row 1: photos; row 2: textured renders through the S3 cameras
    r1, r2 = [], []
    for view in core.FIT_VIEWS:
        r, (x0, y0, x1, y1) = renders[view]
        ph = photos[view][y0:y1, x0:x1]
        fr = (col[view]["s7"]["frame"] or {})
        fb = (col[view]["raw_bake"]["frame"] or {})
        r1.append(_label(_fit(ph, TW, TH), f"photo {view}", small))
        txt = f"render {view}: frame dE00 {fr.get('de00_mean_colour')} (raw bake {fb.get('de00_mean_colour')})"
        r2.append(_label(_fit(shaded_image(r), TW, TH), txt, small))
    rows += [np.hstack(r1), np.hstack(r2)]
    syn = synthetic_views(parts, frame)
    rows.append(np.hstack([_label(_fit(img, TW, TH), name, small) for name, img in syn.items()]))
    # row 4: frame textures + provenance
    prov_colours = np.array([[255, 255, 255], [60, 170, 60], [240, 200, 40], [220, 60, 60], [120, 120, 230]], np.uint8)
    t_front = np.clip(frame_textures[0], 0, 255).astype(np.uint8)
    t_back = np.clip(frame_textures[1], 0, 255).astype(np.uint8)
    t_wall = np.clip(frame_textures[2], 0, 255).astype(np.uint8)
    rows.append(np.hstack([_label(_fit(t_front, TW, TH), "frame_front.png", small),
                           _label(_fit(prov_colours[front_src], TW, TH),
                                  "front: green photo, yellow padded, red bake, blue mirrored back", small),
                           _label(_fit(t_back, TW, TH), "frame_back.png", small),
                           _label(_fit(prov_colours[back_src], TW, TH), "back: green photo, red occluded, blue off-matte", small)]))
    wall_h = int(min(TH * 2, t_wall.shape[0] * (4 * TW) / t_wall.shape[1]))
    rows.append(_label(_fit(t_wall, 4 * TW, max(wall_h, 60)), "frame_wall.png (strips: front edge at the top of each strip)", small))
    body = np.vstack(rows)
    g = result["gain"]
    head = [f"S7 texture {product} ({run}): class {result['material_class']['class']}, factors "
            f"{result['materials']['frame_front']['factors']}, flags {result['flags']}",
            f"gain (linear) {g['linear_gain']} offset {g['linear_offset']}; front-cap mean dE00 generator->photo "
            f"{g.get('front_cap_de00_mean_colour_raw')} raw -> {g.get('front_cap_de00_mean_colour_gained')} gained; "
            f"temple gain applied {result['temples']['gain_applied']}",
            "frame dE00 (region mean / pixel median) s7 vs raw bake: " + "; ".join(
                f"{v} {(col[v]['s7']['frame'] or {}).get('de00_mean_colour')}/{(col[v]['s7']['frame'] or {}).get('de00_pixel_median')}"
                f" vs {(col[v]['raw_bake']['frame'] or {}).get('de00_mean_colour')}/{(col[v]['raw_bake']['frame'] or {}).get('de00_pixel_median')}"
                for v in core.FIT_VIEWS),
            f"front texels: photo {result['front']['share_photo_direct']}, padded {result['front']['share_photo_padded']}, "
            f"bake {result['front']['share_bake']}; back: photo {result['back']['share_photo']}, occluded "
            f"{result['back']['share_bake_occluded']}; walls {result['walls']['px_per_mm']} px/mm, inpainted "
            f"{result['walls']['inpaint']['inpainted_texels']} texels"]
    hh = 30 * len(head) + 10
    top = Image.new("RGB", (body.shape[1], hh), "white")
    d = ImageDraw.Draw(top)
    for i, t in enumerate(head):
        d.text((8, 6 + 30 * i), t, fill=(0, 0, 0), font=font if i == 0 else small)
    sheet = np.vstack([np.asarray(top), body])
    path = stage_dir(run, product, STAGE).root / "sheet.png"
    Image.fromarray(sheet).save(path)
    return str(path)


def contact_sheet(run: str = "m1", products=None, width: int = 2240) -> str:
    products = products or list(core.PRODUCTS)
    tiles = []
    for p in products:
        f = stage_dir(run, p, STAGE).root / "sheet.png"
        if f.exists():
            im = np.asarray(Image.open(f).convert("RGB"))
            k = width / im.shape[1]
            tiles.append(cv2.resize(im, (width, int(im.shape[0] * k)), interpolation=cv2.INTER_AREA))
    out = core.BSA_DATA / "runs" / run / "s7_contact.png"
    if tiles:
        Image.fromarray(np.vstack(tiles)).save(out)
    return str(out)


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="BSA S7 texture")
    ap.add_argument("--product", default=None)
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--contact", action="store_true")
    a = ap.parse_args(argv)
    for p in ([a.product] if a.product else list(core.PRODUCTS)):
        run(p, a.run, a.force)
    if a.contact or not a.product:
        print(contact_sheet(a.run))


if __name__ == "__main__":
    main()
