"""Opt-in camera/hinge diagnostics from named 3D-to-photo correspondences.

Unlike a silhouette match, each observation identifies a physical point. Inputs
are measurements, not nearest points selected from the candidate's own render.
The caller owns correspondence/visibility validation. Rank and uncertainty are
reported so an unobserved frame/hinge degree of freedom cannot masquerade as a
measured pose. This helper is not connected to the frozen WP6 camera fitter.
"""
import numpy as np
from scipy.optimize import least_squares

from . import cameras as C, relief_views as RV, relief_rules as RR


def fit_anchors(world_mm, pixels, parts, *, frame, camera, theta, hinge_axis,
                pivot, unit_px, free_sides=(), sigma_px=1., max_nfev=1000,
                probe_world_mm=None, probe_parts=None):
    world_mm, pixels = np.asarray(world_mm, float), np.asarray(pixels, float)
    parts = np.asarray(parts)
    if (world_mm.ndim != 2 or world_mm.shape[1] != 3 or pixels.shape != (len(world_mm), 2)
            or parts.shape != (len(world_mm),) or not len(world_mm)
            or not np.isfinite(world_mm).all() or not np.isfinite(pixels).all()
            or not np.isin(parts, [-1, 0, 1]).all() or not np.isfinite(sigma_px) or sigma_px <= 0
            or not np.isfinite(unit_px) or unit_px <= 0):
        raise ValueError('Invalid anchor observations')
    parts = parts.astype(int)
    requested = list(free_sides)
    if len(set(requested)) != len(requested) or any(s not in (-1, 1) for s in requested):
        raise ValueError('free_sides must be distinct temple sides')
    # A wholly unobserved temple is never given a fitted angle.
    active = [s for s in requested if np.any(parts == s)]
    fixed = {int(s): float(v) for s, v in theta.items()}
    box = RV._box(camera, unit_px, pivot, RR.REFIT_BOUNDS)
    box += [RR.THETA_RANGE_DEG for s in active]
    lo, hi = np.asarray(box).T
    x0 = np.r_[C.pack(camera, unit_px, pivot), [fixed.get(s, 0.) for s in active]]
    x0 = np.clip(x0, lo+1e-10, hi-1e-10)

    def decode(x):
        return C.unpack(x[:7], unit_px, pivot), {**fixed, **{s: float(x[7+i]) for i, s in enumerate(active)}}

    def project(x, world=world_mm, labels=parts):
        cam, angles = decode(x)
        points = world.copy()
        for side in (1, -1):
            sel = labels == side
            points[sel] = RV.rot_about_hinge(points[sel], side, angles.get(side, 0.), hinge_axis)
        return C.project(frame.to_norm(points), cam)

    def residual(x):
        return ((project(x)-pixels)/sigma_px).ravel()

    result = least_squares(residual, x0, bounds=(lo, hi), x_scale='jac', max_nfev=max_nfev,
                           ftol=1e-12, xtol=1e-12, gtol=1e-12)
    # Normalize by the full permitted parameter span before diagnosing rank.
    scaled = result.jac*(hi-lo)[None, :]
    singular = np.linalg.svd(scaled, compute_uv=False)
    rank = int(np.sum(singular > (singular[0]*1e-7 if len(singular) else 0)))
    dimension = len(x0)
    cam, angles = decode(result.x)
    errors = project(result.x)-pixels
    initial_errors = project(x0)-pixels
    names = ['yaw_deg', 'pitch_deg', 'roll_deg', 'perspective_times_10',
             'log_pivot_magnification_times_100', 'pivot_x_percent_width',
             'pivot_y_percent_width']+[f'theta_{s}_deg' for s in active]
    # Work directly with the SVD: squaring the condition number in J.T @ J
    # discards weak directions and can falsely report small uncertainties.
    _, sv, vt = np.linalg.svd(scaled, full_matrices=False)
    covariance = None
    probes = None
    if rank == dimension:
        covariance = ((vt.T / sv**2) @ vt) * (hi-lo)[:, None] * (hi-lo)[None, :]
    if probe_world_mm is not None or probe_parts is not None:
        world = np.asarray(probe_world_mm, float)
        labels = np.asarray(probe_parts)
        if (world.ndim != 2 or world.shape[1] != 3 or labels.shape != (len(world),)
                or not np.isfinite(world).all() or not np.isin(labels, [-1, 0, 1]).all()):
            raise ValueError('Invalid probe points')
        labels = labels.astype(int)
        sd = None
        if covariance is not None:
            step = np.maximum(1e-5, np.abs(result.x)*1e-6)
            jac = np.empty((len(world), 2, dimension))
            for i in range(dimension):
                offset = np.zeros(dimension); offset[i] = step[i]
                jac[:, :, i] = (project(result.x+offset, world, labels)-project(result.x-offset, world, labels))/(2*step[i])
            pcov = np.einsum('nai,ij,nbj->nab', jac, covariance, jac)
            sd = np.sqrt(np.maximum(0., np.diagonal(pcov, axis1=1, axis2=2))).tolist()
        probes = {'pixels': project(result.x, world, labels).tolist(),
                  'conditional_coordinate_sigma_px': sd,
                  'unmeasured_hinge_points': [int(i) for i, s in enumerate(labels)
                      if s in requested and s not in active]}
    return {'camera': cam, 'theta': angles, 'free_sides': active,
            'unobserved_sides': [s for s in requested if s not in active],
            'rank': rank, 'dimension': dimension, 'full_rank': rank == dimension,
            'singular_values_scaled': singular.tolist(),
            'conditional_packed_parameter_sigma': None if covariance is None else
                dict(zip(names, np.sqrt(np.maximum(0., np.diag(covariance))).tolist())),
            'conditional_sigma_valid': rank == dimension,
            'probe': probes,
            'pixel_residuals': errors.tolist(), 'rms_px': float(np.sqrt(np.mean(errors**2))),
            'initial_rms_px': float(np.sqrt(np.mean(initial_errors**2))),
            'bounds': box, 'at_bound': [name for i, name in enumerate(names)
                if min(result.x[i]-lo[i], hi[i]-result.x[i]) < 1e-3*(hi[i]-lo[i])],
            'nfev': result.nfev, 'solver_success': bool(result.success),
            'identifiability_note': 'Full rank is not evidence of useful precision. Sigma is local, unbounded and conditional on correct independent observations, supplied sigma, rigid anchor geometry and fixed unmeasured hinge angles; it excludes those systematic errors.'}
