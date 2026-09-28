"""Frame see-through: how much of what is behind a translucent front shows through it in the runtime.

Measured only for candidates whose materials include a ``gl.material_translucent`` (kind ``translucent``): the AR harness
renders the front view twice on two solid fixtures (the wearer-proxy skin tone and a contrasting dark blue;
``render_fixtures`` makes the two runs once per candidate for every lens too, since the lens colour is fitted from the
same pair: modeler.lens_colour). The frame
pixels (the frame node projected with the harness's recorded root transform, lens pixels removed) change colour with
the fixture in proportion to the light they transmit::

    see_through = sum_c |mean(A_c - B_c)| / sum_c |fixtureA_c - fixtureB_c|   (0 = opaque, 1 = perfectly clear)

The number is a runtime measurement in sRGB, not a physical transmittance; it tells the author whether the exported
front reads as crystal (about 0.6 and above), translucent (0.2..0.6) or opaque (below 0.1), so ``tint_srgb`` and
``thickness_mm`` can be corrected against the photos.

``temple_see_through`` (in the same result) is the same measurement over the ``temple_R``/``temple_L`` primitives that
carry a translucent material. Head-on the arms are behind the front, so when the temples are translucent the same two
harness runs also render the harness's angled view (35 deg yaw, no extra harness start) and the temples are measured
there, frame and lens pixels removed. Only pixels the runtime draws as temple are averaged: the NEAR arm (the one on the
camera's side of the asset's X; temple_L at +35 deg), projected as the runtime deforms it (``runtime_arm_vertices``: the
fixed 18 mm spread from the hinge to the clip cap and the inward return over the terminal band, ported from
ar/src/render and matching the runtime's own vertices to 4e-9 m on r0002; the vertical drop is 0 in production), only
its geometry in front of the runtime's rear temple clip (the endpoint the harness row records, else the clip the
harness registers), without the arm's own opaque hardware (drawn over the crystal), and no pixel that shows the bare
fixture colour in both renders (the synthetic head's occluders hide the arm there; the harness records no depth or
visibility mask). Against a ground truth (r0002's crystal-temple variant through the local harness with the near
crystal arm painted opaque green, same camera and pose: 0.886 over its 885 eroded pixels) the undeformed,
hardware-inclusive projection read 0.796 (103 of its 757 pixels were not arm, 388 drawn-arm pixels missed, the metal
strip counted as crystal); registered it reads 0.885 over 776 pixels. It is a measurement only: ``not_applicable`` for
opaque temples, ``no_temple_pixels`` when the visible strips are too thin, and a failure is recorded in it without
touching the front's number.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from bsa import archeck
from bsa.tryon import HARNESS_CLIP_ZM

from .lens_colour import _mat4, actual_ar_render, lens_masks_in_render, project_pixels

FIXTURES = {"skin": "#cba68d", "blue": "#3a4f6e"}
FRONT_VIEW = ({"id": "front", "yaw_degrees": 0},)
TEMPLE_VIEW = {"id": "angled", "yaw_degrees": 35}     # the harness's own angled view: the arms are behind the front head-on
MIN_FRAME_PIXELS = 50
# summed |channel - fixture| (8-bit sRGB) up to which a temple pixel counts as the bare fixture in a render; a pixel within
# it in BOTH fixture renders shows no temple (hidden by the head's occluders, or past the rear clip). It also drops the
# clearest crystal pixels, so it pulls a clear arm down: on r0002 (ground truth 0.886) tolerance 0/3/6/10/15/20 read
# 0.897/0.891/0.885/0.873/0.868/0.860 over 884/832/776/677/639/556 pixels
BARE_FIXTURE_TOLERANCE = 6


def hex_rgb(value: str) -> np.ndarray:
    v = value.lstrip("#")
    return np.array([int(v[i:i + 2], 16) for i in (0, 2, 4)], float)


TEMPLE_PARTS = ("temple_R", "temple_L")


def frame_mask_in_render(glb_path: Path, render: dict, shape: tuple[int, int], material_names: set[str] | None = None,
                         part_names: tuple[str, ...] = ("frame",), runtime_shape: bool = False) -> np.ndarray | None:
    """Pixel mask of the named nodes (the frame by default; ``TEMPLE_PARTS`` for the temples) in one actual-AR render (same
    root transform as the lenses: identity node transforms); with ``material_names`` only the primitives carrying those
    materials (the translucent ones, not the hardware); with ``runtime_shape`` the vertices as the runtime drew them in
    that render (``runtime_arm_vertices``). None when no primitive matches."""
    from reconstruction.mesh import load_glb_bytes
    spatial = render.get("spatial") or {}
    cam = render.get("camera") or {}
    lenses = spatial.get("lenses") or []
    if not lenses or not cam.get("projection_matrix"):
        return None
    raw = Path(glb_path).read_bytes()
    mesh = load_glb_bytes(raw)
    vertices = runtime_arm_vertices(mesh, raw, render)[0] if runtime_shape else mesh.vertices
    view = _mat4(cam.get("view_matrix") or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    proj = _mat4(cam["projection_matrix"])
    W, H = int(cam.get("width", shape[1])), int(cam.get("height", shape[0]))
    px, ok = project_pixels(vertices, proj @ view @ _mat4(lenses[0]["mesh_to_world"]), W, H)
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    found = False
    for p in mesh.parts or []:
        if str(p.get("name") or "") not in part_names:
            continue
        if material_names is not None and str(p.get("material") or "") not in material_names:
            continue
        found = True
        for tri in mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]:
            if ok[tri].all():
                draw.polygon([tuple(px[i]) for i in tri], fill=255)
    return (np.asarray(img) > 0) if found else None


def temple_clip_zm(render: dict) -> dict[int, float]:
    """Per arm side (-1: local x < 0, +1: x >= 0) the mesh-local Z behind which the runtime discards temple fragments in
    this render, as renderer.ts applyTempleEndpoint sets it: max(templeEndMaximumZM, templeEnd<Negative|Positive>ZM) from
    the harness row's timing; the clip the harness registers every checked model with (bsa.tryon.HARNESS_CLIP_ZM) when
    the row records no endpoint."""
    t = render.get("timing") or {}

    def num(key):
        v = t.get(key)
        return float(v) if isinstance(v, (int, float)) and np.isfinite(v) else None
    maximum = num("templeEndMaximumZM")
    out = {}
    for side, key in ((-1, "templeEndNegativeZM"), (1, "templeEndPositiveZM")):
        vals = [v for v in (maximum, num(key)) if v is not None]
        out[side] = max(vals) if vals else HARNESS_CLIP_ZM
    return out


# The runtime's fixed arm shape (renderer.ts: rearDrop.setTerminalFit(templeTerminalFit); rearDrop.setShape(0, ARM_SPREAD_M)),
# ported from ar/src/render/face-width.ts (armSpreadCurve, spreadArmX), rear-drop.ts (eligibility, armShaftStartZM) and
# temple-terminal-fit.ts (the posterior return). The vertical drop is always 0 in production, so nothing is dropped.
ARM_LATERAL_MIN_M = 0.045                 # face-width.ts ARM_LATERAL_MIN_M (= rear-drop.ts lateralMinM)
ARM_LATERAL_GUARD_M = 0.0005              # face-width.ts WIDTH_FIT.lateralGuardM
SPREAD_HINGE_ROUND_M = 0.006              # face-width.ts
REAR_DROP_PROXIMAL_GUARD_M = 0.015        # rear-drop.ts REAR_DROP_PARAMETERS.proximalGuardM
HINGE_SLICE_M, HINGE_SHAFT_MAX_HEIGHT_M, HINGE_SHAFT_RUN_SLICES, HINGE_SHAFT_RUN_MIN_SLICES = 0.001, 0.015, 20, 5   # rear-drop.ts
GLASSES_OFFSET_CM = (0.0, 3.271027, 6.531958919387042)   # catalog.ts: the offset every Modeling Auto registration carries
TEMPLE_HEAD_VOLUME_SCALE_CM, TEMPLE_HEAD_VOLUME_CENTER_CM = (7.4, 9.5, 7.5), (0.0, -0.5, -3.5)   # temple-terminal-fit.ts
MAX_TERMINAL_INSET_M = 0.05               # temple-terminal-fit.ts


def arm_spread_curve(z: np.ndarray, start_zm: float, cutoff_zm: float, spread_m: float) -> np.ndarray:
    """face-width.ts armSpreadCurve: 0 at the pivot plane, the full spread at the clip cap, the first
    SPREAD_HINGE_ROUND_M rounded."""
    span = start_zm - cutoff_zm
    if not np.isfinite([start_zm, cutoff_zm, spread_m]).all() or start_zm <= cutoff_zm + SPREAD_HINGE_ROUND_M:
        raise ValueError("The arm-spread span is invalid.")
    z = np.asarray(z, float)
    if spread_m == 0:
        return np.zeros_like(z)
    run = np.clip(start_zm - z, 0.0, span)
    slope = spread_m / (span - SPREAD_HINGE_ROUND_M / 2)
    return np.where(run < SPREAD_HINGE_ROUND_M, slope * run * run / (2 * SPREAD_HINGE_ROUND_M), slope * (run - SPREAD_HINGE_ROUND_M / 2))


def spread_arm_x(x: np.ndarray, z: np.ndarray, start_zm: float, cutoff_zm: float, spread_m: float) -> np.ndarray:
    """face-width.ts spreadArmX: the lateral position after the spread, identity inside ARM_LATERAL_MIN_M."""
    x = np.asarray(x, float)
    if spread_m == 0:
        return x.copy()
    mag = np.abs(x)
    floor = np.minimum(mag, ARM_LATERAL_MIN_M + ARM_LATERAL_GUARD_M)
    spread = np.sign(x) * np.maximum(mag + arm_spread_curve(z, start_zm, cutoff_zm, spread_m), floor)
    return np.where(mag <= ARM_LATERAL_MIN_M, x, spread)


def terminal_fit_x(x: np.ndarray, z: np.ndarray, fit: dict | None) -> np.ndarray:
    """temple-terminal-fit.ts terminalFitX: the C1 inward return over [endZM, startZM], per side inset."""
    x = np.asarray(x, float)
    if not fit:
        return x.copy()
    t = np.clip((fit["startZM"] - np.asarray(z, float)) / (fit["startZM"] - fit["endZM"]), 0.0, 1.0)
    inset = np.where(x < 0, fit["negativeInsetM"], fit["positiveInsetM"])
    return x - np.sign(x) * inset * t * t * (3 - 2 * t)


def _gltf_materials(raw: bytes) -> list[dict]:
    """The GLB's JSON material list (alphaMode is not on the loader's parts)."""
    import struct
    size = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20:20 + size]).get("materials") or []


def _runtime_opaque_parts(mesh, raw: bytes) -> tuple[list[bool], list[bool]]:
    """Per loaded part (primitive): (optical, opaque) as the runtime classifies its material: optical-material.ts
    classifyAssetMaterials/isOpticalMaterial (a canonical asset's frame/temple-only materials are never optical; otherwise
    a lens descriptor or transmission > 0 is), and three's ``transparent`` (glTF alphaMode BLEND) as rear-drop.ts reads
    it: opaque = neither optical nor transparent."""
    parts = mesh.parts or []
    materials = _gltf_materials(raw)
    canonical = any(p.get("has_lens_appearance_extension") for p in parts)
    owners: dict = {}
    for p in parts:
        role, descriptor = p.get("declared_role"), bool(p.get("has_lens_appearance_extension"))
        o = owners.setdefault(p.get("material_index"), {"lens": False, "frame": False, "unknown": False})
        o["lens"] |= role == "lens" or descriptor
        o["frame"] |= role in ("frame", "temple")
        o["unknown"] |= role not in ("lens", "frame", "temple") and not descriptor
    optical, opaque = [], []
    for p in parts:
        o, index = owners[p.get("material_index")], p.get("material_index")
        frame = canonical and o["frame"] and not o["lens"] and not o["unknown"]
        is_optical = not frame and (bool(p.get("has_lens_appearance_extension")) or float(p.get("transmission") or 0) > 0)
        blend = index is not None and 0 <= index < len(materials) and materials[index].get("alphaMode") == "BLEND"
        optical.append(is_optical)
        opaque.append(not is_optical and not blend)
    return optical, opaque


def _arm_shaft_start_zm(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> float | None:
    """rear-drop.ts armShaftStartZM over the opaque vertices outside ARM_LATERAL_MIN_M: walking back from the front, the
    first 1 mm slice whose vertical extent is a bar and stays one (empty slices no evidence) for the next 20 mm."""
    keep = np.abs(x) > ARM_LATERAL_MIN_M
    slices = np.floor(z[keep] / HINGE_SLICE_M).astype(np.int64)
    extents: dict[int, float] = {}
    for s in np.unique(slices):
        ys = y[keep][slices == s]
        extents[int(s)] = float(ys.max() - ys.min())
    for s in sorted(extents, reverse=True):
        if extents[s] > HINGE_SHAFT_MAX_HEIGHT_M:
            continue
        populated, tall = 1, False
        for i in range(1, HINGE_SHAFT_RUN_SLICES + 1):
            h = extents.get(s - i)
            if h is None:
                continue
            if h > HINGE_SHAFT_MAX_HEIGHT_M:
                tall = True
                break
            populated += 1
        if not tall and populated >= HINGE_SHAFT_RUN_MIN_SLICES:
            return s * HINGE_SLICE_M
    return None


def _terminal_fit(mesh, optical: list[bool], opaque: list[bool], maximum_zm: float, spread_m: float, start_zm: float,
                  cutoff_zm: float, model_to_cm: float) -> dict | None:
    """temple-terminal-fit.ts createTempleTerminalFitEvaluator(...)(fitScale): the per-side inset that buries the arm
    ends inside the canonical head volume, from the spread geometry of every opaque, non-optical triangle in the
    terminal band (vertices and edge crossings at its two planes); None where the runtime returns null."""
    end_zm = maximum_zm + 0.005
    start = min(-0.075, end_zm + 0.065)
    if not np.isfinite([maximum_zm, spread_m, start_zm, cutoff_zm, model_to_cm]).all() or end_zm - maximum_zm < 0.004 \
            or start - end_zm < 0.025 or start_zm <= start or not model_to_cm > 0:
        return None
    pts = []
    for p, opt, opq in zip(mesh.parts or [], optical, opaque):
        if opt or not opq:              # the evaluator skips optical and transparent materials, as the deformation does
            continue
        tri = mesh.vertices[mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]].astype(float)   # (n, 3, 3)
        pts.append(tri.reshape(-1, 3))
        for e in range(3):
            a, b = tri[:, e], tri[:, (e + 1) % 3]
            dz = b[:, 2] - a[:, 2]
            ok = np.abs(dz) >= 1e-12
            for plane in (maximum_zm, end_zm):
                t = np.full(len(a), -1.0)
                t[ok] = (plane - a[ok, 2]) / dz[ok]
                sel = (t >= 0) & (t <= 1)
                q = a[sel] + t[sel, None] * (b[sel] - a[sel])
                q[:, 2] = plane
                pts.append(q)
    if not pts:
        return None
    p = np.concatenate(pts)
    p = p[(np.abs(p[:, 0]) > ARM_LATERAL_MIN_M) & (p[:, 2] >= maximum_zm - 1e-9) & (p[:, 2] <= end_zm + 1e-9)]
    side = (p[:, 0] >= 0).astype(int)
    if len(np.unique(side)) < 2:
        return None
    spread_x = spread_arm_x(p[:, 0], p[:, 2], start_zm, cutoff_zm, spread_m)
    (rx, ry, rz), (cx, cy, cz) = TEMPLE_HEAD_VOLUME_SCALE_CM, TEMPLE_HEAD_VOLUME_CENTER_CM
    head_y = (p[:, 1] * model_to_cm + GLASSES_OFFSET_CM[1] - cy) / ry
    head_z = (p[:, 2] * model_to_cm + GLASSES_OFFSET_CM[2] - cz) / rz
    inside = 1 - head_y ** 2 - head_z ** 2
    if not (inside > 0).all():
        return None
    sign = np.where(side == 0, -1.0, 1.0)
    boundary = cx + sign * (rx * np.sqrt(inside) - 0.3)
    over = sign * (spread_x - (boundary - GLASSES_OFFSET_CM[0]) / model_to_cm)
    insets = [max(0.0, float(over[side == s].max())) + 0.000001 for s in (0, 1)]
    if any(not np.isfinite(i) or i > MAX_TERMINAL_INSET_M for i in insets):
        return None
    if ((np.where(side == 0, -spread_x, spread_x) - np.where(side == 0, insets[0], insets[1])) <= 0).any():
        return None
    return {"startZM": start, "endZM": end_zm, "maximumZM": maximum_zm, "negativeInsetM": insets[0], "positiveInsetM": insets[1]}


def runtime_arm_vertices(mesh, raw: bytes, render: dict, cutoff_zm: float = HARNESS_CLIP_ZM) -> tuple[np.ndarray, dict | None]:
    """The mesh's vertices as the runtime drew them in this render: every opaque vertex outside ARM_LATERAL_MIN_M and
    behind the front guard (rear-drop.ts eligible) moved to terminalFitX(spreadArmX(x, z, ...), z, fit). The harness row
    records the spread and its pivot plane (timing armSpreadM, armSpreadStartZM); the clip cap is the one the harness
    registers every model with (``cutoff_zm``); the return is re-derived at the render's fit scale (the asset's scale in
    mesh_to_world: the harness pose is rigid, the asset is metres scaled to centimetres) from the recorded
    templeEndMaximumZM. A row that records no spread (an older harness) leaves the vertices as they are: (vertices, None)."""
    t = render.get("timing") or {}
    spread, start = t.get("armSpreadM"), t.get("armSpreadStartZM")
    vertices = np.asarray(mesh.vertices, float).copy()
    if not all(isinstance(v, (int, float)) and np.isfinite(v) for v in (spread, start)):
        return vertices, None
    spread, start = float(spread), float(start)
    optical, opaque = _runtime_opaque_parts(mesh, raw)
    sel = np.zeros(len(vertices), bool)
    lens = np.zeros(len(vertices), bool)
    for p, opt, opq in zip(mesh.parts or [], optical, opaque):
        span = slice(p["vertex_start"], p["vertex_start"] + p["vertex_count"])
        sel[span] |= opq
        lens[span] |= opt
    if not lens.any():
        return vertices, None           # rear-drop.ts refuses an asset without optics: nothing was deformed
    x, y, z = vertices[:, 0], vertices[:, 1], vertices[:, 2]
    guard = float(z[lens].min()) - REAR_DROP_PROXIMAL_GUARD_M
    hinge = _arm_shaft_start_zm(x[sel], y[sel], z[sel])
    eligible = sel & (np.abs(x) > ARM_LATERAL_MIN_M) & (z < max(guard, hinge if hinge is not None else guard))
    maximum = t.get("templeEndMaximumZM")
    m2w = _mat4(((render.get("spatial") or {}).get("lenses") or [{}])[0].get("mesh_to_world") or np.eye(4).ravel())
    fit = _terminal_fit(mesh, optical, opaque, float(maximum), spread, start, cutoff_zm, float(np.linalg.norm(m2w[:3, 0]))) \
        if isinstance(maximum, (int, float)) and np.isfinite(maximum) else None
    vertices[eligible, 0] = terminal_fit_x(spread_arm_x(x[eligible], z[eligible], start, cutoff_zm, spread), z[eligible], fit)
    return vertices, {"spread_m": round(spread, 4), "spread_start_zm": round(start, 4),
                      "terminal_inset_m": None if fit is None else [round(fit["negativeInsetM"], 4), round(fit["positiveInsetM"], 4)]}


def _clip_in_front(tri: np.ndarray, z0: float) -> np.ndarray:
    """The part of one triangle (3x3, mesh-local) with z >= z0: the triangle, a clipped polygon, or empty."""
    out = []
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        if a[2] >= z0:
            out.append(a)
        if (a[2] >= z0) != (b[2] >= z0):
            out.append(a + (z0 - a[2]) / (b[2] - a[2]) * (b - a))
    return np.array(out).reshape(-1, 3)


def near_temple_masks(glb_path: Path, render: dict, shape: tuple[int, int],
                      material_names: set[str]) -> tuple[np.ndarray | None, np.ndarray | None, dict]:
    """Pixel masks of the near arm in one actual-AR render: its primitives carrying ``material_names`` (the crystal) and
    its other primitives (the temple's own opaque hardware: hinge, metal strips, drawn over the crystal), both as the
    runtime drew them (``runtime_arm_vertices``: spread outward from the hinge, returned inward over the terminal band)
    and only their geometry in front of the runtime's temple clip for that side (``temple_clip_zm``), triangles cut at
    the clip plane. The near arm is the temple part on the camera's side of the asset's X (camera origin brought into
    mesh space through the row's own view and mesh_to_world; the harness's +35 deg yaw turns -X, temple_L, toward the
    camera). (crystal, hardware, info) with info ``{near_temple, clip_zm, arm_shape}``; crystal None when no translucent
    primitive lies on that side, hardware None when the arm carries none."""
    from reconstruction.mesh import load_glb_bytes
    spatial = render.get("spatial") or {}
    cam = render.get("camera") or {}
    lenses = spatial.get("lenses") or []
    if not lenses or not cam.get("projection_matrix"):
        return None, None, {}
    raw = Path(glb_path).read_bytes()
    mesh = load_glb_bytes(raw)
    view = _mat4(cam.get("view_matrix") or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    model_view = view @ _mat4(lenses[0]["mesh_to_world"])
    matrix = _mat4(cam["projection_matrix"]) @ model_view
    W, H = int(cam.get("width", shape[1])), int(cam.get("height", shape[0]))
    camera_x = float((np.linalg.inv(model_view) @ np.array([0.0, 0.0, 0.0, 1.0]))[0])
    side = -1 if camera_x < 0 else 1
    z0 = temple_clip_zm(render)[side]
    crystal, hardware = [], []
    for p in mesh.parts or []:
        if str(p.get("name") or "") not in TEMPLE_PARTS:
            continue
        faces = mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]
        if len(faces) and (-1 if mesh.vertices[faces.ravel(), 0].mean() < 0 else 1) == side:
            (crystal if str(p.get("material") or "") in material_names else hardware).append((str(p["name"]), faces))
    vertices, shape_info = runtime_arm_vertices(mesh, raw, render)
    info = {"near_temple": "+".join(sorted({n for n, _ in crystal})) or None, "clip_zm": round(z0, 4), "arm_shape": shape_info}
    if not crystal:
        return None, None, info
    px, ok = project_pixels(vertices, matrix, W, H)

    def draw_parts(parts) -> np.ndarray:
        img = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(img)
        for _, faces in parts:
            for tri in faces:
                z = vertices[tri, 2]
                if (z >= z0).all():
                    if ok[tri].all():
                        draw.polygon([tuple(px[i]) for i in tri], fill=255)
                elif (z >= z0).any():
                    poly = _clip_in_front(vertices[tri], z0)
                    ppx, pok = project_pixels(poly, matrix, W, H)
                    if pok.all() and len(np.unique(np.round(ppx, 6), axis=0)) >= 3:
                        draw.polygon([tuple(q) for q in ppx], fill=255)
        return np.asarray(img) > 0
    return draw_parts(crystal), (draw_parts(hardware) if hardware else None), info


def translucent_materials(materials_json: Path) -> list[str]:
    mats = json.loads(Path(materials_json).read_text(encoding="utf-8")).get("materials", {})
    return sorted(n for n, s in mats.items() if s.get("kind") == "translucent")


def _front_render(out_dir: Path, view: str = "front") -> tuple[np.ndarray, dict] | None:
    """The actual-AR render of one view (the front by default) from an archeck output directory: (RGB float image, render row)."""
    return actual_ar_render(Path(out_dir), view)


def render_fixtures(glb_path: Path, obs_dir: Path, materials_json: Path, width_mm: float, *,
                    fixtures: dict[str, str] = FIXTURES, timeout_s: int = 900) -> dict:
    """The two solid-fixture harness runs of one candidate, made once and shared: the lens colour needs their front view
    for EVERY lens (lens_colour.lens_colour_metric), the see-through measure for a translucent frame, and the angled view
    too when the temples are translucent. ``not_applicable`` (no harness start) without a lens or a translucent material;
    ``render_failed`` names the fixture whose run was not runtime compatible; else ``rendered`` with the output directory
    per fixture key."""
    mats = json.loads(Path(materials_json).read_text(encoding="utf-8")).get("materials", {})
    names = sorted(n for n, s in mats.items() if s.get("kind") == "translucent")
    if not names and not any(s.get("kind") == "lens" for s in mats.values()):
        return {"status": "not_applicable"}
    try:
        temple_names = translucent_temple_materials(glb_path, set(names)) if names else []
    except Exception:                                       # noqa: BLE001 - frame_see_through_metric records it for the temples
        temple_names = []
    views = FRONT_VIEW + ((TEMPLE_VIEW,) if temple_names else ())
    dirs = {}
    for key, colour in fixtures.items():
        out = Path(obs_dir) / f"ar_see_through_{key}"
        r = archeck.run({"candidate": glb_path}, out, ar_views=views, width_mm={"candidate": float(width_mm)},
                        background="solid", background_color=colour, timeout_s=timeout_s)
        m = r["models"].get("candidate", {})
        if not m.get("runtime_compatible"):
            return {"status": "render_failed", "fixture": key, "error": m.get("error")}
        dirs[key] = str(out)
    return {"status": "rendered", "fixtures": dict(fixtures), "dirs": dirs, "views": [v["id"] for v in views]}


def translucent_temple_materials(glb_path: Path, names: set[str]) -> list[str]:
    """The translucent materials (``names``) that the exported temple primitives carry; empty for opaque temples."""
    from reconstruction.mesh import load_glb_bytes
    mesh = load_glb_bytes(Path(glb_path).read_bytes())
    return sorted({str(p.get("material") or "") for p in mesh.parts or []
                   if str(p.get("name") or "") in TEMPLE_PARTS and str(p.get("material") or "") in names})


def frame_see_through_metric(glb_path: Path, obs_dir: Path, materials_json: Path, width_mm: float, *,
                             fixtures: dict[str, str] = FIXTURES, timeout_s: int = 900, rendered: dict | None = None) -> dict:
    """The metric for one observed candidate; ``not_applicable`` unless a translucent material is present. When the
    temples carry one too, the same two harness runs also render the angled view and ``temple_see_through`` is measured
    there (the arms are behind the front head-on); it is a measurement beside the front's, never fatal to it.
    ``rendered``: the runs ``render_fixtures`` already made (observe.py makes them once for the lens colour too); None
    makes them here."""
    names = translucent_materials(materials_json)
    if not names:
        return {"status": "not_applicable"}
    obs_dir = Path(obs_dir)
    try:
        temple_names = translucent_temple_materials(glb_path, set(names))
    except Exception as e:                                  # noqa: BLE001 - recorded, the front's number still comes out
        temple_names, temple = [], {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    else:
        temple = {"status": "not_applicable"} if not temple_names else {"status": "pending"}
    if rendered is None:
        rendered = render_fixtures(glb_path, obs_dir, materials_json, width_mm, fixtures=fixtures, timeout_s=timeout_s)
    if rendered.get("status") != "rendered":
        return {"status": rendered.get("status") or "render_failed", "fixture": rendered.get("fixture"), "error": rendered.get("error"),
                "translucent_materials": names}
    fixtures = rendered["fixtures"]
    renders: dict[str, tuple[np.ndarray, dict, Path]] = {}
    angled: dict[str, tuple[np.ndarray, dict] | None] = {}
    angled_paths: dict[str, str] = {}
    for key in fixtures:
        out = Path(rendered["dirs"][key])
        fr = _front_render(out)
        if fr is None:
            return {"status": "no_front_render", "fixture": key, "translucent_materials": names}
        renders[key] = (fr[0], fr[1], out / fr[1]["filename"])
        if temple_names:
            angled[key] = _front_render(out, view=TEMPLE_VIEW["id"])
            if angled[key] is not None:
                angled_paths[key] = str(out / angled[key][1]["filename"])
    (key_a, (img_a, render_a, path_a)), (key_b, (img_b, render_b, path_b)) = list(renders.items())[:2]
    if img_a.shape != img_b.shape:
        return {"status": "render_mismatch", "translucent_materials": names}
    from scipy import ndimage
    shape = img_a.shape[:2]
    fa, fb = hex_rgb(fixtures[key_a]), hex_rgb(fixtures[key_b])
    if temple_names:
        try:
            temple = _temple_see_through(glb_path, temple_names, angled.get(key_a), angled.get(key_b), fa, fb)
        except Exception as e:                              # noqa: BLE001 - recorded, the front's number still comes out
            temple = {"status": "failed", "error": f"{type(e).__name__}: {e}", "translucent_materials": temple_names}
        if temple.get("status") == "measured":
            temple["renders"] = {k: angled_paths[k] for k in (key_a, key_b) if k in angled_paths}
    lens_all = np.zeros(shape, bool)
    for m in lens_masks_in_render(glb_path, render_a, shape).values():
        lens_all |= m
    frame = frame_mask_in_render(glb_path, render_a, shape, set(names))
    if frame is None:
        return {"status": "no_frame_projection", "translucent_materials": names, "temple_see_through": temple}
    core = frame & ~ndimage.binary_dilation(lens_all, iterations=2)
    core = ndimage.binary_erosion(core, iterations=1)
    n = int(core.sum())
    if n < MIN_FRAME_PIXELS:
        return {"status": "no_frame_pixels", "frame_pixels": n, "translucent_materials": names, "temple_see_through": temple}
    see_through, per_channel = _see_through_of(img_a, img_b, core, fa, fb)
    return {"status": "measured", "see_through": see_through, "per_channel": per_channel,
            "frame_rgb_on_skin": [round(float(x), 1) for x in img_a[core].mean(0)],
            "frame_rgb_on_blue": [round(float(x), 1) for x in img_b[core].mean(0)],
            "frame_pixels": n, "fixtures": {key_a: fixtures[key_a], key_b: fixtures[key_b]},
            "renders": {key_a: str(path_a), key_b: str(path_b)}, "translucent_materials": names,
            "temple_see_through": temple,
            "note": "share of the fixture colour difference showing through the front's frame pixels in the runtime's front render "
                    "(0 opaque, 1 clear); crystal reads about 0.6 and above, translucent acetate 0.2..0.6"}


TEMPLE_SUMMARY_KEYS = ("see_through", "per_channel", "temple_rgb_on_skin", "temple_pixels", "view", "near_temple")


def temple_summary(result: dict | None) -> dict | None:
    """The ``summary.temple_see_through`` entry for one ``frame_see_through_metric`` result, beside the summary's
    ``frame_see_through`` (the caller, observe.py, writes both). None - the key stays absent - only when the temples are
    opaque (``not_applicable``) or no temple measurement was attempted; a measurement gives its compact numbers; any
    other status of translucent temples (``no_temple_pixels``: the visible strips are too thin, ``no_temple_render``,
    a failure: recorded, never fatal) gives ``{status}`` plus the ``error`` it recorded, so the author can tell
    'translucent but unmeasured' from 'opaque'."""
    t = (result or {}).get("temple_see_through")
    if not isinstance(t, dict) or t.get("status") in (None, "not_applicable"):
        return None
    if t["status"] == "measured":
        return {k: t[k] for k in TEMPLE_SUMMARY_KEYS if k in t}
    return {"status": t["status"], **({"error": t["error"]} if "error" in t else {})}


def _see_through_of(img_a: np.ndarray, img_b: np.ndarray, core: np.ndarray, fa: np.ndarray, fb: np.ndarray) -> tuple[float, list]:
    """The see-through share and its per-channel values over one pixel set of the two fixture renders."""
    dfix = fa - fb
    dpx = (img_a[core] - img_b[core]).mean(0)
    per_channel = np.clip(dpx / np.where(np.abs(dfix) < 1e-6, np.nan, dfix), 0.0, 1.0)
    see_through = float(np.clip(np.abs(dpx).sum() / np.abs(dfix).sum(), 0.0, 1.0))
    return round(see_through, 3), [None if not np.isfinite(x) else round(float(x), 3) for x in per_channel]


def _temple_see_through(glb_path: Path, temple_names: list[str], render_a: tuple[np.ndarray, dict] | None,
                        render_b: tuple[np.ndarray, dict] | None, fa: np.ndarray, fb: np.ndarray) -> dict:
    """``temple_see_through``: the see-through measure over the near arm's primitives carrying a translucent material in
    the angled view's two fixture renders (``near_temple_masks``: the arm as the runtime drew it, in front of its clip;
    the arm's own opaque hardware removed: it is drawn over the crystal; frame and lens pixels removed: the front
    occludes the arms behind it; pixels at the bare fixture colour in both renders removed: the head's occluders hide the
    arm there); ``no_temple_render`` when the harness gave no angled render, ``no_temple_pixels`` when the visible strips
    are too thin."""
    if render_a is None or render_b is None:
        return {"status": "no_temple_render", "view": TEMPLE_VIEW["id"], "translucent_materials": temple_names}
    (img_a, ra), (img_b, rb) = render_a, render_b
    if img_a.shape != img_b.shape:
        return {"status": "render_mismatch", "view": TEMPLE_VIEW["id"], "translucent_materials": temple_names}
    shape = img_a.shape[:2]
    temples, hardware, near = near_temple_masks(glb_path, ra, shape, set(temple_names))
    if temples is None:
        return {"status": "no_temple_projection", "view": TEMPLE_VIEW["id"], "translucent_materials": temple_names, **near}
    from scipy import ndimage
    lens_all = np.zeros(shape, bool)
    for m in lens_masks_in_render(glb_path, ra, shape).values():
        lens_all |= m
    frame_all = frame_mask_in_render(glb_path, ra, shape, runtime_shape=True)   # every frame primitive, hardware included: it occludes
    core = temples & ~ndimage.binary_dilation(lens_all, iterations=2)
    if frame_all is not None:
        core &= ~frame_all
    core = ndimage.binary_erosion(core, iterations=1)
    # the arm's own opaque hardware is drawn over the crystal: removed after the silhouette erosion, since its projection
    # overstates what is drawn (on r0002 319 of its 462 pixels show crystal in the ground truth, the strip being partly
    # behind the crystal from this side) and a ring eroded around it drops drawn crystal: 0.885 after, 0.876 before,
    # against the ground truth's 0.886
    n_hardware = 0
    if hardware is not None:
        n_hardware = int((core & hardware).sum())
        core &= ~hardware
    bare = core & (np.abs(img_a - fa).sum(-1) <= BARE_FIXTURE_TOLERANCE) & (np.abs(img_b - fb).sum(-1) <= BARE_FIXTURE_TOLERANCE)
    core &= ~bare
    n, n_bare = int(core.sum()), int(bare.sum())
    if n < MIN_FRAME_PIXELS:
        return {"status": "no_temple_pixels", "view": TEMPLE_VIEW["id"], "temple_pixels": n, "bare_fixture_pixels": n_bare,
                "hardware_pixels": n_hardware, "translucent_materials": temple_names, **near}
    see_through, per_channel = _see_through_of(img_a, img_b, core, fa, fb)
    return {"status": "measured", "view": TEMPLE_VIEW["id"], "see_through": see_through, "per_channel": per_channel,
            "temple_rgb_on_skin": [round(float(x), 1) for x in img_a[core].mean(0)],
            "temple_rgb_on_blue": [round(float(x), 1) for x in img_b[core].mean(0)],
            "temple_pixels": n, "bare_fixture_pixels": n_bare, "hardware_pixels": n_hardware, "translucent_materials": temple_names,
            **near,
            "note": "the front's see-through measure over the near translucent arm in the runtime's angled view (its geometry "
                    "as the runtime spreads and returns it, in front of the runtime's temple clip; the arm's opaque hardware, "
                    "frame, lens and bare-fixture pixels removed); read it against frame_see_through"}
