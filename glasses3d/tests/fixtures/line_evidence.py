"""Analytic image controls independent of the temple model, renderer and tracer.

The intended edge is distance=0. Other edges are deliberately not labelled for
the estimator. Masks identify known corrupt pixels only, never the true edge.
The matrix is fixed before measuring the tracer; all cases must be reported.
"""
from __future__ import annotations

import itertools

import numpy as np
from scipy.special import ndtr


KINDS = ('clean', 'noise', 'parallel_equal', 'parallel_stronger', 'highlight',
         'clipped', 'decal', 'occluded', 'erased')


def cases(split='calibration'):
    if split == 'calibration':
        angles, blurs, seeds = (0., 31., 87.), (1., 2.), (0, 1, 2, 3)
    elif split == 'validation':
        angles, blurs, seeds = (17., 64., 120.), (.8, 1.6), (10, 11, 12, 13)
    else:
        raise ValueError(split)
    for kind, angle, blur, signs in itertools.product(
            KINDS, angles, blurs, itertools.product((-1., 1.), repeat=2)):
        for seed in (seeds if kind == 'noise' else (0,)):
            yield dict(kind=kind, angle_deg=angle, blur_px=blur, signs=list(signs), seed=seed)


def make_case(kind, angle_deg, blur_px, signs, seed=0):
    """(L*, guide, true endpoints, valid pixels), all in image coordinates."""
    angle = np.deg2rad(angle_deg)
    tangent = np.array([np.cos(angle), np.sin(angle)])
    normal = np.array([-tangent[1], tangent[0]])
    centre = np.array([128., 128.])
    yy, xx = np.mgrid[:256, :256]
    distance = (xx-centre[0])*normal[0] + (yy-centre[1])*normal[1]
    along = (xx-centre[0])*tangent[0] + (yy-centre[1])*tangent[1]
    lightness = 40. + 12.*ndtr(distance/blur_px)
    valid = np.ones(lightness.shape, bool)
    if kind == 'noise':
        lightness += np.random.default_rng(seed).normal(0., 1.5, lightness.shape)
    elif kind in ('parallel_equal', 'parallel_stronger'):
        contrast = 12. if kind == 'parallel_equal' else 24.
        lightness += contrast*ndtr((distance-4.)/blur_px)
    elif kind == 'highlight':
        lightness += 30.*np.exp(-.5*((distance-4.)/1.4)**2)
    elif kind in ('clipped', 'decal', 'occluded'):
        corrupt = (np.abs(along) < 30.) & (np.abs(distance) < 12.)
        if kind == 'clipped':
            lightness[corrupt] = 100.
        elif kind == 'decal':
            ink = 15. + 60.*(np.sin(along/3.) > 0)
            lightness[corrupt] = ink[corrupt]
        else:
            lightness[corrupt] = 40.
        valid[corrupt] = False
    elif kind == 'erased':
        lightness[:] = 40.
        valid[:] = False
    elif kind != 'clean':
        raise ValueError(kind)
    exact = centre + np.array([-75., 75.])[:, None]*tangent
    guide = exact + 6.*np.array(signs)[:, None]*normal
    return lightness, guide, exact, valid


def endpoint_error(line, exact):
    """Distance to the fitted infinite line at BOTH known original endpoints.

    This includes angular error extrapolated beyond a returned trimmed segment.
    No model or production projection/distance helper is used as the oracle.
    """
    a, b = np.asarray([line['p0'], line['p1']], float)
    t = (b-a)/np.linalg.norm(b-a)
    return np.abs((exact-a) @ np.array([-t[1], t[0]]))
