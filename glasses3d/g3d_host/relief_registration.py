"""Bounded joint camera/hinge alignment for WP6's per-model view refit.

The initial alternating camera/hinge pass leaves coupled errors even on known
geometry. Bidirectional contour correspondences provide a joint least-squares
proposal; only descent of the EXISTING silhouette loss may select a step. No
crease line, R3 score, true camera, or true hinge angle is used. The original
camera box and front guard remain binding. Side/unobserved hinges stay fixed.
"""
import time

import numpy as np
from scipy import ndimage, optimize

from . import cameras as C, raster, relief_rules as RR
from .relief_normal_residual import normal_or_point_residual

MAX_ROUNDS = 50             # computational cap, reported; never a quality verdict
LOSS_REL_TOL = 1e-4          # cameras.powell's relative image-loss tolerance


def contour_normals(mask):
    """Unit image normals for proposals; one-pixel smoothing of raster stairs."""
    mask = np.asarray(mask, dtype=float)
    dx = ndimage.gaussian_filter(mask, 1., order=(0, 1))
    dy = ndimage.gaussian_filter(mask, 1., order=(1, 0))
    normal = np.stack([dx, dy], axis=-1)
    return normal / np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-12)


def refine_contour_normals(job, model, key, fit, cam0):
    """Opt-in refinement for non-side views under the existing acceptance test.

    Closest-point matches invent tangential correspondences on an outline. A
    normal residual permits sliding along it while solving the coupled camera
    and observable hinges. Side views retain the existing method: their fixed
    articulation can make a smaller image loss a worse geometric estimate.
    No true coordinates, crease score, new angle eligibility or gate is read.
    """
    if job.kind(key) == 'side':
        return fit
    return align_view(job, model, key, fit, cam0, normal_only=True)


def align_view(job, model, key, fit, cam0, *, normal_only=False):
    """Return a new fit record; preserve the initial fit and all diagnostic rows.

    ``cam0`` is the original refit seed, not the latest camera: bounds must never
    drift when this follows an existing camera block. ``theta_fit`` identifies
    observable hinges from the initial arm-grid search. No geometry is changed.
    """
    from . import relief_views as RV
    started = time.perf_counter()
    free_sides = [s for s in (1, -1) if job.kind(key) != 'side'
                  and (fit.get('theta_fit', {}).get(str(s), {}).get('observed', False))]
    camera, theta = fit['camera'], dict(fit['theta'])
    V, F, _ = model.composite(job, theta)
    initial_scene = raster.get_scene(V, F, job.frame)
    vf = C.ViewFit(job.view_name[key], job.matte(key), initial_scene, pivot=job.pivot)
    pts = C.project(initial_scene.hull, camera)
    lv = C.Level(job.matte(key), C.FINAL_W, 'final', margin=C.FINAL_ROI_MARGIN,
                 include_xyxy=(*pts.min(0), *pts.max(0)))
    box = RV._box(cam0, vf.unit, job.pivot, RR.REFIT_BOUNDS)
    box += [RR.THETA_RANGE_DEG for _ in free_sides]
    lo, hi = np.array(box).T
    x = C.pack(camera, vf.unit, job.pivot)
    x = np.r_[x, [theta[s] for s in free_sides]]
    if not (np.all(np.isfinite(x)) and np.all(x >= lo-1e-8) and np.all(x <= hi+1e-8)):
        raise ValueError('camera alignment starting fit outside original box')
    x = np.clip(x, lo+1e-9, hi-1e-9)

    def decode(z):
        return C.unpack(z[:7], vf.unit, job.pivot), {**theta, **{s: float(z[7+i]) for i, s in enumerate(free_sides)}}

    image_evals, projection_evals = 0, 0
    def evaluate(z):
        nonlocal image_evals
        image_evals += 1
        cm, th = decode(z)
        vv, ff, tags = model.composite(job, th)
        scene = raster.get_scene(vv, ff, job.frame)
        return lv.loss(scene, cm), scene, tags

    loss, scene, tags = evaluate(x)
    initial_loss = loss
    base_loss = lv.loss(initial_scene, cam0)
    image_evals += 1
    history = []
    normal_fallback_samples = 0
    us, vs = raster.pixel_grid(lv.shape, lv.stride, lv.roi)
    pi, pj = np.nonzero(lv.edge)
    photo_uv = np.column_stack([us[pj], vs[pi]])
    photo_normals = contour_normals(lv.ref >= .5) if normal_only else None
    _, pidx = ndimage.distance_transform_edt(~lv.edge, return_indices=True)
    trunc = max(C.ICP_TRUNC*lv.width*lv.stride, 3.*lv.stride)
    # Same relative image-loss tolerance as cameras.powell's ftol. The cap is
    # computational only; hitting it is reported as not converged. Neither this
    # termination rule nor the optimizer can read R3 or synthetic truth.
    termination = 'iteration_cap'
    for iteration in range(MAX_ROUNDS):
        if not len(pi):
            termination = 'insufficient_photo_contour'
            break
        cam, th = decode(x)
        rendered = scene.render(cam, lv.shape, lv.stride, lv.roi, want_points=True)
        edge = C._edge(lv.effective(rendered['mask'], lv.ignored(rendered)))
        if edge.sum() < 8:
            termination = 'insufficient_contour'
            break
        ri, rj = np.nonzero(edge)
        _, ridx = ndimage.distance_transform_edt(~edge, return_indices=True)
        qi, qj = ridx[0][pi, pj], ridx[1][pi, pj]
        W = np.concatenate([rendered['points'][ri, rj], rendered['points'][qi, qj]])
        ids = np.concatenate([rendered['face_id'][ri, rj], rendered['face_id'][qi, qj]])
        target = np.concatenate([np.column_stack([us[pidx[1][ri, rj]], vs[pidx[0][ri, rj]]]), photo_uv])
        current = np.concatenate([np.column_stack([us[rj], vs[ri]]), np.column_stack([us[qj], vs[qi]])])
        if normal_only:
            model_normals = contour_normals(lv.effective(rendered['mask'], lv.ignored(rendered)))
            normals = np.concatenate([photo_normals[pidx[0][ri, rj], pidx[1][ri, rj]],
                                      model_normals[qi, qj]])
        ok = np.isfinite(W).all(1) & (ids >= 0) & (np.linalg.norm(current-target, axis=1) <= trunc)
        if ok.sum() < 16:
            termination = 'insufficient_matches'
            break
        n_forward = int(ok[:len(ri)].sum())
        W, labels, target = W[ok].copy(), tags[ids[ok]], target[ok]
        if normal_only:
            normals = normals[ok]
            # Opposing edges of thin objects can cancel the image gradient.
            # Preserve their full point error rather than losing observations.
            normal_fallback_samples += int(np.count_nonzero(np.linalg.norm(normals, axis=1) <= .99))
        for side in (1, -1):
            selected = labels == side
            W[selected] = RV.rot_about_hinge(W[selected], side, -th.get(side, 0.), job.hinge['axis_xz_mm'])
        weight = np.ones(len(W))
        weight[:n_forward] = 1./max(n_forward, 1)
        weight[n_forward:] = 1./max(len(W)-n_forward, 1)
        weight = np.sqrt(weight*len(W)/2.)[:, None]

        def residual(z):
            nonlocal projection_evals
            projection_evals += 1
            cm, angles = decode(z)
            points = W.copy()
            for side in (1, -1):
                selected = labels == side
                points[selected] = RV.rot_about_hinge(points[selected], side, angles.get(side, 0.),
                                                     job.hinge['axis_xz_mm'])
            uv = raster._project_norm(job.frame.to_norm(points), cm)
            if uv is None:
                return np.full(2*len(points), 1e3)
            if normal_only:
                return (normal_or_point_residual(uv-target, normals)*weight).ravel()
            return ((uv-target)*weight).ravel()

        result = optimize.least_squares(residual, x, bounds=(lo, hi), loss='soft_l1',
                                        f_scale=1.5*lv.stride, max_nfev=C.ICP_NFEV, x_scale='jac')
        trials = []
        best = (loss, x, scene, tags)
        for fraction in (1., .5, .25):
            z = x + fraction*(result.x-x)
            value, sc, tg = evaluate(z)
            trials.append({'fraction': fraction, 'loss': value})
            if value < best[0]:
                best = (value, z, sc, tg)
        history.append({'iteration': iteration, 'loss_start': loss, 'loss': best[0], 'trials': trials,
                        'matches': len(W), 'nfev': result.nfev})
        if best[0] >= loss:
            termination = 'no_image_descent'
            break
        relative_gain = (loss-best[0])/max(abs(loss), abs(best[0]), 1e-12)
        loss, x, scene, tags = best
        if relative_gain <= LOSS_REL_TOL:
            termination = 'image_loss_converged'
            break
    cam, th = decode(x)
    final_render = RV.render_model(job, model, key, cam, th, points=False)
    front_base = fit.get('refit', {}).get('front_contour_start_mm')
    if front_base is None:
        base_render = RV.render_model(job, model, key, cam0, fit['theta'], points=False)
        front_base = RV.front_contour_mm(job, key, base_render, cam0)
    front_final = RV.front_contour_mm(job, key, final_render, cam)
    guard_measured = bool(np.isfinite(front_base) and np.isfinite(front_final))
    kept = bool(loss < initial_loss and guard_measured and front_final <= front_base + RR.FRONT_DEGRADE_MAX_MM)
    elapsed = time.perf_counter()-started
    record = {'initial_loss': initial_loss, 'loss': loss, 'history': history,
              'proposal_metric': 'contour_normal' if normal_only else 'point',
              'normal_fallback_samples': normal_fallback_samples,
              'front_base_mm': front_base, 'front_final_mm': front_final, 'front_guard_measured': guard_measured,
              'kept': kept, 'base_camera': cam0.to_dict(),
              'initial_camera': camera.to_dict(), 'initial_theta': theta,
              'proposed_camera': cam.to_dict(), 'proposed_theta': th,
              'box': box, 'inside_original_box': bool(np.all(x >= lo) and np.all(x <= hi)),
              'termination': termination, 'free_theta_sides': free_sides,
              'image_evals': image_evals, 'projection_evals': projection_evals, 'seconds': elapsed}
    result = {**fit, 'alignment': record}
    if not kept:
        return result
    at_bound = [name for i, name in enumerate(('yaw', 'pitch', 'roll', 'persp', 'scale', 'shift_x', 'shift_y'))
                if min(x[i]-lo[i], hi[i]-x[i]) < 1e-3*max(hi[i]-lo[i], 1e-9)]
    previous = fit.get('refit', {})
    result.update(camera=cam, theta=th, initial_refit=previous, initial_theta_fit=fit.get('theta_fit', {}))
    result['refit'] = {**previous, 'loss_start': float(base_loss), 'loss': float(loss),
                       'front_contour_start_mm': front_base, 'front_contour_mm': front_final, 'kept': True,
                       'at_bound': at_bound, 'evals': previous.get('evals', 0)+image_evals,
                       'seconds': previous.get('seconds', 0.)+elapsed, 'joint_alignment': True}
    result['theta_fit'] = {str(s): {**row, 'theta': th[int(s)],
                                  **({'loss': None, 'loss_basis': 'joint silhouette loss in alignment'}
                                     if int(s) in free_sides else {})}
                           for s, row in fit.get('theta_fit', {}).items()}
    return result
