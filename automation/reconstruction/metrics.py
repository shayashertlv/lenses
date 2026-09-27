"""Deterministic measurements of aligned reference and rendered component masks.

This module measures evidence; it does not choose an acceptance threshold or fit
cameras. Masks must be two-dimensional boolean arrays in the same image space.
Reference foreground means the named component, so lenses/openings and frame
material must be scored separately when those observations are available.

``status == 'measured'`` is NOT a quality pass. An absent reference or one with no
observed foreground is unmeasured. An empty candidate against observed reference
foreground is a measured failure. Unavailable measurements are ``None``, never
zero, NaN, or infinity. All reports can be serialized with ``allow_nan=False``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

import numpy as np
from scipy import ndimage


_NEIGHBORHOOD = np.ones((3, 3), dtype=bool)


def _mask(value: Any, name: str, shape: tuple[int, int] | None = None) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 2 or 0 in array.shape or array.dtype != np.bool_:
        raise ValueError(f"{name} must be a nonempty 2D boolean array")
    if shape is not None and array.shape != shape:
        raise ValueError(f"{name} has shape {array.shape}; expected {shape}")
    return array


def _boundary(mask: np.ndarray) -> np.ndarray:
    # Keep inner hole boundaries as well as the outside silhouette. Extract before
    # applying validity so an occluder's edge cannot become an object contour.
    return mask & ~ndimage.binary_erosion(mask, structure=_NEIGHBORHOOD, border_value=0)


def _stats(distances: np.ndarray) -> dict[str, float]:
    maximum = float(np.max(distances))
    return {
        # Normalizing before summation keeps finite inputs finite even when a
        # caller supplies an extreme (but representable) normalization scale.
        "mean": float(np.mean(distances / maximum) * maximum) if maximum else 0.0,
        "p95": float(np.percentile(distances, 95, method="linear")),
        "max": maximum,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return float(numerator / denominator) if denominator else None


def _empty_boundary(reason: str, tolerance: float) -> dict[str, Any]:
    return {
        "status": "unmeasured", "reason": reason,
        "tolerance_width_fraction": tolerance,
        "precision": None, "recall": None, "f1": None,
        "reference_to_candidate": None, "candidate_to_reference": None,
        "symmetric": None, "missing_candidate_contour": None,
        "missing_contour_penalty": None,
    }


def score_masks(
    reference: np.ndarray | None,
    candidate: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    reference_width_px: float | None = None,
    boundary_tolerance: float = 0.01,
) -> dict[str, Any]:
    """Measure aligned foreground overlap and bidirectional contour disagreement.

    ``valid_mask`` marks pixels with reliable reference labels (including reliable
    background), not merely foreground. IoU uses those pixels only. Contours are
    extracted from each ORIGINAL mask, then restricted to pixels whose entire
    3x3 neighborhood is valid and inside the image. This avoids fabricated edges
    at visibility/crop boundaries. Coverage records the fraction retained; low
    coverage must not be interpreted as full-shape agreement.

    Distances and ``boundary_tolerance`` are fractions of ``reference_width_px``.
    Supply the full reference object's width when comparing individual components
    or partial views. Otherwise the reference foreground's full in-image bounding
    box width is used, including foreground outside ``valid_mask``. This is a
    pixel scale only, NOT a physical or inferred hidden-object dimension.

    Directional distances use Euclidean nearest-boundary distances. Symmetric
    mean equally weights the two directional means; symmetric p95 and max are
    the worse of the directional values, preserving localized disagreement.
    With observable reference contour but no observable candidate contour, all
    contour errors receive a finite image-diagonal/width penalty and boundary
    precision/recall are zero. The explicit ``missing_candidate_contour`` flag
    distinguishes this failure convention from an observed correspondence.

    Empty/missing reference or no observable reference foreground is unmeasured;
    an empty candidate is measured (IoU zero). Foreground can be measured while
    contour evidence is unavailable, so inspect ``boundary.status`` separately.
    Bad array shapes/dtypes or nonfinite/nonpositive scales raise ``ValueError``.
    No alignment, threshold-based quality decision, or aggregation is performed.
    """
    candidate = _mask(candidate, "candidate")
    try:
        tolerance = float(boundary_tolerance)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("boundary_tolerance must be finite and nonnegative") from exc
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("boundary_tolerance must be finite and nonnegative")
    width = None
    if reference_width_px is not None:
        try:
            width = float(reference_width_px)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("reference_width_px must be finite and positive") from exc
        if not math.isfinite(width) or width <= 0:
            raise ValueError("reference_width_px must be finite and positive")
        if not math.isfinite(math.hypot(*candidate.shape) / width):
            raise ValueError("reference_width_px is too small for finite normalized distances")

    valid = (np.ones(candidate.shape, dtype=bool) if valid_mask is None
             else _mask(valid_mask, "valid_mask", candidate.shape))
    known_neighborhood = ndimage.binary_erosion(valid, structure=_NEIGHBORHOOD,
                                               border_value=0)
    candidate_boundary = _boundary(candidate)
    candidate_observable_boundary = candidate_boundary & known_neighborhood
    coverage = {
        "valid_pixels": int(valid.sum()), "total_pixels": int(valid.size),
        "valid_pixel_fraction": float(valid.mean()),
        "reference_foreground_pixels": None,
        "reference_visible_foreground_pixels": None,
        "reference_foreground_fraction": None,
        "reference_boundary_pixels": None,
        "reference_observable_boundary_pixels": None,
        "reference_boundary_fraction": None,
        "candidate_boundary_pixels": int(candidate_boundary.sum()),
        "candidate_observable_boundary_pixels": int(candidate_observable_boundary.sum()),
        "candidate_boundary_fraction": _ratio(int(candidate_observable_boundary.sum()),
                                                int(candidate_boundary.sum())),
        "reference_touches_image_border": None,
    }
    report = {
        "status": "unmeasured", "reason": None,
        "reference_width_px": width,
        "normalization_source": "supplied" if width is not None else None,
        "foreground_iou": None,
        "foreground_precision": None, "foreground_recall": None,
        "missing_foreground_fraction": None, "extra_foreground_fraction": None,
        "reference_foreground_pixels": None,
        "candidate_foreground_pixels": int((candidate & valid).sum()),
        "intersection_pixels": None, "union_pixels": None,
        "coverage": coverage,
        "boundary": _empty_boundary("reference_missing", tolerance),
    }
    if reference is None:
        report["reason"] = "reference_missing"
        return report
    reference = _mask(reference, "reference", candidate.shape)
    reference_boundary = _boundary(reference)
    reference_observable_boundary = reference_boundary & known_neighborhood
    reference_count = int(reference.sum())
    visible_reference_count = int((reference & valid).sum())
    coverage.update({
        "reference_foreground_pixels": reference_count,
        "reference_visible_foreground_pixels": visible_reference_count,
        "reference_foreground_fraction": _ratio(visible_reference_count, reference_count),
        "reference_boundary_pixels": int(reference_boundary.sum()),
        "reference_observable_boundary_pixels": int(reference_observable_boundary.sum()),
        "reference_boundary_fraction": _ratio(int(reference_observable_boundary.sum()),
                                                int(reference_boundary.sum())),
        "reference_touches_image_border": bool(reference[0, :].any() or reference[-1, :].any()
                                                or reference[:, 0].any() or reference[:, -1].any()),
    })
    if width is None and reference_count:
        columns = np.flatnonzero(reference.any(axis=0))
        width = float(columns[-1] - columns[0] + 1)
        report["reference_width_px"] = width
        report["normalization_source"] = "reference_foreground_bbox"
    reason = ("reference_empty" if not reference_count else
              "no_valid_pixels" if not valid.any() else
              "no_visible_reference_foreground" if not visible_reference_count else None)
    if reason is not None:
        report["reason"] = reason
        report["boundary"] = _empty_boundary(reason, tolerance)
        return report

    intersection = int((reference & candidate & valid).sum())
    union = int(((reference | candidate) & valid).sum())
    candidate_count = report["candidate_foreground_pixels"]
    report.update({
        "status": "measured", "foreground_iou": float(intersection / union),
        "foreground_precision": float(intersection / candidate_count) if candidate_count else 0.0,
        "foreground_recall": float(intersection / visible_reference_count),
        "missing_foreground_fraction": float((visible_reference_count - intersection)
                                              / visible_reference_count),
        "extra_foreground_fraction": float((candidate_count - intersection)
                                            / visible_reference_count),
        "reference_foreground_pixels": visible_reference_count,
        "intersection_pixels": intersection, "union_pixels": union,
    })
    if not reference_observable_boundary.any():
        report["boundary"] = _empty_boundary("no_observable_reference_contour", tolerance)
        return report

    missing_candidate = not candidate_observable_boundary.any()
    penalty = math.hypot(*candidate.shape) / width
    if missing_candidate:
        ref_to_candidate = candidate_to_ref = np.asarray([penalty])
        precision = recall = 0.0
    else:
        ref_to_candidate = (ndimage.distance_transform_edt(~candidate_observable_boundary)
                            [reference_observable_boundary] / width)
        candidate_to_ref = (ndimage.distance_transform_edt(~reference_observable_boundary)
                            [candidate_observable_boundary] / width)
        precision = float(np.mean(candidate_to_ref <= tolerance))
        recall = float(np.mean(ref_to_candidate <= tolerance))
    r_stats, c_stats = _stats(ref_to_candidate), _stats(candidate_to_ref)
    report["boundary"] = {
        "status": "measured", "reason": None,
        "tolerance_width_fraction": tolerance,
        "precision": precision, "recall": recall,
        "f1": float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
        "reference_to_candidate": r_stats, "candidate_to_reference": c_stats,
        "symmetric": {"mean": r_stats["mean"] / 2 + c_stats["mean"] / 2,
                      "p95": max(r_stats["p95"], c_stats["p95"]),
                      "max": max(r_stats["max"], c_stats["max"])},
        "missing_candidate_contour": missing_candidate,
        "missing_contour_penalty": float(penalty) if missing_candidate else None,
    }
    return report


def score_components(
    references: Mapping[str, np.ndarray | None],
    candidates: Mapping[str, np.ndarray],
    *,
    valid_masks: Mapping[str, np.ndarray] | None = None,
    reference_width_px: float | None = None,
    boundary_tolerance: float = 0.01,
    required_components: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    """Score each named component independently without a hiding global average.

    Missing candidate keys become empty candidates when the reference shape is
    known, and consequently fail against observed reference foreground. Missing
    reference keys and required components without either mask stay unmeasured.
    Keys are sorted for deterministic reports. Extra candidate-only components
    are reported as unmeasured, not silently discarded or inferred to be wrong.
    Use the same supplied object width for comparable component contour errors.
    """
    names = sorted(set(references) | set(candidates) | set(required_components))
    result = {}
    for name in names:
        reference = references.get(name)
        candidate = candidates.get(name)
        missing_candidate = candidate is None
        valid = None if valid_masks is None else valid_masks.get(name)
        if candidate is None:
            if reference is not None:
                candidate = np.zeros(_mask(reference, f"references[{name!r}]").shape, dtype=bool)
            elif valid is not None:
                candidate = np.zeros(_mask(valid, f"valid_masks[{name!r}]").shape, dtype=bool)
            else:
                # No dimensions exist for this component. The sentinel is never
                # measured; it only lets the ordinary missing-reference contract
                # produce the same report schema without invented observations.
                candidate = np.zeros((1, 1), dtype=bool)
        report = score_masks(reference, candidate, valid_mask=valid,
                             reference_width_px=reference_width_px,
                             boundary_tolerance=boundary_tolerance)
        report["candidate_component_missing"] = missing_candidate
        if reference is None and missing_candidate and valid is None:
            report["coverage"] = None
            report["candidate_foreground_pixels"] = None
        result[name] = report
    return result
