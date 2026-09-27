"""Candidate-guided image-edge proposals; no semantic or final quality claim.

Given a frozen projected contour and its image-space outward normals, search a
bounded normal corridor in an unchanged photograph. Directional RGB derivatives
preserve color-only edges. Comparable peaks remain ambiguous, missing evidence
does not become zero error, and subpixel sampling does not remove native-pixel
uncertainty. These associations depend on the initial model/camera; they can
select a rim, temple, reflection or shadow edge rather than a lens boundary.

Compute and persist this report BEFORE shape optimization. Recomputing it after
each proposed shape change would move the target and risk confirming the model
instead of measuring an improvement. Visibility/occlusion must be supplied by a
separate geometric instrument; this module does not infer them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from numbers import Real
from typing import Any

import numpy as np
from scipy import ndimage, signal


@dataclass(frozen=True)
class EdgeMatchConfig:
    search_radius_px: float | None = None
    radius_fraction: float = 0.02
    minimum_radius_px: float = 2.0
    maximum_radius_px: float = 24.0
    sample_step_px: float = 0.25
    smoothing_sigma_px: float = 0.8
    minimum_strength: float = 4.0
    minimum_prominence: float = 2.0
    noise_multiplier: float = 3.0
    minimum_alignment: float = 0.70
    minimum_peak_separation_px: float = 1.5
    competing_peak_ratio: float = 0.65
    minimum_confidence: float = 0.35
    minimum_sigma_px: float = 0.50

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "search_radius_px" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, float(value))
        for name in ("minimum_alignment", "competing_peak_ratio", "minimum_confidence"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0,1)")
        if self.maximum_radius_px < self.minimum_radius_px or self.sample_step_px > self.minimum_radius_px:
            raise ValueError("Invalid search radius or sampling step")
        if self.sample_step_px < 0.05:
            raise ValueError("Sampling finer than 0.05 pixels is unsupported")


@dataclass(frozen=True)
class EdgeCorrespondenceReport:
    contour_xy: np.ndarray
    unit_normals_xy: np.ndarray
    matched_xy: np.ndarray
    sigma_px: np.ndarray
    confidence: np.ndarray
    status: tuple[str, ...]
    points: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]

    @property
    def selected_mask(self) -> np.ndarray:
        return np.asarray([status == "selected" for status in self.status], dtype=bool)

    def to_report(self) -> dict[str, Any]:
        return {"schema_version": 1, "method": "frozen_candidate_normal_rgb_edge_search_v1",
                "coordinate_system": "native image pixels; xy with positive y downward",
                "scope": "candidate-dependent image-edge proposals only; semantic identity and AR quality unmeasured",
                "diagnostics": self.diagnostics, "points": list(self.points)}


def _points(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.kind not in "fiu" or array.ndim != 2 or array.shape[1] != 2 or not len(array) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a nonempty finite Nx2 array")
    return array.astype(np.float64, copy=True)


def _provenance_hash(value: str | None, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value)):
        raise ValueError(f"{name} must be a SHA-256 hex digest or None")
    return value.lower() if value is not None else None


def match_contour_edges(
    rgb_uint8: np.ndarray, contour_xy: Any, outward_normals: Any, *,
    config: EdgeMatchConfig | None = None, source_sha256: str | None = None,
    model_sha256: str | None = None, camera_sha256: str | None = None,
) -> EdgeCorrespondenceReport:
    """Propose fixed correspondences without forcing an edge for every point.

    Unselected matched coordinates/sigmas are NaN in the array interface and
    None in the JSON report. Confidence is a heuristic diagnostic, not a
    calibrated correctness probability. Caller hashes are provenance labels;
    the decoded image and initial contour also receive independently computed
    hashes. The function never updates a camera, model or image.
    """
    config = config or EdgeMatchConfig()
    rgb = np.asarray(rgb_uint8)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 5:
        raise ValueError("rgb_uint8 must be an RGB uint8 image of at least 5x5 pixels")
    contour = _points(contour_xy, "contour_xy")
    normals = _points(outward_normals, "outward_normals")
    if normals.shape != contour.shape:
        raise ValueError("Contour and normal counts differ")
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 1e-12):
        raise ValueError("Every contour point requires a nonzero outward normal")
    normals /= lengths[:, None]
    provenance = {name: _provenance_hash(value, name) for name, value in (
        ("source_sha256", source_sha256), ("model_sha256", model_sha256), ("camera_sha256", camera_sha256))}
    height, width = rgb.shape[:2]
    extent = float(np.ptp(contour, axis=0).max())
    radius = config.search_radius_px if config.search_radius_px is not None else min(
        config.maximum_radius_px, max(config.minimum_radius_px, config.radius_fraction * extent))
    if radius < config.sample_step_px * 2:
        raise ValueError("Search radius needs at least two sample steps on each side")
    steps = int(np.ceil(2 * radius / config.sample_step_px))
    offsets = np.linspace(-radius, radius, steps + 1)
    spacing = float(offsets[1] - offsets[0])
    gx, gy = [], []
    for channel in range(3):
        values = rgb[:, :, channel].astype(np.float64)
        gx.append(ndimage.gaussian_filter(values, config.smoothing_sigma_px, order=(0, 1), mode="nearest"))
        gy.append(ndimage.gaussian_filter(values, config.smoothing_sigma_px, order=(1, 0), mode="nearest"))
    gx, gy = np.stack(gx, axis=-1), np.stack(gy, axis=-1)
    magnitude = np.sqrt(np.mean(gx * gx + gy * gy, axis=2))
    border_width = max(1, int(round(min(height, width) * 0.02)))
    border_values = np.r_[magnitude[:border_width].ravel(), magnitude[-border_width:].ravel(),
                          magnitude[:, :border_width].ravel(), magnitude[:, -border_width:].ravel()]
    noise = float(np.percentile(border_values, 90))
    strength_threshold = max(config.minimum_strength, config.noise_multiplier * noise)
    prominence_threshold = max(config.minimum_prominence, config.noise_multiplier * noise * 0.5)
    matched = np.full(contour.shape, np.nan)
    sigmas, confidence = np.full(len(contour), np.nan), np.zeros(len(contour))
    records, states = [], []
    margin = max(1.0, 2 * config.smoothing_sigma_px)
    for index, (point, normal) in enumerate(zip(contour, normals)):
        positions = point + offsets[:, None] * normal
        valid = ((positions[:, 0] >= margin) & (positions[:, 0] <= width - 1 - margin) &
                 (positions[:, 1] >= margin) & (positions[:, 1] <= height - 1 - margin))
        record: dict[str, Any] = {"index": index, "initial_xy": point.tolist(), "normal_xy": normal.tolist(),
                                  "valid_search_fraction": float(valid.mean()), "status": "missing", "reason": "no_supported_peak",
                                  "matched_xy": None, "sigma_px": None, "confidence": 0.0, "selected_peak_index": None, "peaks": []}
        if not valid.all():
            record.update(status="out_of_bounds", reason="normal_search_or_smoothing_support_crosses_image_boundary")
            records.append(record); states.append(record["status"])
            continue
        coordinates = np.stack((positions[:, 1], positions[:, 0]))
        dx = np.stack([ndimage.map_coordinates(gx[:, :, c], coordinates, order=1, mode="nearest") for c in range(3)], axis=-1)
        dy = np.stack([ndimage.map_coordinates(gy[:, :, c], coordinates, order=1, mode="nearest") for c in range(3)], axis=-1)
        directional = dx * normal[0] + dy * normal[1]
        strength = np.sqrt(np.mean(directional * directional, axis=1))
        total = np.sqrt(np.mean(dx * dx + dy * dy, axis=1))
        alignment = np.divide(strength, total, out=np.zeros_like(strength), where=total > 0)
        peaks, properties = signal.find_peaks(strength, height=strength_threshold, prominence=prominence_threshold,
                                               distance=max(1, int(np.ceil(config.minimum_peak_separation_px / spacing))))
        widths = signal.peak_widths(strength, peaks, rel_height=0.5)[0] * spacing if len(peaks) else []
        for peak_index, peak in enumerate(peaks):
            refined = float(offsets[peak])
            denominator = strength[peak - 1] - 2 * strength[peak] + strength[peak + 1]
            if denominator < -1e-12:
                shift = 0.5 * (strength[peak - 1] - strength[peak + 1]) / denominator
                if abs(shift) <= 1:
                    refined += float(shift * spacing)
            supported = bool(alignment[peak] >= config.minimum_alignment and abs(refined) <= radius - config.smoothing_sigma_px)
            fwhm = float(widths[peak_index])
            signal_to_noise = float(strength[peak] / max(noise, 1.0))
            sigma = max(config.minimum_sigma_px, fwhm / 4.71, fwhm / (2.355 * max(1.0, signal_to_noise)))
            record["peaks"].append({"peak_index": peak_index, "offset_px": refined, "xy": (point + refined * normal).tolist(),
                                     "strength_rgb_code_per_px": float(strength[peak]), "prominence": float(properties["prominences"][peak_index]),
                                     "normal_alignment": float(alignment[peak]), "fwhm_px": fwhm, "sigma_px": float(sigma),
                                     "supported": supported,
                                     "unsupported_reason": None if supported else "orientation_or_search_endpoint_unsupported"})
        eligible = sorted((peak for peak in record["peaks"] if peak["supported"]), key=lambda peak: peak["strength_rgb_code_per_px"], reverse=True)
        if eligible:
            first = eligible[0]
            second_ratio = eligible[1]["strength_rgb_code_per_px"] / first["strength_rgb_code_per_px"] if len(eligible) > 1 else 0.0
            record["competing_strength_ratio"] = float(second_ratio)
            if second_ratio >= config.competing_peak_ratio:
                record.update(status="ambiguous", reason="multiple_comparable_normal_direction_peaks")
            else:
                quality = min(1 - strength_threshold / first["strength_rgb_code_per_px"], first["normal_alignment"], 1 - second_ratio)
                if quality < config.minimum_confidence:
                    record.update(status="ambiguous" if len(eligible) > 1 else "missing", reason="low_edge_evidence_confidence")
                else:
                    matched[index], sigmas[index], confidence[index] = first["xy"], first["sigma_px"], quality
                    record.update(status="selected", reason="one_supported_dominant_peak", matched_xy=first["xy"],
                                  sigma_px=first["sigma_px"], confidence=float(quality), selected_peak_index=first["peak_index"])
        records.append(record); states.append(record["status"])
    image_digest = hashlib.sha256(str(rgb.shape).encode() + rgb.tobytes()).hexdigest()
    contour_digest = hashlib.sha256(contour.astype("<f8").tobytes() + normals.astype("<f8").tobytes()).hexdigest()
    diagnostics = {"image_size": [width, height], "search_radius_px": float(radius), "actual_sample_step_px": spacing,
                   "border_gradient_p90": noise, "strength_threshold": float(strength_threshold),
                   "prominence_threshold": float(prominence_threshold), "image_pixels_sha256": image_digest,
                   "initial_contour_and_normals_sha256": contour_digest, **provenance,
                   "caller_hashes_verification": "provided labels; encoded source/model/camera not loaded by this function",
                   "counts": {status: states.count(status) for status in ("selected", "ambiguous", "missing", "out_of_bounds")},
                   "config": asdict(config), "frozen_against_initial_candidate": True, "occlusion_visibility": "not_inferred",
                   "confidence_interpretation": "heuristic local edge evidence, not semantic correctness probability",
                   "uncertainty_interpretation": "native pixel floor plus observed transition width; not calibrated localization variance",
                   "semantic_identity": "unmeasured", "final_geometry_quality": "unmeasured"}
    for array in (contour, normals, matched, sigmas, confidence):
        array.setflags(write=False)
    return EdgeCorrespondenceReport(contour, normals, matched, sigmas, confidence, tuple(states), tuple(records), diagnostics)
