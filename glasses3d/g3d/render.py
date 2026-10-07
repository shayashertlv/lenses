"""Renders, bounds and picking inside the worker's Blender (WP2_DESIGN §6). Trusted and read-only: the host sends
camera specs (g3d_host/blender_camera.to_blender) and never code.

``render(params, root)`` builds a temporary ``g3d_render`` scene that links every object the user's render view layer
renders (export_glb.render_status) except lights, cameras and light probes, with its own camera, world and lights
(rig ``photo_v1``), film transparent, Standard view transform, RGBA PNG. Passes:

- ``native``: the scene's own materials under the rig;
- ``clay``: a view-layer material override (grey 0.6, roughness 0.55): opaque everywhere, so its alpha is the
  geometric silhouette (glass lenses included) — the host derives the ``silhouette`` pass from it;
- ``normal``: an override emitting the world normal x 0.5 + 0.5 under the Raw view transform (PNG = the numbers).

Engine: EEVEE (``BLENDER_EEVEE``, 16 samples) on the GPU; Cycles CPU (16 samples) when the GPU is a software
renderer, when EEVEE raises / returns anything but FINISHED / writes no file (then for the rest of the call), or when
asked. The engine is recorded per image. Everything temporary (scene, camera, lights, world, override materials) is
removed in ``finally`` and the scene fingerprint is compared before and after (``unchanged``); the host restores the
head checkpoint if it ever differs.

``bounds()`` = the render-enabled objects' world boxes from their evaluated geometry and the origin marker.
``pick(params)`` = ``Scene.ray_cast`` on the evaluated depsgraph, skipping hits on objects that do not render.
Output paths are confined to ``<job root>/work`` (junctions resolved first: checkpoint.confined).
"""
from __future__ import annotations

import contextlib
import hashlib
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
PASSES = ("native", "clay", "normal")
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
        w = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
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


# ------------------------------------------------------------------------------------------------ temporary scene
class TempRender:
    """The temporary scene with its camera, rig and override materials; everything is removed on exit."""

    def __init__(self, scene, objects, light_frame: dict, samples: int):
        self.user, self.objects, self.light_frame, self.samples = scene, objects, light_frame, int(samples)
        self.made: list = []                 # (collection, datablock) in creation order

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
        us.system, us.scale_length, us.length_unit = src.system, src.scale_length, src.length_unit
        for obj in self.objects:
            sc.collection.objects.link(obj)
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
        sc.eevee.taa_render_samples = self.samples
        sc.cycles.device = "CPU"
        sc.cycles.samples = self.samples
        sc.cycles.use_denoising = False
        sc.cycles.use_adaptive_sampling = False
        return self

    def _rig(self) -> None:
        sc, spec = self.scene, RIG_SPEC
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

    def set_view(self, transform: str) -> None:
        vs = self.scene.view_settings
        vs.view_transform = transform
        vs.look = "None"
        vs.exposure = 0.0
        vs.gamma = 1.0

    def set_pass(self, name: str) -> None:
        layer = self.scene.view_layers[0]
        layer.material_override = {"native": None, "clay": self.clay, "normal": self.normal}[name]
        self.set_view("Raw" if name == "normal" else "Standard")

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


def render(params: dict, root, *, selftest: bool = False) -> dict:
    """Render every view x pass of ``params`` (see module doc). Returns the files, engines and the fingerprint check."""
    t0 = time.perf_counter()
    views = params.get("views") or []
    if not isinstance(views, list) or not views:
        raise RenderRefused("views must be a non-empty list")
    out_dir = work_dir(params.get("out_dir", ""), root)
    passes_all = set()
    names = set()
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
    want = str(params.get("engine", "auto")).lower()
    if want not in ("auto", "eevee", "cycles"):
        raise RenderRefused(f"engine must be auto, eevee or cycles, not {want!r}")
    simulate = bool(params.get("simulate_eevee_failure")) and selftest
    if params.get("simulate_eevee_failure") and not selftest:
        raise PermissionError("simulate_eevee_failure exists only in a --selftest worker")
    samples = int(params.get("samples", SAMPLES_DEFAULT))
    if not 1 <= samples <= 256:
        raise RenderRefused("samples must be 1..256")
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = user_scene()
    state0 = checkpoint.scene_state()
    fp0 = checkpoint.fingerprint(state0)
    objs, skipped = rendered_objects(scene)
    gpu0 = gpu_info()
    engine = "cycles" if want == "cycles" or (want == "auto" and software_gpu(gpu0)) else "eevee"
    fallback = None
    if engine == "cycles" and want == "auto":
        fallback = {"reason": f"software GPU renderer {gpu0.get('renderer')!r}", "at": None}
    results = {v["file_stem"]: {"name": v.get("name"), "files": {}} for v in views}
    with TempRender(scene, objs, params.get("light_frame") or {}, samples) as tr:
        for ps in PASSES:
            todo = [v for v in views if ps in v["passes"]]
            if not todo:
                continue
            tr.set_pass(ps)
            for v in todo:
                apply_camera(tr.cam, v["camera"], tr.scene)
                path = out_dir / f"{v['file_stem']}_{ps}.png"
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
                results[v["file_stem"]]["files"][ps] = {
                    "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
                    "engine": ENGINES[engine], "samples": samples, "seconds": round(time.perf_counter() - t1, 3),
                    "resolution": list(v["camera"]["resolution"])}
    gpu1 = gpu_info()
    state1 = checkpoint.scene_state()
    fp1 = checkpoint.fingerprint(state1)
    out = {"views": list(results.values()), "rig": RIG, "rig_spec": RIG_SPEC, "engine_requested": want,
           "engine_final": ENGINES[engine], "fallback": fallback, "gpu": gpu1 if "error" not in gpu1 else gpu0,
           "software_gpu": software_gpu(gpu1), "objects": [o.name for o in objs], "skipped": skipped,
           "fingerprint_before": fp0, "fingerprint_after": fp1, "unchanged": fp0 == fp1,
           "seconds": round(time.perf_counter() - t0, 3)}
    if fp0 != fp1:
        out["scene_delta"] = checkpoint.scene_delta(state0, state1)
    return out


# ------------------------------------------------------------------------------------------------ pick
def _role(obj):
    for holder in (obj, obj.data):
        if holder is not None and isinstance(holder.get("partRole"), str):
            return holder.get("partRole")
    return None


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


def file_digest(path) -> dict:
    p = Path(path)
    return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
