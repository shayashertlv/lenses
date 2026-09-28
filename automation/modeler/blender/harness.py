"""Blender-side harness: builds a candidate from program modules, exports part arrays, renders observations.

    blender -b --factory-startup --python harness.py -- <job.json>

job.json:
  {"lib_dir": ..., "out_dir": ..., "evidence_path": ... or null,
   "modules": [{"name": "frame", "path": ".../frame.py"}, ...]      (executed in order; a failure stops the chain)
   "renders": [{"id": ..., "kind": "clay"|"textured", "camera": {...}, "width": W, "height": H,
                "transparent": bool, "background": [r,g,b] or null}],
   "export": true, "save_blend": true,
   "mode": "build" | "render_only" (render_only opens "blend_path" and only renders)}

camera (all in the MODEL frame, mm): either
  {"type": "photo", "camera": {yaw,pitch,roll,perspective,scale,center_x,center_y}, "frame": {"center":[x,y,z],
   "extent": e}}  - the host's reconstruction.camera.Camera in a NormFrame, reproduced exactly (see camera_object), or
  {"type": "orbit", "yaw": deg, "pitch": deg, "roll": deg, "ortho": bool, "px_per_mm": f, "target": [x,y,z] or "bbox",
   "distance_mm": d (perspective only), "fov_deg": f (perspective only)}

Writes out_dir/result.json: {ok, module_results, notes, inventory, parts_npz, materials_json, renders, blend, seconds}.
Program errors are reported, never swallowed: the harness itself exits 0 whenever it could write result.json.
"""
import json
import math
import os
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Matrix, Vector

_T0 = time.monotonic()


def _argv_job():
    if "--" not in sys.argv:
        raise SystemExit("usage: blender -b --python harness.py -- job.json")
    return sys.argv[sys.argv.index("--") + 1]


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1, default=_default)
    os.replace(tmp, path)


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


# --------------------------------------------------------------------------- cameras
def camera_basis(yaw_deg, pitch_deg):
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right = np.array([cy, 0.0, -sy])
    up = np.array([-sp * sy, cp, -sp * cy])
    toward = np.array([cp * sy, sp, cp * cy])
    return right, up, toward


def camera_object(spec, width, height):
    """A Blender camera reproducing the host camera model (reconstruction.camera.project) in mm world units.

    Camera model: v_norm = (v_mm - center) / extent; depth = 1 - p (v.toward); x = (v.right)/depth; y = (v.up)/depth;
    px = cx + s (cos r x - sin r y); py = cy - s (sin r x + cos r y). The camera sits at center + extent/p * toward,
    looks along -toward, image right = cos r right - sin r up, image up = sin r right + cos r up, focal/sensor ratio
    F/S = s/(p W) with sensor fit HORIZONTAL; the principal point offset is a lens shift. p == 0: orthographic with
    ortho_scale = W extent / s. The mapping is verified against the host rasterizer by the test suite."""
    cam = bpy.data.cameras.new("mdl_cam")
    obj = bpy.data.objects.new("mdl_cam", cam)
    bpy.context.scene.collection.objects.link(obj)
    cam.sensor_fit = "HORIZONTAL"
    W, H = float(width), float(height)
    big = max(W, H)
    if spec["type"] == "photo":
        c = spec["camera"]
        fr = spec["frame"]
        center = np.asarray(fr["center"], float)
        extent = float(fr["extent"])
        yaw, pitch, roll = float(c["yaw"]), float(c["pitch"]), float(c["roll"])
        p, s = float(c["perspective"]), float(c["scale"])
        cx, cy = float(c["center_x"]), float(c["center_y"])
        right, up, toward = camera_basis(yaw, pitch)
        cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        img_right = cr * right - sr * up
        img_up = sr * right + cr * up
        ortho = p < 1e-4
        if not ortho:
            ratio = s / (p * W)                   # F / sensor_width
            sensor = 36.0
            focal = ratio * sensor
            if focal > 4000.0:                     # keep within Blender's range by shrinking the sensor
                sensor = max(1.0, 4000.0 / ratio)
                focal = ratio * sensor
            if focal > 5000.0:
                ortho = True
        if ortho:
            cam.type = "ORTHO"
            cam.ortho_scale = W * extent / s
            dist = extent * 6.0
        else:
            cam.type = "PERSP"
            cam.sensor_width = sensor
            cam.lens = focal
            dist = extent / p
        cam.shift_x = -(cx - W / 2.0) / big
        cam.shift_y = (cy - H / 2.0) / big
        position = center + toward * dist
        R = Matrix(((img_right[0], img_up[0], toward[0], position[0]),
                    (img_right[1], img_up[1], toward[1], position[1]),
                    (img_right[2], img_up[2], toward[2], position[2]),
                    (0.0, 0.0, 0.0, 1.0)))
        obj.matrix_world = R
        cam.clip_start = max(0.1, dist * 0.02)
        cam.clip_end = dist * 3.0 + extent * 4.0
        return obj
    if spec["type"] == "orbit":
        yaw, pitch, roll = float(spec.get("yaw", 0)), float(spec.get("pitch", 0)), float(spec.get("roll", 0))
        right, up, toward = camera_basis(yaw, pitch)
        cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        img_right = cr * right - sr * up
        img_up = sr * right + cr * up
        target = spec.get("target", "bbox")
        if isinstance(target, str):
            lo, hi = scene_bounds()
            target = (lo + hi) / 2
        target = np.asarray(target, float)
        ppm = spec.get("px_per_mm", "fit")
        if ppm == "fit" or ppm is None:
            # frame the whole registered geometry: project the bbox corners on the image axes
            lo, hi = scene_bounds()
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - target
            ext_x = float(np.ptp(corners @ img_right)) if len(corners) else 100.0
            ext_y = float(np.ptp(corners @ img_up)) if len(corners) else 60.0
            px_per_mm = 0.88 * min(W / max(ext_x, 1.0), H / max(ext_y, 1.0))
        else:
            px_per_mm = float(ppm)
        if spec.get("ortho", True):
            cam.type = "ORTHO"
            cam.ortho_scale = W / px_per_mm
            dist = float(spec.get("distance_mm", 600.0))
        else:
            cam.type = "PERSP"
            dist = float(spec.get("distance_mm", 600.0))
            cam.sensor_width = 36.0
            cam.lens = 36.0 * px_per_mm * dist / W
        position = target + toward * dist
        obj.matrix_world = Matrix(((img_right[0], img_up[0], toward[0], position[0]),
                                   (img_right[1], img_up[1], toward[1], position[1]),
                                   (img_right[2], img_up[2], toward[2], position[2]),
                                   (0.0, 0.0, 0.0, 1.0)))
        cam.clip_start = max(0.1, dist * 0.02)
        cam.clip_end = dist * 4.0
        return obj
    raise ValueError(f"Unknown camera type {spec.get('type')!r}")


def scene_bounds():
    import glasses_lib as gl
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    dg = bpy.context.evaluated_depsgraph_get()
    for o in gl.objects():
        if o.hide_render:
            continue
        ev = o.evaluated_get(dg)
        for c in ev.bound_box:
            w = ev.matrix_world @ Vector(c)
            lo = np.minimum(lo, np.asarray(w))
            hi = np.maximum(hi, np.asarray(w))
    if not np.isfinite(lo).all():
        return np.zeros(3), np.zeros(3)
    return lo, hi


# --------------------------------------------------------------------------- lighting and rendering
def setup_world(background_rgb=None, strength=1.0):
    sc = bpy.context.scene
    world = sc.world or bpy.data.worlds.new("mdl_world")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
        out = world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(bg.outputs[0], out.inputs[0])
    rgb = (0.85, 0.85, 0.85) if background_rgb is None else tuple(background_rgb)
    bg.inputs[0].default_value = (*rgb, 1.0)
    bg.inputs[1].default_value = strength


def setup_lights():
    """A neutral three-point studio in sun lights (their irradiance does not depend on the mm scale)."""
    for name, energy, rot in (("mdl_key", 2.5, (50, -25, 20)), ("mdl_fill", 1.0, (60, 35, -30)), ("mdl_rim", 0.8, (-40, 160, 0))):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.angle = math.radians(8)
        lo = bpy.data.objects.new(name, light)
        bpy.context.scene.collection.objects.link(lo)
        lo.rotation_euler = [math.radians(a) for a in rot]


def _clay_material():
    mat = bpy.data.materials.get("mdl_clay")
    if mat is None:
        mat = bpy.data.materials.new("mdl_clay")
        mat.use_nodes = True
        b = mat.node_tree.nodes.get("Principled BSDF")
        b.inputs["Base Color"].default_value = (0.42, 0.41, 0.40, 1.0)
        b.inputs["Roughness"].default_value = 0.55
        if "Specular IOR Level" in b.inputs:
            b.inputs["Specular IOR Level"].default_value = 0.6
    return mat


def set_raytracing(sc, enabled):
    """EEVEE's use_raytracing for the next render; silently nothing on a version without the property."""
    if hasattr(sc.eevee, "use_raytracing"):
        try:
            sc.eevee.use_raytracing = bool(enabled)
        except Exception:  # noqa: BLE001
            pass


def render_views(renders, out_dir, samples=16):
    import glasses_lib as gl
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    try:
        sc.eevee.taa_render_samples = samples
    except Exception:  # noqa: BLE001 - property names differ across versions
        pass
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.resolution_percentage = 100
    setup_lights()
    results = []
    objs = [o for o in gl.objects() if not o.hide_render]
    saved = {o.name: [m for m in o.data.materials] for o in objs}
    for spec in renders:
        t0 = time.monotonic()
        W, H = int(spec["width"]), int(spec["height"])
        sc.render.resolution_x, sc.render.resolution_y = W, H
        sc.render.film_transparent = bool(spec.get("transparent", True))
        clay = spec.get("kind", "textured") == "clay"
        # EEVEE ray tracing (screen-space reflections/refractions) only where a material can show it: the textured
        # views. Clay views swap every material for the opaque mdl_clay below, so tracing there only costs time on
        # the worker's CPU rasterizer (no measurement reads these renders: the sheets are the only consumer).
        set_raytracing(sc, not clay)
        # clay: a dimmer ambient so the key light's shading reads as shape; textured: a bright neutral studio
        setup_world(spec.get("background"), strength=0.45 if clay else 0.7)
        cam = camera_object(spec["camera"], W, H)
        sc.camera = cam
        if clay:
            m = _clay_material()
            for o in objs:
                for i in range(len(o.data.materials)):
                    o.data.materials[i] = m
                if not o.data.materials:
                    o.data.materials.append(m)
        path = os.path.join(out_dir, f"{spec['id']}.png")
        sc.render.filepath = path
        err = None
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        if clay:
            for o in objs:
                for i, m in enumerate(saved[o.name]):
                    if i < len(o.data.materials):
                        o.data.materials[i] = m
        bpy.data.objects.remove(cam, do_unlink=True)
        results.append({"id": spec["id"], "path": path if err is None else None, "kind": spec.get("kind", "textured"),
                        "width": W, "height": H, "camera": spec["camera"], "error": err,
                        "seconds": round(time.monotonic() - t0, 2)})
    return results


# --------------------------------------------------------------------------- build
def run_modules(modules, gl):
    ns = {"bpy": bpy, "bmesh": __import__("bmesh"), "gl": gl, "math": math, "np": np, "numpy": np,
          "E": gl.EVIDENCE, "Vector": Vector, "Matrix": Matrix, "__name__": "__program__"}
    results = []
    for m in modules:
        t0 = time.monotonic()
        with open(m["path"], "r", encoding="utf-8") as f:
            src = f.read()
        row = {"name": m["name"], "path": m["path"], "ok": True, "error": None, "traceback": None}
        try:
            code = compile(src, f"<{m['name']}>", "exec")
            exec(code, ns)
        except Exception as e:  # noqa: BLE001 - reported to the author verbatim
            row.update(ok=False, error=f"{type(e).__name__}: {e}",
                       traceback="".join(traceback.format_exception(type(e), e, e.__traceback__))[-6000:])
        row["seconds"] = round(time.monotonic() - t0, 2)
        results.append(row)
        if not row["ok"]:
            break
    return results


def export_parts(out_dir, gl):
    parts, mats = gl.collect_parts()
    arrays = {}
    meta = {}
    for name, p in parts.items():
        key = name.replace("/", "_")
        arrays[f"{key}__V"] = p["V"].astype(np.float32)
        arrays[f"{key}__F"] = p["F"].astype(np.int32)
        arrays[f"{key}__M"] = p["face_material"].astype(np.int32)
        if p["UV"] is not None:
            arrays[f"{key}__UV"] = p["UV"].astype(np.float32)
        meta[key] = {"object": name, "part": p["part"], "component": p["component"], "materials": p["materials"]}
    npz = os.path.join(out_dir, "parts.npz")
    np.savez_compressed(npz, **arrays)
    sc = bpy.context.scene
    declarations = {"bridge_underside_mm": list(sc["mdl_bridge_underside"]) if "mdl_bridge_underside" in sc else None,
                    "temple_clip_z_mm": float(sc["mdl_temple_clip_z_mm"]) if "mdl_temple_clip_z_mm" in sc else None}
    write_json(os.path.join(out_dir, "materials.json"), {"materials": mats, "objects": meta, "declarations": declarations})
    return npz, os.path.join(out_dir, "materials.json"), gl.inventory(parts)


def main():
    job_path = _argv_job()
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    result = {"ok": False, "mode": job.get("mode", "build"), "module_results": [], "notes": [], "inventory": [],
              "parts_npz": None, "materials_json": None, "renders": [], "blend": None, "error": None}
    sys.path.insert(0, job["lib_dir"])
    try:
        import glasses_lib as gl
        if job.get("mode", "build") == "render_only":
            bpy.ops.wm.open_mainfile(filepath=job["blend_path"])
            import importlib
            importlib.reload(gl)
        else:
            gl.reset_scene()
            if job.get("evidence_path"):
                gl.load_evidence(job["evidence_path"])
            result["module_results"] = run_modules(job["modules"], gl)
            result["notes"] = gl.notes()
            built = all(r["ok"] for r in result["module_results"])
            if built and job.get("export", True):
                npz, mats, inv = export_parts(out_dir, gl)
                result.update(parts_npz=npz, materials_json=mats, inventory=inv)
                result["notes"] = gl.notes()      # export-time notes too (normals made consistent, font fallbacks)
            if job.get("save_blend", True):
                blend = os.path.join(out_dir, "candidate.blend")
                bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True)
                result["blend"] = blend
            result["ok"] = built
        if job.get("renders"):
            if result["mode"] == "render_only" or result["ok"]:
                result["renders"] = render_views(job["renders"], out_dir, samples=int(job.get("samples", 16)))
                if result["mode"] == "render_only":
                    result["ok"] = all(r["error"] is None for r in result["renders"])
    except Exception as e:  # noqa: BLE001 - the host reads result.json
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-6000:]
        result["ok"] = False
    result["seconds"] = round(time.monotonic() - _T0, 2)
    result["blender_version"] = bpy.app.version_string
    try:
        import hashlib
        lib_path = os.path.join(job["lib_dir"], "glasses_lib.py")
        result["lib_sha256"] = hashlib.sha256(open(lib_path, "rb").read()).hexdigest()
        result["harness_sha256"] = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
    except Exception:  # noqa: BLE001 - provenance only
        pass
    write_json(os.path.join(out_dir, "result.json"), result)
    print("MODELER_HARNESS| done ok=%s" % result["ok"], flush=True)


if __name__ == "__main__":
    main()
