"""Analytic derivatives of the existing conditional photo objective.

This changes neither optical equations nor residual weighting. Quantization and
clipping intervals use zero derivative on their boundary (a declared one-sided
choice at a nondifferentiable point); finite-difference comparisons must avoid
crossing those boundaries. The current frozen numerical experiment does not use
this module. Derivative correctness is separate from material identification.
"""
from __future__ import annotations

import numpy as np

from . import photo_lens_fit as photo


def prediction_jacobian(model, x, data):
    """Derivative of extended sRGB codes, shape (eligible pixels, 3, parameters)."""
    arrays, name = data['arrays'], data['observation']['photo_id']
    size, count = len(arrays['code']), len(model.lower)
    density_basis = data['basis'] if model.gradient else np.ones((size, 1))
    density = density_basis @ x[model.slices['density']].reshape(-1, 3)
    attenuation = np.exp(-density * data['path'])
    if model.angular:
        reflection_basis = data['angle_basis'][:, :3]
        reflection = reflection_basis @ x[model.slices['reflection']].reshape(3, 3) + data['angle_basis'][:, 3, None]
    else:
        normal = x[model.slices['reflection']] if model.mirror else np.full(3, .04)
        reflection = normal + (1-normal)*data['schlick']
        reflection_basis = 1-data['schlick']
    transmission = (1-reflection)*attenuation
    background = arrays['background']
    rear_slice = model.slices.get((name, 'rear'))
    if model.rear_mode == 'geometry_conditioned_rear':
        background = background*(1-arrays['rear_weight'][:, None]) + np.nan_to_num(arrays['rear'])*arrays['rear_weight'][:, None]
    elif rear_slice is not None:
        background = background*(1-arrays['rear_weight'][:, None]) + x[rear_slice]*arrays['rear_weight'][:, None]
    shape_slice = model.slices.get((name, 'shape'))
    field = np.exp(data['features'] @ x[shape_slice].reshape(4, 3)) if shape_slice is not None else np.ones((size, 3))
    environment = x[model.slices[(name, 'environment')]]*field
    radiance = transmission*background + reflection*environment
    gain = np.ones(3)
    exposure_slice = model.slices.get((name, 'exposure'))
    if exposure_slice is not None:
        a, b = x[model.slices[(name, 'wb')]]
        gain = np.exp(x[exposure_slice][0] + np.array([a, -a-b, b]))
    encoded_radiance = radiance*gain
    # Avoid evaluating negative powers at zero in the unselected branch.
    encode_slope = np.full_like(encoded_radiance, 255*12.92)
    nonlinear = encoded_radiance > .0031308
    encode_slope[nonlinear] = 255*1.055/2.4*encoded_radiance[nonlinear]**(1/2.4-1)
    jac = np.zeros((size, 3, count))

    def rgb_block(parameter_slice, coefficients, channel_factor):
        for channel in range(3):
            jac[:, channel, parameter_slice.start+channel:parameter_slice.stop:3] = coefficients*channel_factor[:, channel, None]

    rgb_block(model.slices['density'], density_basis, -data['path']*transmission*background)
    if model.mirror:
        rgb_block(model.slices['reflection'], reflection_basis, environment-attenuation*background)
    rgb_block(model.slices[(name, 'environment')], np.ones((size, 1)), reflection*field)
    if shape_slice is not None:
        rgb_block(shape_slice, data['features'], reflection*environment)
    if rear_slice is not None:
        rgb_block(rear_slice, arrays['rear_weight'][:, None], transmission)
    jac *= (gain*encode_slope)[:, :, None]
    if exposure_slice is not None:
        jac[:, :, exposure_slice] = (encoded_radiance*encode_slope)[:, :, None]
        wb = model.slices[(name, 'wb')]
        jac[:, :, wb] = (encoded_radiance*encode_slope)[:, :, None]*np.array([[1., 0.], [-1., -1.], [0., 1.]])[None, :, :]
    return jac


def _data_jacobian(model, x, data):
    prediction = model.predict(x, data)
    interval_error = photo._interval_residual(prediction, data['arrays']['code'])
    scaled_error = interval_error/model.policy.code_robust_scale
    s = np.sqrt(1+scaled_error*scaled_error)
    robust_slope = np.sqrt((s+1)/2)/s
    slope = robust_slope/model.policy.code_robust_scale*(interval_error != 0)
    train = data['train']
    value = prediction_jacobian(model, x, data)[train]
    value *= (slope[train]*data['train_weights'][:, None])[:, :, None]
    return value.reshape(-1, len(x))


def _material_jacobian(model):
    rows = []
    if model.gradient:
        # One second difference per channel per interior knot, in the order penalties() ravels them.
        block = np.zeros((3*(model.knots-2), len(model.lower)))
        start = model.slices['density'].start
        for k in range(model.knots-2):
            for channel in range(3):
                block[3*k+channel, start+channel+3*k:start+channel+3*(k+3):3] = model.policy.density_curvature_penalty*np.array([1., -2., 1.])
        rows.append(block)
    if model.angular:
        block = np.zeros((6, len(model.lower)))
        start = model.slices['reflection'].start
        for channel in range(3):
            block[channel, start+channel:start+9:3] = model.policy.reflectance_curvature_penalty*np.array([1., -2., 1.])
            block[3+channel, start+3+channel:start+9:3] = model.policy.reflectance_curvature_penalty*np.array([1., -2.])
        rows.append(block)
    return np.concatenate(rows) if rows else np.empty((0, len(model.lower)))


def _prior_block(count, parameter_slice, weight):
    block = np.zeros((parameter_slice.stop-parameter_slice.start, count))
    block[:, parameter_slice] = weight*np.eye(len(block))
    return block


def single_residual_jacobian(model, x):
    """Same row order as photo_lens_fit._Model.residual, including its priors."""
    rows = [_data_jacobian(model, x, data) for data in model.data]
    rows.append(_material_jacobian(model))
    for key, parameter_slice in model.slices.items():
        if isinstance(key, tuple) and key[1] in ('shape', 'exposure', 'wb'):
            rows.append(_prior_block(len(x), parameter_slice, model.policy.nuisance_penalty))
    return np.concatenate(rows)


def joint_residual_jacobian(model, x):
    """Same aliased parameters and row order as _JointModel.residual."""
    rows = []
    for group, local_model in model.models.items():
        indices = model.maps[group]
        if len(np.unique(indices)) != len(indices):
            raise ValueError('Unexpected parameter alias within one local model')
        local = x[indices]
        for block in [*[_data_jacobian(local_model, local, data) for data in local_model.data], _material_jacobian(local_model)]:
            expanded = np.zeros((len(block), len(x)))
            expanded[:, indices] = block
            rows.append(expanded)
    for key, parameter_slice in model.blocks.items():
        if key[0] == 'photo' and key[2] in ('shape', 'exposure', 'wb'):
            rows.append(_prior_block(len(x), parameter_slice, model.policy.nuisance_penalty))
    return np.concatenate(rows)
