"""The glasses3d Blender worker (WP2_DESIGN §3). Runs inside headless Blender 5.2, started by agent/worker_client.py:

    blender.exe -b --factory-startup --disable-autoexec <head>.blend --python glasses3d/g3d/worker.py --
        --receipt <receipt.json> --root <job dir> [--selftest] [--probe]

Startup: the token comes from `G3D_WORKER_TOKEN` (popped; only its sha256 is kept). The worker binds 127.0.0.1:0 with
SO_EXCLUSIVEADDRUSE and writes `receipt.json` atomically (`g3d.worker/1`: port, pid, create_time, versions, file,
token_sha256; with `--probe` also the GPU, a 64x64 EEVEE probe, the safe-mode self-check and the g3d facade report,
which a restart skips to come up fast). The first frame on a connection must be
`{"method": "hello", "token": T}`; a bad token closes only that connection; after the first authenticated client the
listener closes, and when that client disconnects the worker exits.

Astra's code arrives only through `exec` and is always validated (g3d/safe_mode.py, again here), then runs in a fresh
namespace under restricted builtins, the guarded import/getattr family and an audit hook armed only while it runs,
followed by a post-call check of the active file, save/load handlers and persistence surfaces. Trusted methods
(checkpoints, fonts, bounds, render, pick, export_glb; later g3d calls) take JSON parameters and never code text;
their output paths are confined to the job root (render and export to `<root>/work`). `exec_unvalidated` and the
render's `simulate_eevee_failure` exist only with `--selftest` (they test the runtime layers and the Cycles fallback)
and are refused otherwise.

Soft timeout: Blender leaves no Python SIGINT handler under -b (defect 4), so one is installed here that raises
SoftTimeout only while a snippet is armed; a timer thread calls `_thread.interrupt_main()` at the deadline and again
every second until the snippet ends. A C call (numpy SVD) ignores it until it returns (defect 5): the host's socket
deadline then kills the worker through its Job Object and restarts it from the head checkpoint.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import json
import os
import signal
import socket
import sys
import threading
import time
import traceback
import _thread
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
G3D_DIR = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True

import bpy  # noqa: E402

from glasses3d.g3d import checkpoint, rpc, safe_mode  # noqa: E402

SCHEMA = "g3d.worker/1"
SOFT_MIN_S, SOFT_MAX_S, SOFT_DEFAULT_S = 0.5, 600.0, 120.0
STDOUT_MAX, STDERR_MAX, RESULT_MAX = 20_000, 5_000, 20_000
FONT_DIR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "Fonts"
FONTS = {"arial": "arial.ttf", "arialbd": "arialbd.ttf", "times": "times.ttf", "timesbd": "timesbd.ttf",
         "calibri": "calibri.ttf", "segoeui": "segoeui.ttf"}


def _args() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out, i = {}, 0
    while i < len(argv):
        key = argv[i].lstrip("-")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            out[key] = argv[i + 1]
            i += 2
        else:
            out[key] = True
            i += 1
    return out


ARGS = _args()
ROOT = Path(os.path.realpath(str(ARGS.get("root") or "."))).resolve()
SELFTEST = bool(ARGS.get("selftest"))
_token = os.environ.pop("G3D_WORKER_TOKEN", "")
TOKEN_SHA = hashlib.sha256(_token.encode()).hexdigest() if _token else ""
del _token


def log(*parts) -> None:
    print("[g3d.worker]", *parts, flush=True)


def _create_time() -> float | None:
    """This process's creation time in epoch seconds (GetProcessTimes), as the host reads it."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    if not k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(c), ctypes.byref(e), ctypes.byref(k),
                               ctypes.byref(u)):
        return None
    return ((c.dwHighDateTime << 32) | c.dwLowDateTime) / 1e7 - 11644473600.0


CREATE_TIME = _create_time()


# ------------------------------------------------------------------------------------------------ modules
def _preimport() -> dict:
    """Import every allowed module now, so a snippet's import never reads a file under the armed audit hook."""
    import importlib
    loaded = {}
    for name in sorted(safe_mode.MODULE_PATHS):
        if name.split(".")[0] in ("g3d",):
            continue
        try:
            loaded[name] = importlib.import_module(name)
        except ImportError as exc:
            log("allowed module missing:", name, exc)
    return loaded


MODULES = _preimport()
FACADE, FACADE_ERRORS = None, None


def facade():
    """The g3d facade, built on first use OUTSIDE the armed window (its imports compile seat_bpy and temple_bpy:
    ~0.4 s that a restart need not pay before the first snippet that names g3d)."""
    global FACADE, FACADE_ERRORS
    if FACADE_ERRORS is None:
        try:
            from glasses3d.g3d import lib as _lib
            FACADE, FACADE_ERRORS = _lib.build()
        except Exception as exc:  # noqa: BLE001
            FACADE, FACADE_ERRORS = None, {"facade": f"{type(exc).__name__}: {exc}"}
    return FACADE


PREBIND = {"bpy": bpy, "bmesh": MODULES.get("bmesh"), "mathutils": MODULES.get("mathutils"),
           "numpy": MODULES.get("numpy")}


def _path_specific(module: str) -> bool:
    """True when some safe-mode rule for `module` depends on reaching it by its own path."""
    if module.split(".")[0] in ("bpy", "numpy", "g3d", "string"):
        return True
    return any(safe_mode._name_rule(a, False) is None and a not in safe_mode.REROOT_SEGMENTS and a != "types"
               for a in safe_mode.ESCAPE_EDGES.get(module, ()))


def _selfcheck() -> dict:
    """Module attributes reachable from the allowed modules that lead outside them and that the static rules do not
    deny (must be empty: the policy table `safe_mode.ESCAPE_EDGES` is measured in this Blender)."""
    import types as _types
    unlisted = []
    for name, mod in MODULES.items():
        if name.split(".")[0] == "bpy":
            continue
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            try:
                value = getattr(mod, attr)
            except Exception:  # noqa: BLE001
                continue
            # a foreign module, or an allowed one re-rooted under another path whose rules depend on the path
            # (object_utils.bpy; fractions.re is harmless: re's only edge, copyreg, is denied by name anywhere)
            if isinstance(value, _types.ModuleType) and (
                    value.__name__ not in safe_mode.MODULE_PATHS
                    or (value.__name__ != f"{name}.{attr}" and _path_specific(value.__name__))):
                if safe_mode.path_rule(f"{name}.{attr}") is None and safe_mode._name_rule(attr, False) is None:
                    unlisted.append(f"{name}.{attr} -> {value.__name__}")
    return {"unlisted_edges": sorted(unlisted)}


# ------------------------------------------------------------------------------------------------ runtime layers
class SoftTimeout(BaseException):
    """Raised inside an armed snippet by the SIGINT handler when its soft timeout fires."""


ARMED = threading.Event()


def _on_sigint(signum, frame):
    if ARMED.is_set():
        raise SoftTimeout("soft timeout: the snippet was interrupted")


signal.signal(signal.SIGINT, _on_sigint)

AUDIT = safe_mode.AuditGuard([Path(bpy.app.binary_path).resolve().parent, Path(os.__file__).resolve().parent,
                              G3D_DIR])
AUDIT.install()
IO_EVENTS: list = []


@bpy.app.handlers.persistent
def _on_save_pre(*args):
    if ARMED.is_set():
        path = next((a for a in args if isinstance(a, str)), "")
        IO_EVENTS.append({"event": "save", "path": path, "existed_before": bool(path) and os.path.exists(path)})


@bpy.app.handlers.persistent
def _on_load_pre(*args):
    if ARMED.is_set():
        path = next((a for a in args if isinstance(a, str)), "")
        IO_EVENTS.append({"event": "load", "path": path})


bpy.app.handlers.save_pre.append(_on_save_pre)
bpy.app.handlers.load_pre.append(_on_load_pre)
OWN_HANDLERS = {id(_on_save_pre), id(_on_load_pre)}


def _persistence() -> dict:
    """Counts of the surfaces that would carry code past a call (handlers, driver namespace, texts, libraries)."""
    handlers = {}
    for name in dir(bpy.app.handlers):
        lst = getattr(bpy.app.handlers, name, None)
        if isinstance(lst, list):
            handlers[name] = sum(1 for h in lst if id(h) not in OWN_HANDLERS)
    return {"handlers": handlers, "driver_namespace": sorted(bpy.app.driver_namespace.keys()),
            "texts": len(bpy.data.texts), "libraries": len(bpy.data.libraries)}


def _tail(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[-limit:], True)


def _format_tb(exc: BaseException, code: str) -> str:
    lines = code.splitlines()
    frames = traceback.extract_tb(exc.__traceback__)
    first = next((i for i, fr in enumerate(frames) if fr.filename == "<snippet>"), 0)
    frames = frames[first:][-6:]
    out = ["Traceback (most recent call last, at most 6 frames):"]
    for fr in frames:
        if fr.filename == "<snippet>":
            text = lines[fr.lineno - 1].strip() if fr.lineno and fr.lineno <= len(lines) else ""
            out.append(f"  snippet line {fr.lineno}, in {fr.name}\n    {text}")
        else:
            out.append(f"  {Path(fr.filename).name} line {fr.lineno}, in {fr.name}")
    out.append(f"{type(exc).__name__}: {exc}")
    return "\n".join(out)


def _result_json(value) -> object:
    data = rpc.to_jsonable(value)
    text = json.dumps(data, allow_nan=False)
    if len(text) <= RESULT_MAX:
        return data
    return {"truncated": True, "chars": len(text), "preview": text[:RESULT_MAX]}


def _run_snippet(code: str, soft_s: float, prebound: dict) -> dict:
    """Run validated (or selftest) code under every runtime layer. Never raises."""
    g3d = facade() if "g3d" in code else None
    guard = safe_mode.RuntimeGuard(g3d_facade=g3d)
    ns = {"__builtins__": guard.builtins(), "__name__": "__g3d_snippet__", "result": None}
    for local, module in prebound.items():
        value = g3d if module == "g3d" else PREBIND.get(module)
        if value is not None:
            ns[local] = value
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        code_obj = compile(code, "<snippet>", "exec")
    except SyntaxError as exc:
        return {"status": "error", "traceback": f"SyntaxError: {exc.msg} (line {exc.lineno})", "stdout": "",
                "stderr": "", "runtime_violations": []}
    IO_EVENTS.clear()
    path_before = bpy.data.filepath
    persist_before = _persistence()
    done = threading.Event()
    fired = threading.Event()

    def interrupter():
        if done.wait(soft_s):
            return
        while not done.is_set():
            fired.set()
            _thread.interrupt_main()
            done.wait(1.0)

    threading.Thread(target=interrupter, name="g3d-soft-timeout", daemon=True).start()
    status, tb, audit_refusals = "ok", None, []
    t0 = time.perf_counter()
    try:
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                AUDIT.arm()
                ARMED.set()
                try:
                    exec(code_obj, ns)
                finally:
                    ARMED.clear()
                    audit_refusals = AUDIT.disarm()
        except SoftTimeout:
            status, tb = "timeout", f"soft timeout after {soft_s:g} s: the snippet was interrupted"
        except BaseException as exc:  # noqa: BLE001 - scene code errors are results, not worker failures
            status, tb = "error", _format_tb(exc, code)
    except SoftTimeout:                       # an interrupt landing while the error above was being handled
        status, tb = "timeout", f"soft timeout after {soft_s:g} s: the snippet was interrupted"
    finally:
        ARMED.clear()
        AUDIT.disarm()
        done.set()
    seconds = time.perf_counter() - t0
    violations = [{"layer": "builtins", "message": m} for m in guard.refusals]
    violations += [{"layer": "audit", "message": m} for m in audit_refusals]
    stray = []
    for ev in IO_EVENTS:
        violations.append({"layer": "blend_io", "message": f"a .blend {ev['event']} during scene code: {ev['path']}"})
        if ev["event"] == "save" and ev["path"] and not ev.get("existed_before"):
            p = Path(ev["path"])
            for cand in (p, p.with_name(p.name + "1")):
                with contextlib.suppress(OSError):
                    if cand.is_file():
                        os.chmod(cand, 0o666)
                        cand.unlink()
                        stray.append({"path": str(cand), "deleted": True})
        elif ev["event"] == "save":
            stray.append({"path": ev["path"], "deleted": False, "note": "the file existed before and was overwritten"})
    if bpy.data.filepath != path_before:
        violations.append({"layer": "blend_io", "message": f"the active file changed to {bpy.data.filepath!r}"})
    persist_after = _persistence()
    if persist_after != persist_before:
        violations.append({"layer": "persistence", "message": "handlers, driver namespace, texts or libraries "
                                                              f"changed: {persist_before} -> {persist_after}"})
    if fired.is_set() and status in ("ok", "error"):
        # the snippet outlived its soft timeout and swallowed the interrupt (a bare except): its effect is not
        # what it would have been, so it is a timeout and the host restores checkpoint_before
        status = "timeout"
        tb = tb or f"soft timeout after {soft_s:g} s: the snippet caught the interrupt and went on"
    if violations and status in ("ok", "error", "timeout"):
        status = "refused_runtime"
    stdout, so_trunc = _tail(out_buf.getvalue(), STDOUT_MAX)
    stderr, se_trunc = _tail(err_buf.getvalue(), STDERR_MAX)
    res = {"status": status, "result": None, "stdout": stdout, "stdout_truncated": so_trunc, "stderr": stderr,
           "stderr_truncated": se_trunc, "traceback": tb, "runtime_violations": violations,
           "exec_seconds": round(seconds, 4)}
    if stray:
        res["stray_files"] = stray
    if status == "ok":
        try:
            res["result"] = _result_json(ns.get("result"))
        except Exception as exc:  # noqa: BLE001
            res["result"] = {"unserializable": f"{type(exc).__name__}: {exc}"}
    return res


def _soft(params: dict) -> float:
    try:
        soft = float(params.get("soft_timeout_s", SOFT_DEFAULT_S))
    except (TypeError, ValueError):
        soft = SOFT_DEFAULT_S
    return min(max(soft, SOFT_MIN_S), SOFT_MAX_S)


def _mutating(params: dict, body) -> dict:
    """The worker half of the per-call protocol (WP2_DESIGN §5): fingerprint before (refusing on drift from the
    host's head), run, and when ok protect and fingerprint after with the scene delta. A non-ok result leaves the
    restore to the host (it knows checkpoint_before)."""
    t0 = time.perf_counter()
    state0 = checkpoint.scene_state()
    fp0 = checkpoint.fingerprint(state0)
    expect = params.get("expect_fingerprint")
    if expect and fp0 != expect:
        return {"status": "drift", "fingerprint_before": fp0, "expected": expect,
                "seconds": round(time.perf_counter() - t0, 4)}
    res = body()
    res["fingerprint_before"] = fp0
    if res.get("status") == "ok":
        res["protected"] = checkpoint.protect()
        state1 = checkpoint.scene_state()
        fp1 = checkpoint.fingerprint(state1)
        res["fingerprint_after"] = fp1
        res["changed"] = fp1 != fp0
        res["scene_delta"] = checkpoint.scene_delta(state0, state1) if fp1 != fp0 else None
    res["seconds"] = round(time.perf_counter() - t0, 4)
    return res


# ------------------------------------------------------------------------------------------------ methods
def m_ping(params):
    return {"pong": True, "pid": os.getpid(), "file": bpy.data.filepath}


def m_exec(params, validated: bool = True):
    code = params.get("code")
    if not isinstance(code, str):
        return {"status": "refused", "violations": [{"line": None, "message": "code must be a string",
                                                     "use_instead": None}]}
    if validated:
        analysis = safe_mode.analyse(code)
        if analysis.violations:
            return {"status": "refused", "violations": [v.as_dict() for v in analysis.violations]}
        prebound = analysis.prebound
    else:
        prebound = dict(safe_mode.PREBOUND_MODULES)
    soft = _soft(params)
    return _mutating(params, lambda: _run_snippet(code, soft, prebound))


def m_exec_unvalidated(params):
    if not SELFTEST:
        raise PermissionError("exec_unvalidated exists only in a --selftest worker")
    return m_exec(params, validated=False)


def m_fingerprint(params):
    t0 = time.perf_counter()
    state = checkpoint.scene_state()
    out = {"fingerprint": checkpoint.fingerprint(state), "objects": len(state["objects"]),
           "meshes": len(state["meshes"]), "seconds": None}
    if params.get("state"):
        out["state"] = state
    out["seconds"] = round(time.perf_counter() - t0, 4)
    return out


def m_checkpoint_save(params):
    return checkpoint.save(params["path"], ROOT, compress=bool(params.get("compress", True)))


def m_checkpoint_restore(params):
    return checkpoint.restore(params["path"], ROOT, expect_fingerprint=params.get("expect_fingerprint"),
                              expect_sha256=params.get("expect_sha256"))


def m_load_font(params):
    family = str(params.get("family", "")).lower()

    def body():
        if family not in FONTS:
            return {"status": "refused", "code": "E_FONT",
                    "message": f"font {family!r} is not available; choose one of {sorted(FONTS)}"}
        path = FONT_DIR / FONTS[family]
        if not path.is_file():
            return {"status": "refused", "code": "E_FONT", "message": f"{path} is missing on this machine"}
        font = bpy.data.fonts.load(str(path), check_existing=True)
        if font.packed_file is None:
            font.pack()
        return {"status": "ok", "result": {"name": font.name, "family": family, "file": path.name}}
    return _mutating(params, body)


def _closed_and_parts(me) -> tuple[bool | None, int | None]:
    import numpy as np
    nv, ne = len(me.vertices), len(me.edges)
    if not nv:
        return None, 0
    edges = np.zeros(ne * 2, np.int32)
    me.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    closed = None
    if len(me.polygons):
        keys = np.zeros(len(me.loops), np.int32)
        me.loops.foreach_get("edge_index", keys)
        use = np.bincount(keys, minlength=ne)
        closed = bool(ne and (use == 2).all())
    label = np.arange(nv)
    for _ in range(nv):
        a, b = label[edges[:, 0]], label[edges[:, 1]]
        m = np.minimum(a, b)
        new = label.copy()
        np.minimum.at(new, edges[:, 0], m)
        np.minimum.at(new, edges[:, 1], m)
        new = new[new]
        if (new == label).all():
            break
        label = new
    return closed, int(len(np.unique(label)))


def m_scene_summary(params):
    """Units, axes and origin marker; per object {partRole, vertices, triangles, materials, bbox_mm, closed,
    loose_parts}; materials; warnings (missing partRole on renderable objects, a material shared by a lens and a
    non-lens part)."""
    scene = bpy.context.scene
    want = params.get("objects", "all")
    us = scene.unit_settings
    state = checkpoint.scene_state()
    objects, warnings, mat_roles = {}, [], {}
    for obj in scene.objects:
        if want != "all" and obj.name not in (want or []):
            continue
        info = dict(state["summary"].get(obj.name, {}))
        info["materials"] = [s.material.name if s.material else None for s in obj.material_slots]
        info["hide_render"] = obj.hide_render
        if obj.type == "MESH" and obj.data is not None:
            info["closed"], info["loose_parts"] = _closed_and_parts(obj.data)
        role = info.get("partRole")
        if obj.type in ("MESH", "CURVE", "FONT", "SURFACE") and not obj.hide_render and role is None:
            warnings.append(f"{obj.name}: renders but has no partRole (lens, frame or temple)")
        for m in info["materials"]:
            if m:
                mat_roles.setdefault(m, set()).add(role)
        objects[obj.name] = info
    for m, roles in sorted(mat_roles.items()):
        if "lens" in roles and len(roles - {"lens"}) > 0:
            warnings.append(f"material {m!r} is shared by a lens and a non-lens part")
    axes = next((scene.get(k) for k in ("blender_agent_axes", "authoring_axes", "g3d_axes") if scene.get(k)), None)
    origin = scene.get("mdl_bridge_underside")
    return {"units": {"system": us.system, "scale_length": us.scale_length, "length_unit": us.length_unit},
            "axes": axes, "origin_marker": rpc.to_jsonable(origin), "objects": objects,
            "materials": sorted(m.name for m in bpy.data.materials), "warnings": warnings,
            "fingerprint": checkpoint.fingerprint(state)}


def m_bounds(params):
    """Render-enabled objects' world boxes (evaluated geometry) and the origin marker (read-only)."""
    from glasses3d.g3d import render as _render
    return _render.bounds(params)


def m_render(params):
    """Render camera specs in a temporary scene (trusted, read-only; g3d/render.py). The fingerprint is compared
    before and after; `unchanged: false` makes the host restore the head checkpoint."""
    from glasses3d.g3d import render as _render
    return _render.render(params, ROOT, selftest=SELFTEST)


def m_pick(params):
    """Ray-cast scene-mm rays on the evaluated depsgraph (trusted, read-only)."""
    from glasses3d.g3d import render as _render
    return _render.pick(params)


def m_export_glb(params):
    """Export the scene with g3d/export_glb.py to a path under <root>/work/exports (trusted; the scene must be
    unchanged afterwards, which the exporter itself and the fingerprint both check)."""
    from glasses3d.g3d import export_glb as _exp
    from glasses3d.g3d import render as _render
    t0 = time.perf_counter()
    path = _render.work_dir(params.get("path", ""), ROOT)
    base = Path(os.path.realpath(str(ROOT / "work" / "exports")))
    try:
        path.relative_to(base)
    except ValueError:
        raise PermissionError(f"export path outside <root>/work/exports: {path}") from None
    fp0 = checkpoint.fingerprint()
    try:
        receipt = _exp.export_glb(path)
    except _exp.ExportError as exc:
        return {"status": "refused", "code": "E_EXPORT", "message": str(exc), "fingerprint_before": fp0,
                "fingerprint_after": checkpoint.fingerprint(), "seconds": round(time.perf_counter() - t0, 4)}
    fp1 = checkpoint.fingerprint()
    return {"status": "exported", "path": str(path), "sha256": checkpoint.sha256_file(path),
            "bytes": path.stat().st_size, "receipt": receipt, "fingerprint_before": fp0, "fingerprint_after": fp1,
            "unchanged": fp0 == fp1, "seconds": round(time.perf_counter() - t0, 4)}


METHODS = {
    "ping": m_ping, "exec": m_exec, "exec_unvalidated": m_exec_unvalidated, "fingerprint": m_fingerprint,
    "checkpoint_save": m_checkpoint_save, "checkpoint_restore": m_checkpoint_restore, "load_font": m_load_font,
    "scene_summary": m_scene_summary, "bounds": m_bounds, "render": m_render, "pick": m_pick,
    "export_glb": m_export_glb,
}


# ------------------------------------------------------------------------------------------------ probe + receipt
def _probe() -> dict:
    """A 64x64 EEVEE render of a temporary cube in a temporary scene (deleted after): seconds and non-empty pixels,
    plus the GPU the render ran on."""
    out = {}
    t0 = time.perf_counter()
    made = {}
    try:
        sc = bpy.data.scenes.new(checkpoint.RENDER_SCENE + "_probe")
        made["scene"] = sc
        me = bpy.data.meshes.new("g3d_probe_mesh")
        made["mesh"] = me
        import bmesh as _bmesh
        bm = _bmesh.new()
        _bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new("g3d_probe", me)
        made["object"] = ob
        sc.collection.objects.link(ob)
        cd = bpy.data.cameras.new("g3d_probe_cam")
        made["camera_data"] = cd
        cam = bpy.data.objects.new("g3d_probe_cam", cd)
        made["camera"] = cam
        sc.collection.objects.link(cam)
        cam.location = (0.0, -6.0, 0.0)
        cam.rotation_euler = (1.5708, 0.0, 0.0)
        sc.camera = cam
        sc.render.engine = "BLENDER_EEVEE"
        sc.render.resolution_x = sc.render.resolution_y = 64
        sc.render.resolution_percentage = 100
        sc.render.film_transparent = True
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        work = ROOT / "worker"
        work.mkdir(parents=True, exist_ok=True)
        png = work / f"probe-{os.getpid()}.png"
        sc.render.filepath = str(png)
        with contextlib.redirect_stdout(io.StringIO()):
            bpy.ops.render.render(write_still=True, scene=sc.name)
        out["seconds"] = round(time.perf_counter() - t0, 3)
        img = bpy.data.images.load(str(png))
        try:
            import numpy as np
            px = np.zeros(img.size[0] * img.size[1] * 4, np.float32)
            img.pixels.foreach_get(px)
            out["nonempty_px"] = int((px.reshape(-1, 4)[:, 3] > 0.0).sum())
        finally:
            bpy.data.images.remove(img)
            with contextlib.suppress(OSError):
                png.unlink()
        try:
            import gpu
            out["gpu"] = {"vendor": gpu.platform.vendor_get(), "renderer": gpu.platform.renderer_get(),
                          "backend": gpu.platform.backend_type_get()}
        except Exception as exc:  # noqa: BLE001
            out["gpu"] = {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for key in ("object", "camera"):
            if key in made:
                with contextlib.suppress(Exception):
                    bpy.data.objects.remove(made[key])
        for key, coll in (("mesh", bpy.data.meshes), ("camera_data", bpy.data.cameras), ("scene", bpy.data.scenes)):
            if key in made:
                with contextlib.suppress(Exception):
                    coll.remove(made[key])
    return out


def _write_receipt(path: Path, receipt: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    checkpoint.replace_with_retry(tmp, path)


# ------------------------------------------------------------------------------------------------ serve
def _token_ok(token) -> bool:
    if not TOKEN_SHA or not isinstance(token, str):
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), TOKEN_SHA)


def _dispatch(msg: dict) -> dict:
    t0 = time.perf_counter()
    method = msg["method"]
    fn = METHODS.get(method)
    if fn is None:
        return rpc.error(msg["id"], "UnknownMethod", f"no method {method!r}", None, time.perf_counter() - t0)
    try:
        return rpc.ok(msg["id"], fn(msg.get("params") or {}), time.perf_counter() - t0)
    except SoftTimeout as exc:                # a late interrupt outside any snippet
        return rpc.error(msg["id"], "SoftTimeout", str(exc), None, time.perf_counter() - t0)
    except BaseException as exc:  # noqa: BLE001
        return rpc.error(msg["id"], type(exc).__name__, str(exc), traceback.format_exc(limit=8)[-4000:],
                         time.perf_counter() - t0)


def _hello_result() -> dict:
    t0 = time.perf_counter()
    fp = checkpoint.fingerprint()
    return {"pid": os.getpid(), "create_time": CREATE_TIME, "blender": bpy.app.version_string,
            "python": sys.version.split()[0], "file": bpy.data.filepath, "fingerprint": fp,
            "fingerprint_seconds": round(time.perf_counter() - t0, 4), "selftest": SELFTEST}


def serve() -> None:
    if not TOKEN_SHA:
        log("refusing to start: no G3D_WORKER_TOKEN")
        return
    if "receipt" not in ARGS:
        log("refusing to start: no --receipt")
        return
    probe = _probe() if ARGS.get("probe") else None
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    import numpy
    receipt = {"schema": SCHEMA, "port": port, "pid": os.getpid(), "create_time": CREATE_TIME,
               "blender": bpy.app.version_string, "python": sys.version.split()[0], "numpy": numpy.__version__,
               "file": bpy.data.filepath, "root": str(ROOT), "token_sha256": TOKEN_SHA, "selftest": SELFTEST,
               "probe": probe, "gpu": (probe or {}).get("gpu"),
               "safe_mode_selfcheck": _selfcheck() if ARGS.get("probe") else None,
               "g3d_facade": ({"names": list(getattr(facade(), "__all__", [])), "errors": FACADE_ERRORS}
                              if ARGS.get("probe") else None),
               "started": time.time()}
    _write_receipt(Path(ARGS["receipt"]), receipt)
    log("ready on port", port, "pid", os.getpid(), "file", bpy.data.filepath or "<factory>")
    conn = None
    hello_id = 0
    while conn is None:
        c, _addr = srv.accept()
        c.settimeout(10.0)
        try:
            hello = rpc.recv_frame(c, max_bytes=8192)
        except (rpc.FrameError, OSError) as exc:
            log("dropped a connection before hello:", exc)
            with contextlib.suppress(OSError):
                c.close()
            continue
        if hello.get("method") != "hello" or not _token_ok(hello.get("token")):
            log("refused a connection: bad token")
            with contextlib.suppress(Exception):
                rpc.send_frame(c, rpc.error(hello.get("id"), "Auth", "bad token"))
            with contextlib.suppress(OSError):
                c.close()
            continue
        hello_id = hello.get("id") if isinstance(hello.get("id"), int) else 0
        try:
            rpc.send_frame(c, rpc.ok(hello.get("id"), _hello_result(), 0.0))
        except (rpc.FrameError, OSError):
            with contextlib.suppress(OSError):
                c.close()
            continue
        conn = c
    srv.close()
    conn.settimeout(None)
    last_id = hello_id
    while True:
        try:
            msg = rpc.recv_frame(conn)
        except rpc.PeerClosed:
            log("client closed the connection; exiting")
            break
        except (rpc.FrameError, OSError) as exc:
            log("frame error; exiting:", exc)
            with contextlib.suppress(Exception):
                rpc.send_frame(conn, rpc.error(None, "FrameError", str(exc)))
            break
        why = rpc.check_request(msg, last_id)
        if why:
            log("bad request; exiting:", why)
            with contextlib.suppress(Exception):
                rpc.send_frame(conn, rpc.error(msg.get("id"), "BadRequest", why))
            break
        last_id = msg["id"]
        if msg["method"] == "shutdown":
            with contextlib.suppress(Exception):
                rpc.send_frame(conn, rpc.ok(msg["id"], {"bye": True, "pid": os.getpid()}, 0.0))
            log("shutdown requested")
            break
        try:
            reply = _dispatch(msg)
        except SoftTimeout:
            reply = rpc.error(msg["id"], "SoftTimeout", "late interrupt")
        try:
            rpc.send_frame(conn, reply)
        except rpc.FrameError as exc:
            rpc.send_frame(conn, rpc.error(msg["id"], "FrameError", f"result not sendable: {exc}"))
        except OSError as exc:
            log("send failed; exiting:", exc)
            break
    with contextlib.suppress(OSError):
        conn.close()


if __name__ == "__main__":
    serve()
