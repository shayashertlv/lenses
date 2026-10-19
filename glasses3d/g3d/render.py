"""Renders, bounds and picking inside the worker's Blender (glasses3d/plan/archive/20261019/WP2_DESIGN.md §6, WP6_FACETS §3.2). Trusted and read-only:
the host sends camera specs (g3d_host/blender_camera.to_blender) and never code.

``render(params, root)`` builds a temporary ``g3d_render`` scene that links a COPY (``obj.copy()``: shared data,
material slots linked to the copy, no parent, no constraints or animation, the original's evaluated world matrix) of
every object the user's render view layer renders (export_glb.render_status) except lights, cameras and light probes,
with its own camera, world and lights, film transparent, RGBA PNG. The originals are never linked or touched.

Rigs (``params["rig"]``, recorded with their frozen values):

- ``photo_v1`` (default): uniform grey world, a key and a fill area light (RIG_SPEC);
- ``studio_dome_v1`` (DOME_SPEC): an elevation-only world gradient along the scene's up axis (+Y, the WP3b
  convention: the glasses' up): a floor, a dark band just below the horizon and a steep ramp through it to bright
  above; one large soft overhead-front area key. "Front" is the camera's side, as in a product studio where the
  lights stay with the camera while the product turns: per view the key sits at key_elevation_deg above the horizon
  on the camera's azimuth; no rim or side keys; EEVEE raytracing off. A uniform world would give every glossy facet
  the same reflection.
- ``photo_env`` (ENV_SPEC): a world of smooth lobes fitted to the photos (g3d_host/lighting.py), camera-relative as on
  a turntable: elevation e from the scene's up axis, azimuth a from the camera's horizontal direction (per view, the
  horizontal part of the camera's back axis minus its up axis, so a top view keeps the photographer's side). Radiance
  = sum over the sectors front / side / back (S = max(cos a, 0), 1 - |cos a|, max(-cos a, 0); left = right) of
  S(a) x R_sector(e), R_sector = sum_k w[sector, k] T_k(e) + w_floor F(e): T_k the linear tents on
  elevation_knots_deg, F = 1 below floor_deg[0] falling to 0 at floor_deg[1] (the floor's horizon). Weights come
  from ``params["env"]`` = {"weights": [...]} (lobe order ``env_lobes()``), per view overridable (one lobe alone
  renders a basis image). No lights; EEVEE raytracing off, world sun extraction off (the render stays linear in the
  weights).
- ``catalog_band_v1`` (CATALOG_SPEC): a catalogue horizon-band prior declared before any render with it (WP6_FACETS
  §3.2 amended 2026-10-11): world radiance = world_strength x ramp(elevation e above the scene's up axis +Y), linear
  in degrees between the declared knots ``ramp_deg``, the same at every azimuth (camera-relative like photo_env, with
  nothing to turn). No lamps; EEVEE raytracing off, world sun extraction off.

Passes:

- ``native``: the scene's own materials under the rig (Standard view transform, 8-bit);
- ``clay``: a view-layer material override (grey 0.6, roughness 0.55): opaque everywhere, so its alpha is the
  geometric silhouette (glass lenses included) — the host derives the ``silhouette`` pass from it;
- ``normal``: an override emitting the world normal x 0.5 + 0.5 under the Raw view transform (PNG = the numbers);
- ``native_linear``: the scene's materials, Raw view transform, 16-bit PNG, dither 0 (linear radiance, clipped at 1);
  with ``params["linear_format"] == "float"`` both linear passes are written as OpenEXR float and converted in the
  worker to ``<view>_<pass>.npz`` {rgb (straight alpha), alpha} float16, unclipped;
- ``gloss_black``: as native_linear with the acetate body materials (those of the front body and the temple bodies,
  ``acetate_bodies``) replaced on the copies by black glossy acetate (GLOSS_BLACK); lenses (unless ``lens_material``)
  and metals keep theirs.

Per view (all optional): ``pose`` = {"+1": {"theta_deg", "axis_xz_mm"}, "-1": ...} opens each side's temple-role
copies (selected by the sign of their box centre x, asset mm) about the vertical hinge axis (asset mm, x signed):
matrix_world = T(axis) R_y T(-axis) M with x' = c dx - s dz, z' = s dx + c dz at angle side x theta, so + is tip
outward on both sides (= g3d/temple/build.py to_world and g3d_host/pose.rot_about_hinge). ``mesh_overrides`` =
{object name: mesh datablock name} swaps a copy's data (non-mutating variants); materials stay the original's.
``lens_material`` (per view, else ``params["lens_material"]``; default None = the scene's own) = a LENS_MATERIALS
name: every material slot of the lens-role copies (partRole "lens" on the object or its data) gets that fixed
material in the native, native_linear and gloss_black passes, so a sheet can show one lens under several rigs
(``dark_tint_v1``: unlit, emission only, no reflection or transmission, so it reads the same under every rig). A lens
copy without a material slot is refused; each file records the material and the lens objects it covered.

Engine: EEVEE (``BLENDER_EEVEE``; ``params["samples"]``, else the rig's frozen ``samples``, else 16; the samples used
are recorded) on the GPU; Cycles CPU (same samples) when the GPU is a software renderer, when EEVEE raises / returns
anything but FINISHED / writes no file (then for the rest of the call), or when asked. The engine is recorded per
image. Everything temporary (scene, copies, camera, lights, world, override
materials) is removed in ``finally`` and the scene fingerprint is compared before and after (``unchanged``); the host
restores the head checkpoint if it ever differs.

``bounds()`` = the render-enabled objects' world boxes from their evaluated geometry and the origin marker.
``pick(params)`` = ``Scene.ray_cast`` on the evaluated depsgraph, skipping hits on objects that do not render.
Output paths are confined to ``<job root>/work`` (junctions resolved first: checkpoint.confined).
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import re
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import checkpoint
from .export_glb import ORIGIN_KEY, SURFACE_TYPES, render_status, render_view_layer

RIG = "photo_v1"
RIG_SPEC = {"world_color": 0.8, "world_strength": 0.5, "key_irradiance": 2.0, "fill_irradiance": 0.8,
            "key_dir": (-0.45, 0.65, 0.95), "fill_dir": (0.85, 0.25, 0.55), "distance_radii": 4.0, "size_radii": 2.0}
# WP6_FACETS §3.2, frozen 2026-10-10 by the T2 rig check (its script was deleted 2026-10-13, see git tag
# review-20261013-pre-cleanup; every value tried is in data/wp6v2/t2/trials.jsonl). ramp = (elevation deg, world
# radiance) knots along the up axis, linear between knots.
# The design's ramp (floor 0.45, flat dark band 0.12 over -5..+10 deg, 1.0 at 60 deg) failed the gloss_black target
# (0.0 L8): the side cameras look down ~14 deg, so the facets around a +1 mm node (tilted up a few degrees) mirror
# the sky just above the horizon, inside that flat band. The frozen ramp keeps floor 0.45 and a dark point but moves
# it to -10 deg and rises steeply through the horizon to 1.0 at +12 deg.
DOME_RIG = "studio_dome_v1"
DOME_SPEC = {"up_axis": "Y", "world_strength": 1.0,
             "ramp": ((-90.0, 0.45), (-20.0, 0.45), (-10.0, 0.06), (12.0, 1.0), (90.0, 1.0)),
             "key_irradiance": 1.0, "key_elevation_deg": 55.0, "key_azimuth": "camera", "key_size_radii": 2.0,
             "distance_radii": 4.0, "raytracing": False, "samples": 16}
# The photo-estimated environment (module doc; g3d_host/lighting.py LOBES is the same layout and fits the weights).
ENV_RIG = "photo_env"
ENV_SPEC = {"up_axis": "Y", "azimuth": "camera", "elevation_knots_deg": (-90.0, -35.0, -12.0, 12.0, 35.0, 90.0),
            "sectors": ("front", "side", "back"), "floor_deg": (-1.0, 0.0), "raytracing": False, "samples": 16,
            "sun_threshold": 0.0}
ENV_MAX_WEIGHT = 1e3
# Catalogue CGI is lit by bright strip lights / a white sweep at the camera's horizon, over a dark ground and under a
# dimmer ceiling; in the black photo the logo panel (mirror direction ~+2.8 deg) is ~3x brighter than the facet that
# mirrors ~-2 deg. The knots say exactly that and were declared before the first render with this rig (2026-10-11);
# they are never tuned. world_strength is the one scale, chosen once so the native gain on the front acetate is ~1
# (the gain is applied anyway): at strength 1.0 the Prada original's 04 front gain was 2.90 (luminance of the
# per-channel gain; data/wp6v2/light_choice/calibration_strength1.json), and world light scales linearly.
CATALOG_RIG = "catalog_band_v1"
CATALOG_SPEC = {"up_axis": "Y", "azimuth": "uniform",
                "ramp_deg": ((-90.0, 0.08), (-2.0, 0.08), (0.0, 1.0), (12.0, 1.0), (25.0, 0.35), (55.0, 0.35),
                             (90.0, 0.6)),
                "world_strength": 2.9, "lamps": 0, "raytracing": False, "samples": 16, "sun_threshold": 0.0}
RIGS = {RIG: RIG_SPEC, DOME_RIG: DOME_SPEC, ENV_RIG: ENV_SPEC, CATALOG_RIG: CATALOG_SPEC}
LINEAR_FORMATS = ("png16", "float")
GLOSS_BLACK = {"base": 0.01, "roughness": 0.12, "coat": 0.6, "coat_roughness": 0.05, "ior": 1.49}
# Sheet-only lens materials (module doc). dark_tint_v1 is a dark brown-grey emission (linear RGB; about sRGB 19/17/14
# at gain 1): no BSDF, so no rig's reflections or transmission reach it and it is the same radiance under every rig.
LENS_MATERIALS = {"dark_tint_v1": {"shader": "emission", "emission_rgb": (0.006, 0.005, 0.004), "strength": 1.0}}
PASSES =("native", "clay", "normal", "native_linear", "gloss_black")
LINEAR_PASSES = ("native_linear", "gloss_black")
POSE_SIDES = {"+1": 1, "1": 1, "-1": -1}
TEMPLE_MIN_REACH_MM = 60.0           # a temple body reaches this far back (= temple_bpy.temple_candidates)
TEMPLE_MIN_OFFSET_MM = 20.0          # and is centred this far from x = 0 (one rule with g3d_host/pose.temple_body)
ENGINES = {"eevee": "BLENDER_EEVEE", "cycles": "CYCLES"}
SAMPLES_DEFAULT = 16
CLAY_COLOR, CLAY_ROUGHNESS = (0.6, 0.6, 0.6, 1.0), 0.55
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swiftshader", "microsoft basic render", "gdi generic")
NOT_RENDERED_TYPES = ("LIGHT", "CAMERA", "LIGHT_PROBE", "SPEAKER")
NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
PICK_MAX_RAYS = 64
PICK_SKIPS = 16


class RenderRefused(ValueError):
    """The render request is malformed or reaches outside the job root."""


def safe_name(name: str) -> str:
    out = NAME_RE.sub("_", str(name)).strip("._")
    if not out:
        raise RenderRefused(f"view name {name!r} has no usable characters")
    return out[:80]


def sha256_file(path) -> str:
    return checkpoint.sha256_file(path)


def work_dir(path, root) -> Path:
    """`path` confined to `<root>/work` (junctions and symlinks resolved first)."""
    base = Path(os.path.realpath(str(Path(root) / "work")))
    real = checkpoint.confined(path, root)
    try:
        real.relative_to(base)
    except ValueError:
        raise PermissionError(f"output path outside the job's work folder: {real}") from None
    return real


# ------------------------------------------------------------------------------------------------ camera
def apply_camera(cam_obj, spec: dict, scene) -> None:
    """Set a Blender camera object and the scene resolution from a ``blender_camera.to_blender`` spec."""
    cd = cam_obj.data
    cd.type = spec["type"]
    cd.sensor_fit = "AUTO"
    cd.sensor_width = float(spec["sensor_mm"])
    if spec["type"] == "PERSP":
        cd.lens_unit = "MILLIMETERS"
        cd.lens = float(spec["lens_mm"])
    else:
        cd.ortho_scale = float(spec["ortho_scale"])
    cd.shift_x, cd.shift_y = float(spec["shift_x"]), float(spec["shift_y"])
    cd.clip_start, cd.clip_end = float(spec["clip_start"]), float(spec["clip_end"])
    cam_obj.matrix_world = Matrix([list(r) for r in spec["matrix_world"]])
    W, H = (int(v) for v in spec["resolution"])
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = W, H, 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    r.use_border = False
    scene.camera = cam_obj


# ------------------------------------------------------------------------------------------------ what renders
def user_scene():
    return bpy.context.scene


def rendered_objects(scene=None) -> tuple[list, dict]:
    """(objects the render view layer renders, minus lights/cameras/probes; {name: reason} of those left out)."""
    scene = scene or user_scene()
    layer, _notes = render_view_layer(scene, bpy.context.view_layer)
    status = render_status(layer)
    keep, skipped = [], {}
    for obj in scene.objects:
        why = status.get(obj, "not in the view layer")
        if why is None and obj.type in NOT_RENDERED_TYPES:
            why = f"{obj.type.lower()} (the render uses its own rig)"
        if why is None:
            keep.append(obj)
        else:
            skipped[obj.name] = why
    return keep, skipped


def origin_marker(scene=None):
    scene = scene or user_scene()
    try:
        o = [float(v) for v in scene[ORIGIN_KEY]]
    except (KeyError, TypeError, ValueError):
        return None
    return o if len(o) == 3 and all(math.isfinite(v) for v in o) else None


def bounds(params: dict | None = None) -> dict:
    """World boxes (Blender mm) of every render-enabled surface object from its evaluated geometry."""
    scene = user_scene()
    objs, skipped = rendered_objects(scene)
    dg = bpy.context.evaluated_depsgraph_get()
    out, lo, hi = {}, np.full(3, np.inf), np.full(3, -np.inf)
    for obj in objs:
        if obj.type not in SURFACE_TYPES:
            continue
        ev = obj.evaluated_get(dg)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            continue
        try:
            n = len(me.vertices)
            if not n or not len(me.polygons):
                continue
            co = np.zeros(n * 3, np.float64)
            me.vertices.foreach_get("co", co)
        finally:
            ev.to_mesh_clear()
        m = np.array(ev.matrix_world, float)
        if not np.isfinite(co).all() or not np.isfinite(m).all():
            cause = "evaluated vertex coordinates" if not np.isfinite(co).all() else "world transform"
            return {"status": "refused", "code": "E_NONFINITE", "object": obj.name,
                    "message": f"bounds: object {obj.name!r} has non-finite {cause}; repair it before retrying."}
        w = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        if not np.isfinite(w).all():
            return {"status": "refused", "code": "E_NONFINITE", "object": obj.name,
                    "message": f"bounds: object {obj.name!r} has non-finite world vertex coordinates (transform overflow)."}
        a, b = w.min(0), w.max(0)
        role = obj.get("partRole") if isinstance(obj.get("partRole"), str) else None
        if role is None and obj.data is not None and isinstance(obj.data.get("partRole"), str):
            role = obj.data.get("partRole")
        out[obj.name] = {"type": obj.type, "partRole": role, "lo": a.tolist(), "hi": b.tolist(), "vertices": n}
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    have = bool(out)
    return {"origin_mm": origin_marker(scene), "objects": out, "lo": lo.tolist() if have else None,
            "hi": hi.tolist() if have else None, "skipped": skipped,
            "units": [scene.unit_settings.system, scene.unit_settings.scale_length]}


# ------------------------------------------------------------------------------------------------ GPU
def gpu_info() -> dict:
    try:
        import gpu
        return {"vendor": gpu.platform.vendor_get(), "renderer": gpu.platform.renderer_get(),
                "backend": gpu.platform.backend_type_get()}
    except Exception as exc:  # noqa: BLE001 - no GPU context yet, or none at all
        return {"error": f"{type(exc).__name__}: {exc}"}


def software_gpu(info: dict) -> bool:
    name = str(info.get("renderer", "")).lower()
    return any(s in name for s in SOFTWARE_RENDERERS)


# ------------------------------------------------------------------------------------------------ pose
def pose_matrix(side: int, theta_deg: float, axis_xz) -> np.ndarray:
    """4x4 world matrix opening a side's temple by theta (deg, + = tip outward) about the vertical axis through
    (axis_x, axis_z) (scene mm, x signed): T(axis) R_y T(-axis) with x' = c dx - s dz, z' = s dx + c dz at side x theta
    (g3d/temple/build.py to_world for side +1; its mirror for side -1)."""
    t = math.radians(float(theta_deg)) * int(side)
    c, s = math.cos(t), math.sin(t)
    ax, az = float(axis_xz[0]), float(axis_xz[1])
    R = np.array([[c, 0.0, -s, 0.0], [0.0, 1.0, 0.0, 0.0], [s, 0.0, c, 0.0], [0.0, 0.0, 0.0, 1.0]])
    T, Ti = np.eye(4), np.eye(4)
    T[0, 3], T[2, 3] = ax, az
    Ti[0, 3], Ti[2, 3] = -ax, -az
    return T @ R @ Ti


def parse_pose(pose) -> dict:
    """{side: (theta_deg, (axis_x, axis_z))} of a temporary view, or {} for None.

    Finite angles retain their raw value, including full turns and shifted fitter deltas, as on the host and AR
    paths. This is a rigid rendering transform, not a physical hinge limit. Malformed/nonfinite poses refuse.
    """
    if pose is None:
        return {}
    if not isinstance(pose, dict):
        raise RenderRefused('pose must be {"+1": {"theta_deg", "axis_xz_mm"}, "-1": ...}')
    out = {}
    for key, rec in pose.items():
        side = POSE_SIDES.get(str(key))
        if side is None or side in out:
            raise RenderRefused(f"pose side {key!r} must be '+1' or '-1' (once each)")
        try:
            th = float(rec["theta_deg"])
            ax, az = (float(v) for v in rec["axis_xz_mm"])
        except (KeyError, TypeError, ValueError):
            raise RenderRefused(f"pose {key}: needs theta_deg and axis_xz_mm [x, z]") from None
        if not all(math.isfinite(v) for v in (th, ax, az)):
            raise RenderRefused(f"pose {key}: angle and axis values must be finite")
        if side * ax <= 0:
            raise RenderRefused(f"pose {key}: axis x {ax} lies on the other side")
        out[side] = (th, (ax, az))
    return out


def _role(obj):
    for holder in (obj, obj.data):
        if holder is not None and isinstance(holder.get("partRole"), str):
            return holder.get("partRole")
    return None


def _world_box(obj) -> tuple[np.ndarray, np.ndarray]:
    m = np.array(obj.matrix_world, float)
    c = np.array([tuple(v) for v in obj.bound_box], float) @ m[:3, :3].T + m[:3, 3]
    return c.min(0), c.max(0)


def temple_sides(objects, origin) -> dict:
    """{object name: side} of the temple-role objects (temple bodies, decals, temple knuckles, leaves, pins) by the
    sign of their box centre x (asset mm); objects centred on x = 0 get no side."""
    out = {}
    for obj in objects:
        if _role(obj) != "temple":
            continue
        lo, hi = _world_box(obj)
        cx = float((lo[0] + hi[0]) / 2) - float(origin[0])
        if abs(cx) > 1e-6:
            out[obj.name] = 1 if cx > 0 else -1
    return out


def acetate_bodies(objects, origin) -> tuple[list, list]:
    """(names, materials) of the acetate bodies: the frame-role mesh widest in x (the front) and, per side, the
    temple-role mesh reaching furthest back (>= 60 mm, centred >= 20 mm from x = 0) — geometry and roles only, never
    names. The same rule as g3d_host/pose.front_body / temple_body (the host cannot share this bpy module)."""
    front, temples = None, {1: None, -1: None}
    for obj in objects:
        if obj.type not in SURFACE_TYPES:
            continue
        lo, hi = _world_box(obj)
        role = _role(obj)
        if role == "frame":
            w = float(hi[0] - lo[0])
            if front is None or w > front[0]:
                front = (w, obj)
        elif role == "temple":
            reach, cx = float(hi[2] - lo[2]), float((lo[0] + hi[0]) / 2) - float(origin[0])
            if reach >= TEMPLE_MIN_REACH_MM and abs(cx) >= TEMPLE_MIN_OFFSET_MM:
                s = 1 if cx > 0 else -1
                if temples[s] is None or reach > temples[s][0]:
                    temples[s] = (reach, obj)
    bodies = [b[1] for b in [front, temples[1], temples[-1]] if b is not None]
    mats = []
    for obj in bodies:
        for slot in obj.material_slots:
            if slot.material is not None and slot.material not in mats:
                mats.append(slot.material)
    return [o.name for o in bodies], mats


# ------------------------------------------------------------------------------------------------ photo_env
def env_lobes(spec: dict = ENV_SPEC) -> list[str]:
    """The lobe names in weight order: sector x elevation knot, then the floor (= g3d_host/lighting.py LOBES)."""
    return [f"{s}{k:+g}" for s in spec["sectors"] for k in spec["elevation_knots_deg"]] + ["floor"]


def env_profiles(weights, spec: dict = ENV_SPEC) -> tuple[list, dict]:
    """(ramp positions in elevation deg, {sector: R_sector at those positions}) of the lobe weights: R is piecewise
    linear between the elevation knots and the floor edges, so its values there define it exactly."""
    knots = [float(k) for k in spec["elevation_knots_deg"]]
    f0, f1 = (float(v) for v in spec["floor_deg"])
    pos = sorted(set(knots) | {f0, f1})
    e = np.asarray(pos)
    T = np.zeros((len(e), len(knots)))
    for k, ek in enumerate(knots):
        if k > 0:
            lo = knots[k - 1]
            sel = (e >= lo) & (e <= ek)
            T[sel, k] = (e[sel] - lo) / (ek - lo)
        if k < len(knots) - 1:
            hi = knots[k + 1]
            sel = (e >= ek) & (e <= hi)
            T[sel, k] = (hi - e[sel]) / (hi - ek)
    F = np.clip((f1 - e) / (f1 - f0), 0.0, 1.0)
    w = np.asarray(weights, float)
    K = len(knots)
    prof = {sec: (T @ w[i * K:(i + 1) * K] + F * w[-1]).tolist() for i, sec in enumerate(spec["sectors"])}
    return pos, prof


def env_azimuth(cam_matrix, up=(0.0, 1.0, 0.0)) -> tuple:
    """The camera's horizontal direction (unit, scene axes): the horizontal part of its back axis minus its up axis
    (= its azimuth for any pitch below 90 deg; the image-bottom side when it looks straight down); +Z without a
    camera."""
    upv = Vector(up)
    if cam_matrix is None:
        return (0.0, 0.0, 1.0)
    back = Vector([cam_matrix[i][2] for i in range(3)])
    cup = Vector([cam_matrix[i][1] for i in range(3)])
    h = back - cup
    h = h - upv * h.dot(upv)
    return tuple(h.normalized()) if h.length > 1e-9 else (0.0, 0.0, 1.0)


def parse_env(env, n: int):
    """The lobe weights of an ``env`` {"weights": [n non-negative numbers]} (None -> None). Raises RenderRefused."""
    if env is None:
        return None
    try:
        w = [float(x) for x in env["weights"]]
    except (KeyError, TypeError, ValueError):
        raise RenderRefused('env must be {"weights": [numbers]}') from None
    if len(w) != n or not all(math.isfinite(x) and 0.0 <= x <= ENV_MAX_WEIGHT for x in w):
        raise RenderRefused(f"env weights must be {n} finite numbers in [0, {ENV_MAX_WEIGHT:g}] ({env_lobes()})")
    return w


# ------------------------------------------------------------------------------------------------ temporary scene
class TempRender:
    """The temporary scene with its object copies, camera, rig and override materials; everything is removed on
    exit."""

    def __init__(self, scene, objects, light_frame: dict, samples: int, rig: str = RIG, spec: dict | None = None):
        self.user, self.objects, self.light_frame, self.samples = scene, objects, light_frame, int(samples)
        self.rig = rig
        self.spec = dict(RIGS[rig] if spec is None else spec)
        self.made: list = []                 # (collection, datablock) in creation order
        self.copies: dict = {}               # original name -> copy
        self.base: dict = {}                 # original name -> (data, matrix_world, [slot materials])
        self.normal_modifiers: dict = {}     # original flags restored between overridden and ordinary views
        self.origin = origin_marker(scene) or [0.0, 0.0, 0.0]

    def _new(self, coll, *args):
        idb = coll.new(*args)
        self.made.append((coll, idb))
        return idb

    def __enter__(self) -> "TempRender":
        try:
            return self._build()
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _build(self) -> "TempRender":
        sc = self._new(bpy.data.scenes, checkpoint.RENDER_SCENE)
        if sc.name != checkpoint.RENDER_SCENE:
            raise RuntimeError(f"a scene named {checkpoint.RENDER_SCENE!r} already exists")
        self.scene = sc
        us, src = sc.unit_settings, self.user.unit_settings
        # Blender's unit-system update resets length/mass on the context scene, even when setting another scene.
        # Scope that callback to the temporary scene; the user's authored unit choices must remain untouched.
        with bpy.context.temp_override(scene=sc):
            us.system, us.scale_length, us.length_unit = src.system, src.scale_length, src.length_unit
        dg = bpy.context.evaluated_depsgraph_get()
        for obj in self.objects:
            mw = obj.evaluated_get(dg).matrix_world.copy()
            mats = [slot.material for slot in obj.material_slots]
            c = obj.copy()
            self.made.append((bpy.data.objects, c))
            c.animation_data_clear()
            c.constraints.clear()
            c.parent = None
            c.matrix_world = mw
            sc.collection.objects.link(c)
            self.copies[obj.name] = c
            self.base[obj.name] = (obj.data, mw, mats)
            self.normal_modifiers[obj.name] = [(m, m.show_viewport, m.show_render) for m in c.modifiers
                                               if m.type == "WEIGHTED_NORMAL"]
            self._set_materials(c, mats)
        self.sides = temple_sides(self.objects, self.origin)
        self.temples = {o.name for o in self.objects if _role(o) == "temple"}
        self.bodies, self.acetate = acetate_bodies(self.objects, self.origin)
        self.lenses = sorted(o.name for o in self.objects if _role(o) == "lens")
        self.lens_mats: dict = {}
        cd = self._new(bpy.data.cameras, "g3d_render_cam")
        self.cam = self._new(bpy.data.objects, "g3d_render_cam", cd)
        sc.collection.objects.link(self.cam)
        sc.camera = self.cam
        self._rig()
        self.clay = self._new(bpy.data.materials, "g3d_render_clay")
        self.clay.use_nodes = True
        bsdf = self.clay.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = CLAY_COLOR
        bsdf.inputs["Roughness"].default_value = CLAY_ROUGHNESS
        self.clay.diffuse_color = CLAY_COLOR
        self.normal = self._new(bpy.data.materials, "g3d_render_normal")
        self.normal.use_nodes = True
        nt = self.normal.node_tree
        nt.nodes.clear()
        geo, mad = nt.nodes.new("ShaderNodeNewGeometry"), nt.nodes.new("ShaderNodeVectorMath")
        mad.operation = "MULTIPLY_ADD"
        mad.inputs[1].default_value = (0.5, 0.5, 0.5)
        mad.inputs[2].default_value = (0.5, 0.5, 0.5)
        em, out = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeOutputMaterial")
        em.inputs["Strength"].default_value = 1.0
        nt.links.new(geo.outputs["Normal"], mad.inputs[0])
        nt.links.new(mad.outputs[0], em.inputs["Color"])
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        self.gloss = self._new(bpy.data.materials, "g3d_render_gloss_black")
        self.gloss.use_nodes = True
        b = self.gloss.node_tree.nodes.get("Principled BSDF")
        g = GLOSS_BLACK
        b.inputs["Base Color"].default_value = (g["base"], g["base"], g["base"], 1.0)
        b.inputs["Roughness"].default_value = g["roughness"]
        b.inputs["Coat Weight"].default_value = g["coat"]
        b.inputs["Coat Roughness"].default_value = g["coat_roughness"]
        b.inputs["IOR"].default_value = g["ior"]
        self.gloss.diffuse_color = (g["base"], g["base"], g["base"], 1.0)
        r = sc.render
        r.film_transparent = True
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        r.image_settings.color_depth = "8"
        r.image_settings.compression = 15
        r.use_compositing = False
        r.use_sequencer = False
        sc.display_settings.display_device = "sRGB"
        self.set_view("Standard")
        self.dither0 = r.dither_intensity
        sc.eevee.taa_render_samples = self.samples
        if self.rig in (DOME_RIG, ENV_RIG, CATALOG_RIG):
            sc.eevee.use_raytracing = bool(self.spec["raytracing"])
        sc.cycles.device = "CPU"
        sc.cycles.samples = self.samples
        sc.cycles.use_denoising = False
        sc.cycles.use_adaptive_sampling = False
        return self

    def _rig(self) -> None:
        if self.rig == DOME_RIG:
            return self._dome()
        if self.rig == ENV_RIG:
            return self._env()
        if self.rig == CATALOG_RIG:
            return self._catalog()
        sc, spec = self.scene, self.spec
        world = self._new(bpy.data.worlds, "g3d_render_world")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        c = spec["world_color"]
        bg.inputs["Color"].default_value = (c, c, c, 1.0)
        bg.inputs["Strength"].default_value = spec["world_strength"]
        sc.world = world
        center = Vector(self.light_frame.get("center", (0.0, 0.0, 0.0)))
        radius = max(float(self.light_frame.get("radius", 100.0)), 1e-3)
        dist = spec["distance_radii"] * radius
        for name, d, irr in (("key", spec["key_dir"], spec["key_irradiance"]),
                             ("fill", spec["fill_dir"], spec["fill_irradiance"])):
            ld = self._new(bpy.data.lights, f"g3d_render_{name}", "AREA")
            ld.size = spec["size_radii"] * radius
            ld.energy = float(irr * math.pi * dist * dist)       # irradiance at the centre ~ P / (pi d^2)
            lo = self._new(bpy.data.objects, f"g3d_render_{name}", ld)
            dv = Vector(d).normalized()
            lo.location = center + dv * dist
            lo.rotation_euler = (-dv).to_track_quat("-Z", "Y").to_euler()
            sc.collection.objects.link(lo)

    def _dome(self) -> None:
        """studio_dome_v1: world radiance = ramp(elevation of the view direction above the up axis) x strength (the
        world's Generated coordinate is the unit direction; ColorRamp position = (sin(elevation) + 1) / 2), plus one
        overhead-front area key."""
        sc, spec = self.scene, self.spec
        world = self._new(bpy.data.worlds, "g3d_render_world")
        world.use_nodes = True
        nt = world.node_tree
        bg = nt.nodes.get("Background")
        tc, sep = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeSeparateXYZ")
        mr, cr = nt.nodes.new("ShaderNodeMapRange"), nt.nodes.new("ShaderNodeValToRGB")
        mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = -1.0, 1.0
        mr.inputs["To Min"].default_value, mr.inputs["To Max"].default_value = 0.0, 1.0
        mr.clamp = True
        nt.links.new(tc.outputs["Generated"], sep.inputs[0])
        nt.links.new(sep.outputs[spec["up_axis"]], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], cr.inputs["Fac"])
        nt.links.new(cr.outputs["Color"], bg.inputs["Color"])
        ramp = cr.color_ramp
        ramp.interpolation = "LINEAR"
        knots = [((math.sin(math.radians(el)) + 1.0) / 2.0, float(v)) for el, v in spec["ramp"]]
        ends = sorted(ramp.elements, key=lambda e: e.position)     # the default pair at 0 and 1
        for e, (pos, v) in ((ends[0], knots[0]), (ends[-1], knots[-1])):
            e.position, e.color = pos, (v, v, v, 1.0)
        for pos, v in knots[1:-1]:
            e = ramp.elements.new(pos)
            e.color = (v, v, v, 1.0)
        got = sorted((e.position, e.color[0]) for e in ramp.elements)        # float32 in Blender
        want = sorted(knots)
        if len(got) != len(want) or any(abs(a - c) > 1e-5 or abs(b - d) > 1e-5 for (a, b), (c, d) in zip(got, want)):
            raise RuntimeError(f"the dome ramp did not build as specified: {got}")
        bg.inputs["Strength"].default_value = spec["world_strength"]
        sc.world = world
        center = Vector(self.light_frame.get("center", (0.0, 0.0, 0.0)))
        radius = max(float(self.light_frame.get("radius", 100.0)), 1e-3)
        dist = spec["distance_radii"] * radius
        ld = self._new(bpy.data.lights, "g3d_render_key", "AREA")
        ld.size = spec["key_size_radii"] * radius
        ld.energy = float(spec["key_irradiance"] * math.pi * dist * dist)
        self.key = self._new(bpy.data.objects, "g3d_render_key", ld)
        self.key_center, self.key_dist = center, dist
        sc.collection.objects.link(self.key)
        self.aim_key(None)

    def _math(self, nt, op: str, *inputs):
        m = nt.nodes.new("ShaderNodeMath")
        m.operation = op
        for i, x in enumerate(inputs):
            if isinstance(x, (int, float)):
                m.inputs[i].default_value = float(x)
            else:
                nt.links.new(x, m.inputs[i])
        return m.outputs[0]

    def _env(self) -> None:
        """photo_env: the lobe world of the module doc; set_env fills the ramps and the azimuth per view."""
        sc, spec = self.scene, self.spec
        world = self._new(bpy.data.worlds, "g3d_render_world")
        world.use_nodes = True
        if hasattr(world, "sun_threshold"):
            world.sun_threshold = float(spec["sun_threshold"])
        nt = world.node_tree
        N = nt.nodes.new
        bg = nt.nodes.get("Background")
        bg.inputs["Strength"].default_value = 1.0
        tc, nrm, sep = N("ShaderNodeTexCoord"), N("ShaderNodeVectorMath"), N("ShaderNodeSeparateXYZ")
        nrm.operation = "NORMALIZE"
        nt.links.new(tc.outputs["Generated"], nrm.inputs[0])
        nt.links.new(nrm.outputs["Vector"], sep.inputs[0])
        cl = N("ShaderNodeClamp")
        cl.inputs["Min"].default_value, cl.inputs["Max"].default_value = -1.0, 1.0
        nt.links.new(sep.outputs[spec["up_axis"]], cl.inputs["Value"])
        pos = self._math(nt, "MULTIPLY_ADD", self._math(nt, "ARCSINE", cl.outputs["Result"]), 1.0 / math.pi, 0.5)
        dot, flat, ln = N("ShaderNodeVectorMath"), N("ShaderNodeVectorMath"), N("ShaderNodeVectorMath")
        dot.operation, flat.operation, ln.operation = "DOT_PRODUCT", "MULTIPLY", "LENGTH"
        nt.links.new(nrm.outputs["Vector"], dot.inputs[0])
        nt.links.new(nrm.outputs["Vector"], flat.inputs[0])
        flat.inputs[1].default_value = tuple(0.0 if a == spec["up_axis"] else 1.0 for a in "XYZ")
        nt.links.new(flat.outputs["Vector"], ln.inputs[0])
        c = self._math(nt, "DIVIDE", dot.outputs["Value"], self._math(nt, "MAXIMUM", ln.outputs["Value"], 1e-6))
        cc = N("ShaderNodeClamp")
        cc.inputs["Min"].default_value, cc.inputs["Max"].default_value = -1.0, 1.0
        nt.links.new(c, cc.inputs["Value"])
        c = cc.outputs["Result"]
        weight = {"front": self._math(nt, "MAXIMUM", c, 0.0),
                  "side": self._math(nt, "SUBTRACT", 1.0, self._math(nt, "ABSOLUTE", c)),
                  "back": self._math(nt, "MAXIMUM", self._math(nt, "MULTIPLY", c, -1.0), 0.0)}
        positions, _prof = env_profiles([0.0] * len(env_lobes(spec)), spec)
        ps = [(e + 90.0) / 180.0 for e in positions]
        self.env_ramps, acc = {}, None
        for sector in spec["sectors"]:
            cr = N("ShaderNodeValToRGB")
            ramp = cr.color_ramp
            ramp.interpolation = "LINEAR"
            ends = sorted(ramp.elements, key=lambda el: el.position)
            ends[0].position, ends[-1].position = ps[0], ps[-1]
            for q in ps[1:-1]:
                ramp.elements.new(q)
            got = sorted(el.position for el in ramp.elements)
            if len(got) != len(ps) or max(abs(a - b) for a, b in zip(got, ps)) > 1e-5:
                raise RuntimeError(f"the env ramp did not build as specified: {got}")
            nt.links.new(pos, cr.inputs["Fac"])
            scale = N("ShaderNodeVectorMath")
            scale.operation = "SCALE"
            nt.links.new(cr.outputs["Color"], scale.inputs[0])
            nt.links.new(weight[sector], scale.inputs["Scale"])
            if acc is None:
                acc = scale.outputs["Vector"]
            else:
                add = N("ShaderNodeVectorMath")
                add.operation = "ADD"
                nt.links.new(acc, add.inputs[0])
                nt.links.new(scale.outputs["Vector"], add.inputs[1])
                acc = add.outputs["Vector"]
            self.env_ramps[sector] = ramp
        nt.links.new(acc, bg.inputs["Color"])
        self.env_dot = dot
        sc.world = world

    def _catalog(self) -> None:
        """catalog_band_v1: world radiance = ramp_deg(elevation of the view direction above the up axis) x
        world_strength (ColorRamp position = (asin(up component) + 90 deg) / 180 deg, linear between the knots); no
        lamps."""
        sc, spec = self.scene, self.spec
        world = self._new(bpy.data.worlds, "g3d_render_world")
        world.use_nodes = True
        if hasattr(world, "sun_threshold"):
            world.sun_threshold = float(spec["sun_threshold"])
        nt = world.node_tree
        N = nt.nodes.new
        bg = nt.nodes.get("Background")
        tc, nrm, sep = N("ShaderNodeTexCoord"), N("ShaderNodeVectorMath"), N("ShaderNodeSeparateXYZ")
        nrm.operation = "NORMALIZE"
        nt.links.new(tc.outputs["Generated"], nrm.inputs[0])
        nt.links.new(nrm.outputs["Vector"], sep.inputs[0])
        cl = N("ShaderNodeClamp")
        cl.inputs["Min"].default_value, cl.inputs["Max"].default_value = -1.0, 1.0
        nt.links.new(sep.outputs[spec["up_axis"]], cl.inputs["Value"])
        pos = self._math(nt, "MULTIPLY_ADD", self._math(nt, "ARCSINE", cl.outputs["Result"]), 1.0 / math.pi, 0.5)
        cr = N("ShaderNodeValToRGB")
        nt.links.new(pos, cr.inputs["Fac"])
        nt.links.new(cr.outputs["Color"], bg.inputs["Color"])
        ramp = cr.color_ramp
        ramp.interpolation = "LINEAR"
        knots = [((float(el) + 90.0) / 180.0, float(v)) for el, v in spec["ramp_deg"]]
        ends = sorted(ramp.elements, key=lambda e: e.position)     # the default pair at 0 and 1
        for e, (p, v) in ((ends[0], knots[0]), (ends[-1], knots[-1])):
            e.position, e.color = p, (v, v, v, 1.0)
        for p, v in knots[1:-1]:
            e = ramp.elements.new(p)
            e.color = (v, v, v, 1.0)
        got = sorted((e.position, e.color[0]) for e in ramp.elements)        # float32 in Blender
        want = sorted(knots)
        if len(got) != len(want) or any(abs(a - c) > 1e-5 or abs(b - d) > 1e-5 for (a, b), (c, d) in zip(got, want)):
            raise RuntimeError(f"the catalogue ramp did not build as specified: {got}")
        bg.inputs["Strength"].default_value = float(spec["world_strength"])
        sc.world = world

    def set_env(self, weights, cam_matrix) -> None:
        """Fill the photo_env ramps with ``weights`` and turn the sectors to the camera's azimuth."""
        _pos, prof = env_profiles(weights, self.spec)
        for sector, ramp in self.env_ramps.items():
            for el, v in zip(sorted(ramp.elements, key=lambda e: e.position), prof[sector]):
                el.color = (v, v, v, 1.0)
        self.env_h = env_azimuth(cam_matrix)
        self.env_dot.inputs[1].default_value = self.env_h

    def aim_key(self, cam_matrix) -> None:
        """Place the dome key at key_elevation_deg above the horizon on the camera's azimuth (its view axis projected
        on the horizontal plane; +Z front when the camera looks straight down or there is no camera)."""
        if self.rig != DOME_RIG:
            return
        up = Vector((0.0, 1.0, 0.0))
        h = Vector((0.0, 0.0, 1.0))
        if cam_matrix is not None:
            back = Vector([cam_matrix[i][2] for i in range(3)])     # camera local +Z: from the scene to the camera
            back = back - up * back.dot(up)
            if back.length > 1e-6:
                h = back.normalized()
        el = math.radians(self.spec["key_elevation_deg"])
        dv = (h * math.cos(el) + up * math.sin(el)).normalized()
        self.key.location = self.key_center + dv * self.key_dist
        self.key.rotation_euler = (-dv).to_track_quat("-Z", "Y").to_euler()
        self.key_dir = tuple(dv)

    def set_view(self, transform: str) -> None:
        vs = self.scene.view_settings
        vs.view_transform = transform
        vs.look = "None"
        vs.exposure = 0.0
        vs.gamma = 1.0

    def set_pass(self, name: str, linear_format: str = "png16") -> None:
        layer = self.scene.view_layers[0]
        layer.material_override = {"clay": self.clay, "normal": self.normal}.get(name)
        r = self.scene.render
        if name in LINEAR_PASSES and linear_format == "float":
            self.set_view("Raw")
            r.image_settings.file_format = "OPEN_EXR"
            r.image_settings.color_mode = "RGBA"
            r.image_settings.color_depth = "32"
            r.dither_intensity = 0.0
            return
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        if name in LINEAR_PASSES:
            self.set_view("Raw")
            r.image_settings.color_depth = "16"
            r.dither_intensity = 0.0
        else:
            self.set_view("Raw" if name == "normal" else "Standard")
            r.image_settings.color_depth = "8"
            r.dither_intensity = 0.0 if name == "normal" else self.dither0

    def _set_materials(self, c, mats) -> None:
        """Link every slot of a copy to the object and give it ``mats`` (the original's materials, by index)."""
        for i, slot in enumerate(c.material_slots):
            slot.link = "OBJECT"
            slot.material = mats[i] if i < len(mats) else None

    def lens_material(self, name: str):
        """The fixed sheet-only lens material ``name`` (LENS_MATERIALS; made once per render call)."""
        m = self.lens_mats.get(name)
        if m is None:
            spec = LENS_MATERIALS[name]
            m = self._new(bpy.data.materials, f"g3d_render_lens_{name}")
            m.use_nodes = True
            nt = m.node_tree
            nt.nodes.clear()
            em, out = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeOutputMaterial")
            em.inputs["Color"].default_value = (*spec["emission_rgb"], 1.0)
            em.inputs["Strength"].default_value = float(spec["strength"])
            nt.links.new(em.outputs[0], out.inputs["Surface"])
            m.diffuse_color = (*spec["emission_rgb"], 1.0)
            self.lens_mats[name] = m
        return m

    def configure(self, pose: dict, overrides: dict, pass_name: str, lens: str | None = None) -> None:
        """Set every copy's data, world matrix and materials for one view and pass (posed sides, mesh overrides,
        the gloss_black acetate swap, the lens material override)."""
        for name, c in self.copies.items():
            data, mw, mats = self.base[name]
            want = bpy.data.meshes[overrides[name]] if name in overrides else data
            if c.data is not want:
                c.data = want
            # faceted_temple removes these modifiers when it writes the authored corner normals.
            # Match that surface on the temporary copy, then restore the flags for a later head view.
            variant = name in overrides and want.get("g3d_variant") is not None and name in self.temples
            for modifier, viewport, render in self.normal_modifiers[name]:
                modifier.show_viewport = False if variant else viewport
                modifier.show_render = False if variant else render
            side = self.sides.get(name)
            if side in pose:
                th, (ax, az) = pose[side]
                o = self.origin
                m = pose_matrix(side, th, (ax + o[0], az + o[2])) @ np.array(mw, float)
                c.matrix_world = Matrix([list(row) for row in m])
            elif c.matrix_world != mw:
                c.matrix_world = mw
            if pass_name == "gloss_black":
                mats = [self.gloss if mt in self.acetate else mt for mt in mats]
            if lens is not None and name in self.lenses:
                mats = [self.lens_material(lens)] * len(c.material_slots)
            self._set_materials(c, mats)

    def __exit__(self, *exc) -> None:
        for coll, idb in reversed(self.made):
            with contextlib.suppress(Exception):
                coll.remove(idb, do_unlink=True)
        self.made.clear()


# ------------------------------------------------------------------------------------------------ render
def _engine_render(sc, path: Path, engine: str, simulate_eevee_failure: bool) -> None:
    sc.render.engine = ENGINES[engine]
    sc.render.filepath = str(path)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    if engine == "eevee" and simulate_eevee_failure:
        raise RuntimeError("simulated EEVEE failure (selftest)")
    with contextlib.redirect_stdout(io.StringIO()):
        result = bpy.ops.render.render(write_still=True, scene=sc.name)
    if "FINISHED" not in result:
        raise RuntimeError(f"render returned {sorted(result)}")
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"the renderer wrote no file at {path}")


def exr_to_npz(exr: Path, npz: Path) -> None:
    """A float RGBA EXR render (premultiplied) -> ``npz`` {rgb straight alpha, alpha} float16, top row first; the
    temporary image datablock and the EXR are removed."""
    im = bpy.data.images.load(str(exr), check_existing=False)
    try:
        W, H = im.size
        a = np.zeros(W * H * 4, np.float32)
        im.pixels.foreach_get(a)
    finally:
        bpy.data.images.remove(im)
    a = a.reshape(H, W, 4)[::-1]
    alpha = a[..., 3]
    rgb = np.where(alpha[..., None] > 1e-6, a[..., :3] / np.maximum(alpha[..., None], 1e-6), 0.0)
    with open(npz, "wb") as f:
        np.savez_compressed(f, rgb=rgb.astype(np.float16), alpha=alpha.astype(np.float16))
    exr.unlink()


def render(params: dict, root, *, selftest: bool = False) -> dict:
    """Render every view x pass of ``params`` (see module doc). Returns the files, engines and the fingerprint check."""
    t0 = time.perf_counter()
    views = params.get("views") or []
    if not isinstance(views, list) or not views:
        raise RenderRefused("views must be a non-empty list")
    out_dir = work_dir(params.get("out_dir", ""), root)
    passes_all = set()
    names = set()
    lens0 = params.get("lens_material")
    for v in views:
        if not isinstance(v, dict) or "camera" not in v:
            raise RenderRefused("each view needs a name and a camera spec")
        v["file_stem"] = safe_name(v.get("name", ""))
        if v["file_stem"] in names:
            raise RenderRefused(f"duplicate view name {v['file_stem']!r}")
        names.add(v["file_stem"])
        ps = list(v.get("passes") or ["native"])
        bad = [p for p in ps if p not in PASSES]
        if bad:
            raise RenderRefused(f"unknown passes {bad}; the worker renders {PASSES}")
        v["passes"] = ps
        passes_all.update(ps)
        v["pose_parsed"] = parse_pose(v.get("pose"))
        if v.get("env") is not None and str(params.get("rig") or RIG) != ENV_RIG:
            raise RenderRefused(f"env weights need rig {ENV_RIG!r}")
        mo = v.get("mesh_overrides") or {}
        if not isinstance(mo, dict) or not all(isinstance(k, str) and isinstance(m, str) for k, m in mo.items()):
            raise RenderRefused("mesh_overrides must be {object name: mesh datablock name}")
        v["overrides"] = dict(mo)
        v["lens"] = v.get("lens_material", lens0)
        if v["lens"] is not None and (not isinstance(v["lens"], str) or v["lens"] not in LENS_MATERIALS):
            raise RenderRefused(f"lens_material must be None or one of {sorted(LENS_MATERIALS)}, not {v['lens']!r}")
    rig = str(params.get("rig") or RIG)
    if rig not in RIGS:
        raise RenderRefused(f"unknown rig {rig!r}; rigs: {sorted(RIGS)}")
    rig_spec = dict(RIGS[rig])
    if params.get("rig_spec_override") is not None:          # rig tuning (T2) only: a frozen rig never changes
        if not selftest:
            raise PermissionError("rig_spec_override exists only in a --selftest worker")
        bad = sorted(set(params["rig_spec_override"]) - set(rig_spec))
        if bad:
            raise RenderRefused(f"rig_spec_override: unknown keys {bad}")
        rig_spec.update(params["rig_spec_override"])
    want = str(params.get("engine", "auto")).lower()
    if want not in ("auto", "eevee", "cycles"):
        raise RenderRefused(f"engine must be auto, eevee or cycles, not {want!r}")
    simulate = bool(params.get("simulate_eevee_failure")) and selftest
    if params.get("simulate_eevee_failure") and not selftest:
        raise PermissionError("simulate_eevee_failure exists only in a --selftest worker")
    samples = int(params.get("samples", rig_spec.get("samples", SAMPLES_DEFAULT)))
    if not 1 <= samples <= 256:
        raise RenderRefused("samples must be 1..256")
    rig_spec["samples"] = samples                              # the samples actually used
    linear_format = str(params.get("linear_format") or "png16")
    if linear_format not in LINEAR_FORMATS:
        raise RenderRefused(f"linear_format must be one of {LINEAR_FORMATS}")
    if params.get("env") is not None and rig != ENV_RIG:
        raise RenderRefused(f"env weights need rig {ENV_RIG!r}")
    n_lobes = len(env_lobes(rig_spec)) if rig == ENV_RIG else 0
    env0 = parse_env(params.get("env"), n_lobes) if rig == ENV_RIG else None
    for v in views:
        if rig == ENV_RIG:
            v["env_weights"] = parse_env(v.get("env"), n_lobes) or env0
            if v["env_weights"] is None:
                raise RenderRefused(f"rig {ENV_RIG} needs env weights (params or per view)")
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = user_scene()
    state0 = checkpoint.scene_state()
    fp0 = checkpoint.fingerprint(state0)
    objs, skipped = rendered_objects(scene)
    known = {o.name: o for o in objs}
    for v in views:
        for name, mesh in v["overrides"].items():
            if name not in known or known[name].type != "MESH":
                raise RenderRefused(f"mesh_overrides: {name!r} is not a rendered mesh object")
            if mesh not in bpy.data.meshes:
                raise RenderRefused(f"mesh_overrides: no mesh datablock {mesh!r}")
    if any(v["lens"] is not None for v in views):
        bare = sorted(o.name for o in objs if _role(o) == "lens" and not len(o.material_slots))
        if bare:
            raise RenderRefused(f"lens_material: lens objects without a material slot cannot take it: {bare}")
    gpu0 = gpu_info()
    engine = "cycles" if want == "cycles" or (want == "auto" and software_gpu(gpu0)) else "eevee"
    fallback = None
    if engine == "cycles" and want == "auto":
        fallback = {"reason": f"software GPU renderer {gpu0.get('renderer')!r}", "at": None}
    results = {v["file_stem"]: {"name": v.get("name"), "files": {}} for v in views}
    with TempRender(scene, objs, params.get("light_frame") or {}, samples, rig, rig_spec) as tr:
        bodies, acetate = tr.bodies, [m.name for m in tr.acetate]
        lamps = sorted(o.name for o in tr.scene.objects if o.type == "LIGHT")
        posed = {}
        for ps in PASSES:
            todo = [v for v in views if ps in v["passes"]]
            if not todo:
                continue
            tr.set_pass(ps, linear_format)
            as_float = ps in LINEAR_PASSES and linear_format == "float"
            for v in todo:
                tr.configure(v["pose_parsed"], v["overrides"], ps, v["lens"])
                posed[v["file_stem"]] = sorted(n for n, s in tr.sides.items() if s in v["pose_parsed"])
                apply_camera(tr.cam, v["camera"], tr.scene)
                tr.aim_key(v["camera"]["matrix_world"])
                if rig == ENV_RIG:
                    tr.set_env(v["env_weights"], v["camera"]["matrix_world"])
                path = out_dir / f"{v['file_stem']}_{ps}.{'exr' if as_float else 'png'}"
                t1 = time.perf_counter()
                try:
                    _engine_render(tr.scene, path, engine, simulate)
                except Exception as exc:  # noqa: BLE001 - EEVEE failure: Cycles CPU for the rest of the call
                    if engine != "eevee" or want == "eevee":
                        raise
                    fallback = {"reason": f"{type(exc).__name__}: {exc}"[:300], "at": f"{v['file_stem']}/{ps}"}
                    engine = "cycles"
                    t1 = time.perf_counter()
                    _engine_render(tr.scene, path, engine, simulate)
                if as_float:
                    npz = path.with_suffix(".npz")
                    exr_to_npz(path, npz)
                    path = npz
                rec = results[v["file_stem"]]["files"][ps] = {
                    "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
                    "engine": ENGINES[engine], "samples": samples, "seconds": round(time.perf_counter() - t1, 3),
                    "resolution": list(v["camera"]["resolution"]),
                    "bit_depth": ("float16" if as_float else 16) if ps in LINEAR_PASSES else 8,
                    "view_transform": tr.scene.view_settings.view_transform}
                if rig == DOME_RIG:
                    rec["key_dir"] = [round(c, 6) for c in tr.key_dir]
                if rig == ENV_RIG:
                    rec["env_weights"] = list(v["env_weights"])
                    rec["env_azimuth"] = [round(c, 6) for c in tr.env_h]
                if v["lens"] is not None and ps not in ("clay", "normal"):      # those two override every material
                    rec["lens_material"] = {"name": v["lens"], "objects": list(tr.lenses)}
        lens_objects = list(tr.lenses)
    gpu1 = gpu_info()
    state1 = checkpoint.scene_state()
    fp1 = checkpoint.fingerprint(state1)
    for v in views:
        rec = results[v["file_stem"]]
        rec["pose"] = v.get("pose")
        rec["posed_objects"] = posed.get(v["file_stem"], [])
        rec["mesh_overrides"] = v["overrides"]
    out = {"views": list(results.values()), "rig": rig, "rig_spec": rig_spec, "samples": samples,
           "linear_format": linear_format, **({"env_lobes": env_lobes(rig_spec)} if rig == ENV_RIG else {}),
           "rig_overridden": params.get("rig_spec_override") is not None, "engine_requested": want,
           "acetate_bodies": bodies, "acetate_materials": acetate, "gloss_black": GLOSS_BLACK, "lamps": lamps,
           "lens_objects": lens_objects,
           "lens_materials": {n: LENS_MATERIALS[n] for n in sorted({v["lens"] for v in views} - {None})},
           "engine_final": ENGINES[engine], "fallback": fallback, "gpu": gpu1 if "error" not in gpu1 else gpu0,
           "software_gpu": software_gpu(gpu1), "objects": [o.name for o in objs], "skipped": skipped,
           "fingerprint_before": fp0, "fingerprint_after": fp1, "unchanged": fp0 == fp1,
           "seconds": round(time.perf_counter() - t0, 3)}
    if fp0 != fp1:
        out["scene_delta"] = checkpoint.scene_delta(state0, state1)
    return out


# ------------------------------------------------------------------------------------------------ pick
def pick(params: dict) -> dict:
    """Cast each ray {origin, direction} (scene mm) against the evaluated scene; hits on objects that do not render
    are passed through. Returns per ray {hit, object, partRole, point_mm, asset_mm, normal, face, distance}."""
    rays = params.get("rays") or []
    if not isinstance(rays, list) or not rays or len(rays) > PICK_MAX_RAYS:
        raise RenderRefused(f"rays must be a list of 1..{PICK_MAX_RAYS}")
    scene = user_scene()
    objs, _skipped = rendered_objects(scene)
    allowed = {o.name for o in objs}
    origin = origin_marker(scene) or [0.0, 0.0, 0.0]
    dg = bpy.context.evaluated_depsgraph_get()
    far = float(params.get("max_distance_mm", 1e7))
    out = []
    for ray in rays:
        o = Vector([float(x) for x in ray["origin"]])
        d = Vector([float(x) for x in ray["direction"]])
        if d.length == 0:
            raise RenderRefused("a ray direction is zero")
        d.normalize()
        start, travelled, rec = o.copy(), 0.0, {"hit": False}
        for _ in range(PICK_SKIPS):
            hit, loc, nrm, face, obj, _m = scene.ray_cast(dg, start, d, distance=far - travelled)
            if not hit:
                break
            travelled = (loc - o).dot(d)
            if obj is not None and obj.original.name in allowed:
                src = obj.original
                n = Vector(nrm)
                if n.length:
                    n.normalize()
                rec = {"hit": True, "object": src.name, "partRole": _role(src), "point_mm": list(loc),
                       "asset_mm": [loc[i] - origin[i] for i in range(3)], "normal": list(n), "face": int(face),
                       "distance": float(travelled)}
                break
            start = loc + d * 1e-4
        out.append(rec)
    return {"hits": out, "origin_mm": origin}
