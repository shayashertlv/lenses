"""Per-material appearance: each material of a candidate measured against the author photos at matched views.

The lens had a measured target (modeler.lens_colour); the other materials had none, and test-pilot-002's owner saw on
r0006 that the crystal is clearer than the photos, the gold hinge and anchor blocks look heavier than the T, and the
gold is slightly yellower than the photos' pale rose-champagne (review MVP-04, MVP-05, MVP-11). This module gives
every metal and crystal material a measured target the same way.

The runtime is rendered by the local AR harness (bsa.archeck) at a pose matching each author photo's FITTED camera
(``harness_pose``: the harness turns a synthetic face, not the camera, and the glasses sit 6.5 cm in front of the face's
pivot, so the pose that shows the glasses from a photo's direction is solved, not copied; test-pilot-002's left photo,
camera yaw 86.8 / pitch 21.4, is harness yaw -71.2 / pitch 0.5 / roll -25.8 within 1 degree) over a solid background
equal to the photos' own backdrop (evidence ``backdrop_rgb``, else the border median as lens_colour samples it), in ONE
extra harness run (17-19 s on the calibration assets; with the analysis 19-22 s per build). Held-out views are never used. Every pixel of both images is labelled by the
model itself: the photo through the host rasterizer and the fitted camera (bsa.raster), the render through a ray cast
with the harness's recorded camera, the runtime's own arm spread (see_through.runtime_arm_vertices) and its temple clip.

1. Metal colour. The metal a photo shows: the metal material's footprint where the first surface is that metal or a
   translucent one (hardware inside crystal is seen), widened by the camera fit's tolerance (``FIT_TOL_MM``). Inside
   that window the metal pixels are the chroma bins OVER-REPRESENTED against a ring around it (``METAL_CONTRAST``):
   a crystal's own warm tint, a navy or tortoise acetate and the backdrop are as common in the ring as in the window and
   drop out, the metal's colour does not. The same rule selects the render's metal (window 1 px: the render is labelled
   exactly), so photo and render are selected alike. Hue (circular median) and HSV saturation (median) are compared;
   brightness is not (studio vs the runtime room light). A neutral metal (authored saturation below
   ``NEUTRAL_METAL_SATURATION``: nickel, steel) has no hue to compare and is reported, not flagged.
2. Crystal visibility. In a band around the lens outline (``RIM_BAND_MM`` outside the lens footprint, first surface
   translucent, metal windows removed), how far each pixel sits from the backdrop (max channel, 8-bit): share over
   ``VISIBLE_LEVELS``, mean, median, p90, photo vs render at the same view and backdrop. The runtime draws crystal
   through a sharp camera-transmission twin (ar/src/render/translucent-twin.ts, translucent-look-through.ts): the
   look-through is the background at the fragment's own pixel x the white base colour x a UNIFORM Beer-Lambert factor
   attenuation_colour ^ (thickness / attenuation distance), mixed with the lit surface by the transmission; no
   refraction offset, no roughness blur, no path length. So ``tint_srgb`` and ``thickness_mm`` (the uniform factor) and
   ``transmission`` change it; ``roughness``, ``ior`` and ``coat`` change only the room reflection (``CRYSTAL_KNOBS``,
   measured on r0006 variants). The recommendation is the tint that brings the render's MEDIAN level to the photo's: the
   crystal's body (recommended.target 'body_median'). When the photo's visibility is the refraction structure of its
   edges (the photo's band spread p90 - p10 is at least ``EDGE_SPREAD_RATIO`` x the render's even after that tint; r0006:
   mean 47-49 over a median of 30-31, spread 93) no knob reaches the edges: ``crystal_clarity_runtime_limited``, and the
   author matches the body and does not chase the edge contrast.
3. Hardware size. Per region (``endpiece``: beyond the lenses and within ``ENDPIECE_DEPTH_MM`` of the front, hinge and
   T; ``temple``; ``tip``: the rear ``1 - TIP_START_FRACTION`` of the depth) and per side, the metal pixels' share of the
   region's projected geometry, photo vs render at the same view and at MATCHED resolution (the photo box-filtered to
   the runtime's px/mm, ``match_resolution``: the harness draws 1.3-2.4 px/mm against the photos' 6.4-7.8, so a 1 mm part
   is 2-3 antialiased render pixels). ``ratio`` = render / photo; ``hardware_heavy`` above ``AREA_RATIO_HIGH``,
   ``hardware_light`` below ``AREA_RATIO_LOW``. Only EXPOSED metal is compared: where more than ``EMBEDDED_SHARE`` of the
   region's metal is seen through a translucent first surface (metal embedded in crystal) the photo's crystal refracts
   and magnifies it outside the model's footprint into the contrast ring, while the runtime draws it sharp (no
   refraction), so its area share measures the optics, not the hardware (test-pilot-002 r0006's temple core wire read
   1.99 'heavy' while the photo shows a pale gold band about 2 mm wide, at least as wide as the runtime's line); such a
   region reads status ``embedded``, never a flag. A side whose model footprint differs between the two labellings by
   more than ``FOOTPRINT_AGREEMENT`` (the photo camera fit cuts a hinge at the image edge, the runtime spreads the arms)
   is not compared either.
4. The ``material_match`` sheet: per material the photo crop beside the render crop at the matched view, with swatches
   and the numbers.

Thresholds: see the constants; each cites the measurement that set it (r0006, which the owner says should flag gold
colour, hinge-block size and crystal clarity, against the owner-accepted vb-astra1 c0004 and miu-astra2 c0002 gold,
which should not).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .lens_colour import _mat4, linear_to_srgb, photo_backdrop, srgb_to_linear
from .paths import AUTOMATION

# ------------------------------------------------------------------------------------------------ constants
# Data (2026-09-28, the local AR harness at the matched poses; photo / runtime):
#   metal hue (circular median of the selected pixels): r0006 front 0.0904 / 0.1167, left 0.0904 / 0.1212, back 0.0915 /
#     0.1162 (diff -0.026 / -0.031 / -0.025); miu-astra2 c0002 front 0.1078 / 0.1193, left 0.1081 / 0.1209 (-0.012 / -0.013);
#     vb-astra1 c0004 left 0.1155 / 0.1235 (-0.008). Saturation photo / runtime: r0006 0.56 / 0.66 (front, left), miu 0.61 /
#     0.73, vb 0.47: the runtime draws every gold more saturated than a studio photo, accepted or not.
#   crystal (r0006 only; no owner-accepted crystal exists): mean distance from the white backdrop photo 47.3 (front rim band)
#     and 50.6 (side body) vs the runtime's 4.9 and 3.6; the photo's p90 - p10 spread 103 and 83 vs 7 and 7. Variants of
#     r0006's crystal through the harness (front, white): roughness 0.5 4.2, ior 1.9 3.9 (no gain), attenuation distance / 4 or
#     thickness x 4 21.0, attenuation colour 0.7 49.0 with the spread still 8, transmission 0.5 0.0 (the lit white base on white).
#   hardware share of the region (runtime / photo), full resolution, every metal pixel: r0006 endpiece front 1.45, left
#     1.90, back 1.65, temple left 1.99; miu endpiece left 0.98, temple left 0.91; vb temple left 0.98. At MATCHED resolution
#     per side: r0006 front endpiece 1.45 / 1.59 and left 1.62, temple left 1.53, but 70-79 % (front) and 88-92 % (left) of
#     that metal is seen through the crystal; the back sees the hinge blocks directly (4-18 % embedded): side +1 1.07, side -1
#     not comparable (the model's hinge footprint 7 photo px vs 50 runtime px: the photo fit cuts it). Per hinge, back view,
#     1.34 px/mm: the runtime's blocks 18.5 and 17.4 mm2 of selected metal, the photo's intact one 17.9 mm2. miu left
#     endpiece 0.95, temple 0.85 (footprints 1.67x / 1.36x apart); vb temple 1.03 (1.81x).
#   rayban-astra1 (nickel), invu and oakley (dark steel): neutral or hidden metal, no crystal: nothing flags.
# the harness (ar/qa/provider-comparison-ar.html): the face pivot 23 cm in front of the camera, the glasses at the
# catalogue's offset from it (catalog.ts, the same numbers see_through.GLASSES_OFFSET_CM ports), pose limits
FACE_DISTANCE_CM = 23.0
POSE_LIMITS = {"yaw_degrees": 80.0, "pitch_degrees": 60.0, "roll_degrees": 60.0}
# a view is matched when the solved pose shows the glasses within this angle of the photo's camera direction (r0006: front
# 0.1, left 1.0, back by the asset-back inspection 7.9; vb's and miu's backs, fitted 9 degrees from above, are 13.9 and
# 18.5 off and are skipped: the inspection cannot look down)
MAX_DIRECTION_ERROR_DEG = 12.0
APPEARANCE_VIEWS = ("front", "left", "right", "back", "angled")
# views rendered per build (one harness run, 17-19 s whatever the count: 7 cases x 1 view took 25.5 s, 1 case x 3 views
# 22-26 s, so the cost is the browser start); front, one side and the back cover the regions
MAX_VIEWS = 3
# the run uses one backdrop (the front photo's); a view whose own backdrop differs by more than this (8-bit, any channel)
# is still used for metal colour and size (opaque) but not for crystal visibility, which is measured against the backdrop
# (r0006's back photo is on 234 grey, its front on 255)
BACKDROP_TOLERANCE = 6.0
# the camera fit's tolerance in the photo (observe: contour means about 1 mm) and the ring the metal is contrasted with
FIT_TOL_MM = 1.0
CONTEXT_MM = 2.0
RENDER_TOL_PX = 0        # the render is labelled exactly; a 1 px widening doubled a 2-3 px part at 2.4 px/mm (miu 1.75 vs 0.98)
# metal selection: chroma bins (HSV saturation x hue as a vector) of BIN width, over-represented in the window at least
# METAL_CONTRAST x their share of the ring; pixels below MIN_CHROMA saturation carry no hue and never count. A fixed
# saturation cut could not work: r0006's crystal is itself warm (saturation 0.1-0.2 around the gold) and vb's navy acetate
# is more saturated (0.25-0.55) than its gold
BIN = 0.04
METAL_CONTRAST = 2.0
MIN_CHROMA = 0.06
MIN_VALUE = 0.15
MIN_METAL_PX = {"photo": 40, "render": 12}
NEUTRAL_METAL_SATURATION = 0.15   # rayban's nickel 0.086, invu/oakley's steel 0.07-0.24 (hidden); the golds 0.39-0.49
# metal colour: the hue tolerance sits 1.5x above the largest accepted difference (miu 0.013) and 1.2x under r0006's
# smallest (0.025); saturation is flagged only outside what the accepted golds read (0.47-0.73, widened) and a
# recommendation then corrects relative to their geometric mean (the runtime's usual boost)
METAL_HUE_TOLERANCE = 0.02
METAL_SATURATION_RANGE = (0.4, 0.95)
SATURATION_RATIO_TYPICAL = 0.59
# crystal: facing the lenses a band RIM_BAND_OFFSET_MM..+RIM_BAND_MM outside the lens outline (inside every rim:
# test-pilot-002's brow is 1.68 mm), a pixel 'visible' past VISIBLE_LEVELS (the see-through fixer's MVP-04 measure)
RIM_BAND_OFFSET_MM = 0.3
RIM_BAND_MM = 1.2
VISIBLE_LEVELS = 8.0
MIN_BAND_PX = {"photo": 200, "render": 40}
# render / photo mean distance: r0006 reads 0.07-0.10; half the photo's visibility is the flag (no accepted crystal exists
# to set it tighter)
CRYSTAL_RATIO_LOW = 0.5
CRYSTAL_RATIO_HIGH = 2.0
# the photo's spread (p90 - p10) beyond this multiple of what the recommended uniform tint gives is refraction structure
# the twin cannot draw (r0006: 93 vs 8-9 after the tint, a factor above 10; a uniformly tinted acetate reads about 1)
EDGE_SPREAD_RATIO = 2.0
# hardware regions: the endpiece holds the T, hinge and anchor (r0006 x 61-68, z 0..-11 mm); the tip starts where the
# temple bends (r0006 about 72% of the depth)
ENDPIECE_DEPTH_MM = 15.0
TIP_START_FRACTION = 0.72
# runtime / photo share at matched resolution: the accepted assets read 0.85-1.03 and r0006's exposed hinge 1.07; an
# exposed block drawn 1.4x the photo's share is heavy
AREA_RATIO_HIGH = 1.4
AREA_RATIO_LOW = 0.5
# a region side is compared when both images show at least this much of it (mm2 at the matched px/mm: miu's front endpiece
# sides are about 15 mm2 in the runtime, too small; its left endpiece 52 mm2 in the photo) and at least this much metal
MIN_REGION_MM2 = 30.0
MIN_REGION_METAL_MM2 = 1.5
# embedded metal: more than this share of the region's metal footprint seen through a translucent first surface (r0006:
# front endpiece 0.70-0.79, left 0.88-0.90, temple 0.90-1.00; the back's hinge blocks 0.04-0.18)
EMBEDDED_SHARE = 0.5
# the two labellings must show the same hardware: the model's area-metal footprint in the side's region within this factor
# (mm2; accepted assets 1.36-1.81 apart as the runtime spreads the arms, r0006's back hinge cut by the photo fit 7.1x)
FOOTPRINT_AGREEMENT = 2.5
EMBEDDED_REASON = ("not compared: the metal here is seen through the crystal, whose refraction moves and magnifies it in the photo "
                   "past the model's outline while the runtime draws it sharp and undimmed (no refraction), so an area share would "
                   "measure the optics, not the hardware's size; judge it on the material_match sheet")
# which author knobs change the crystal in the runtime (translucent-twin.ts, confirmed on the r0006 variants above)
CRYSTAL_KNOBS = {
    "tint_srgb": "works: the look-through is multiplied by tint ^ (thickness / 4 mm) (uniform: no edge or path-length term)",
    "thickness_mm": "works: the same uniform factor's exponent (thicker = denser, uniformly)",
    "transmission": "works: mixes the lit surface in (below 1 the crystal turns milky-opaque, also over the skin)",
    "roughness": "no effect on the look-through (no blur in the twin); only the room reflection's sharpness",
    "ior": "no effect on the look-through (no refraction offset in the twin); only the reflection strength",
    "coat": "no effect on the look-through; only the reflection",
}
ROLE_BY_KIND = {"metal": "metal", "translucent": "crystal", "lens": "lens"}
LENS_PARTS = ("lens_R", "lens_L", "lens_C")
TEMPLE_PARTS = ("temple_R", "temple_L")


# ------------------------------------------------------------------------------------------------ colour helpers
# srgb_to_linear / linear_to_srgb come from modeler.lens_colour (imported above).
def rgb_to_hsv(img: np.ndarray) -> np.ndarray:
    """(..., 3) 8-bit RGB -> (..., 3) HSV in 0..1 (colorsys conventions, vectorised)."""
    a = np.asarray(img, float) / 255.0
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn
    safe = np.where(d > 1e-9, d, 1.0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rc, gc, bc = (mx - r) / safe, (mx - g) / safe, (mx - b) / safe
    h = np.where(r == mx, bc - gc, np.where(g == mx, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(d > 1e-9, (h / 6.0) % 1.0, 0.0)
    s = np.where(mx > 0, d / np.maximum(mx, 1e-9), 0.0)
    return np.stack([h, s, mx], -1)


def hsv_to_rgb(h: float, s: float, v: float) -> list[float]:
    import colorsys
    return [255.0 * x for x in colorsys.hsv_to_rgb(h % 1.0, float(np.clip(s, 0, 1)), float(np.clip(v, 0, 1)))]


def circular_median(h: np.ndarray) -> float:
    """A robust centre of hues (0..1, circular): the median of the unit vectors' components."""
    ang = 2 * np.pi * np.asarray(h, float)
    return float((math.atan2(np.median(np.sin(ang)), np.median(np.cos(ang))) / (2 * np.pi)) % 1.0)


def hue_difference(a: float, b: float) -> float:
    """Signed a - b on the hue circle, in -0.5..0.5 (positive: a is further toward yellow/green than b, for warm hues)."""
    d = (a - b) % 1.0
    return d - 1.0 if d > 0.5 else d


# ------------------------------------------------------------------------------------------------ poses
def _rot(axis: str, deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _offset_cm() -> np.ndarray:
    from .see_through import GLASSES_OFFSET_CM
    return np.asarray(GLASSES_OFFSET_CM, float)


def harness_axes(yaw: float, pitch: float, roll: float, centre_cm) -> tuple[np.ndarray, np.ndarray]:
    """(direction from the glasses' ``centre_cm`` to the camera, the image's up) in the ASSET frame for a harness face
    pose (THREE Euler YXZ = Ry Rx Rz, the face pivot FACE_DISTANCE_CM in front of the camera)."""
    M = _rot("y", yaw) @ _rot("x", pitch) @ _rot("z", roll)
    d = harness_camera_cm(yaw, pitch, roll) - np.asarray(centre_cm, float)
    return d / np.linalg.norm(d), M.T @ np.array([0.0, 1.0, 0.0])


def harness_camera_cm(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Where the harness camera sits in the ASSET frame (cm) for a face pose: the face pivot FACE_DISTANCE_CM in front of
    the camera, the glasses at the catalogue offset from it. test-pilot-002 r0006's recorded camera_origin_in_asset:
    (0, -3.3, 16.5) cm at pose 0 and (-13.2, -3.3, 12.3) at yaw 35."""
    M = _rot("y", yaw) @ _rot("x", pitch) @ _rot("z", roll)
    return M.T @ np.array([0.0, 0.0, FACE_DISTANCE_CM]) - _offset_cm()


def asset_back_direction(centre_cm) -> np.ndarray:
    """The camera direction of the harness's asset-back inspection in the asset frame: the asset is turned 180 degrees
    about Y around the face pivot (asset z = -offset z), so the camera lands at z = -(FACE_DISTANCE_CM + offset z)
    (r0006's recorded back camera_origin_in_asset: (0, -3.3, -29.5) cm)."""
    cam = harness_camera_cm(0.0, 0.0, 0.0)
    pz = -_offset_cm()[2]
    cam = np.array([-cam[0], cam[1], 2 * pz - cam[2]])
    d = cam - np.asarray(centre_cm, float)
    return d / np.linalg.norm(d)


def photo_axes(camera: dict) -> tuple[np.ndarray, np.ndarray]:
    """(toward the camera, image up after roll) of a BSA photo camera (reconstruction.camera.project), model frame."""
    y, p, r = (math.radians(float(camera[k])) for k in ("yaw", "pitch", "roll"))
    right = np.array([math.cos(y), 0.0, -math.sin(y)])
    up = np.array([-math.sin(p) * math.sin(y), math.cos(p), -math.sin(p) * math.cos(y)])
    toward = np.array([math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)])
    return toward, math.sin(r) * right + math.cos(r) * up


def harness_pose(camera: dict, centre_cm) -> dict:
    """The harness view that shows the glasses from a photo camera's direction: {view (harness keys), direction_error_deg}.
    A camera behind the glasses gets the asset-back inspection; otherwise yaw/pitch/roll are solved within the harness
    limits for the direction first, the image's up second."""
    from scipy import optimize
    toward, up = photo_axes(camera)
    if toward[2] < -0.5:
        d = asset_back_direction(centre_cm)
        return {"view": {"type": "asset-back"}, "direction_error_deg": round(math.degrees(math.acos(float(np.clip(d @ toward, -1, 1)))), 2)}

    def loss(x):
        d, u = harness_axes(*x, centre_cm)
        return 4.0 * float(np.sum((d - toward) ** 2)) + float(np.sum((u - up) ** 2))
    bounds = [(-POSE_LIMITS["yaw_degrees"], POSE_LIMITS["yaw_degrees"]), (-POSE_LIMITS["pitch_degrees"], POSE_LIMITS["pitch_degrees"]),
              (-POSE_LIMITS["roll_degrees"], POSE_LIMITS["roll_degrees"])]
    best = None
    for y0 in (-70.0, -35.0, 0.0, 35.0, 70.0):
        for p0 in (-30.0, 0.0, 30.0):
            res = optimize.minimize(loss, [y0, p0, 0.0], bounds=bounds, method="L-BFGS-B")
            if best is None or res.fun < best.fun:
                best = res
    d, _ = harness_axes(*best.x, centre_cm)
    yaw, pitch, roll = (round(float(v), 2) for v in best.x)
    return {"view": {"yaw_degrees": yaw, "pitch_degrees": pitch, "roll_degrees": roll},
            "direction_error_deg": round(math.degrees(math.acos(float(np.clip(d @ toward, -1, 1)))), 2)}


def glb_centre_cm(mesh) -> np.ndarray:
    """The front piece's centre (frame + lens primitives) of a loaded GLB in centimetres (asset units are metres)."""
    idx = [np.arange(p["vertex_start"], p["vertex_start"] + p["vertex_count"]) for p in mesh.parts or []
           if str(p.get("name") or "") in ("frame",) + LENS_PARTS]
    V = mesh.vertices[np.concatenate(idx)] if idx else mesh.vertices
    return 100.0 * (V.min(0) + V.max(0)) / 2.0


def hex_colour(rgb) -> str:
    return "#" + "".join(f"{int(round(float(np.clip(c, 0, 255)))):02x}" for c in rgb[:3])


def view_backdrop(evidence: dict, vid: str, image: np.ndarray | None = None) -> list[float] | None:
    """A photo's backdrop: the intake's ``backdrop_rgb`` when recorded, else the border median (lens_colour.photo_backdrop)."""
    e = (evidence.get("views") or {}).get(vid) or {}
    if e.get("backdrop_rgb") is not None:
        return [float(x) for x in e["backdrop_rgb"][:3]]
    if image is None:
        return None
    return [float(x) for x in photo_backdrop(image)[0]]


def photo_path(evidence: dict, vid: str) -> Path | None:
    row = next((r for r in evidence.get("inputs", []) if r.get("id") == vid and not r.get("held_out")), None)
    if row is None:
        return None
    p = Path(row["path"])
    p = p if p.is_absolute() else AUTOMATION / p
    return p if p.is_file() else None


def plan_views(views: dict, evidence: dict, centre_cm, *, max_views: int = MAX_VIEWS) -> tuple[list[dict], dict]:
    """The author views to match: fitted (a camera), author-visible, a supported view, a photo on disk, a pose within
    MAX_DIRECTION_ERROR_DEG. Front first, then the sides, the back, the angled view, at most ``max_views``.
    Returns (planned, skipped {vid: reason})."""
    order = {"front": 0, "left": 1, "right": 1, "back": 2, "angled": 3}
    planned, skipped, have_side = [], {}, False
    for vid, rec in sorted(views.items(), key=lambda kv: order.get((kv[1] or {}).get("view"), 9)):
        view = (rec or {}).get("view")
        if view not in APPEARANCE_VIEWS or "camera" not in (rec or {}):
            skipped[vid] = "no fitted camera for this view"
            continue
        if view in ("left", "right") and have_side:
            skipped[vid] = "one side view per build (the other side mirrors it)"
            continue
        path = photo_path(evidence, vid)
        if path is None:
            skipped[vid] = "no author photo on disk"
            continue
        pose = harness_pose(rec["camera"], centre_cm)
        if pose["direction_error_deg"] > MAX_DIRECTION_ERROR_DEG:
            skipped[vid] = f"the harness cannot show this direction (closest pose {pose['direction_error_deg']:.1f} deg off)"
            continue
        if len(planned) >= max_views:
            skipped[vid] = f"at most {max_views} views per build"
            continue
        have_side |= view in ("left", "right")
        planned.append({"vid": vid, "view": view, "harness_id": f"app_{vid}"[:40].lower(), "harness_view": pose["view"],
                        "direction_error_deg": pose["direction_error_deg"], "photo": str(path)})
    return planned, skipped


def photo_lens_region(evidence: dict, view: str, shape) -> np.ndarray | None:
    """The lenses as the front photo shows them: the intake's measured outlines (native photo pixels), filled. None for
    any other view (no measured outline there) or without outlines."""
    lenses = (evidence.get("front") or {}).get("lenses") or []
    if view != "front" or not lenses:
        return None
    img = Image.new("L", (int(shape[1]), int(shape[0])), 0)
    d = ImageDraw.Draw(img)
    for lens in lenses:
        pts = np.asarray(lens.get("outline_px") or [], float)
        if len(pts) >= 3:
            d.polygon([tuple(p) for p in pts], fill=255)
    m = np.asarray(img) > 0
    return m if m.any() else None


def crystal_zone_for(view: str) -> str:
    return "rim_band" if view in ("front", "back") else "body"


# ------------------------------------------------------------------------------------------------ labels
def face_materials(objects: dict) -> np.ndarray:
    """The material name of every face in ``observe.mesh_of`` order ('' where none)."""
    out = []
    for o in objects.values():
        if not len(o["F"]):
            continue
        names = list(o.get("materials") or [])
        M = np.asarray(o.get("M", np.zeros(len(o["F"]), np.int64)), np.int64)
        arr = np.array(names + [""], dtype=object)
        out.append(arr[np.where((M >= 0) & (M < len(names)), M, len(names))])
    return np.concatenate(out) if out else np.zeros(0, object)


REGIONS = ("endpiece", "temple", "tip")


def face_regions(V_mm: np.ndarray, F: np.ndarray, part: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(region name per face or '', side -1/+1 per face) from the geometry itself: ``endpiece`` beyond the lenses' outer x
    and within ENDPIECE_DEPTH_MM of the front, ``tip`` in the rear 1 - TIP_START_FRACTION of the depth, ``temple`` the
    temple parts in between. Lens faces have no region."""
    c = V_mm[F].mean(1)
    lens = np.isin(part, LENS_PARTS)
    lens_x = float(np.abs(V_mm[np.unique(F[lens])][:, 0]).max()) if lens.any() else 0.8 * float(np.abs(V_mm[:, 0]).max())
    front = np.isin(part, ("frame",) + LENS_PARTS)
    z_front = float(V_mm[np.unique(F[front])][:, 2].max()) if front.any() else float(V_mm[:, 2].max())
    depth = z_front - c[:, 2]
    max_depth = max(float(z_front - V_mm[:, 2].min()), 1e-6)
    region = np.full(len(F), "", dtype=object)
    beyond = np.abs(c[:, 0]) >= lens_x - 1.0
    region[beyond & (depth <= ENDPIECE_DEPTH_MM)] = "endpiece"
    temple = np.isin(part, TEMPLE_PARTS) | beyond
    region[temple & (depth > ENDPIECE_DEPTH_MM)] = "temple"
    region[temple & (depth >= TIP_START_FRACTION * max_depth)] = "tip"
    region[lens] = ""
    return region, np.where(c[:, 0] < 0, -1, 1)


# ------------------------------------------------------------------------------------------------ photo labelling
def photo_labels(V_mm, F, frame, camera: dict, shape) -> dict:
    """First-hit face ids of the model through a photo's fitted camera (host rasterizer)."""
    from bsa import raster
    from bsa.core import camera_from_dict
    r = raster.RasterScene(V_mm, F, frame).render(camera_from_dict(camera), tuple(shape), 1, None)
    return {"face_id": r["face_id"]}


def footprint(V_mm, F, sel: np.ndarray, frame, camera: dict, shape) -> np.ndarray:
    from bsa import raster
    from bsa.core import camera_from_dict
    if not sel.any():
        return np.zeros(tuple(shape), bool)
    return raster.RasterScene(V_mm, F[sel], frame).render(camera_from_dict(camera), tuple(shape), 1, None)["mask"]


# ------------------------------------------------------------------------------------------------ render labelling
class RenderModel:
    """One exported GLB as the harness drew it: per-face material, part, object-free region labels (from the undeformed
    geometry) and a ray cast of the runtime-deformed geometry through a render row's recorded camera."""

    def __init__(self, glb_path: Path, materials: dict):
        from reconstruction.mesh import load_glb_bytes
        self.raw = Path(glb_path).read_bytes()
        self.mesh = load_glb_bytes(self.raw)
        n = len(self.mesh.faces)
        self.material = np.full(n, "", dtype=object)
        self.part = np.full(n, "", dtype=object)
        for p in self.mesh.parts or []:
            s = slice(p["face_start"], p["face_start"] + p["face_count"])
            self.material[s] = str(p.get("material") or "")
            self.part[s] = str(p.get("name") or "")
        self.kind = np.array([(materials.get(m) or {}).get("kind", "") for m in self.material], dtype=object)
        self.region, self.side = face_regions(self.mesh.vertices * 1000.0, self.mesh.faces, self.part)

    def centre_cm(self) -> np.ndarray:
        return glb_centre_cm(self.mesh)

    def cast(self, render: dict, shape, select: np.ndarray | None = None) -> dict:
        """{face_id (H, W, -1 miss), px_per_mm at the front piece, camera_x_sign} for one harness render row; with
        ``select`` (a face mask) only those faces are cast (a footprint)."""
        import open3d as o3d
        from .see_through import runtime_arm_vertices, temple_clip_zm
        cam = render["camera"]
        lenses = (render.get("spatial") or {}).get("lenses") or []
        H, W = int(cam.get("height", shape[0])), int(cam.get("width", shape[1]))
        m2w = _mat4(lenses[0]["mesh_to_world"])
        view = _mat4(cam["view_matrix"])
        proj = _mat4(cam["projection_matrix"])
        verts, _ = runtime_arm_vertices(self.mesh, self.raw, render)
        F = self.mesh.faces
        # the runtime discards temple fragments behind its clip: drop the temple faces wholly behind it
        clip = temple_clip_zm(render)
        z = self.mesh.vertices[F][:, :, 2]
        temple = np.isin(self.part, TEMPLE_PARTS)
        behind = temple & np.where(self.side < 0, (z < clip[-1]).all(1), (z < clip[1]).all(1))
        keep = np.nonzero(~behind & (True if select is None else np.asarray(select, bool)))[0]
        if not len(keep):
            return {"face_id": np.full((H, W), -1, np.int64), "px_per_mm": float("nan"), "camera_x_sign": 1}
        mv = view @ m2w
        Vc = (np.c_[verts, np.ones(len(verts))] @ mv.T)[:, :3]
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(Vc, np.float32)),
                            o3d.core.Tensor(np.ascontiguousarray(F[keep], np.uint32)))
        jj, ii = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
        ndc = np.stack([2 * jj.ravel() / W - 1, 1 - 2 * ii.ravel() / H, np.ones(H * W), np.ones(H * W)], 1)
        far = ndc @ np.linalg.inv(proj).T
        far = far[:, :3] / far[:, 3:4]
        d = far / np.linalg.norm(far, axis=1, keepdims=True)
        rays = np.c_[np.zeros_like(d), d].astype(np.float32)
        ans = scene.cast_rays(o3d.core.Tensor(rays))
        prim = ans["primitive_ids"].numpy().astype(np.int64)
        hit = prim != 0xFFFFFFFF
        fid = np.full(H * W, -1, np.int64)
        fid[hit] = keep[prim[hit]]
        # px per mm at the front piece: 1 mm steps along the asset axes projected through the render's matrices; the
        # longest is the one across the line of sight (x is edge-on in a side view)
        c = self.centre_cm() / 100.0
        pts = np.vstack([c, c + np.eye(3) * 0.001])
        clipc = np.c_[pts, np.ones(4)] @ (proj @ mv).T
        pix = np.c_[(clipc[:, 0] / clipc[:, 3] + 1) * W / 2, (1 - clipc[:, 1] / clipc[:, 3]) * H / 2]
        ppm = float(np.linalg.norm(pix[1:] - pix[0], axis=1).max())
        camera_asset = (np.linalg.inv(mv) @ np.array([0.0, 0.0, 0.0, 1.0]))[:3]
        return {"face_id": fid.reshape(H, W), "px_per_mm": ppm,
                "camera_x_sign": -1 if camera_asset[0] < 0 else 1}


# ------------------------------------------------------------------------------------------------ measurements
def metal_pixels(hsv: np.ndarray, window: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """The window's pixels whose chroma bin is over-represented against the ring (``METAL_CONTRAST``): the metal's
    colour, not the crystal's own tint, an acetate around it or the backdrop, which are as common in the ring."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    ok = (s >= MIN_CHROMA) & (v >= MIN_VALUE)
    nb = int(round(2 * 0.9 / BIN))
    bx = np.clip(((s * np.cos(2 * np.pi * h) + 0.9) / BIN).astype(int), 0, nb - 1)
    by = np.clip(((s * np.sin(2 * np.pi * h) + 0.9) / BIN).astype(int), 0, nb - 1)
    key = bx * nb + by
    nw, nr = max(int(window.sum()), 1), max(int(ring.sum()), 1)
    hw = np.bincount(key[window & ok], minlength=nb * nb).astype(float)
    hr = np.bincount(key[ring & ok], minlength=nb * nb).astype(float)
    keep = (hw >= 3) & (hw / nw >= METAL_CONTRAST * hr / nr)
    return window & ok & keep[key]


def colour_stats(hsv: np.ndarray, sel: np.ndarray, img: np.ndarray) -> dict:
    hs = hsv[sel]
    return {"hue": round(circular_median(hs[:, 0]), 4), "saturation": round(float(np.median(hs[:, 1])), 4),
            "value": round(float(np.median(hs[:, 2])), 4), "rgb": [round(float(x), 1) for x in np.median(img[sel], 0)],
            "pixels": int(sel.sum())}




def band_stats(img: np.ndarray, zone: np.ndarray, backdrop) -> dict:
    """How far a zone's pixels sit from the backdrop: max-channel |pixel - backdrop| (8-bit). ``_pixels`` (private,
    never written) keeps the zone's colours for the tint prediction."""
    px = img[zone]
    d = np.abs(px - np.asarray(backdrop, float)[None]).max(-1)
    return {"visible_share": round(float((d > VISIBLE_LEVELS).mean()), 4), "mean_delta": round(float(d.mean()), 2),
            "median_delta": round(float(np.median(d)), 2), "p10_delta": round(float(np.percentile(d, 10)), 2),
            "p90_delta": round(float(np.percentile(d, 90)), 2), "pixels": int(zone.sum()),
            "median_linear": [round(float(x), 4) for x in np.median(srgb_to_linear(px), 0)], "_pixels": px}


def _dilate(mask: np.ndarray, px: float) -> np.ndarray:
    n = int(round(px))
    return ndimage.binary_dilation(mask, iterations=n) if n > 0 and mask.any() else mask.copy()


def _nearest_labels(label: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """``label`` extended to every pixel from its nearest ``valid`` pixel."""
    if not valid.any():
        return label
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    return label[idx[0], idx[1]]


# regions compared per view: facing the front or the back only the endpieces are seen whole (the temples run along the
# line of sight); a side shows all three on its near side
VIEW_REGIONS = {"front": ("endpiece",), "back": ("endpiece",), "left": REGIONS, "right": REGIONS, "angled": ("endpiece", "temple")}


def analyse_image(img: np.ndarray, fid: np.ndarray, *, kind: np.ndarray, material: np.ndarray, region: np.ndarray,
                  side: np.ndarray, part: np.ndarray, metal_footprints: dict, lens_footprint: np.ndarray | None, ppm: float,
                  tol_px: float, backdrop, regions_side: int | None, source: str, crystal_zone: str = "rim_band",
                  lens_region: np.ndarray | None = None, area_metals: set[str] | None = None) -> dict:
    """The per-image measurements, the same rule for a photo and a render (``source`` 'photo' | 'render' only sets the
    minimum pixel counts): per metal material the selected pixels and their colour, per crystal material its zone's
    distance from the backdrop, per region the metal share of the ``area_metals`` (default: every metal; the caller passes
    the coloured ones: a neutral metal is not told from a grey crystal or a dark acetate by colour, and rayban-astra1's
    nickel read a false 1.65 on its temple). ``fid``: first-hit face ids (-1 miss) into the per-face
    arrays; ``metal_footprints``: {metal material: every pixel its faces cover, occluded or not}; ``tol_px``: the
    labelling's tolerance (the photo camera fit; 0 for a render, labelled exactly); ``regions_side``: only this side's
    regions (a side view: the near arm), None for both; ``lens_region``: the lens as this image shows it (the front
    photo's measured outlines), else the model's lens."""
    hsv = rgb_to_hsv(img)
    hit = fid >= 0
    fk = np.full(fid.shape, "", dtype=object)
    fk[hit] = kind[fid[hit]]
    fmat = np.full(fid.shape, "", dtype=object)
    fmat[hit] = material[fid[hit]]
    through = fk == "translucent"
    out = {"metal": {}, "crystal": {}, "masks": {}, "px_per_mm": round(float(ppm), 4)}
    all_sel = np.zeros(fid.shape, bool)
    windows = np.zeros(fid.shape, bool)
    area_fp = np.zeros(fid.shape, bool)          # the area metals' footprint this image shows (exposed or through crystal)
    area_emb = np.zeros(fid.shape, bool)         # the part of it seen through a translucent first surface (embedded)
    for name, fp in metal_footprints.items():
        # the metal this image shows: its footprint where the first surface is that metal or a translucent one
        visible = fp & ((fmat == name) | through)
        if area_metals is None or name in area_metals:
            area_fp |= visible
            area_emb |= fp & through
        if not visible.any():
            out["metal"][name] = {"status": "hidden", "visible_px": 0}
            continue
        window = _dilate(visible, tol_px)
        ring = _dilate(window, CONTEXT_MM * ppm) & ~window
        sel = metal_pixels(hsv, window, ring)
        windows |= window
        if area_metals is None or name in area_metals:
            all_sel |= sel
        out["masks"][name] = sel
        out["masks"]["window:" + name] = window
        rec = {"visible_px": int(visible.sum()), "window_px": int(window.sum())}
        if sel.sum() >= MIN_METAL_PX[source]:
            rec.update(status="measured", **colour_stats(hsv, sel, img))
        else:
            rec.update(status="too_few_pixels", pixels=int(sel.sum()))
        out["metal"][name] = rec
    # crystal: facing the lenses a band RIM_BAND_OFFSET_MM..+RIM_BAND_MM outside the lens (inside every rim:
    # test-pilot-002's brow is 1.68 mm), else (a side) the translucent body; the tolerance and the metal windows removed
    lens = np.zeros(fid.shape, bool)
    lens[hit] = np.isin(part[fid[hit]], LENS_PARTS)
    lens_all = lens_region if lens_region is not None else (lens | (lens_footprint if lens_footprint is not None else False))
    for name in sorted({str(m) for m, k in zip(material, kind) if k == "translucent"}):
        mine = fmat == name
        if crystal_zone == "rim_band" and lens_all.any():
            inner = _dilate(lens_all, tol_px + RIM_BAND_OFFSET_MM * ppm)
            zone = _dilate(inner, RIM_BAND_MM * ppm) & ~inner & _dilate(mine, tol_px)
        else:
            zone = ndimage.binary_erosion(mine, iterations=max(1, int(round(tol_px)))) & ~_dilate(lens_all, tol_px)
        zone &= ~windows
        out["masks"]["crystal:" + name] = zone
        out["crystal"][name] = (dict(band_stats(img, zone, backdrop), zone=crystal_zone) if zone.sum() >= MIN_BAND_PX[source]
                                else {"status": "too_few_pixels", "pixels": int(zone.sum()), "zone": crystal_zone})
    # regions: the first surface's region (the geometry); a metal pixel belongs to its nearest labelled pixel's region. Per
    # side ('-1' / '+1', the model's x): the region's pixels, the selected metal, the area metals' footprint and the part of
    # it seen through a translucent first surface (compare_regions compares exposed metal only)
    reg = np.full(fid.shape, "", dtype=object)
    reg[hit] = region[fid[hit]]
    sd = np.zeros(fid.shape, int)
    sd[hit] = side[fid[hit]]
    reg_ext = _nearest_labels(reg, hit)
    sd_ext = _nearest_labels(sd, hit)
    areas = {}
    keys = ("region_px", "metal_px", "footprint_px", "embedded_px")
    for rname in REGIONS:
        sides, union = {}, np.zeros(fid.shape, bool)
        for s in (-1, 1):
            if regions_side and s != regions_side:
                continue
            rm = (reg == rname) & (sd == s)
            mm = all_sel & (reg_ext == rname) & (sd_ext == s)
            union |= rm | mm
            sides[f"{s:+d}"] = {"region_px": int(rm.sum()), "metal_px": int(mm.sum()), "share": round(float(mm.sum()) / max(int(rm.sum()), 1), 4),
                                "footprint_px": int((area_fp & rm).sum()), "embedded_px": int((area_emb & rm).sum())}
        out["masks"]["region:" + rname] = union
        agg = {k: sum(v[k] for v in sides.values()) for k in keys}
        areas[rname] = dict(agg, share=round(agg["metal_px"] / max(agg["region_px"], 1), 4), sides=sides)
    out["regions"] = areas
    return out


def match_resolution(img: np.ndarray, labels: dict, factor: float) -> tuple[np.ndarray, dict]:
    """A photo and its label maps at ``factor`` x its resolution (the runtime's px/mm over the photo's): the image
    box-filtered (area average, as the runtime's antialiasing mixes a thin part with its surroundings), each label map
    sampled at the new pixels' centres (the render's labels are a ray per pixel centre). A factor of 1 or more returns
    the photo unchanged: a coarser photo is never upsampled."""
    if not factor < 1.0:
        return img, dict(labels)
    H, W = img.shape[:2]
    h, w = max(1, int(round(H * factor))), max(1, int(round(W * factor)))
    small = np.asarray(Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((w, h), Image.BOX)).astype(float)
    rows = np.clip(((np.arange(h) + 0.5) * H / h).astype(int), 0, H - 1)
    cols = np.clip(((np.arange(w) + 0.5) * W / w).astype(int), 0, W - 1)
    return small, {k: (v[rows][:, cols] if v is not None else None) for k, v in labels.items()}


# ------------------------------------------------------------------------------------------------ comparisons
def _base_srgb(spec: dict) -> list[float] | None:
    lin = spec.get("base_color_linear")
    return [float(x) for x in linear_to_srgb(lin[:3])] if lin else None


def compare_metal(name: str, spec: dict, per_view: list[tuple[str, dict, dict]]) -> dict:
    """One metal material over the matched views ([(view id, photo record, render record)]): the median per-view hue
    difference (photo - render, on the circle) and saturation ratio (photo / render), flags and a recommended base colour
    (the authored colour turned by the hue difference; its saturation changed only when the ratio is off, and then
    relative to the runtime's usual boost ``SATURATION_RATIO_TYPICAL``). A neutral metal is reported, never flagged."""
    base = _base_srgb(spec)
    base_hsv = rgb_to_hsv(np.asarray(base, float)) if base else None
    rows = [(v, p, r) for v, p, r in per_view if p.get("status") == "measured" and r.get("status") == "measured"]
    rec = {"role": "metal", "base_color_srgb": [round(x) for x in base] if base else None, "flags": [], "views": [v for v, _, _ in rows]}
    if not rows:
        why = sorted({f"{v}: photo {p.get('status')}, render {r.get('status')}" for v, p, r in per_view})
        rec.update(status="unmeasured", reason=("no view shows this metal in both the photo and the render: " + "; ".join(why)) if why else "no matched view")
        return rec
    photo = {"hue": round(circular_median(np.array([p["hue"] for _, p, _ in rows])), 4),
             "saturation": round(float(np.median([p["saturation"] for _, p, _ in rows])), 4),
             "rgb": [round(float(x), 1) for x in np.median([p["rgb"] for _, p, _ in rows], 0)], "pixels": int(sum(p["pixels"] for _, p, _ in rows))}
    render = {"hue": round(circular_median(np.array([r["hue"] for _, _, r in rows])), 4),
              "saturation": round(float(np.median([r["saturation"] for _, _, r in rows])), 4),
              "rgb": [round(float(x), 1) for x in np.median([r["rgb"] for _, _, r in rows], 0)], "pixels": int(sum(r["pixels"] for _, _, r in rows))}
    dh = float(np.median([hue_difference(p["hue"], r["hue"]) for _, p, r in rows]))
    sr = float(np.median([p["saturation"] / max(r["saturation"], 1e-3) for _, p, r in rows]))
    per = {v: {"hue": round(hue_difference(p["hue"], r["hue"]), 4), "saturation_ratio": round(p["saturation"] / max(r["saturation"], 1e-3), 3)}
           for v, p, r in rows}
    rec.update(status="measured", photo=photo, render=render, deltas={"hue": round(dh, 4), "saturation_ratio": round(sr, 3), "per_view": per})
    if base_hsv is not None and base_hsv[1] < NEUTRAL_METAL_SATURATION:
        rec["status"] = "neutral"
        rec["note"] = (f"a neutral metal (authored saturation {base_hsv[1]:.2f}): no hue to compare; the numbers are reported, "
                       "never flagged, and brightness is not compared (studio vs the runtime room)")
        return rec
    hue_off = abs(dh) > METAL_HUE_TOLERANCE
    sat_off = not METAL_SATURATION_RANGE[0] <= sr <= METAL_SATURATION_RANGE[1]
    if hue_off:
        rec["flags"].append("metal_hue_off")
    if sat_off:
        rec["flags"].append("metal_saturation_off")
    if base_hsv is not None and (hue_off or sat_off):
        s_new = base_hsv[1] * (sr / SATURATION_RATIO_TYPICAL) if sat_off else base_hsv[1]
        rgb = hsv_to_rgb(base_hsv[0] + dh, s_new, base_hsv[2])
        rec["recommended"] = {"base_color_srgb": [int(round(x)) for x in rgb], "hue": round(float((base_hsv[0] + dh) % 1.0), 4),
                              "from": [round(x) for x in base]}
    direction = "toward rose/red" if dh < 0 else "toward yellow/green"
    rec["note"] = (f"photo metal hue {photo['hue']:.3f} vs runtime {render['hue']:.3f} ({abs(dh):.3f} {direction}); saturation ratio "
                   f"{sr:.2f} (the runtime renders metal more saturated than a studio photo: accepted assets read "
                   f"{METAL_SATURATION_RANGE[0]:.2f}-{METAL_SATURATION_RANGE[1]:.2f} as matching); brightness is not compared")
    return rec


def _tint_prediction(pixels: np.ndarray, k, backdrop) -> tuple[float, float, float]:
    """(mean delta, p90 - p10 spread, median delta) of a render zone's pixels after its look-through is scaled by ``k`` per
    channel (a uniform tint or thickness change multiplies the look-through, which is the whole crystal pixel over a solid
    backdrop up to a small reflection)."""
    px = linear_to_srgb(srgb_to_linear(pixels) * np.asarray(k, float)[None])
    d = np.abs(px - np.asarray(backdrop, float)[None]).max(-1)
    return float(d.mean()), float(np.percentile(d, 90) - np.percentile(d, 10)), float(np.median(d))


def compare_crystal(name: str, spec: dict, per_view: list[tuple]) -> dict:
    """One translucent material over the matched views ([(view id, photo zone stats, render zone stats, backdrop matches,
    the run's backdrop)]): the ratio render / photo of the mean distance from the backdrop, the per-channel factor that
    brings the render's median to the photo's and the tint it takes at the authored thickness, and whether the photo's
    spread (its refraction lines) is reachable by that uniform factor at all."""
    tr = spec.get("translucent") or {}
    rows = [(v, p, r, bd) for v, p, r, ok, bd in per_view if ok and p.get("status") is None and r.get("status") is None]
    rec = {"role": "crystal", "flags": [], "views": [v for v, *_ in rows], "knobs": CRYSTAL_KNOBS}
    mismatched = [v for v, _, _, ok, _ in per_view if not ok]
    if not rows:
        rec.update(status="unmeasured", reason=("no matched view shows the crystal in both images on the run's backdrop"
                                                + (f" (a different backdrop: {mismatched})" if mismatched else "")))
        return rec
    ratios, ks, spreads_photo, spreads_pred, pred_means, pred_medians = [], [], [], [], [], []
    for v, p, r, bd in rows:
        ratios.append(r["mean_delta"] / max(p["mean_delta"], 1e-3))
        # the per-channel factor that brings the runtime's MEDIAN crystal colour to the photo's: the body, not the edges
        k = np.clip(np.asarray(p["median_linear"], float) / np.maximum(np.asarray(r["median_linear"], float), 1e-3), 0.02, 1.5)
        ks.append(k)
        m, sp, md = _tint_prediction(r["_pixels"], k, bd) if "_pixels" in r else (float("nan"), float("nan"), float("nan"))
        pred_means.append(m)
        spreads_pred.append(sp)
        pred_medians.append(md)
        spreads_photo.append(p["p90_delta"] - p["p10_delta"])
    ratio = float(np.median(ratios))
    k = np.median(np.array(ks), 0)
    sp_photo = float(np.median(spreads_photo))
    finite = [x for x in spreads_pred if np.isfinite(x)]
    sp_pred = float(np.median(finite)) if finite else float("nan")
    rec.update(status="measured",
               photo={"mean_delta": round(float(np.median([p["mean_delta"] for _, p, _, _ in rows])), 1),
                      "median_delta": round(float(np.median([p["median_delta"] for _, p, _, _ in rows])), 1),
                      "visible_share": round(float(np.median([p["visible_share"] for _, p, _, _ in rows])), 3), "spread": round(sp_photo, 1)},
               render={"mean_delta": round(float(np.median([r["mean_delta"] for _, _, r, _ in rows])), 1),
                       "median_delta": round(float(np.median([r["median_delta"] for _, _, r, _ in rows])), 1),
                       "visible_share": round(float(np.median([r["visible_share"] for _, _, r, _ in rows])), 3),
                       "spread": round(float(np.median([r["p90_delta"] - r["p10_delta"] for _, _, r, _ in rows])), 1)},
               deltas={"visibility_ratio": round(ratio, 3), "per_view": {v: round(x, 3) for (v, *_), x in zip(rows, ratios)},
                       "lookthrough_factor": [round(float(x), 3) for x in k]})
    if ratio < CRYSTAL_RATIO_LOW:
        rec["flags"].append("crystal_too_clear")
    elif ratio > CRYSTAL_RATIO_HIGH:
        rec["flags"].append("crystal_too_dense")
    thickness = float(tr.get("thickness_mm") or 4.0)
    distance = float(tr.get("attenuation_distance_mm") or 4.0)
    att = np.clip(np.asarray(tr.get("attenuation_rgb_linear") or spec.get("base_color_linear") or [1, 1, 1], float)[:3], 0.01, 1.0)
    tint = np.clip((att ** (thickness / distance) * k) ** (distance / thickness), 0.01, 1.0)
    finite_means = [x for x in pred_means if np.isfinite(x)]
    finite_medians = [x for x in pred_medians if np.isfinite(x)]
    photo_median, photo_mean = rec["photo"]["median_delta"], rec["photo"]["mean_delta"]
    limited = (np.isfinite(sp_pred) and "crystal_too_clear" in rec["flags"] and sp_photo > EDGE_SPREAD_RATIO * max(sp_pred, VISIBLE_LEVELS))
    if limited:
        rec["flags"].append("crystal_clarity_runtime_limited")
        rec["note"] = (f"the photo's crystal shows by its edges and internal-reflection lines (spread p90-p10 {sp_photo:.0f} levels, mean "
                       f"{photo_mean:.0f} over a median of {photo_median:.0f}); the runtime draws the crystal as a uniform look-through "
                       f"(translucent-twin.ts: no refraction, no path length, no roughness blur), so even the recommended tint leaves a "
                       f"spread of {sp_pred:.0f}: the tint matches the crystal's body, not the edge structure. No author knob reaches "
                       "the edges; roughness, ior and coat have no effect on the look-through, and a uniform tint also darkens the skin "
                       "seen through the rim")
    if rec["flags"]:
        # The build reply clips strings to 120 characters, so the instruction comes first and the explanation after it.
        if limited:
            note = (f"apply once: matches the crystal BODY (median {photo_median:.0f}); do not chase the photo's mean {photo_mean:.0f} "
                    "or its edges. ")
        else:
            note = f"matches the crystal BODY (the photo's median {photo_median:.0f}); roughness, ior and coat do not change it. "
        note += (f"gl.material_translucent tint_srgb at the same thickness_mm brings the runtime's median distance from the backdrop "
                 f"to the photo's median ({photo_median:.0f} levels); the look-through is multiplied uniformly, and roughness, ior and "
                 "coat do not change it in the runtime")
        if limited:
            note += (f". The photo's mean ({photo_mean:.0f}) and its spread ({sp_photo:.0f}) are edge and refraction contrast the runtime "
                     "cannot draw: do not chase them (a tint dark enough to reach the mean over-darkens the body and the skin behind "
                     "the rim); apply this tint once")
        rec["recommended"] = {"tint_srgb": [int(round(x)) for x in linear_to_srgb(tint)], "thickness_mm": round(thickness, 2),
                              "target": "body_median", "target_median_delta": photo_median,
                              "predicted_median_delta": round(float(np.median(finite_medians)), 1) if finite_medians else None,
                              "predicted_mean_delta": round(float(np.median(finite_means)), 1) if finite_means else None,
                              "note": note}
    return rec


_NO_SIDE = {"region_px": 0, "metal_px": 0, "share": 0.0, "footprint_px": 0, "embedded_px": 0}


def compare_regions(per_view: list[tuple]) -> dict:
    """The hardware regions over the matched views ([(view id, view, photo regions, render regions, px/mm)], px/mm the
    matched resolution both were measured at, or a (photo, render) pair): per region the median per-view-and-side metal
    shares and ratio render / photo, from the sides that show the region in both images (``VIEW_REGIONS``, at least
    ``MIN_REGION_MM2`` of it, metal in at least one of them) with EXPOSED metal (at most ``EMBEDDED_SHARE`` of its
    footprint seen through a translucent surface) and the same hardware in both labellings (``FOOTPRINT_AGREEMENT``).
    A region whose metal is embedded wherever it is seen reads {status 'embedded', flag None, reason}; the sides left out
    are listed with their reason (``not_compared``)."""
    out = {}
    for rname in REGIONS:
        rows, notes, skipped = [], [], {}
        for vid, view, p, r, ppm in per_view:
            if rname not in VIEW_REGIONS.get(view, ()):
                continue
            pp, rp = (float(ppm[0]), float(ppm[1])) if isinstance(ppm, (tuple, list)) else (float(ppm), float(ppm))
            p_sides, r_sides = (p.get(rname) or {}).get("sides") or {}, (r.get(rname) or {}).get("sides") or {}
            for sk in sorted(set(p_sides) | set(r_sides)):
                key = f"{vid}:{sk}"
                pr, rr = p_sides.get(sk) or _NO_SIDE, r_sides.get(sk) or _NO_SIDE
                p_metal, r_metal = MIN_REGION_METAL_MM2 * pp ** 2, MIN_REGION_METAL_MM2 * rp ** 2
                # a region one image does not show is said only when the other shows metal there
                if pr["region_px"] < MIN_REGION_MM2 * pp ** 2:
                    if rr["metal_px"] >= r_metal:
                        notes.append(f"{key}: the region is not in the photo's view")
                    continue
                if rr["region_px"] < MIN_REGION_MM2 * rp ** 2:
                    if pr["metal_px"] >= p_metal:
                        notes.append(f"{key}: the photo shows metal here but the runtime does not draw the region at this pose "
                                     "(behind its temple clip or too small)")
                    continue
                if pr["metal_px"] < p_metal and rr["metal_px"] < r_metal:
                    continue                                       # no metal there in either image
                if not pr["footprint_px"] and not rr["footprint_px"]:
                    continue                                       # metal-coloured, but none of the model's metal is there
                emb = max(pr["embedded_px"] / max(pr["footprint_px"], 1), rr["embedded_px"] / max(rr["footprint_px"], 1))
                if emb > EMBEDDED_SHARE:
                    skipped[key] = f"{emb:.0%} of the metal seen through the crystal"
                    continue
                pa, ra = pr["footprint_px"] / pp ** 2, rr["footprint_px"] / rp ** 2
                if min(pa, ra) <= 0 or max(pa, ra) / min(pa, ra) > FOOTPRINT_AGREEMENT:
                    skipped[key] = (f"the photo's fitted camera and the runtime's pose show different amounts of this hardware (model "
                                    f"footprint {pa:.0f} vs {ra:.0f} mm2)")
                    continue
                ps = max(pr["share"], p_metal / pr["region_px"])
                rows.append((key, pr["share"], rr["share"], min(rr["share"] / ps, 10.0)))
        if not rows:
            embedded = {k: v for k, v in skipped.items() if "seen through" in v}
            if embedded and len(embedded) == len(skipped):
                out[rname] = {"status": "embedded", "flag": None, "not_compared": skipped,
                              "reason": EMBEDDED_REASON + " (" + "; ".join(f"{k} {v}" for k, v in skipped.items()) + ")"}
            elif skipped or notes:
                out[rname] = {"status": "unmeasured", "flag": None, "not_compared": skipped,
                              "reason": "; ".join(notes + [f"{k}: {v}" for k, v in skipped.items()])}
            continue
        ratio = float(np.median([x[3] for x in rows]))
        flag = "hardware_heavy" if ratio > AREA_RATIO_HIGH else "hardware_light" if ratio < AREA_RATIO_LOW else None
        out[rname] = {"photo_share": round(float(np.median([x[1] for x in rows])), 4), "render_share": round(float(np.median([x[2] for x in rows])), 4),
                      "ratio": round(ratio, 3), "flag": flag, "views": {x[0]: round(x[3], 3) for x in rows}}
        if skipped:
            out[rname]["not_compared"] = skipped
    return out


# ------------------------------------------------------------------------------------------------ the sheet
SHEET_ROW_HEIGHT = 220
SHEET_TEXT_WIDTH = 430


def _crop(img: np.ndarray, mask: np.ndarray | None, margin_px: float) -> Image.Image | None:
    if mask is None or not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    m = int(round(margin_px))
    H, W = mask.shape
    box = (max(0, xs.min() - m), max(0, ys.min() - m), min(W, xs.max() + 1 + m), min(H, ys.max() + 1 + m))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).crop(box)


def _one_side(mask: np.ndarray | None, near: np.ndarray | None = None) -> np.ndarray | None:
    """A metal crop's mask: within the endpiece region when the metal is there (``near``), and only its image-left half,
    so the photo and the runtime tiles zoom on the same hardware rather than the whole frame."""
    if mask is None or not mask.any():
        return mask
    if near is not None and (mask & near).any():
        mask = mask & near
    xs = np.nonzero(mask)[1]
    keep = np.zeros_like(mask)
    cut = int((xs.min() + xs.max()) / 2) + 1
    keep[:, :cut] = True
    return mask & keep if (mask & keep).any() else mask


def _sheet_row(lines: list[str], tiles: list[tuple[str, Image.Image | None]], swatches: list[tuple[str, list]]) -> Image.Image:
    from .observe import _font, _label
    h = SHEET_ROW_HEIGHT
    parts = []
    text = Image.new("RGB", (SHEET_TEXT_WIDTH, h), (255, 255, 255))
    d = ImageDraw.Draw(text)
    for i, line in enumerate(lines[:13]):
        d.text((6, 4 + 16 * i), line[:66], fill=(0, 0, 0), font=_font(13))
    parts.append(text)
    for label, im in tiles:
        if im is None:
            continue
        im = im.resize((max(1, int(im.size[0] * h / max(im.size[1], 1))), h), Image.LANCZOS)
        if im.size[0] > 900:
            im = im.crop((0, 0, 900, h))
        parts.append(_label(im, label))
    if swatches:
        sw = Image.new("RGB", (110, h), (255, 255, 255))
        d = ImageDraw.Draw(sw)
        step = h // len(swatches)
        for i, (label, rgb) in enumerate(swatches):
            d.rectangle([0, i * step, 109, (i + 1) * step - 2], fill=tuple(int(round(float(x))) for x in rgb[:3]))
            d.rectangle([0, i * step, 8 + 7 * len(label), i * step + 16], fill=(0, 0, 0))
            d.text((3, i * step + 1), label, fill=(255, 255, 255), font=_font(12))
        parts.append(sw)
    row = Image.new("RGB", (sum(p.size[0] for p in parts) + 8 * (len(parts) - 1), h), (255, 255, 255))
    x = 0
    for p in parts:
        row.paste(p, (x, 0))
        x += p.size[0] + 8
    return row


def material_match_sheet(record: dict, images: dict, out: Path) -> Path | None:
    """The ``material_match`` sheet: one row per measured metal and crystal material and per measured hardware region,
    [the numbers | the photo crop | the runtime's crop at the matched pose over the photo's backdrop | swatches]."""
    from .observe import stack
    rows = []
    for name, m in (record.get("materials") or {}).items():
        if m.get("role") == "metal" and m.get("status") in ("measured", "neutral"):
            vid = max(m["views"], key=lambda v: images[v]["photo"]["metal"].get(name, {}).get("pixels", 0))
            im = images[vid]
            lines = [f"METAL {name}  ({m['status']}, view {vid})",
                     f"hue photo {m['photo']['hue']:.3f}  runtime {m['render']['hue']:.3f}  (diff {m['deltas']['hue']:+.3f})",
                     f"saturation photo {m['photo']['saturation']:.2f}  runtime {m['render']['saturation']:.2f}  ratio {m['deltas']['saturation_ratio']:.2f}",
                     f"authored base_color_srgb {m.get('base_color_srgb')}",
                     f"recommended {((m.get('recommended') or {}).get('base_color_srgb'))}",
                     f"flags {m.get('flags') or 'none'}",
                     "tiles: photo crop | runtime crop (same pose, photo's backdrop)",
                     "swatches: median photo metal, runtime metal, recommended"]
            sw = [("photo", m["photo"]["rgb"]), ("runtime", m["render"]["rgb"])]
            if m.get("recommended"):
                sw.append(("recommended base", m["recommended"]["base_color_srgb"]))
            tiles = [(label, _crop(im[key + "_img"], _one_side(im[key]["masks"].get("window:" + name), im[key]["masks"].get("region:endpiece")),
                                   3 * im[key + "_ppm"])) for label, key in (("photo", "photo"), ("runtime", "render"))]
            rows.append(_sheet_row(lines, tiles, sw))
        elif m.get("role") == "crystal" and m.get("status") == "measured":
            vid = m["views"][0]
            im = images[vid]
            key = "crystal:" + name
            lines = [f"CRYSTAL {name}  (view {vid}, zone {im['photo']['crystal'][name].get('zone')})",
                     f"distance from the backdrop, mean: photo {m['photo']['mean_delta']:.0f}  runtime {m['render']['mean_delta']:.0f} levels",
                     f"visible share (>{VISIBLE_LEVELS:.0f} levels): photo {m['photo']['visible_share']:.2f}  runtime {m['render']['visible_share']:.2f}",
                     f"spread p90-p10: photo {m['photo']['spread']:.0f}  runtime {m['render']['spread']:.0f}",
                     f"visibility ratio runtime/photo {m['deltas']['visibility_ratio']:.2f}",
                     f"recommended tint_srgb {((m.get('recommended') or {}).get('tint_srgb'))}",
                     f"flags {m.get('flags') or 'none'}",
                     "tiles: photo crop | runtime crop (same pose, photo's backdrop)"]
            rows.append(_sheet_row(lines, [("photo", _crop(im["photo_img"], im["photo"]["masks"].get(key), 4 * im["photo_ppm"])),
                                           ("runtime", _crop(im["render_img"], im["render"]["masks"].get(key), 4 * im["render_ppm"]))], []))
    for rname, a in (record.get("hardware_area") or {}).items():
        if "ratio" not in a:
            continue
        vkey = max(a["views"], key=lambda v: abs(math.log(max(a["views"][v], 1e-3))))
        vid = vkey.split(":")[0]
        im = images[vid]
        key = "region:" + rname
        lines = [f"HARDWARE {rname}  (view {vkey}, exposed metal, matched px/mm)",
                 f"metal share of the region: photo {a['photo_share']:.3f}  runtime {a['render_share']:.3f}",
                 f"ratio runtime/photo {a['ratio']:.2f}  per view {a['views']}",
                 f"flag {a.get('flag') or 'none'}",
                 f"not compared: {', '.join(a.get('not_compared') or {}) or 'none'}",
                 "(metal seen through crystal and hardware the photo fit",
                 "cuts off are not compared)"]
        rows.append(_sheet_row(lines, [("photo (matched px/mm)", _crop(im["area_img"], im["area"]["masks"].get(key), 3 * im["area_ppm"])),
                                       ("runtime", _crop(im["render_img"], im["render"]["masks"].get(key), 3 * im["render_ppm"]))], []))
    if not rows:
        return None
    stack(rows).save(out)
    return Path(out)


# ------------------------------------------------------------------------------------------------ the measurement
def _strip_private(o):
    if isinstance(o, dict):
        return {k: _strip_private(v) for k, v in o.items() if not str(k).startswith("_") and k != "masks"}
    if isinstance(o, list):
        return [_strip_private(v) for v in o]
    return o


def roles_of(materials: dict, used: set[str]) -> dict:
    return {n: ROLE_BY_KIND.get(s.get("kind"), s.get("kind") or "other") for n, s in materials.items() if n in used}


def appearance_metric(obs_dir: Path, glb_path: Path, *, views: dict, frame, V: np.ndarray, F: np.ndarray, part: np.ndarray,
                      objects: dict, materials: dict, evidence: dict, width_mm: float, runner=None,
                      sheet_png: Path | None = None) -> dict:
    """The whole measurement for one observed candidate: plan the matched views, ONE harness run over the front photo's
    backdrop, label and measure photo and render per view, compare per material and region, write the sheet.
    ``views``: the observation's author-visible view records (fitted cameras); ``frame``: the NormFrame they were fitted
    in; ``V``/``F``/``part``/``objects``/``materials``: the exported candidate (observe.mesh_of order); ``runner``:
    ``bsa.archeck.run`` by default. Never raises for a measurement problem: the record says it (``reliable``/``reason``)."""
    t0 = time.time()
    fmat = face_materials(objects)
    used = {str(m) for m in fmat}
    roles = roles_of(materials, used)
    metal_names = sorted(n for n, r in roles.items() if r == "metal")
    crystal_names = sorted(n for n, r in roles.items() if r == "crystal")
    base = {"roles": roles, "seconds": 0.0}
    if not metal_names and not crystal_names:
        return dict(base, status="not_applicable", reliable=False, reason="no metal or crystal material to compare")
    rm = RenderModel(Path(glb_path), materials)
    planned, skipped = plan_views(views, evidence, rm.centre_cm())
    base["skipped_views"] = skipped
    if not planned:
        return dict(base, status="no_matched_view", reliable=False, reason="no author view with a fitted camera the harness can match",
                    seconds=round(time.time() - t0, 1))
    photos = {p["vid"]: np.asarray(Image.open(p["photo"]).convert("RGB")).astype(float) for p in planned}
    backdrops = {p["vid"]: view_backdrop(evidence, p["vid"], photos[p["vid"]]) for p in planned}
    run_bd = backdrops[planned[0]["vid"]]
    out_dir = Path(obs_dir) / "ar_appearance"
    if runner is None:
        from bsa import archeck
        runner = archeck.run
    th = time.time()
    result = runner({"candidate": Path(glb_path)}, out_dir, ar_views=[dict(id=p["harness_id"], **p["harness_view"]) for p in planned],
                    background="solid", background_color=hex_colour(run_bd), width_mm={"candidate": float(width_mm)})
    harness_s = round(time.time() - th, 1)
    base.update(harness_seconds=harness_s, backdrop_rgb=[round(x, 1) for x in run_bd])
    val = (result or {}).get("validation") or {}
    rp = out_dir / "report.json"
    if not val.get("ok") or not rp.is_file():
        return dict(base, status="harness_failed", reliable=False, seconds=round(time.time() - t0, 1),
                    reason="the appearance harness run did not validate: " + "; ".join((val.get("reasons") or [])
                                                                                      + ((val.get("models") or {}).get("candidate") or {}).get("reasons", []))[:300])
    report = json.loads(rp.read_text(encoding="utf-8"))
    rows = {r.get("view"): r for r in ((report.get("cases") or [{}])[0] or {}).get("renders", []) if r.get("mode") == "actual-ar"}
    kind = np.array([(materials.get(m) or {}).get("kind", "") for m in fmat], dtype=object)
    region, side = face_regions(V, F, part)
    coloured = {n for n in metal_names if (b := _base_srgb(materials[n])) is not None
                and rgb_to_hsv(np.asarray(b, float))[1] >= NEUTRAL_METAL_SATURATION}
    images, per_view = {}, []
    for p in planned:
        vid, view, rec = p["vid"], p["view"], views[p["vid"]]
        row = rows.get(p["harness_id"])
        if row is None:
            continue
        img = photos[vid]
        shape = img.shape[:2]
        fid = photo_labels(V, F, frame, rec["camera"], shape)["face_id"]
        fps = {n: footprint(V, F, fmat == n, frame, rec["camera"], shape) for n in metal_names}
        lfp = footprint(V, F, np.isin(part, LENS_PARTS), frame, rec["camera"], shape)
        toward, _ = photo_axes(rec["camera"])
        side_view = view in ("left", "right", "angled")
        ppm = float(rec.get("px_per_mm") or 1.0)
        zone = crystal_zone_for(view)
        lens_region = photo_lens_region(evidence, view, shape)
        p_side = (1 if toward[0] > 0 else -1) if side_view else None
        ph = analyse_image(img, fid, kind=kind, material=fmat, region=region, side=side, part=part, metal_footprints=fps, lens_footprint=lfp,
                           ppm=ppm, tol_px=FIT_TOL_MM * ppm, backdrop=backdrops[vid], regions_side=p_side,
                           source="photo", crystal_zone=zone, lens_region=lens_region, area_metals=coloured)
        rimg = np.asarray(Image.open(out_dir / row["filename"]).convert("RGB")).astype(float)
        cast = rm.cast(row, rimg.shape[:2])
        rppm = cast["px_per_mm"]
        rfps = {n: rm.cast(row, rimg.shape[:2], select=rm.material == n)["face_id"] >= 0 for n in metal_names}
        rlfp = rm.cast(row, rimg.shape[:2], select=np.isin(rm.part, LENS_PARTS))["face_id"] >= 0
        rn = analyse_image(rimg, cast["face_id"], kind=rm.kind, material=rm.material, region=rm.region, side=rm.side, part=rm.part,
                           metal_footprints=rfps, lens_footprint=rlfp, ppm=rppm, tol_px=RENDER_TOL_PX, backdrop=run_bd,
                           regions_side=cast["camera_x_sign"] if side_view else None, source="render", crystal_zone=zone,
                           area_metals=coloured)
        # the hardware areas at MATCHED resolution: the photo box-filtered to the runtime's px/mm, labels at pixel centres
        factor = rppm / ppm if np.isfinite(rppm) and ppm > 0 else 1.0
        aimg, lab = match_resolution(img, {"fid": fid, "lens": lfp, "lens_region": lens_region, **{"fp:" + n: fps[n] for n in metal_names}}, factor)
        appm = ppm * min(factor, 1.0)
        pa = analyse_image(aimg, lab["fid"], kind=kind, material=fmat, region=region, side=side, part=part,
                           metal_footprints={n: lab["fp:" + n] for n in metal_names}, lens_footprint=lab["lens"], ppm=appm,
                           tol_px=FIT_TOL_MM * appm, backdrop=backdrops[vid], regions_side=p_side, source="photo", crystal_zone=zone,
                           lens_region=lab["lens_region"], area_metals=coloured)
        ph["regions"] = pa["regions"]
        ph["regions_px_per_mm"] = round(appm, 4)
        match = float(np.abs(np.asarray(backdrops[vid]) - np.asarray(run_bd)).max()) <= BACKDROP_TOLERANCE
        images[vid] = {"photo": ph, "render": rn, "photo_img": img, "render_img": rimg, "photo_ppm": ppm, "render_ppm": rppm,
                       "area": pa, "area_img": aimg, "area_ppm": appm}
        per_view.append({"vid": vid, "view": view, "harness_view": p["harness_view"], "direction_error_deg": p["direction_error_deg"],
                         "backdrop_photo": [round(x, 1) for x in backdrops[vid]], "backdrop_matches": match, "render_file": str(out_dir / row["filename"]),
                         "photo": ph, "render": rn, "area_px_per_mm": [round(appm, 4), round(rppm, 4)]})
    mats = {}
    for n in metal_names:
        mats[n] = compare_metal(n, materials[n], [(v["vid"], v["photo"]["metal"].get(n, {}), v["render"]["metal"].get(n, {})) for v in per_view])
    for n in crystal_names:
        mats[n] = compare_crystal(n, materials[n], [(v["vid"], v["photo"]["crystal"].get(n, {"status": "absent"}),
                                                     v["render"]["crystal"].get(n, {"status": "absent"}), v["backdrop_matches"], run_bd) for v in per_view])
    for n, r in roles.items():
        if n not in mats:
            mats[n] = {"role": r, "status": "see summary.lens_colour" if r == "lens" else "not_measured", "flags": []}
    areas = compare_regions([(v["vid"], v["view"], v["photo"]["regions"], v["render"]["regions"], tuple(v["area_px_per_mm"])) for v in per_view])
    flags = [f"{f}:{n}" for n, m in mats.items() for f in m.get("flags", [])] + [f"{a['flag']}:{r}" for r, a in areas.items() if a.get("flag")]
    measured = [n for n, m in mats.items() if m.get("status") in ("measured", "neutral")] + [r for r, a in areas.items() if "ratio" in a]
    record = dict(base, status="measured" if measured else "unmeasured", reliable=bool(measured),
                  reason=None if measured else "the matched views show too few metal or crystal pixels in the photo or the render",
                  views=[{k: v for k, v in pv.items()} for pv in per_view], materials=mats, hardware_area=areas, flags=flags)
    if sheet_png is not None and measured:
        try:
            p = material_match_sheet(record, images, Path(sheet_png))
            if p is not None:
                record["sheet"] = str(p)
        except Exception as e:  # noqa: BLE001 - the numbers stand without the sheet
            record["sheet_error"] = f"{type(e).__name__}: {e}"
    record["seconds"] = round(time.time() - t0, 1)
    return _strip_private(record)


SUMMARY_KEYS = ("role", "status", "photo", "render", "deltas", "recommended", "flags")


def summary_of(record: dict) -> dict:
    """``summary.appearance``: the shared interface, compact (no per-view detail, no raw stats beyond the compared
    numbers): {materials: {name: {role, status, photo, render, deltas, recommended, flags}}, hardware_area: {region:
    {photo_share, render_share, ratio, flag} or {flag None, status, reason}}, flags, reliable, reason}. A metal or crystal
    with no target (status neutral or unmeasured) carries no deltas or recommendation, a status flag ('neutral_metal' |
    'unmeasured') and its reason, so the build reply's digest explains itself."""
    mats = {}
    for n, m in (record.get("materials") or {}).items():
        e = {k: m[k] for k in SUMMARY_KEYS if k in m}
        if "deltas" in e:
            e["deltas"] = {k: v for k, v in e["deltas"].items() if k != "per_view"}
        for side in ("photo", "render"):
            if side in e:
                e[side] = {k: v for k, v in e[side].items() if k not in ("pixels",)}
        if m.get("reason"):
            e["reason"] = m["reason"]
        if m.get("role") in ("metal", "crystal") and m.get("status") in ("neutral", "unmeasured"):
            # no target: the build reply (tools.appearance_digest passes role, deltas, recommended and flags) must not show
            # numbers for it. rayban-astra1's owner-accepted nickel read hue -0.0805 and saturation 2.24x, meaningless on a
            # colourless metal; the status flag says why there is nothing to match (never a problem flag: the summary's
            # flag list is the record's, which has none for it)
            e.pop("deltas", None)
            e.pop("recommended", None)
            e["flags"] = list(e.get("flags") or []) + ["neutral_metal" if m["status"] == "neutral" else "unmeasured"]
            e["reason"] = m.get("reason") or m.get("note") or ("a neutral metal: no hue to compare" if m["status"] == "neutral" else "not measured")
        mats[n] = e
    areas = {r: {k: a.get(k) for k in ("photo_share", "render_share", "ratio", "flag", "status", "reason") if k in a}
             for r, a in (record.get("hardware_area") or {}).items()}
    out = {"materials": mats, "hardware_area": areas, "flags": list(record.get("flags") or []), "reliable": bool(record.get("reliable")),
           "reason": record.get("reason"), "status": record.get("status")}
    if record.get("views"):
        out["views"] = [v["vid"] for v in record["views"]]
    return out
