"""Fit every supplied product photo with camera, per-photo openings and automatic matte evidence.

Generic CLI: python -m glasses3d.g3d_host.fit_views views --glb MODEL --out-dir DIR
    --image front=PHOTO --image side=PHOTO [--workers N]
Historical product reproduction helpers live in tests/fixtures/camera_products.py.

Fit non-receiving views first, then angled views using the first batch's eligible
camera winners as cross seeds; the angled views start as soon as the photos that can
seed them are fitted. One memory-bounded pool shares independent photo and
matte-alternative work. Every attempted photo and both automatic alternatives
retain their diagnostics; existing camera usability and view-sign checks apply.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from . import camera_ambiguity, cameras, fitmesh, hinge_axes, hinge_fit, pose
from . import matte as M
from .camera import CAMERA_FIELDS, Camera
from .resources import fit_workers, single_threaded_children

CAMERAS_JSON = "cameras.json"
CONTOUR_PERCENTILES = (90, 95)
MIN_USABLE_IOU = 0.5  # at least half the effective foreground union overlaps; a camera-status label, not a finish gate

# PLAN §5 labels -> candidate views. fit_best keeps the lowest common final-level loss among them.
LABEL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "front": ("front",),
    "rear": ("back",),
    "rear_threequarter": ("rear_threequarter_left", "rear_threequarter_right"),
    "side": ("right", "left"),                        # the label does not say which side
    "top": ("top", "topb"),                           # seen over the front or over the back; camera above either way
    "bottom": ("bottom", "bottomb"),                  # camera below, seen under the front or under the back
    "elevated side": ("top_oblique_left", "top_oblique_right"),
    "angled": ("angled",),
}
LABEL_PITCH_SIGN = {"top": 1, "bottom": -1, "elevated side": 1}
CROSS_RECEIVERS = ("angled",)                         # bsa fit_all: the angled view only receives cross seeds
# every accepted view kind: the labels, every exact cameras view ('top' stays the label), and spelling aliases
VIEW_KINDS: dict[str, tuple[str, ...]] = {
    **LABEL_CANDIDATES,
    **{v: (v,) for v in cameras.VIEWS if v not in LABEL_CANDIDATES},
    "top oblique": cameras.TOP_OBLIQUE_VIEWS,
    "rear three quarter": ("rear_threequarter_left", "rear_threequarter_right"),
}


def _norm_kind(kind: str) -> str:
    return " ".join(str(kind).strip().lower().replace("_", " ").replace("-", " ").split())


_KIND_TABLE = {_norm_kind(k): v for k, v in VIEW_KINDS.items()}


def view_candidates(kind: str) -> tuple[str, ...]:
    """The cameras views a view kind can mean (case, '_' and '-' are ignored: 'Elevated_Side' = 'elevated side')."""
    try:
        return _KIND_TABLE[_norm_kind(kind)]
    except KeyError:
        raise ValueError(f"unknown view kind {kind!r}; known: {', '.join(VIEW_KINDS)}") from None


def kind_pitch_sign(kind: str) -> int | None:
    """Required camera side of the horizontal plane: +1 above, -1 below, otherwise unspecified."""
    above = cameras.TOP_VIEWS + cameras.TOP_OBLIQUE_VIEWS
    candidates = view_candidates(kind)
    if all(v in above for v in candidates):
        return 1
    return -1 if all(v in cameras.BOTTOM_VIEWS for v in candidates) else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- measurement helpers
def sign_check(kind: str, cam: Camera, *, pivot_norm=None) -> str | None:
    """Refusal text when the camera's pitch sign contradicts the view kind (``kind_pitch_sign``), else None."""
    s = kind_pitch_sign(kind)
    if s is not None and cam.pitch * s <= 0:
        return (f"pitch {cam.pitch:.2f} contradicts label {kind!r} (the camera must be "
                f"{'above' if s > 0 else 'below'} the glasses)")
    if (cam.eye_x or cam.eye_y) and cam.perspective > 0:
        refusals = [cameras.eye_side_refusal(view,cam,pivot_norm) for view in view_candidates(kind)]
        if all(refusals):
            return f"off-axis eye contradicts label {kind!r}: " + "; ".join(refusals)
    return None


def camera_usability(metrics: dict) -> dict:
    """Label registration, without changing the fit objective, metric values or delivery quality gates.

    Optional-lens IoU is authoritative when present: a transparent lens's raw filled silhouette can legitimately
    disagree with the open-frame matte. Non-finite metrics never certify a usable camera.
    """
    metric = "iou_lens_optional" if "iou_lens_optional" in metrics else "iou"
    value = float(metrics[metric])
    usable = bool(np.isfinite(value) and value >= MIN_USABLE_IOU)
    return {"metric": metric, "value": value if np.isfinite(value) else None,
            "minimum_iou": MIN_USABLE_IOU, "usable": usable,
            "reason": None if usable else
            f"{metric} {value:.4f} is below the usable camera overlap {MIN_USABLE_IOU:.2f} or is non-finite; "
            "camera, matte, overlay and candidate evidence are retained; this is not a finish gate"}


def select_camera_candidate(view_kind, best, results, pivot=None):
    """Prefer already fitted candidates that pass the unchanged label and overlap checks.

    A low-loss camera below a photo labelled top cannot invalidate an available usable camera
    above it. If every candidate fails, preserve the original winner and its ordinary refusal.
    """
    checks = [(result, sign_check(view_kind, result["camera"], pivot_norm=pivot),
               camera_usability(result["metrics"])) for result in results]
    eligible = [result for result, refusal, usable in checks if refusal is None and usable["usable"]]
    selected = min(eligible, key=lambda result: result["common_loss"]) if eligible else best

    def summary(result):
        index = next(i for i, row in enumerate(results) if row is result)
        _, refusal, usable = checks[index]
        return {"id": f"view_{index + 1}", "view": result["view"], "camera": _cam(result["camera"]),
                "loss_common_level": result["common_loss"], "label_refusal": refusal, "usability": usable}

    changed = selected is not best
    return selected, {
        "schema": "g3d.camera_candidate_selection/1", "changed": changed,
        "rule": "Prefer existing candidates that pass the unchanged photo-label and camera-usability checks, "
                "then lowest common-level loss; if none pass, retain the original winner and its refusal.",
        "reason": ("usable_label_consistent_candidate_over_invalid_loss_winner" if changed else
                   "lowest_loss_among_usable_label_consistent_candidates" if eligible else
                   "no_candidate_passes_unchanged_checks"),
        "eligible_candidates": [f"view_{i + 1}" for i, (_, refusal, usable) in enumerate(checks)
                                if refusal is None and usable["usable"]],
        "original": summary(best), "selected": summary(selected)}


def contour_summary(render: np.ndarray, photo: np.ndarray, percentiles=CONTOUR_PERCENTILES) -> dict:
    """``cameras.contour_stats`` with more percentiles and both directed means (same arithmetic: the mean of the two
    directed means; pNN = the larger of the two directed NNth percentiles). inf when either mask has no edge."""
    er, ep = cameras._edge(render), cameras._edge(photo)
    if not er.any() or not ep.any():
        return {"mean": float("inf"), "render_to_photo_mean": float("inf"), "photo_to_render_mean": float("inf"),
                **{f"p{q}": float("inf") for q in percentiles}}
    dr = cameras._dist_to(ep)[er]           # render contour -> photo contour
    dp = cameras._dist_to(er)[ep]           # photo contour -> render contour
    return {"mean": float((dr.mean() + dp.mean()) / 2), "render_to_photo_mean": float(dr.mean()),
            "photo_to_render_mean": float(dp.mean()),
            **{f"p{q}": float(max(np.percentile(dr, q), np.percentile(dp, q))) for q in percentiles}}


def overlay_image(img: np.ndarray, fg: np.ndarray, render: np.ndarray, thickness: int | None = None) -> np.ndarray:
    """The photo with the matte edge (green) and the rendered silhouette edge (magenta, on top). Edges are thickened
    with the image size (1 px per ~700 px of the longer side) so they survive a downscaled view."""
    t = max(1, round(max(img.shape[:2]) / 700)) if thickness is None else int(thickness)
    ov = np.array(img[..., :3], dtype=np.uint8, copy=True)

    def thick(mask, n):
        e = cameras._edge(mask)
        return ndimage.binary_dilation(e, iterations=n) if n > 0 and e.any() else e
    ov[thick(fg, (t - 1) // 2)] = (0, 200, 0)
    ov[thick(render, t - 1)] = (255, 0, 255)
    return ov


def _cam(c: Camera) -> dict:
    return {k: float(v) for k, v in c.to_dict().items()}


def rigid_fit_marker(model_sha256: str, photo_sha256: str, camera: dict) -> dict:
    """Provenance for this fitter's fixed authored mesh; zero is a delta, not a measured opening angle."""
    camera_sha = hashlib.sha256(json.dumps(camera, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode("utf-8")).hexdigest()
    return {"schema": "g3d.rigid_camera_fit/1", "source_model_sha256": model_sha256,
            "photo_sha256": photo_sha256, "camera_sha256": camera_sha,
            "temple_delta_deg": {"+1": 0.0, "-1": 0.0}}


def articulated_fit_marker(model_sha256, photo_sha256, camera, theta, axes, observed, axis_source, initial_theta):
    """Bind a fitted pose to the exact authored model, photo, camera and axes."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()
    return {"mode": "articulated_fit", "schema": "g3d.articulated_camera_fit/1",
            "opening_calibrated": any(observed.values()),
            "meaning": "per-photo opening delta from the captured authored GLB; unobserved sides may be bounded by the photo without identifying an exact angle",
            "source_model_sha256": model_sha256, "photo_sha256": photo_sha256,
            "camera_sha256": digest(camera), "axes_sha256": digest(axes), "theta_sha256": digest(theta),
            "axis_source": axis_source, "input_theta_deg": initial_theta}


def _candidate_record(r: dict, ppm: float) -> dict:
    m = r["metrics"]
    return {"view": r["view"], "camera": _cam(r["camera"]), "loss": r["loss"], "loss_common_level": r.get("common_loss"),
            "iou": m["iou"], "contour_mean_px": m["contour_mean_px"], "px_per_mm_at_front_pivot": ppm,
            "contour_mean_mm": m["contour_mean_px"] / ppm, "sign": r["sign"],
            "winner_origin": r["refine"]["origin"], "evaluations": r["fit"].evals,
            **({"theta_deg":r['opening']['theta_deg'], "theta_observed":r['opening']['theta_observed'],
                "hinge_axes":"shared top-level hinge_axis_xz_mm",
                "opening_selection":r['opening']['opening_fit']['selected'],
                "opening_common_losses":r['opening']['opening_fit']['common_losses']}
               if 'opening' in r else {}),
            **{k: v for k, v in m.items() if k.endswith('_lens_optional') or k == 'lens_pixels_optional'},
            **({"off_axis": r["refine"]["off_axis"]} if "off_axis" in r["refine"] else {})}


def lens_loss_policy(glb_path, mesh: fitmesh.FitMesh) -> tuple[dict, list[int]]:
    """Use the seating check's construction evidence, and only the actual lens primitives of exact nodes.

    This changes the camera objective only; it never changes a seating verdict or the reported raw silhouette.
    Unknown construction, ambiguous duplicate node names, and failed checks retain the full silhouette.
    """
    from . import seat_check
    report = seat_check.check_glb(glb_path)
    construction = [{key: row.get(key) for key in ('node', 'frame_type', 'frame_type_source', 'provisional')}
                    for row in report.get('lenses', [])]
    by_node = {}
    for row in construction:
        by_node.setdefault(row['node'], []).append(row)
    identities = {}
    for name, node in zip(mesh.names, mesh.node_indices):
        identities.setdefault(name, set()).add(node)
    optional_nodes = {name for name, rows in by_node.items()
                      if len(identities.get(name, ())) == 1
                      and all(row['frame_type'] in ('rimless', 'semi_rimless') for row in rows)}
    optional = [i for i, (name, lens) in enumerate(zip(mesh.names, mesh.lens_parts))
                if lens and name in optional_nodes]
    actual_nodes = sorted({mesh.names[i] for i in optional})
    policy = {'mode': 'optional' if optional else 'full', 'optional_nodes': actual_nodes,
              'optional_faces': int(np.isin(mesh.part, optional).sum()), 'construction': construction}
    if report.get('error'):
        policy['note'] = f"construction check unavailable; full silhouette retained: {report['error']}"
    return policy, optional


def _model_record(glb_path, mesh, pivot, skip, front_parts):
    return {"path": str(glb_path), "sha256": sha256_file(Path(glb_path)), "skip": list(skip),
            "skipped": mesh.skipped, "front_parts": None if front_parts is None else list(front_parts),
            "front_pivot_mm": [float(x) for x in mesh.frame.to_mm(pivot[None])[0]],
            "frame": mesh.frame.to_dict(), "parts": len(mesh.names), "faces": int(len(mesh.F))}


def _fit_context(glb_path, skip, front_parts, mesh=None, pivot=None):
    mesh = mesh if mesh is not None else fitmesh.load_fit_mesh(glb_path, skip=skip)
    pivot = pivot if pivot is not None else mesh.front_pivot(front_parts)
    policy, optional = lens_loss_policy(glb_path, mesh)
    model = _model_record(glb_path, mesh, pivot, skip, front_parts)
    roles = pose.part_roles(glb_path)
    opening = hinge_axes.derive_axes(glb_path, model['sha256'], mesh=mesh, roles=roles)
    return {'model': model, 'lens_loss': policy, 'optional_parts': optional,
            'opening': opening, 'part_roles': roles}


def _opening_candidates(mesh, context, fg, pivot, results, ignore_faces, off_axis, initial_theta):
    """Keep rigid and articulated candidates on ONE final grid, preserving existing camera gates."""
    axes, roles = context['opening']['hinge_axis_xz_mm'], context['part_roles']
    domain = hinge_fit.OpeningDomain(mesh, roles, axes)
    input_is_physical = domain.valid({k: initial_theta.get(k, 0.) for k in axes})
    alternatives = []
    initial_mesh = pose.posed(mesh, {k: {"theta_deg": initial_theta.get(k, 0.), "axis_xz_mm": v}
                                   for k, v in axes.items()}, roles)
    for result in results:
        proposal = hinge_fit.refine(mesh, roles, axes, fg, result['camera'], result['view'], pivot,
                                    initial_theta=initial_theta, ignore_faces=ignore_faces, off_axis=off_axis)
        alternatives.append((result, proposal))
    points = []
    for result, proposal in alternatives:
        candidates = [(proposal['mesh'], proposal['camera'])]
        if input_is_physical:
            candidates.append((initial_mesh, result['camera']))
        for candidate_mesh, cam in candidates:
            points.append(cameras.project(candidate_mesh.scene().hull, cam))
    points = np.vstack(points)
    level = cameras.Level(fg, cameras.FINAL_W, 'opening_common', margin=cameras.FINAL_ROI_MARGIN,
                          include_xyxy=(*points.min(0), *points.max(0)))
    level.ignore_faces = ignore_faces
    for result, proposal in alternatives:
        rigid_loss = level.loss(initial_mesh.scene(), result['camera']) if input_is_physical else float('inf')
        articulated_loss = level.loss(proposal['mesh'].scene(), proposal['camera'])
        kept = articulated_loss < rigid_loss
        proposal['opening_fit'].update(input_pose_physical=input_is_physical,
                                      common_losses={'input_pose': float(rigid_loss) if input_is_physical else None,
                                                      'articulated': float(articulated_loss)},
                                      selected='articulated' if kept else 'input_pose')
        if kept:
            result['camera'], result['posed_mesh'] = proposal['camera'], proposal['mesh']
            vf = cameras.ViewFit(result['view'], fg, proposal['mesh'].scene(), pivot)
            vf.set_ignore(ignore_faces)
            if off_axis:
                vf.eye_limit = float(np.linalg.norm(mesh.frame.to_norm(proposal['mesh'].V), axis=1).max())
            result['sign'] = vf.pitch_twin(result['camera'])
            result['fit'].evals += vf.evals
        else:
            result['posed_mesh'] = initial_mesh
            proposal['theta_deg'] = {k: initial_theta.get(k, 0.) for k in axes}
            proposal['theta_observed'] = {k: False for k in axes}
            visible = initial_mesh.scene().render(result['camera'], fg.shape)['face_id']
            tags = pose.side_of_faces(mesh, roles)
            for key, row in proposal['opening_fit']['per_side'].items():
                sensitivity = {name:row.pop(name) for name in
                    ('camera_coupling','independent_px_per_degree','one_pixel_sensitivity_deg','sensitivity_note')
                    if name in row}
                row.update(proposed_deg=row['theta_deg'], theta_deg=proposal['theta_deg'][key],
                           selected_deg=proposal['theta_deg'][key], visibility_pixel_stride=1,
                           proposed_visible_pixels_final=row.get('visible_pixels_final'),
                           visible_pixels_final=int(np.count_nonzero((visible>=0) & (tags[np.maximum(visible,0)]==int(key)))),
                           photo_supported_pixels=int(np.count_nonzero((visible>=0) & fg &
                                                        (tags[np.maximum(visible,0)]==int(key)))),
                           proposal_sensitivity=sensitivity, identifiability='unobserved',
                           observed=False, status='input_pose_selected_on_common_loss')
                if 'local_photo_loss_increases' in row:
                    row['proposal_local_photo_loss_increases'] = row.pop('local_photo_loss_increases')
        result['opening'] = proposal
        result['common_loss'] = result['loss'] = float(min(rigid_loss, articulated_loss))
        metrics = cameras.native_metrics(result['posed_mesh'].scene(), result['camera'], fg,
                                          cameras.matte_roi(fg), ignore_faces)
        result['metrics'] = {k:v for k,v in metrics.items() if k not in ('mask', 'face_id')}
    return min(results, key=lambda r:r['common_loss']), results


def _matte_mode(requested: str | None, context: dict) -> str:
    """Resolve absence from actual optional lens faces; an explicit photo mode always wins."""
    if requested is not None:
        if not isinstance(requested, str) or requested not in M.MODES:
            raise ValueError(f'unknown matte mode {requested!r}; known: {M.MODES}')
        return requested
    return ('open_frame' if context.get('optional_parts') and context.get('lens_loss', {}).get('mode') == 'optional'
            else 'default')


# ----------------------------------------------------------------------------- one photo
class _EmptyMatteError(ValueError):
    """Direct fits reject empty input; grouped fits can retain completed alternative diagnostics."""
    fit_report = None


def _validated_candidates(view_kind, candidates):
    cands = tuple(candidates) if candidates else view_candidates(view_kind)
    bad = [v for v in cands if v not in cameras.VIEWS]
    if not cands or bad:
        raise ValueError(f"candidates {cands}: unknown views {bad}; known {cameras.VIEWS}")
    return cands


def _fit_image_mode(glb_path: str | Path, image_path: str | Path, view_kind: str, *, skip=(), front_parts=None,
              candidates: tuple[str, ...] | None = None, n_refine: int | None = None,
              cross_winners: dict[str, dict] | None = None, overlay_path: str | Path | None = None,
              matte_mode: str | None = None, _model_context: dict | None = None, off_axis: bool = False,
              initial_theta: dict | None = None, fit_openings: bool = True) -> dict:
    """Fit one photo to the model (runs in a worker process; returns plain data). ``candidates`` overrides the kind's
    views; ``cross_winners`` = {view: camera dict} of already fitted photos, used as cross seeds for a
    ``CROSS_RECEIVERS`` candidate. An absent ``matte_mode`` uses open_frame only for actual optional lens faces;
    explicit modes are authoritative. The record stores the resolved mode so frozen evidence uses the same matte.
    Fits use the validated seven-parameter search by default; ``off_axis=True`` adds a bounded lateral-eye
    continuation. Better silhouette agreement does not certify camera-parameter accuracy. The record's ``model``
    entry describes the mesh (``fit_views`` lifts it out)."""
    t0 = time.perf_counter()
    cands = _validated_candidates(view_kind, candidates)
    nr = cameras.N_COARSE_POWELL if n_refine is None else int(n_refine)
    mesh = fitmesh.load_fit_mesh(glb_path, skip=tuple(skip))
    pivot = mesh.front_pivot(None if front_parts is None else tuple(front_parts))
    context = _model_context or _fit_context(glb_path, skip, front_parts, mesh, pivot)
    model = _model_record(glb_path, mesh, pivot, skip, front_parts)
    if model != context['model']:
        raise ValueError('the model changed since shared camera-fit preparation')
    matte_mode = _matte_mode(matte_mode, context)
    ignore_faces = np.isin(mesh.part, context['optional_parts']) if context['optional_parts'] else None
    axes = ((context.get('opening') or {}).get('hinge_axis_xz_mm') or {}) if fit_openings else {}
    initial_theta = {f'{int(k):+d}': float(v) for k,v in (initial_theta or {}).items()}
    if set(initial_theta)-set(axes):
        raise ValueError('initial openings require recognized, signed hinge axes')
    initial_mesh = pose.posed(mesh, {k:{'theta_deg':initial_theta.get(k, 0.),'axis_xz_mm':v}
                                   for k,v in axes.items()}, context.get('part_roles') or {})
    scene = initial_mesh.scene()
    photo_sha = sha256_file(Path(image_path))
    img = M.load_photo(image_path)
    fg = M.matte(img, mode=matte_mode)
    policy = context['lens_loss']
    if matte_mode in ('glossy_floor', 'glossy_open_frame'):
        # The floor matte deliberately leaves background-like lens interiors open.
        # Lens pixels present in the photo still count; only absent interiors are optional.
        optional_parts = [i for i, lens in enumerate(mesh.lens_parts) if lens]
        if optional_parts:
            ignore_faces = np.isin(mesh.part, optional_parts)
            policy = {**policy, 'mode': 'optional',
                      'optional_nodes': sorted({mesh.names[i] for i in optional_parts}),
                      'optional_faces': int(ignore_faces.sum()),
                      'reason': 'Floor matte leaves backdrop-like lens interiors open; observed lens pixels remain required.'}
    if not fg.any():
        raise _EmptyMatteError(f"{image_path}: the matte is empty (no foreground against the border background model)")
    cross, n_cross, sources = None, 0, []
    receivers = [v for v in cands if v in CROSS_RECEIVERS]
    if receivers and cross_winners:
        winners = {v: Camera.from_dict(c) for v, c in cross_winners.items()}
        cross = {v: cameras.cross_seeds(v, winners) for v in receivers}
        n_cross = sum(len(cameras.wrap_seeds(v, s)) for v, s in cross.items())
        sources = sorted(winners)
    best, results = cameras.fit_best(fg, scene, pivot, [(v, None) for v in cands], nr, cross=cross,
                                     ignore_faces=ignore_faces, **({"off_axis": True} if off_axis else {}))
    if axes:
        best, results = _opening_candidates(mesh, context, fg, pivot, results, ignore_faces, off_axis, initial_theta)
    best, candidate_selection = select_camera_candidate(view_kind, best, results, pivot)
    if axes:
        scene = best['posed_mesh'].scene()
    pivot_mm = mesh.frame.to_mm(pivot[None])[0]
    ppm = {id(r): cameras.px_per_mm_at(r["camera"], mesh.frame, pivot_mm) for r in results}
    cam, m, p = best["camera"], best["metrics"], ppm[id(best)]
    roi = cameras.matte_roi(fg)                       # the ROI native_metrics measured in (fit_view)
    x0, y0, x1, y1 = roi
    cs = contour_summary(scene.render(cam, fg.shape, 1, roi)["mask"], fg[y0:y1, x0:x1])
    refusal = sign_check(view_kind, cam, pivot_norm=pivot)  # orientation AND the actual eye respect the label
    usability = camera_usability(m)
    flags = [f for f in ((best["sign"] or {}).get("flag"),) if f]
    if refusal:
        flags.append("label_sign_contradicted")
    if not usability["usable"]:
        flags.append("insufficient_camera_overlap")
    if (best["refine"].get("off_axis") or {}).get("candidate_at_eye_bound"):
        flags.append("off_axis_candidate_at_domain_bound")
    try:
        orientation, orientation_overlays = camera_ambiguity.evidence(
            best, results, fg, scene, pivot, ignore_faces, off_axis=off_axis)
    except Exception as exc:
        # Optional diagnostics cannot discard the completed fit or change any existing gate result.
        orientation, orientation_overlays = {
            "schema": "g3d.camera_orientation/1", "status": "unavailable", "selection_changed": False,
            "note": "Orientation alternatives could not be evaluated; the installed camera is retained. "
                    "Its physical orientation has not been independently established.",
            "error": {"type": type(exc).__name__, "message": str(exc)}, "candidates": []}, []
    if orientation is not None:
        orientation.update(selection_changed=candidate_selection["changed"],
                           candidate_selection_reason=candidate_selection["reason"],
                           diagnostic_changes_selection=False)
        orientation["note"] = orientation.get("note", "") + (
            " The installed camera was chosen by the step reported in candidate_selection; "
            "these orientation diagnostics do not override that choice.")
    if orientation is not None and refusal is None:
        flags.append("camera_orientation_unresolved" if orientation["status"] == "unresolved"
                     else "camera_orientation_unavailable")
    rec = {
        "image": str(image_path), "image_sha256": photo_sha, "shape": list(fg.shape),
        "view_kind": view_kind, "candidates": list(cands), "n_refine": nr,
        "status": "refused" if refusal else "ok" if usability["usable"] else "unusable",
        "refusal": refusal or usability["reason"], "flags": flags, "usability": usability,
        "code": "E_VIEW_SIGN" if refusal else None if usability["usable"] else "E_CAMERA_UNUSABLE",
        "view": best["view"], "winner_origin": best["refine"]["origin"], "camera": _cam(cam),
        "loss": best["loss"], "loss_common_level": best["common_loss"],
        "iou": m["iou"], "contour_mean_px": m["contour_mean_px"], "contour_p90_px": cs["p90"],
        "contour_p95_px": m["contour_p95_px"],
        "contour_render_to_photo_mean_px": cs["render_to_photo_mean"],
        "contour_photo_to_render_mean_px": cs["photo_to_render_mean"],
        "outer_contour_mean_px": m["outer_contour_mean_px"], "outer_contour_p95_px": m["outer_contour_p95_px"],
        "px_per_mm_at_front_pivot": p, "contour_mean_mm": m["contour_mean_px"] / p, "contour_p90_mm": cs["p90"] / p,
        "contour_p95_mm": m["contour_p95_px"] / p, "outer_contour_mean_mm": m["outer_contour_mean_px"] / p,
        "roi": [int(v) for v in roi], "photo_only_px": m["photo_only_px"], "render_only_px": m["render_only_px"],
        "extent": m["extent"], "sign": best["sign"],
        "all_candidates": [_candidate_record(r, ppm[id(r)]) for r in results],
        "cross_seeds": n_cross, "cross_sources": sources,
        "matte": {"foreground_px": int(fg.sum()), "shape": list(fg.shape)},
        "matte_mode": matte_mode, "lens_loss": policy,
        **{k: v for k, v in m.items() if k.endswith('_lens_optional') or k == 'lens_pixels_optional'},
        "overlay": None if overlay_path is None else str(overlay_path),
        "model": model,
        "candidate_selection": candidate_selection,
        **({"camera_ambiguity": orientation} if orientation is not None else {}),
    }
    if off_axis:
        rec["off_axis"] = dict(best["refine"]["off_axis"])
        if axes:
            limit = scene.max_radius
            rec['off_axis'].update(camera_after_opening_fit=rec['camera'],
                opening_fit_eye_domain=[-limit,limit],
                opening_fit_at_eye_bound=any(abs(v) >= limit*(1-1e-5) for v in (cam.eye_x,cam.eye_y)))
            if rec['off_axis']['opening_fit_at_eye_bound']:
                flags.append('opening_fit_candidate_at_eye_bound')
    if sha256_file(Path(image_path)) != photo_sha or sha256_file(Path(glb_path)) != model['sha256']:
        raise ValueError('photo or model changed during fitting')
    if axes:
        opening = best['opening']
        rec.update(theta_deg=opening['theta_deg'], theta_observed=opening['theta_observed'],
                   hinge_axis_xz_mm=axes, opening_fit=opening['opening_fit'])
        rec['opening_fit']['unavailable'] = context['opening']['unavailable']
        rec['pose_provenance'] = articulated_fit_marker(model['sha256'], photo_sha, rec['camera'],
            rec['theta_deg'], axes, rec['theta_observed'], context['opening']['axis_source'],
            {k:initial_theta.get(k, 0.) for k in axes})
    elif context.get('opening') and fit_openings:
        rec['opening_fit'] = {'schema':'g3d.opening_fit/1', 'status':'unavailable',
                              'unavailable':context['opening']['unavailable'],
                              'note':'Rigid camera retained; no unrecognized hinge angle was invented.'}
    if rec["status"] == "ok" and not axes:
        rec["rigid_fit"] = rigid_fit_marker(rec["model"]["sha256"], rec["image_sha256"], rec["camera"])
    if overlay_path is not None:
        op = Path(overlay_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(overlay_image(img, fg, scene.render(cam, fg.shape, 1)["mask"])).save(op)
        for candidate, candidate_scene, candidate_camera in orientation_overlays:
            path = op.with_name(f"{op.stem}_orientation_{candidate['id']}{op.suffix}")
            try:
                Image.fromarray(overlay_image(img, fg, candidate_scene.render(candidate_camera, fg.shape, 1)["mask"])).save(path)
                candidate["overlay_sha256"] = sha256_file(path)
                candidate["overlay"] = path.name
            except Exception as exc:
                candidate["overlay_error"] = {"type": type(exc).__name__, "message": str(exc)}
    rec["seconds"] = time.perf_counter() - t0
    return rec


AUTO_MATTE_RULE = ("Fit the base and floor mattes identically; prefer unchanged camera-gate passes, "
                   "then higher native silhouette IoU (lens-optional when applicable); exact ties choose the base matte.")


def _matte_fit_summary(row):
    fields = ('status','code','refusal','flags','usability','matte_mode','matte','camera','iou','iou_lens_optional',
              'loss','loss_common_level','contour_mean_px','contour_mean_mm','theta_deg','theta_observed',
              'n_refine','cross_sources','seconds','overlay')
    return {key: row.get(key) for key in fields}


def fit_image(glb_path: str | Path, image_path: str | Path, view_kind: str, *, skip=(), front_parts=None,
              candidates: tuple[str, ...] | None = None, n_refine: int | None = None,
              cross_winners: dict[str, dict] | None = None, overlay_path: str | Path | None = None,
              matte_mode: str | None = None, _model_context: dict | None = None, off_axis: bool = False,
              initial_theta: dict | None = None, fit_openings: bool = True) -> dict:
    """Fit with an explicit matte, existing open-frame construction, or both automatic alternatives.

    Automatic selection uses only the two fitted records and the fixed rule above.
    Every alternative is retained even when it fails; no product/photo identity is a selector.
    """
    started = time.perf_counter()
    candidates = _validated_candidates(view_kind, candidates)
    context = _model_context or _fit_context(glb_path, tuple(skip), front_parts)
    resolved = _matte_mode(matte_mode, context)
    automatic = matte_mode is None
    kwargs = dict(skip=skip, front_parts=front_parts, candidates=candidates, n_refine=n_refine,
                  cross_winners=cross_winners, _model_context=context, off_axis=off_axis,
                  initial_theta=initial_theta, fit_openings=fit_openings)
    if not automatic:
        row = _fit_image_mode(glb_path,image_path,view_kind,matte_mode=resolved,overlay_path=overlay_path,**kwargs)
        row['matte_selection'] = {
            'schema':'g3d.matte_selection/1','mode':'explicit',
            'chosen':resolved,'rule':AUTO_MATTE_RULE,
            'reason':'Explicit photo matte retained.',
            'alternatives':{resolved:_matte_fit_summary(row)},'seconds':time.perf_counter()-started}
        return row
    photo_sha = sha256_file(Path(image_path))
    alternatives = {}
    empty_inputs = []
    base_mode = resolved
    floor_mode = 'glossy_open_frame' if resolved == 'open_frame' else 'glossy_floor'
    for mode in (base_mode, floor_mode):
        path = (Path(overlay_path).with_name(f'{Path(overlay_path).stem}_matte_{mode}{Path(overlay_path).suffix}')
                if overlay_path is not None else None)
        tick = time.perf_counter()
        try:
            row = _fit_image_mode(glb_path,image_path,view_kind,matte_mode=mode,overlay_path=path,**kwargs)
        except Exception as exc:
            if isinstance(exc, _EmptyMatteError):
                empty_inputs.append(exc)
            row = _failed_photo(image_path,view_kind,{**kwargs,'matte_mode':mode},exc)
            row['seconds'] = time.perf_counter()-tick
            row['model'] = context['model']
        alternatives[mode] = row
    row = _assemble_mattes(glb_path, image_path, context, photo_sha, alternatives,
                           base_mode, floor_mode, overlay_path, started)
    if len(empty_inputs) == len(alternatives):
        empty_inputs[0].fit_report = row
        raise empty_inputs[0]
    return row


def _assemble_mattes(glb_path, image_path, context, photo_sha, alternatives,
                     base_mode, floor_mode, overlay_path, started):
    """One selection rule for sequential and process-pool alternative fits."""
    if (sha256_file(Path(image_path)) != photo_sha or
            sha256_file(Path(glb_path)) != context['model']['sha256'] or
            any(value.get('image_sha256') not in (None, photo_sha) or
                value.get('model', context['model']) != context['model']
                for value in alternatives.values())):
        raise ValueError('photo or model changed between matte alternatives')

    def rank(mode):
        row = alternatives[mode]
        value = row.get('iou_lens_optional', row.get('iou'))
        score = float(value) if isinstance(value,(int,float)) and np.isfinite(value) else -np.inf
        return row.get('status') == 'ok', score, mode == base_mode

    selected = max(alternatives, key=rank)
    row = dict(alternatives[selected])
    if overlay_path is not None and row.get('overlay'):
        shutil.copyfile(row['overlay'],overlay_path)
        row['overlay'] = str(overlay_path)
    ranks = {mode:rank(mode) for mode in alternatives}
    if ranks[base_mode][0] != ranks[floor_mode][0]:
        reason = f'{selected} alone passes the unchanged camera checks.'
    elif ranks[base_mode][1] == ranks[floor_mode][1]:
        reason = f'Equal native silhouette IoU (lens-optional where applicable, or neither finite); {base_mode} wins the fixed tie rule.'
    else:
        reason = f'{selected} has higher native silhouette IoU (lens-optional where applicable) among alternatives with equal camera-check status.'
    row['matte_selection'] = {'schema':'g3d.matte_selection/1','mode':'automatic_fit_comparison',
        'chosen':selected,'rule':AUTO_MATTE_RULE,'reason':reason,
        'alternatives':{mode:_matte_fit_summary(value) for mode,value in alternatives.items()},
        'seconds':time.perf_counter()-started}
    row['matte_alternatives'] = alternatives
    row['seconds'] = time.perf_counter()-started
    return row


# ----------------------------------------------------------------------------- every photo of a model
def _run(job):
    args, kwargs = job
    kwargs = dict(kwargs)
    alternative = kwargs.pop('_matte_alternative', False)
    started = time.perf_counter()
    try:
        return (_fit_image_mode if alternative else fit_image)(*args, **kwargs)
    except _EmptyMatteError as exc:
        row = exc.fit_report if exc.fit_report is not None else _failed_photo(args[1], args[2], kwargs, exc)
    except Exception as exc:
        row = _failed_photo(args[1], args[2], kwargs, exc)
    if alternative:
        row.update(seconds=time.perf_counter()-started, model=kwargs['_model_context']['model'])
    return row


def _failed_photo(image_path, kind, kwargs, exc):
    """Keep every attempted photo, including decodable empty mattes and wholly unreadable inputs."""
    mode = _matte_mode(kwargs.get('matte_mode'), kwargs.get('_model_context') or {})
    row = {'image': str(image_path), 'image_sha256': None, 'view_kind': kind,
           'candidates': list(kwargs.get('candidates') or view_candidates(kind)), 'n_refine': kwargs.get('n_refine'),
           'status': 'failed', 'code': 'E_PHOTO_FIT', 'refusal': f'{type(exc).__name__}: {exc}',
           'error': {'type': type(exc).__name__, 'message': str(exc)}, 'flags': [], 'camera': None, 'view': None,
           'shape': None, 'matte_mode': mode, 'matte': None, 'roi': None, 'overlay': None,
           'lens_loss': (kwargs.get('_model_context') or {}).get('lens_loss')}
    try:
        row['image_sha256'] = sha256_file(Path(image_path))
        image = M.load_photo(image_path)
        row['shape'] = list(image.shape[:2])
        mask = M.matte(image, mode=mode)
        row['matte'] = {'foreground_px': int(mask.sum()), 'shape': list(mask.shape)}
        if mask.any():
            row['roi'] = [int(v) for v in cameras.matte_roi(mask)]
    except Exception:
        pass  # The recorded original failure remains authoritative; unavailable evidence is explicit None.
    return row


def _map(jobs, workers):
    if not jobs:
        return []
    workers = min(fit_workers(workers), len(jobs))
    if workers <= 1 or len(jobs) == 1:
        return [_run(j) for j in jobs]
    with single_threaded_children(), ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_run, jobs))


def _map_photos(jobs, workers):
    """Share one bounded pool across independent photo/matte alternatives, including one-photo batches."""
    if workers <= 1:
        return _map(jobs, workers)
    started = time.perf_counter()
    attempts, groups = _photo_attempts(jobs)
    fitted = _map(attempts, workers)
    return [_photo_record(job, group, fitted, started) for job, group in zip(jobs, groups)]


def _photo_attempts(jobs):
    """Pool attempts for photo jobs: an automatic-matte photo becomes its base and floor alternatives."""
    attempts, groups = [], []
    for args, kwargs in jobs:
        context = kwargs['_model_context']
        base = _matte_mode(kwargs.get('matte_mode'), context)
        floor = 'glossy_open_frame' if base == 'open_frame' else 'glossy_floor'
        try:
            digest = sha256_file(Path(args[1]))
        except OSError:
            digest = None  # Ordinary failure reporting retains the unreadable photo.
        automatic = kwargs.get('matte_mode') is None and digest is not None
        first = len(attempts)
        if automatic:
            overlay = kwargs.get('overlay_path')
            for mode in (base, floor):
                path = (Path(overlay).with_name(f'{Path(overlay).stem}_matte_{mode}{Path(overlay).suffix}')
                        if overlay is not None else None)
                attempts.append((args, {**kwargs, 'matte_mode': mode, 'overlay_path': path,
                                        '_matte_alternative': True}))
        else:
            attempts.append((args, kwargs))
        groups.append((first, automatic, digest, base, floor))
    return attempts, groups


def _photo_record(job, group, fitted, started):
    """One photo's record from its fitted attempts (``_photo_attempts`` group); one selection rule."""
    (args, kwargs), (first, automatic, digest, base, floor) = job, group
    if not automatic:
        return fitted[first]
    try:
        return _assemble_mattes(args[0], args[1], kwargs['_model_context'], digest,
            dict(zip((base, floor), fitted[first:first+2])), base, floor, kwargs.get('overlay_path'), started)
    except Exception as exc:
        return _failed_photo(args[1], args[2], kwargs, exc)


def _map_photo_stages(first_jobs, sources, make_second, n_second, workers):
    """``_map_photos`` of the first batch, then of the receiving batch, in ONE bounded pool.

    The receivers' jobs (``make_second({index: record})`` of the ``sources``, the first-batch photos that can seed
    them) are made and queued as soon as those photos are fitted; the other first-batch photos keep running beside
    them. The jobs and records equal the two sequential batches; only start times (and batch-relative seconds) change.
    In the 10-22 INVU run the angled photo waited about six minutes for the top photo, which never seeds it.
    """
    started = time.perf_counter()
    attempts, groups = _photo_attempts(first_jobs)
    workers = min(fit_workers(workers), len(attempts) + 2 * n_second)
    firsts, fitted = [None] * len(first_jobs), [None] * len(attempts)
    with single_threaded_children(), ProcessPoolExecutor(max_workers=workers) as ex:
        pending = [ex.submit(_run, attempt) for attempt in attempts]

        def record(i):
            first, automatic = groups[i][:2]
            for k in range(first, first + (2 if automatic else 1)):
                fitted[k] = pending[k].result()
            firsts[i] = _photo_record(first_jobs[i], groups[i], fitted, started)

        for i in sources:
            record(i)
        second_jobs = make_second({i: firsts[i] for i in sources})
        second_started = time.perf_counter()
        later, later_groups = _photo_attempts(second_jobs)
        queued = [ex.submit(_run, attempt) for attempt in later]
        for i in range(len(first_jobs)):
            if firsts[i] is None:
                record(i)
        later_fitted = [future.result() for future in queued]
    seconds = [_photo_record(job, group, later_fitted, second_started) for job, group in zip(second_jobs, later_groups)]
    return firsts, seconds


def _per_name(value, names, what: str) -> dict:
    """An int (every name) or {name: value} -> {name: value or None}; unknown names raise."""
    if value is None:
        return {n: None for n in names}
    if isinstance(value, dict):
        unknown = sorted(set(value) - set(names))
        if unknown:
            raise ValueError(f"{what}: unknown images {unknown}; known: {list(names)}")
        return {n: value.get(n) for n in names}
    return {n: value for n in names}


def fit_views(glb_path: str | Path, images: dict[str, str | Path], views: dict[str, str], out_dir: str | Path, *,
              skip=(), front_parts=None, candidates: dict[str, tuple[str, ...]] | None = None,
              n_refine: int | dict[str, int] | None = None, workers: int | None = None, log=print,
              matte_modes: str | dict[str, str | None] | None = None, off_axis: bool = False,
              initial_openings: dict[str, dict] | None = None) -> dict:
    """Fit a camera for every photo in ``images`` (name -> path) against the model, each with the candidates of its
    view kind in ``views`` (name -> kind, ``view_candidates``), on the schedule above. Writes
    ``out_dir/cameras.json`` and ``out_dir/overlay_<name stem>.png`` per photo; returns the cameras.json content.

    ``skip`` / ``front_parts`` go to ``fitmesh.load_fit_mesh`` / ``FitMesh.front_pivot`` (None: the geometric front
    piece); ``candidates`` ({name: views}) overrides a photo's kind; ``n_refine`` is an int or {name: int}.
    ``matte_modes`` may provide one mode or per-photo modes; absent entries compare both base/floor alternatives,
    including optional-lens open-frame construction. Every result records the choice and its evidence.
    An explicit default retains the historical hole-filled matte.
    ``off_axis=True`` explicitly adds a lateral-eye continuation to the validated seven-parameter default."""
    t0 = time.perf_counter()
    glb_path = Path(glb_path)
    images = {str(n): Path(p) for n, p in images.items()}
    names = list(images)
    if not names:
        raise ValueError("no images")
    if set(views) != set(names):
        raise ValueError(f"views {sorted(views)} != images {sorted(names)}: every image needs exactly one view kind")
    if not glb_path.is_file():
        raise FileNotFoundError(f"not found: {glb_path}")
    cands = {n: (tuple(c) if c else None) for n, c in _per_name(candidates, names, "candidates").items()}
    nref = _per_name(n_refine, names, "n_refine")
    modes = _per_name(matte_modes, names, 'matte_modes')
    openings = _per_name(initial_openings, names, 'initial_openings')
    for name, mode in modes.items():
        if mode is not None and (not isinstance(mode, str) or mode not in M.MODES):
            raise ValueError(f'{name}: unknown matte mode {mode!r}; known: {M.MODES}')
    stems = [Path(n).stem for n in names]
    if len(set(stems)) != len(stems):
        raise ValueError(f"image names {names} share a stem: their overlays would collide")
    for n in names:                                    # fail fast on a bad kind or candidate list
        for v in cands[n] or view_candidates(views[n]):
            if v not in cameras.VIEWS:
                raise ValueError(f"{n}: unknown view {v!r}; known {cameras.VIEWS}")
    context = _fit_context(glb_path, tuple(skip), None if front_parts is None else tuple(front_parts))
    # Preserve absence for each photo's automatic/construction choice.
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def receives(n):
        return any(v in CROSS_RECEIVERS for v in (cands[n] or view_candidates(views[n])))

    def job(n, winners):
        return ((glb_path, images[n], views[n]),
                {"skip": tuple(skip), "front_parts": None if front_parts is None else tuple(front_parts),
                 "candidates": cands[n], "n_refine": nref[n], "cross_winners": winners,
                 "matte_mode": modes[n], "_model_context": context,
                 "off_axis": off_axis,
                 "initial_theta": openings[n],
                 "overlay_path": out_dir / f"overlay_{Path(n).stem}.png"})

    def cross_winners(fitted):
        winners: dict[str, dict] = {}
        for n in first:                                # cross sources: ok fits whose chosen view may seed others
            r = fitted.get(n)
            if r is not None and r["status"] == "ok" and r["view"] not in cameras.NOT_CROSS_SOURCES:
                winners.setdefault(r["view"], r["camera"])  # first photo wins a duplicate view
        return winners

    first = [n for n in names if not receives(n)]
    second = [n for n in names if receives(n)]
    nw = fit_workers(workers)
    if second and nw > 1:
        # A photo whose candidate views are all NOT_CROSS_SOURCES can never add a winner, so the receivers
        # need not wait for it: same winners, earlier start (_map_photo_stages).
        sources = [i for i, n in enumerate(first)
                   if any(v not in cameras.NOT_CROSS_SOURCES for v in (cands[n] or view_candidates(views[n])))]
        seeded = {}

        def receivers(fitted):
            seeded["winners"] = cross_winners({first[i]: r for i, r in fitted.items()})
            return [job(n, seeded["winners"] or None) for n in second]

        firsts, seconds = _map_photo_stages([job(n, None) for n in first], sources, receivers, len(second), nw)
        recs, winners = dict(zip(first, firsts)), seeded["winners"]
        recs.update(zip(second, seconds))
    else:
        recs = dict(zip(first, _map_photos([job(n, None) for n in first], nw)))
        winners = cross_winners(recs)
        recs.update(zip(second, _map_photos([job(n, winners or None) for n in second], nw)))
    models = [recs[n].pop("model") for n in names if 'model' in recs[n]]
    if any(mo != context['model'] for mo in models) or sha256_file(glb_path) != context['model']['sha256']:
        raise RuntimeError("the worker processes loaded different meshes from one GLB")
    res = {"created_utc": _utc(), "model": context['model'], "images": {n: recs[n] for n in names},
           "cross_winners": winners,
           "policy": {"view_kinds": {k: list(v) for k, v in VIEW_KINDS.items()},
                      "cross_receivers": list(CROSS_RECEIVERS), "not_cross_sources": list(cameras.NOT_CROSS_SOURCES),
                      "n_coarse_powell": cameras.N_COARSE_POWELL, "pitch_bounds": cameras.PITCH_BOUNDS,
                      "pitch_windows": cameras.PITCH_WINDOWS,
                      "roll": {"default_bounds": cameras.ROLL_BOUNDS, "axial_views": cameras.AXIAL_VIEWS,
                               "axial_seeds": cameras.AXIAL_ROLL_SEEDS,
                               "axial_window": "nearest 45-degree chart + default bounds; output canonicalized to [-180,180)"},
                      "camera_usability": {"minimum_iou": MIN_USABLE_IOU,
                                           "metric": "iou_lens_optional when present, otherwise iou",
                                           "scope": "camera evidence status, not a finish gate"},
                      "choice": "existing label-consistent usable candidates first, then lowest common final-level loss; "
                                "if none pass, retain the original lowest-loss candidate and its refusal",
                      "automatic_matte": AUTO_MATTE_RULE,
                      "mm": "px / px_per_mm_at(camera, frame, front pivot)",
                      "contour": "symmetric: mean of the two directed means; pNN = max of the two directed pNN",
                      "roi": "cameras.matte_roi(matte): matte bbox + 15 % of its width + 8 px (x0, y0, x1, y1)"},
           "seconds": time.perf_counter() - t0}
    if off_axis:
        res["policy"]["off_axis"] = {"mode": "legacy_search_then_off_axis_continuation",
                                   "eye_domain": "each coordinate +/- normalized model sphere radius",
                                   "existing_bounds_changed": False, "legacy_candidate_retained": True}
    from .compare import write_json
    write_json(out_dir / CAMERAS_JSON, res)
    for n in names:
        log(_row(n, res["images"][n]))
    return res


def _row(name: str, r: dict) -> str:
    if r.get('status') == 'failed':
        return f"{name:18s} {r['view_kind']:13s} -> failed {r.get('code')}: {r.get('refusal')}"
    c = r["camera"]
    return (f"{name:18s} {r['view_kind']:13s} -> {r['view']:17s} {r['status']:7s} IoU {r['iou']:.4f} "
            f"contour {r['contour_mean_px']:6.2f} px = {r['contour_mean_mm']:5.2f} mm (p90 {r['contour_p90_mm']:5.2f} mm, "
            f"{r['px_per_mm_at_front_pivot']:.2f} px/mm) yaw {c['yaw']:7.2f} pitch {c['pitch']:6.2f} "
            f"roll {c['roll']:7.2f} "
            f"sign margin {_margin(r)} {r['flags'] or ''}")


def _margin(r: dict) -> str:
    s = r.get("sign") or {}
    return f"{s['margin']:+.4f}" if "margin" in s else "n/a"


def _json(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o).__name__)


# ----------------------------------------------------------------------------- CLI
def _parse_candidates(items: list[str] | None) -> dict[str, tuple[str, ...]]:
    out = {}
    for it in items or ():
        name, _, views = it.partition("=")
        vs = tuple(v.strip() for v in views.split(",") if v.strip())
        bad = [v for v in vs if v not in cameras.VIEWS]
        if not vs or bad:
            raise SystemExit(f"--candidates {it!r}: unknown views {bad or '(none given)'}; known {cameras.VIEWS}")
        out[name] = vs
    return out


def _parse_images(items: list[str]) -> tuple[dict[str, Path], dict[str, str]]:
    """``KIND=PATH`` items -> ({file name: path}, {file name: kind})."""
    images, views = {}, {}
    for it in items:
        kind, sep, path = it.partition("=")
        if not sep or not kind.strip() or not path.strip():
            raise SystemExit(f"--image {it!r}: expected KIND=PATH")
        try:
            view_candidates(kind)
        except ValueError as e:
            raise SystemExit(f"--image {it!r}: {e}") from None
        p = Path(path.strip())
        if p.name in images:
            raise SystemExit(f"--image {it!r}: a second image named {p.name!r}")
        images[p.name], views[p.name] = p, kind.strip()
    return images, views


def _parse_n_refine(items: list[str] | None, names: list[str]) -> int | dict[str, int] | None:
    """``N`` (every image) and/or ``NAME=N`` items -> None, an int, or {name: int} for every name."""
    default, per = None, {}
    for it in items or ():
        name, sep, n = it.rpartition("=")
        try:
            k = int(n)
        except ValueError:
            raise SystemExit(f"--n-refine {it!r}: expected N or NAME=N") from None
        if k < 1:
            raise SystemExit(f"--n-refine {it!r}: N must be >= 1")
        if sep and name not in names:
            raise SystemExit(f"--n-refine {it!r}: no image named {name!r}; images: {names}")
        if sep:
            per[name] = k
        else:
            default = k
    if not per:
        return default
    d = cameras.N_COARSE_POWELL if default is None else default
    return {n: per.get(n, d) for n in names}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m glasses3d.g3d_host.fit_views", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("views", help="fit every photo given on the command line to one GLB")
    v.add_argument("--glb", required=True, type=Path)
    v.add_argument("--image", required=True, action="append", metavar="KIND=PATH",
                   help=f"repeat per photo; KIND is one of: {', '.join(VIEW_KINDS)}")
    v.add_argument("--out-dir", required=True, type=Path)
    v.add_argument("--skip", nargs="*", default=[], help="skip parts whose node name contains any of these")
    v.add_argument("--front-parts", nargs="*", help="pivot parts (default: the geometric front piece)")
    v.add_argument("--candidates", nargs="*", help="NAME=view1,view2 (overrides that photo's kind)")
    v.add_argument("--n-refine", nargs="*", help="N (every photo) and/or NAME=N")
    v.add_argument("--workers", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "views":
        images, views = _parse_images(args.image)
        nr = _parse_n_refine(args.n_refine, list(images))
        res = fit_views(args.glb, images, views, args.out_dir, skip=tuple(args.skip),
                        front_parts=None if args.front_parts is None else tuple(args.front_parts),
                        candidates=_parse_candidates(args.candidates), n_refine=nr, workers=args.workers)
        print("wrote", args.out_dir / CAMERAS_JSON)
        print(f"{len(res['images'])} images in {res['seconds']:.0f} s")
        return 0


if __name__ == "__main__":
    sys.exit(main())
