"""Conditional photo-region signal, not calibrated or identified lens optics.

Pixels are the caller's uint8 sRGB-encoded RGB(A) grid. The module neither
estimates exposure/white balance nor infers a lens, coating, illumination, R or
optical density from image color. Inverse-sRGB values are decoded image signals,
not calibrated scene radiance. Nonopaque alpha is never composited onto an
invented background. A supplied v/angle field is a *candidate-coordinate
hypothesis*, including when v was approximated from local model Y rather than UV.
Unknown coordinates may be NaN; reports always use JSON null, never NaN.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np
from scipy import ndimage


EROSION_RADIUS_PX = 2
INTRINSIC_HEIGHT_BIN_COUNT = 4
MAX_PHOTOMETRIC_SAMPLES = 64
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value, name):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _json_copy(value):
    def check(item):
        if item is None or isinstance(item, (str, bool)) or type(item) is int:
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("provenance must contain only finite JSON values with string keys")
    check(value)
    return json.loads(json.dumps(value, allow_nan=False))


def _array_hash(value: np.ndarray, kind: str) -> str:
    """Hash a domain tag, dimensions and C-order bytes; not an encoded-file hash."""
    header = json.dumps({"kind": kind, "shape": list(value.shape), "dtype": value.dtype.str}, sort_keys=True).encode()
    return hashlib.sha256(header + b"\0" + np.ascontiguousarray(value).tobytes()).hexdigest()


def _coordinates(value, shape, name, upper):
    if value is None:
        return None
    array = np.asarray(value)
    if array.shape != shape or array.dtype.kind != "f":
        raise ValueError(f"{name} must be a float array matching the image grid")
    if np.isinf(array).any() or ((np.isfinite(array)) & ((array < 0) | (array > upper))).any():
        raise ValueError(f"{name} values must be NaN (unknown) or within [0,{upper}]")
    array = array.astype("<f8", copy=True)
    array[np.isnan(array)] = np.nan  # canonicalize NaN payload bits for hashing
    array[array == 0] = 0  # canonicalize negative zero
    return array


def _linearize(rgb):
    code = np.asarray(rgb, dtype=np.float64) / 255.0
    return np.where(code <= .04045, code / 12.92, ((code + .055) / 1.055) ** 2.4)


def _stats(values):
    if len(values) == 0:
        return None
    values = np.asarray(values, dtype=np.float64)
    median = np.median(values, axis=0)
    return {"minimum": np.min(values, axis=0).tolist(), "p10": np.quantile(values, .1, axis=0).tolist(),
            "median": median.tolist(), "p90": np.quantile(values, .9, axis=0).tolist(),
            "maximum": np.max(values, axis=0).tolist(),
            "median_absolute_deviation": np.median(np.abs(values - median), axis=0).tolist()}


def _measure_codes(codes):
    count = len(codes)
    low, high = codes == 0, codes == 255
    endpoints = np.any(low | high, axis=1)
    unclipped = codes[~endpoints]
    return {"count": count, "observed_code_rgb": _stats(codes), "linear_signal_rgb": _stats(_linearize(codes)),
            "endpoint_flags": {
                "low_count_rgb": low.sum(axis=0).astype(int).tolist(),
                "high_count_rgb": high.sum(axis=0).astype(int).tolist(),
                "low_fraction_rgb": low.mean(axis=0).tolist() if count else None,
                "high_fraction_rgb": high.mean(axis=0).tolist() if count else None,
                "any_channel_count": int(endpoints.sum()),
                "any_channel_fraction": float(endpoints.mean()) if count else None,
                "fully_unclipped_count": len(unclipped)},
            "fully_unclipped": {"count": len(unclipped), "observed_code_rgb": _stats(unclipped),
                                 "linear_signal_rgb": _stats(_linearize(unclipped))}}


def _coordinate_coverage(value, mask, valid):
    if value is None:
        return {"status": "unavailable", "finite_region_count": 0, "finite_measured_count": 0,
                "fraction_of_measured_pixels": None, "measured_range": None, "field_sha256": None}
    finite = np.isfinite(value)
    measured = value[finite & valid]
    return {"status": "supplied_coordinate_hypothesis" if len(measured) else "supplied_but_unmeasured",
            "finite_region_count": int(np.count_nonzero(finite & mask)), "finite_measured_count": len(measured),
            "fraction_of_measured_pixels": float(len(measured) / np.count_nonzero(valid)) if valid.any() else None,
            "measured_range": [float(measured.min()), float(measured.max())] if len(measured) else None,
            "field_sha256": _array_hash(value, "candidate_coordinate_float64_nan_unknown_v1")}


def _sample_indices(mask, interior, opaque, endpoints):
    # Reserve space for each validity/censoring stratum before filling the rest.
    # Statistics use ALL eligible pixels; this bounded ledger is not a weighted
    # or random sample and must not be used to estimate prevalence.
    strata = (interior & opaque & ~endpoints, interior & opaque & endpoints,
              interior & ~opaque, mask & ~interior)
    selected = set()
    def spaced(indices, limit):
        if len(indices) <= limit:
            return indices
        positions = np.linspace(0, len(indices)-1, limit, dtype=np.int64)
        return indices[positions]
    for stratum in strata:
        selected.update(map(int, spaced(np.flatnonzero(stratum), MAX_PHOTOMETRIC_SAMPLES // len(strata))))
    candidates = np.flatnonzero(mask)
    remaining = candidates[~np.isin(candidates, list(selected))]
    selected.update(map(int, spaced(remaining, MAX_PHOTOMETRIC_SAMPLES - len(selected))))
    return sorted(selected)


def measure_region_appearance(image: np.ndarray, mask: np.ndarray, *, source_sha256: str, region_id: str,
                              provenance: dict, intrinsic_v: np.ndarray | None = None,
                              incidence_degrees: np.ndarray | None = None) -> dict:
    """Measure observed signal on a conservative region interior; return JSON.

    Inputs: uint8 HWC RGB/RGBA, bool HW mask on that exact grid, caller-declared
    encoded source SHA-256, a nonempty region id, and finite JSON provenance with
    a nonempty ``method``. A provenance ``source_sha256``, if present, must match.
    Array bytes are separately pinned; this function cannot verify encoded-file
    identity without receiving those bytes. No coordinates refer to a resized or
    original file unless the caller records that grid mapping in provenance.

    Optional float HW fields accept NaN as unknown; v is in [0,1], incidence in
    [0,180]. They require ``provenance.coordinate_fields`` with ``source`` equal
    to ``candidate``, a lowercase ``candidate_sha256`` and a nonempty ``method``.
    Assumptions such as model-local-Y height proxies are retained verbatim, never
    promoted to verified intrinsic UVs. No bins are fabricated from image rows.

    A fixed 5x5 square erosion treats outside-image pixels as excluded. Only
    alpha=255 interior pixels contribute statistics. Endpoint 0/255 flags are
    possible censoring, not proof of sensor clipping; both all-code and fully
    non-endpoint statistics are retained. At most 64 deterministic samples also
    expose excluded pixels, with null linear measurements and explicit flags.

    ``status=measured`` means only that at least one eligible stored image signal
    exists. Calibration, uncertainty, semantics and optical parameters always
    remain unmeasured. Empty evidence is never absence, acceptance or a fit.
    """
    source_sha256 = _digest(source_sha256, "source_sha256")
    region_id = _text(region_id, "region_id")
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be a dictionary")
    provenance = _json_copy(provenance)
    _text(provenance.get("method"), "provenance.method")
    if "source_sha256" in provenance and provenance["source_sha256"] != source_sha256:
        raise ValueError("provenance.source_sha256 does not match source_sha256")
    pixels, region = np.asarray(image), np.asarray(mask)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4) or min(pixels.shape[:2]) <= 0:
        raise ValueError("image must be nonempty uint8 HWC RGB or RGBA")
    if region.dtype != np.bool_ or region.shape != pixels.shape[:2]:
        raise ValueError("mask must be bool HW on the image grid")
    v = _coordinates(intrinsic_v, region.shape, "intrinsic_v", 1)
    angles = _coordinates(incidence_degrees, region.shape, "incidence_degrees", 180)
    coordinate_provenance = None
    if v is not None or angles is not None:
        coordinate_provenance = provenance.get("coordinate_fields")
        if not isinstance(coordinate_provenance, dict) or coordinate_provenance.get("source") != "candidate":
            raise ValueError("coordinate fields require explicit candidate provenance")
        _digest(coordinate_provenance.get("candidate_sha256"), "coordinate_fields.candidate_sha256")
        _text(coordinate_provenance.get("method"), "coordinate_fields.method")
    rgb = pixels[:, :, :3]
    alpha = pixels[:, :, 3] if pixels.shape[2] == 4 else np.full(region.shape, 255, dtype=np.uint8)
    opaque = alpha == 255
    interior = ndimage.binary_erosion(region, structure=np.ones((2*EROSION_RADIUS_PX+1,)*2, dtype=bool), border_value=0)
    valid = interior & opaque
    region_count, interior_count, measured_count = map(int, (region.sum(), interior.sum(), valid.sum()))
    reasons = []
    if not region_count:
        reasons.append("empty_region_hypothesis")
    elif not interior_count:
        reasons.append("no_interior_after_fixed_pixel_erosion")
    elif not measured_count:
        reasons.append("no_opaque_interior_pixels")
    endpoints = np.any((rgb == 0) | (rgb == 255), axis=2)
    samples = []
    for index in _sample_indices(region, interior, opaque, endpoints):
        y, x = np.unravel_index(index, region.shape)
        code, alpha_valid, photometric_valid = rgb[y, x], bool(opaque[y, x]), bool(valid[y, x])
        angle = float(angles[y, x]) if angles is not None and np.isfinite(angles[y, x]) else None
        samples.append({"xy": [int(x), int(y)], "code_rgb": code.astype(int).tolist(), "alpha_code": int(alpha[y, x]),
                        "opaque_alpha": alpha_valid, "interior": bool(interior[y, x]), "photometric_valid": photometric_valid,
                        "linear_signal_rgb": _linearize(code).tolist() if photometric_valid else None,
                        "clipped_low_rgb": (code == 0).tolist(), "clipped_high_rgb": (code == 255).tolist(),
                        "unclipped_rgb": ((code > 0) & (code < 255)).tolist(), "fully_unclipped": not bool(endpoints[y, x]),
                        "intrinsic_v_hypothesis": float(v[y, x]) if v is not None and np.isfinite(v[y, x]) else None,
                        "incidence_degrees_hypothesis": angle,
                        "front_interface_eligible": angle < 90 if angle is not None else None})
    bins = None
    if v is not None and np.any(valid & np.isfinite(v)):
        bins = []
        for index in range(INTRINSIC_HEIGHT_BIN_COUNT):
            lower, upper = index/INTRINSIC_HEIGHT_BIN_COUNT, (index+1)/INTRINSIC_HEIGHT_BIN_COUNT
            last = index == INTRINSIC_HEIGHT_BIN_COUNT - 1
            members = valid & np.isfinite(v) & (v >= lower) & ((v <= upper) if last else (v < upper))
            selected_v = v[members]
            selected_angles = angles[members & np.isfinite(angles)] if angles is not None else np.array([])
            bins.append({"index": index, "lower_inclusive": lower, "upper": upper, "upper_inclusive": last,
                         "status": "measured" if len(selected_v) else "unmeasured", "coordinate_hypothesis": True,
                         "observed_v_range": [float(selected_v.min()), float(selected_v.max())] if len(selected_v) else None,
                         "signal": _measure_codes(rgb[members]),
                         "incidence_degrees_hypothesis": {"count": len(selected_angles), "statistics": _stats(selected_angles)}})
    report = {
        "schema_version": 1, "method": "conditional_region_signal_v1", "region_id": region_id,
        "status": "measured" if measured_count else "unmeasured", "reasons": reasons,
        "measurement_scope": "stored_photo_signal_in_region_hypothesis",
        "source_sha256": source_sha256, "encoded_source_hash_verified_here": False,
        "decoded_pixels_sha256": _array_hash(pixels, "uint8_photo_grid_v1"),
        "region_mask_sha256": _array_hash(region, "bool_region_hypothesis_v1"),
        "image_size": [int(pixels.shape[1]), int(pixels.shape[0])], "channels": int(pixels.shape[2]),
        "pixel_coordinates": "supplied_image_pixel_centers_xy_x_right_y_down",
        "provenance": provenance, "coordinate_provenance": coordinate_provenance,
        "calibration_status": "unmeasured", "measurement_uncertainty_status": "unmeasured",
        "semantic_identity_status": "unmeasured", "optical_parameters_status": "unmeasured",
        "linear_signal_interpretation": "inverse_sRGB_decoded_image_signal_not_calibrated_radiance",
        "dispersion_interpretation": "spatial_signal_variation_not_measurement_noise",
        "policy": {"erosion_radius_px": EROSION_RADIUS_PX, "erosion_neighborhood": "square_Chebyshev",
                   "outside_image": "excluded", "eligible_alpha_code": 255, "srgb_encoding_assumed": True,
                   "endpoint_flags": "codes_0_and_255_possible_censoring_not_proven_sensor_clipping",
                   "intrinsic_height_bins": INTRINSIC_HEIGHT_BIN_COUNT, "maximum_samples": MAX_PHOTOMETRIC_SAMPLES,
                   "samples": "validity_stratified_then_evenly_spaced_row_major_not_population_weights",
                   "array_hash_encoding": "sorted_JSON_kind_shape_dtype_NUL_C_order_bytes"},
        "coverage": {"region_pixels": region_count, "interior_pixels": interior_count,
                     "boundary_excluded_pixels": region_count-interior_count, "opaque_interior_pixels": measured_count,
                     "nonopaque_region_pixels": int(np.count_nonzero(region & ~opaque)),
                     "nonopaque_interior_pixels": int(np.count_nonzero(interior & ~opaque)),
                     "measured_fraction_of_region": measured_count/region_count if region_count else None,
                     "interior_fraction_of_region": interior_count/region_count if region_count else None,
                     "semantic_region_coverage": "unmeasured"},
        "signal": _measure_codes(rgb[valid]),
        "coordinate_coverage": {"intrinsic_v_hypothesis": _coordinate_coverage(v, region, valid),
                                "incidence_degrees_hypothesis": _coordinate_coverage(angles, region, valid)},
        "intrinsic_height_bins": bins, "samples": samples,
        "limitations": ["Region membership and semantic lens identity are hypotheses, not established by color.",
                        "Inverse sRGB does not undo exposure, white balance, local edits or photographic tone mapping.",
                        "Tint, reflection, illumination and background remain inseparable from these observations alone.",
                        "Supplied coordinates and incidence depend on the pinned candidate and declared method; UV correctness is unmeasured.",
                        "Fixed pixel erosion limits boundary mixing but cannot prove every retained pixel belongs to one material.",
                        "Endpoint codes may be censored; robust spread is not a calibrated measurement uncertainty."]}
    report["measurement_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, allow_nan=False,
                                                            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return report
