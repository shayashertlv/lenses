"""Joint camera/opening continuation with whole-scene occlusion and immutable geometry.

Angles are deltas from the authored GLB. All sides and view labels use the same
algorithm. Visibility, conditional angle evidence and camera-coupled sensitivity
are separate reports; none is a new quality gate. Hidden arms retain their input.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

import numpy as np
from scipy import ndimage, optimize

from . import cameras as C, pose as P, raster

MAX_ROUNDS = 16
ANGLE_RANGE = (-180.0, 180.0)  # one complete periodic domain, relative to the input
OBSERVED_RANGE = .002          # historical whole-scene arm-ROI criterion


def _keys(values):
    return {f"{int(k):+d}": float(v) for k, v in (values or {}).items()}


def _pose(theta, axes):
    return {k: {"theta_deg": float(t), "axis_xz_mm": axes[k]} for k, t in theta.items() if k in axes}


def _rendered(mesh, roles, axes, theta, cam, level, *, points=False):
    posed = P.posed(mesh, _pose(theta, axes), roles)
    scene = posed.scene()
    return posed, scene, scene.render(cam, level.shape, level.stride, level.roi, want_points=points)


def _trial_rendered(*args):
    """An invalid search proposal is not failure of the valid input camera."""
    try:
        return _rendered(*args)
    except ValueError as exc:
        if 'near plane' not in str(exc):
            raise
        return None


def _visible(rendered, face_sides, side):
    fid = rendered["face_id"]
    return (fid >= 0) & (face_sides[np.maximum(fid, 0)] == side)


def _angle_search(mesh, roles, axes, theta, cam, lv, side, face_sides, bounds):
    """The other arm is fixed at theta throughout this search, never removed."""
    key, start = f"{side:+d}", theta[f"{side:+d}"]
    _, base_scene, base = _rendered(mesh, roles, axes, theta, cam, lv)
    # Visibility describes the whole photograph. The foreground-derived fitting
    # ROI need not contain an initially displaced arm, even at native stride.
    native = base_scene.render(cam, lv.shape)
    visible = int(_visible(native, face_sides, side).sum())
    info = {"input_deg": start, "theta_deg": start, "observed": False,
            "visible_pixels_initial": visible, "pixel_stride": lv.stride, "visibility_pixel_stride": 1,
            "visibility_domain": "full_photo",
            "fixed_other_theta_deg": {k: v for k, v in theta.items() if k != key},
            "bounds_deg": list(bounds), "grid": [], "grid_range": 0.0, "evaluations": 0}
    if not visible:
        return start, {**info, "status": "no_visible_pixels"}
    lo, hi = bounds
    # Broad coverage is a search budget, not a product/side constraint. Include
    # the exact input and refine several local minima to avoid grid aliasing.
    grid = np.unique(np.r_[np.arange(lo, hi + 1e-8, min(15., (hi-lo)/16)), start, hi])
    rendered = []
    for angle in grid:
        trial = _trial_rendered(mesh, roles, axes, {**theta, key: float(angle)}, cam, lv)
        rendered.append(None if trial is None else trial[2])
    roi = ndimage.binary_dilation(np.logical_or.reduce([_visible(r, face_sides, side) for r in rendered if r is not None]),
                                  iterations=2)

    def score(r):
        # Full photo against full scene in the arm's observable footprint. Fixed
        # front/other arm explain their pixels; isolated-arm matching is forbidden.
        if r is None:
            return 3.0
        mask, ref = r["mask"] & roi, lv.ref * roi
        optional = lv.ignored(r)
        hard = mask if optional is None else mask & ~optional
        union = float(hard.sum()) + float(ref[~hard].sum())
        return 1.0 - float(ref[mask].sum()) / max(union, 1.)

    losses = np.array([score(r) for r in rendered])
    valid = np.array([r is not None for r in rendered])
    info["grid"] = [[float(t), float(v) if ok else None] for t, v, ok in zip(grid, losses, valid)]
    info['invalid_near_plane_trials'] = int((~valid).sum())
    info["grid_range"] = float(np.ptp(losses[valid]))
    info["evaluations"] = len(grid)
    if info['grid_range'] < OBSERVED_RANGE:
        return start, {**info, "status": "flat_whole_scene_objective"}
    best = [score(base), start]

    def objective(angle):
        trial = _trial_rendered(mesh, roles, axes, {**theta, key: float(angle)}, cam, lv)
        value = score(None if trial is None else trial[2])
        info['invalid_near_plane_trials'] += int(trial is None)
        info["evaluations"] += 1
        if value < best[0]:
            best[:] = value, float(angle)
        return value

    for index in np.argsort(losses)[:3]:
        if losses[index] < best[0]:
            best[:] = float(losses[index]), float(grid[index])
        left, right = grid[max(0, index-1)], grid[min(len(grid)-1, index+1)]
        if right > left:
            # Raster losses have plateaus; a two-degree fine grid brackets a
            # local bounded search without presuming a unimodal 30-degree arc.
            fine = np.unique(np.r_[np.arange(left, right, 2.), right, grid[index]])
            fl = [objective(t) for t in fine]
            i = int(np.argmin(fl))
            a, b = fine[max(0, i-1)], fine[min(len(fine)-1, i+1)]
            if b > a:
                optimize.minimize_scalar(objective, bounds=(a, b), method="bounded",
                                         options={"xatol": .025, "maxiter": 24})
    info.update(theta_deg=best[1], observed=True, status="visible_angle_evidence", loss=best[0])
    return best[1], info


def _normals(mask):
    f = np.asarray(mask, float)
    n = np.stack([ndimage.gaussian_filter(f, 1., order=(0, 1)),
                  ndimage.gaussian_filter(f, 1., order=(1, 0))], axis=-1)
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)


def _matches(mesh, roles, axes, theta, cam, lv, face_sides):
    _, _, r = _rendered(mesh, roles, axes, theta, cam, lv, points=True)
    edge = C._edge(lv.effective(r["mask"], lv.ignored(r)))
    if edge.sum() < 8 or lv.edge.sum() < 8:
        return None
    us, vs = raster.pixel_grid(lv.shape, lv.stride, lv.roi)
    ri, rj = np.nonzero(edge)
    pi, pj = np.nonzero(lv.edge)
    _, photo_idx = ndimage.distance_transform_edt(~lv.edge, return_indices=True)
    _, render_idx = ndimage.distance_transform_edt(~edge, return_indices=True)
    qi, qj = render_idx[0][pi, pj], render_idx[1][pi, pj]
    W = np.concatenate([r["points"][ri, rj], r["points"][qi, qj]])
    ids = np.concatenate([r["face_id"][ri, rj], r["face_id"][qi, qj]])
    target = np.concatenate([np.c_[us[photo_idx[1][ri, rj]], vs[photo_idx[0][ri, rj]]], np.c_[us[pj], vs[pi]]])
    current = np.concatenate([np.c_[us[rj], vs[ri]], np.c_[us[qj], vs[qi]]])
    pn, rn = _normals(lv.ref >= .5), _normals(lv.effective(r["mask"], lv.ignored(r)))
    normals = np.concatenate([pn[photo_idx[0][ri, rj], photo_idx[1][ri, rj]], rn[qi, qj]])
    ok = (np.isfinite(W).all(1) & (ids >= 0) &
          (np.linalg.norm(current-target, axis=1) <= max(C.ICP_TRUNC*lv.width_native, 3.*lv.stride)))
    # Borrowed optional-lens interior edges are photo evidence, not a surface
    # correspondence. They may never move the camera or an opening angle.
    if lv.ignore_faces is not None:
        optional = np.zeros(len(ids), bool)
        optional[ids >= 0] = lv.ignore_faces[ids[ids >= 0]]
        geometric = np.concatenate([C._edge(r["mask"])[ri, rj], C._edge(r["mask"])[qi, qj]])
        ok &= ~optional | geometric
    if ok.sum() < 16:
        return None
    n1 = int(ok[:len(ri)].sum())
    labels = face_sides[ids[ok]]
    W = P.unpose_points(W[ok], labels, _pose(theta, axes))
    weight = np.r_[np.full(n1, 1./max(n1, 1)), np.full(ok.sum()-n1, 1./max(ok.sum()-n1, 1))]
    weight = np.sqrt(weight * len(W)/2)
    # Deterministic subsampling caps projection cost without changing ray hits.
    take = np.arange(0, len(W), max(1, math.ceil(len(W)/4000)))
    return W[take], labels[take], target[ok][take], normals[ok][take], weight[take]


def refine(mesh, roles, axes, fg, camera, view, pivot, initial_theta=None, ignore_faces=None,
           off_axis=False, *, fit_camera=True, sides=(1, -1), angle_bounds=None, max_rounds=MAX_ROUNDS):
    """Return camera, posed mesh, theta_deg/observed and explicit fit diagnostics.

    Optional per-side angle_bounds are absolute delta degrees; default is a full
    periodic domain around each input. fit_camera=False isolates the opening
    instrument for independent controls. Inputs and authored vertices are immutable.
    """
    started = time.perf_counter()
    axes = {f"{int(k):+d}": list(v) for k, v in axes.items()}
    theta0 = {k: _keys(initial_theta).get(k, 0.) for k in axes}
    P.parse_pose(_pose(theta0, axes))
    active = [s for s in sides if f"{s:+d}" in axes and P.temple_parts(mesh, s, roles)]
    tags = P.side_of_faces(mesh, roles)
    theta, cam = dict(theta0), camera
    level = C.Level(fg, C.FINAL_W, "opening_final", margin=C.FINAL_ROI_MARGIN)
    level.ignore_faces = ignore_faces
    search = C.Level(fg, C.FIT_W if fit_camera else 1e9, "opening_search", margin=C.FINAL_ROI_MARGIN)
    search.ignore_faces = ignore_faces
    unit = level.width_native
    eye_limit = float(np.linalg.norm(mesh.frame.to_norm(mesh.V), axis=1).max()) if off_axis else None
    box = C._bounds(camera, unit, C.yaw_window(view, camera.yaw), pivot=pivot,
                    pitch_win=C.pitch_window(view), roll_win=C.roll_window(view, camera.roll), eye_limit=eye_limit)
    lo, hi = np.array(box).T
    bounds = {s: tuple((angle_bounds or {}).get(f"{s:+d}", (angle_bounds or {}).get(s,
                    (theta0[f"{s:+d}"]+ANGLE_RANGE[0], theta0[f"{s:+d}"]+ANGLE_RANGE[1])))) for s in active}
    for s, (a, b) in bounds.items():
        if not np.isfinite([a, b]).all() or not a < b or not a <= theta0[f"{s:+d}"] <= b:
            raise ValueError("angle bounds must be finite, ordered and contain the input angle")
    posed, scene, initial_render = _rendered(mesh, roles, axes, theta, cam, search)
    initial_render = scene.render(cam, search.shape)
    initial_visible = {s:int(_visible(initial_render, tags, s).sum()) for s in active}
    initial_loss = level.loss(scene, cam)
    loss = initial_loss
    info, history, projection_evals, image_evals = {}, [], 0, 1
    # A camera refinement can reveal an initially hidden arm; reevaluate its
    # visibility at each pass. A hidden side never moves to manufacture evidence.
    for outer in range(2 if fit_camera else 1):
        for side in active:
            angle, row = _angle_search(mesh, roles, axes, theta, cam, search, side, tags, bounds[side])
            row['search_input_deg'] = row['input_deg']
            row['visible_pixels_search_start'] = row['visible_pixels_initial']
            row['input_deg'] = theta0[f'{side:+d}']
            row['visible_pixels_initial'] = initial_visible[side]
            info[f"{side:+d}"] = row
            proposal = {**theta, f"{side:+d}": angle}
            trial = _trial_rendered(mesh, roles, axes, proposal, cam, level)
            value = 3.0 if trial is None else level.loss(trial[1], cam)
            image_evals += row["evaluations"] + 1
            if trial is not None and value < loss:
                pm, sc, _ = trial
                theta, posed, scene, loss = proposal, pm, sc, value
            row["selected_deg"] = theta[f"{side:+d}"]
        if fit_camera:
            candidate, value, count = C.powell(scene, level, cam, box, 100, unit, xtol=.02, pivot=pivot)
            image_evals += count
            if value < loss:
                cam, loss = candidate, value

    free = [s for s in active if info.get(f"{s:+d}", {}).get("observed")]
    ncam = len(box)
    x = np.r_[C.pack(cam, unit, pivot, include_eye=off_axis), [theta[f"{s:+d}"] for s in free]]
    lower, upper = np.r_[lo, [bounds[s][0] for s in free]], np.r_[hi, [bounds[s][1] for s in free]]
    x = np.clip(x, lower+1e-9, upper-1e-9)
    fixed_theta = dict(theta)

    def decode(z):
        return C.unpack(z[:ncam], unit, pivot), {**fixed_theta, **{f"{s:+d}": float(z[ncam+i]) for i,s in enumerate(free)}}

    if fit_camera:
        cam, theta = decode(x)
        posed, scene, _ = _rendered(mesh, roles, axes, theta, cam, level)
        loss = level.loss(scene, cam)
        image_evals += 1

    def residual_for(matches):
        W, labels, target, normals, weights = matches
        def residual(z):
            nonlocal projection_evals
            projection_evals += 1
            cm, th = decode(z)
            points = W.copy()
            for key in axes:
                side = int(key)
                select = labels == side
                if select.any():
                    points[select] = P.rot_about_hinge(points[select], side, th[key],
                                                      (abs(axes[key][0]), axes[key][1]))
            uv = raster._project_norm(mesh.frame.to_norm(points), cm)
            if uv is None:
                return np.full(2*len(W), 1e3)
            delta = uv-target
            normal_delta = np.einsum("ij,ij->i", delta, normals)[:, None]*normals
            error = np.where((np.linalg.norm(normals, axis=1) > .99)[:,None], normal_delta, delta)
            return (error*weights[:,None]).ravel()
        return residual

    termination = "fixed_camera" if not fit_camera else "iteration_cap"
    if fit_camera:
        for iteration in range(max_rounds):
            cam, theta = decode(x)
            matches = _matches(mesh, roles, axes, theta, cam, level, tags)
            if matches is None:
                termination = "insufficient_contour_matches"
                break
            residual = residual_for(matches)
            result = optimize.least_squares(residual, x, bounds=(lower, upper), loss="soft_l1",
                                            f_scale=1.5*level.stride, max_nfev=C.ICP_NFEV, x_scale="jac")
            best = (loss, x, posed, scene)
            trials = []
            for fraction in (1., .5, .25):
                z = x+fraction*(result.x-x)
                cm, th = decode(z)
                trial = _trial_rendered(mesh, roles, axes, th, cm, level)
                value = 3.0 if trial is None else level.loss(trial[1], cm)
                image_evals += 1
                trials.append({"fraction": fraction, "loss": float(value),
                               'invalid_near_plane':trial is None})
                if trial is not None and value < best[0]:
                    pm, sc, _ = trial
                    best = value, z, pm, sc
            history.append({"iteration": iteration, "loss_start": loss, "loss": best[0], "trials": trials})
            if best[0] >= loss:
                termination = "no_image_descent"
                break
            gain = (loss-best[0])/max(loss, best[0], 1e-12)
            loss, x, posed, scene = best
            if gain <= 1e-4:
                termination = "image_loss_converged"
                break
        cam, theta = decode(x)
    # A hold can reveal its own side or occlude the other side. Restore only
    # toward the invocation inputs, then evaluate the actual returned geometry.
    # This is monotonic: each side can change to its input at most once.
    reset = False
    for _ in range(len(active) + 1):
        posed = P.posed(mesh, _pose(theta, axes), roles)
        scene = posed.scene()
        render = scene.render(cam, level.shape)
        changed = False
        for side in active:
            key = f"{side:+d}"
            row = info[key]
            visible = int(_visible(render, tags, side).sum())
            row.update(visible_pixels_final=visible, theta_deg=theta[key])
            if not visible or not row["observed"]:
                row["observed"] = False
                if not visible:
                    row["status"] = "no_visible_pixels"
                elif row["status"] == "no_visible_pixels":
                    row["status"] = "held_input_without_angle_evidence"
                if theta[key] != theta0[key]:
                    theta[key] = theta0[key]
                    changed = reset = True
        if not changed:
            break
    else:
        raise AssertionError("hidden-arm holds did not reach their monotonic input pose")
    if reset:
        loss = level.loss(scene, cam)

    # Local projected sensitivity after camera/other-arm nuisance directions
    # are removed. This is a one-pixel sensitivity, NOT a statistical interval.
    free = [s for s in free if info[f'{s:+d}']['observed']]
    matches = _matches(mesh, roles, axes, theta, cam, level, tags)
    if matches is not None and free:
        fixed_theta = dict(theta)
        x = np.r_[C.pack(cam, unit, pivot, include_eye=off_axis), [theta[f"{s:+d}"] for s in free]]
        fn = residual_for(matches)
        steps = np.r_[np.full(ncam, .01), np.full(len(free), .01)]
        jac = np.column_stack([(fn(x+np.eye(len(x))[j]*steps[j])-fn(x-np.eye(len(x))[j]*steps[j]))/(2*steps[j])
                               for j in range(len(x))])
        for i, side in enumerate(free):
            j = ncam+i
            nuisance = np.delete(jac, j, axis=1) if fit_camera else np.delete(jac[:, ncam:], i, axis=1)
            column = jac[:,j]
            projected = column-nuisance@np.linalg.lstsq(nuisance, column, rcond=1e-7)[0] if nuisance.size else column
            raw = float(np.sqrt(np.mean(column**2)))
            independent = float(np.sqrt(np.mean(projected**2)))
            row = info[f"{side:+d}"]
            row.update(camera_coupling=1.-independent/max(raw,1e-12),
                       independent_px_per_degree=independent,
                       one_pixel_sensitivity_deg=None if independent < 1e-8 else 1./independent,
                       identifiability="weak_or_camera_coupled" if independent < 1. else "locally_sensitive",
                       sensitivity_note="local contour-normal one-pixel sensitivity; not a confidence interval")
    for key, row in info.items():
        row.setdefault("identifiability", "unobserved" if not row["observed"] else "unavailable")
    return {"camera": cam, "mesh": posed, "theta_deg": theta,
            "theta_observed": {k: bool(info.get(k, {}).get("observed", False)) for k in theta},
            "opening_fit": {"schema": "g3d.opening_fit/1", "method": "whole_scene_grid_then_joint_contour",
                            "initial_theta_deg": theta0, "per_side": info,
                            "initial_camera": camera.to_dict(),
                            "loss_start": float(initial_loss), "loss": float(loss),
                            "camera_box": [list(b) for b in box], "fit_camera": fit_camera,
                            "termination": termination, "history": history,
                            "image_evaluations": image_evals, "projection_evaluations": projection_evals,
                            "seconds": time.perf_counter()-started,
                            "note": "Opening and sensitivity reports do not change camera usability or finish checks."}}
