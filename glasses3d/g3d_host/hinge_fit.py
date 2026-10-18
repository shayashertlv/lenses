"""Joint camera/opening continuation with whole-scene occlusion and immutable geometry.

Angles are deltas from the authored GLB. All sides and view labels use the same
algorithm. Visibility, conditional angle evidence and camera-coupled sensitivity
are separate reports; none is a new quality gate. Equal photo losses prefer the input;
empty photo regions can bound an opening without identifying its exact angle.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

import numpy as np
from scipy import ndimage, optimize

from . import cameras as C, pose as P, raster
from .hinge_domain import OpeningDomain

MAX_ROUNDS = 16
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


def _angle_search(mesh, roles, axes, theta, cam, lv, side, face_sides, bounds, domain=None):
    """Photo foreground and empty regions both constrain the whole-scene arm footprint."""
    key, start = f"{side:+d}", theta[f"{side:+d}"]
    domain = domain or OpeningDomain(mesh, roles, axes)
    _, _, base = _rendered(mesh, roles, axes, theta, cam, lv)
    native = P.posed(mesh, _pose(theta, axes), roles).scene().render(cam, lv.shape)
    info = {"input_deg": start, "theta_deg": start, "observed": False,
            "visible_pixels_initial": int(_visible(native, face_sides, side).sum()),
            "pixel_stride": lv.stride, "visibility_pixel_stride": 1,
            "visibility_domain": "full_photo", "photo_evidence_domain": "foreground_in_swept_arm_footprint",
            "fixed_other_theta_deg": {k: v for k, v in theta.items() if k != key},
            "bounds_deg": list(bounds), "grid": [], "grid_range": 0., "evaluations": 0,
            "invalid_physical_trials": 0, "invalid_near_plane_trials": 0}
    lo, hi = bounds
    grid = np.unique(np.r_[np.arange(lo, hi, domain.sides[key]['step']), start, hi])
    # Retain three scalar sufficient statistics per trial, not a full-image mask/id
    # per angle. Outside the swept footprint all poses have the same first hits.
    statistics, roi = [], np.zeros(lv.ref.shape, bool)
    def render(angle):
        info['evaluations'] += 1
        if not domain.valid_side(key, angle):
            info['invalid_physical_trials'] += 1
            return None
        trial = _trial_rendered(mesh, roles, axes, {**theta, key: float(angle)}, cam, lv)
        if trial is None:
            info['invalid_near_plane_trials'] += 1
            return None
        return {k: trial[2][k] for k in ('mask', 'face_id')}
    def counts(r, region=None):
        mask = r['mask'] if region is None else r['mask'] & region
        ref = lv.ref if region is None else lv.ref * region
        optional = lv.ignored(r)
        hard = mask if optional is None else mask & ~optional
        return np.array([float(ref[mask].sum()), float(hard.sum()), float(ref[~hard].sum())])
    for angle in grid:
        r = render(angle)
        statistics.append(None if r is None else counts(r))
        if r is not None:
            roi |= _visible(r, face_sides, side)
    roi = ndimage.binary_dilation(roi, iterations=2)
    info['photo_foreground_pixels_in_sweep'] = float((lv.ref*roi).sum())
    def score(r):
        if r is None:
            return 3.
        inter, hard, missing = counts(r, roi)
        return 1.-inter/max(hard+missing, 1.)
    constant = counts(base, ~roi)
    losses = np.array([3. if row is None else 1.-(row-constant)[0]/max((row-constant)[1:].sum(), 1.)
                       for row in statistics])
    valid = np.array([row is not None for row in statistics])
    info['grid'] = [[float(t), float(v) if ok else None] for t,v,ok in zip(grid,losses,valid)]
    info['grid_range'] = float(np.ptp(losses[valid])) if valid.any() else 0.
    if info['grid_range'] < OBSERVED_RANGE:
        return start, {**info, 'status': 'flat_whole_scene_objective'}
    best = [score(base), start]
    def prefer(value, angle):
        if value < best[0]-1e-12 or (abs(value-best[0]) <= 1e-12 and abs(angle-start) < abs(best[1]-start)):
            best[:] = float(value), float(angle)
    def objective(angle):
        value = score(render(angle))
        prefer(value, angle)
        return value
    order = sorted(np.flatnonzero(valid), key=lambda i:(losses[i], abs(grid[i]-start)))
    for index in order[:3]:
        prefer(losses[index], grid[index])
        left, right = grid[max(0,index-1)], grid[min(len(grid)-1,index+1)]
        if right > left:
            optimize.minimize_scalar(objective, bounds=(left,right), method='bounded',
                                     options={'xatol':.01, 'maxiter':30})
    # Refine the edge of a raster plateau toward the input, rather than its arbitrary far edge.
    if best[1] != start and best[0] < score(base)-1e-12:
        a, b = best[1], start
        for _ in range(12):
            middle = (a+b)/2
            value = objective(middle)
            if value <= best[0]+1e-12:
                a = middle
            else:
                b = middle
    info.update(theta_deg=best[1], observed=True, status='photo_constrained_proposal', loss=best[0])
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

    Optional per-side angle_bounds are absolute delta degrees; default is the rearward geometric domain with front/lens crossing refusal. fit_camera=False isolates the opening
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
    # Search proposals on the existing camera-fit grid; every acceptance and the
    # returned evidence still use the final grid. Fixed-camera controls stay native.
    search = C.Level(fg, C.FIT_W if fit_camera else max(fg.shape), "opening_search", margin=C.FINAL_ROI_MARGIN)
    search.ignore_faces = ignore_faces
    unit = level.width_native
    eye_limit = (P.posed(mesh, _pose(theta0, axes), roles).scene().max_radius if off_axis else None)
    box = C._bounds(camera, unit, C.yaw_window(view, camera.yaw), pivot=pivot,
                    pitch_win=C.pitch_window(view), roll_win=C.roll_window(view, camera.roll), eye_limit=eye_limit)
    lo, hi = np.array(box).T
    domain = OpeningDomain(mesh, roles, axes)
    bounds = {}
    for side in active:
        key = f'{side:+d}'
        physical = domain.bounds(key)
        requested = (angle_bounds or {}).get(key, (angle_bounds or {}).get(side, physical))
        a, b = max(physical[0], requested[0]), min(physical[1], requested[1])
        if not np.isfinite([a,b]).all() or not a < b:
            raise ValueError('angle bounds have no finite physical opening domain')
        bounds[side] = (a,b)
        if not a <= theta0[key] <= b or not domain.valid_side(key, theta0[key]):
            if not a <= 0. <= b or not domain.valid_side(key, 0.):
                raise ValueError(f'{key}: input and authored arm poses cross the front/lenses or lie outside the rearward domain')
            theta[key] = 0.
    posed, scene, initial_render = _rendered(mesh, roles, axes, theta, cam, search)
    initial_render = scene.render(cam, fg.shape)
    initial_visible = {s:int(_visible(initial_render, tags, s).sum()) for s in active}
    initial_loss = level.loss(scene, cam)
    loss = initial_loss
    info, history, projection_evals, image_evals = {}, [], 0, 1
    # A camera refinement can reveal an initially hidden arm; reevaluate its
    # visibility at each pass. Empty photo regions may bound an initially visible arm.
    for outer in range(2 if fit_camera else 1):
        for side in active:
            angle, row = _angle_search(mesh, roles, axes, theta, cam, search, side, tags, bounds[side], domain)
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
            elif abs(angle-theta[f'{side:+d}']) > 1e-9:
                row.update(observed=False, status='proposal_rejected')
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
                physical = all(domain.valid_side(f'{side:+d}', th[f'{side:+d}']) for side in active)
                trial = _trial_rendered(mesh, roles, axes, th, cm, level) if physical else None
                value = 3.0 if trial is None else level.loss(trial[1], cm)
                image_evals += 1
                trials.append({"fraction": fraction, "loss": float(value),
                               'invalid_physical':not physical, 'invalid_near_plane':physical and trial is None})
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
    # Re-evaluate the actual returned pose against the photo. Hidden/empty regions
    # can bound an opening but do not determine one angle: retain the best physical
    # pose and label it as bounded, never restore a visibly wrong input.
    posed, scene, render = _rendered(mesh, roles, axes, theta, cam, level)
    loss = level.loss(scene, cam)
    native = scene.render(cam, fg.shape)
    for side in active:
        key, row = f'{side:+d}', info[f'{side:+d}']
        visible = _visible(native, tags, side)
        supported = visible & fg
        row.update(visible_pixels_final=int(visible.sum()), photo_supported_pixels=int(supported.sum()),
                   theta_deg=theta[key], selected_deg=theta[key])
        # A fixed-input optimum is observed only if neighbouring physical angles
        # change the photo objective. A rejected search is explicitly unobserved.
        neighbours = []
        for delta in (-1., 1.):
            angle = theta[key]+delta
            if bounds[side][0] <= angle <= bounds[side][1] and domain.valid_side(key, angle):
                trial = _trial_rendered(mesh, roles, axes, {**theta,key:angle}, cam, level)
                if trial is not None:
                    neighbours.append(level.loss(trial[1], cam)-loss)
        row['local_photo_loss_increases'] = neighbours
        constrained = (bool(supported.any()) and len(neighbours) == 2 and min(neighbours) > 1e-6
                       and row['status'] != 'proposal_rejected')
        row['observed'] = constrained
        row['status'] = ('photo_constrained_angle' if constrained else
                         'photo_bounds_opening' if theta[key] != theta0[key] else
                         'proposal_rejected' if row['status'] == 'proposal_rejected' else
                         'held_input_without_angle_evidence')

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
