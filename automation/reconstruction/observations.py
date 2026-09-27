"""Conservative visible-foreground evidence from studio product photographs.

This is a background-contrast instrument, not an eyeglasses segmenter.  A
``measured`` result only means that a useful, stable contrast mask was observed.
It does not establish that all frame/lens pixels were recovered or that the
foreground is even glasses.  In particular, transparent optics, reflections,
hard shadows and background-colored frame parts cannot be classified here.

The original pixel grid is retained.  There is no hole filling, convex hull,
largest-component selection, resizing or per-product parameter adjustment.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage


LIMITATIONS = (
    "Contrast foreground is not a semantic frame, lens, temple or opening mask.",
    "Transparent lenses and background-colored or faint frame parts may be absent.",
    "Shadows, reflections, printed marks and other objects may enter the mask; "
    "a hard high-contrast shadow cannot be distinguished from geometry.",
    "Holes are preserved as observed background, not identified as lens openings.",
    "Confidence describes heuristic measurement stability, not calibrated accuracy "
    "or reconstruction quality.",
    "No physical dimensions, hidden surfaces, camera pose or complete silhouette "
    "are established by this measurement.",
)


@dataclass(frozen=True)
class ObservationPolicy:
    """Fixed pixel-space instrument settings; intentionally not tuned per frame."""

    minimum_contrast: float = 12.0
    strong_contrast: float = 24.0
    background_margin: float = 6.0
    maximum_background_p90: float = 10.0
    maximum_side_color_difference: float = 12.0
    maximum_weak_fraction: float = 0.30
    minimum_foreground_pixels: int = 64
    minimum_long_span: int = 80
    minimum_short_span: int = 8
    maximum_foreground_fraction: float = 0.70
    minimum_confidence: float = 0.55

    def __post_init__(self) -> None:
        numeric = asdict(self)
        if any(not np.isfinite(value) or value <= 0 for value in numeric.values()):
            raise ValueError("Observation policy values must be positive and finite")
        if self.strong_contrast <= self.minimum_contrast:
            raise ValueError("strong_contrast must exceed minimum_contrast")
        for name in ("maximum_weak_fraction", "maximum_foreground_fraction", "minimum_confidence"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be between zero and one")


@dataclass
class ForegroundObservation:
    """Evidence plus a diagnostic bool mask on the original image pixel grid.

    Always inspect ``status`` before fitting: an unmeasured result can still
    contain a candidate mask useful for diagnosis, but it is not usable evidence.
    ``bbox_xyxy`` uses exclusive right/bottom coordinates.  ``to_report`` omits
    mask pixels by default to keep reports compact and JSON serializable.
    """

    status: Literal["measured", "unmeasured"]
    mask: np.ndarray = field(repr=False)
    image_size: tuple[int, int]
    confidence: float
    bbox_xyxy: tuple[int, int, int, int] | None
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    policy: ObservationPolicy = field(default_factory=ObservationPolicy)
    source: str | None = None
    method: str = "studio_border_contrast_v1"
    limitations: tuple[str, ...] = LIMITATIONS

    @property
    def usable_mask(self) -> np.ndarray | None:
        return self.mask if self.status == "measured" else None

    def to_report(self, *, include_mask: bool = False) -> dict[str, Any]:
        report = {
            "schema_version": 1,
            "method": self.method,
            "status": self.status,
            "source": self.source,
            "image_size": list(self.image_size),
            "confidence": self.confidence,
            "bbox_xyxy": list(self.bbox_xyxy) if self.bbox_xyxy else None,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "policy": asdict(self.policy),
            "limitations": list(self.limitations),
            "mask_is_usable": self.status == "measured",
            "measurement_scope": ("authored alpha geometry support only" if self.method == "authored_alpha_geometry_only_v1"
                                  else "visible background-contrast foreground only"),
            "semantic_component_coverage": "unmeasured",
            "complete_glasses_silhouette_coverage": "unmeasured",
            "coordinate_system": "observed image pixels; xy bbox has exclusive right/bottom",
        }
        if include_mask:
            report["mask"] = self.mask.tolist()
        return report


def _color_distance(values: np.ndarray, center: np.ndarray) -> np.ndarray:
    """RMS RGB distance in 8-bit code values, independent of color direction."""
    return np.sqrt(np.mean(np.square(values.astype(np.float64) - center), axis=-1))


def _observe_alpha(pixels, policy, source):
    """Use an authored cutout only for geometric foreground, never lens optics."""
    alpha = pixels[..., 3]
    mask = alpha > 32
    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    count = len(xs)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if count else None
    span = (bbox[2] - bbox[0], bbox[3] - bbox[1]) if bbox else (0, 0)
    fraction = count / mask.size
    weak = float(np.count_nonzero(mask & (alpha < 223)) / count) if count else 1.
    reasons = []
    if count < policy.minimum_foreground_pixels:
        reasons.append('no_usable_alpha_foreground')
    if max(span) < policy.minimum_long_span or min(span) < policy.minimum_short_span:
        reasons.append('alpha_foreground_too_small_for_shape_measurement')
    if fraction > policy.maximum_foreground_fraction:
        reasons.append('alpha_foreground_dominates_image')
    if np.any(mask[[0, -1], :]) or np.any(mask[:, [0, -1]]):
        reasons.append('alpha_foreground_touches_image_border')
    if weak > policy.maximum_weak_fraction:
        reasons.append('alpha_support_is_mostly_translucent_or_threshold_sensitive')
    confidence = min(1. - weak, min(1., max(span) / (2 * policy.minimum_long_span)))
    if confidence < policy.minimum_confidence:
        reasons.append('low_measurement_confidence')
    return ForegroundObservation(status='unmeasured' if reasons else 'measured', mask=mask,
        image_size=(width, height), confidence=float(confidence), bbox_xyxy=bbox, reasons=tuple(reasons),
        metrics={'foreground_pixels': count, 'foreground_fraction': fraction, 'foreground_span_px': list(span),
                 'weak_foreground_fraction': weak, 'alpha_support_threshold': 32, 'strong_alpha_threshold': 223,
                 'hidden_rgb_ignored': True, 'photometric_background_known': False,
                 'physical_lens_transmission_inferred': False, 'native_resolution_preserved': True},
        policy=policy, source=source, method='authored_alpha_geometry_only_v1',
        limitations=LIMITATIONS + ('Alpha is a supplied cutout/compositing channel, not measured physical lens transmission.',))


def observe_image(
    image: Image.Image | np.ndarray,
    *,
    policy: ObservationPolicy | None = None,
    source: str | None = None,
) -> ForegroundObservation:
    """Extract conservative contrast evidence from PIL or uint8 RGB(A) pixels.

    Nonopaque inputs use authored alpha for geometry support, never as a known
    background or physical lens transmission. Array inputs must be uint8 to avoid interpreting a
    normalized float image as low contrast.  Malformed inputs raise ValueError.
    PIL orientation should already be applied; ``observe_image_path`` does it.
    """
    policy = policy or ObservationPolicy()
    if isinstance(image, Image.Image):
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    else:
        pixels = np.asarray(image)
        if pixels.dtype != np.uint8:
            raise ValueError("Image arrays must contain uint8 RGB or RGBA values")
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("Image must have shape (height, width, 3 or 4)")
    height, width = pixels.shape[:2]
    if height < 3 or width < 3:
        raise ValueError("Image must be at least 3 by 3 pixels")
    rgb = pixels[:, :, :3]
    reasons: list[str] = []
    if pixels.shape[2] == 4 and np.any(pixels[:, :, 3] != 255):
        return _observe_alpha(pixels, policy, source)

    # The outer 1% supplies enough border samples while keeping a tiny product
    # well inside the image from contaminating the background estimate.
    band = max(1, int(round(min(width, height) * 0.01)))
    sides = (
        rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3),
        rgb[band:-band, :band].reshape(-1, 3),
        rgb[band:-band, -band:].reshape(-1, 3),
    )
    border = np.concatenate(sides, axis=0)
    background = np.median(border, axis=0)
    border_distance = _color_distance(border, background)
    background_p90 = float(np.percentile(border_distance, 90))
    background_p99 = float(np.percentile(border_distance, 99))
    side_difference = max(
        float(_color_distance(np.median(side, axis=0), background))
        for side in sides if len(side)
    )
    if (background_p90 > policy.maximum_background_p90 or
            side_difference > policy.maximum_side_color_difference):
        reasons.append("background_not_uniform")

    # Derive the threshold from measured background noise.  A rare foreground
    # boundary entering the border can raise p99; it cannot create a false pass
    # because border intrusion is also measured below at the minimum contrast.
    threshold = max(policy.minimum_contrast, background_p99 + policy.background_margin)
    strong_threshold = max(policy.strong_contrast, threshold + policy.background_margin)
    contrast = _color_distance(rgb, background)
    mask = contrast > threshold
    strong = contrast > strong_threshold
    # Keep every component and every hole.  Filtering to a largest blob would
    # erase disconnected rims or temples and could hide extra foreground.
    count = int(mask.sum())
    strong_count = int(strong.sum())
    fraction = count / (width * height)
    weak_fraction = (count - strong_count) / count if count else None
    foreground_contrast = float(np.median(contrast[mask])) if count else None
    _, components = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if count else None
    span = (bbox[2] - bbox[0], bbox[3] - bbox[1]) if bbox else (0, 0)

    # A minimum-contrast border check remains independent of a raised adaptive
    # threshold, so a cropped object cannot disappear into background noise.
    border_outliers = border_distance > max(policy.minimum_contrast, background_p90 + 6.0)
    cropped = bool(np.any(border_outliers))
    if bbox:
        margin = max(1, int(round(min(width, height) * 0.005)))
        cropped = cropped or min(bbox[0], bbox[1], width - bbox[2], height - bbox[3]) < margin
    if cropped:
        reasons.append("foreground_touches_image_border_or_background_is_contaminated")
    if count < policy.minimum_foreground_pixels:
        reasons.append("no_usable_foreground")
    if max(span) < policy.minimum_long_span or min(span) < policy.minimum_short_span:
        reasons.append("foreground_too_small_for_shape_measurement")
    if fraction > policy.maximum_foreground_fraction:
        reasons.append("foreground_dominates_image_background_assumption_unreliable")
    if weak_fraction is not None and weak_fraction > policy.maximum_weak_fraction:
        reasons.append("threshold_sensitive_foreground_possible_shadow_or_faint_material")

    # These are diagnostic factors, not estimated probabilities.  Their minimum
    # cannot hide a failing factor by averaging it with several good factors.
    factors = {
        "background_uniformity": max(0.0, 1.0 - background_p90 / (2 * policy.maximum_background_p90)),
        "threshold_stability": 1.0 - weak_fraction if weak_fraction is not None else 0.0,
        "contrast_strength": min(1.0, foreground_contrast / strong_threshold) if foreground_contrast else 0.0,
        "source_resolution": min(1.0, max(span) / (2 * policy.minimum_long_span)),
    }
    confidence = float(min(factors.values()))
    if confidence < policy.minimum_confidence:
        reasons.append("low_measurement_confidence")
    metrics = {
        "foreground_pixels": count,
        "strong_foreground_pixels": strong_count,
        "foreground_fraction": float(fraction),
        "foreground_span_px": list(span),
        "connected_components": int(components),
        "background_rgb": background.tolist(),
        "background_p90_distance": background_p90,
        "background_p99_distance": background_p99,
        "maximum_side_background_difference": side_difference,
        "threshold_rgb_rms": float(threshold),
        "strong_threshold_rgb_rms": float(strong_threshold),
        "median_foreground_contrast_rgb_rms": foreground_contrast,
        "weak_foreground_fraction": float(weak_fraction) if weak_fraction is not None else None,
        "border_outlier_pixels": int(border_outliers.sum()),
        "confidence_factors": factors,
        "native_resolution_preserved": True,
    }
    return ForegroundObservation(
        status="unmeasured" if reasons else "measured",
        mask=mask.astype(bool), image_size=(width, height), confidence=confidence,
        bbox_xyxy=bbox, reasons=tuple(reasons), metrics=metrics,
        policy=policy, source=source,
    )


def observe_image_path(
    path: str | Path, *, policy: ObservationPolicy | None = None,
) -> ForegroundObservation:
    """Read a photograph, apply its EXIF orientation and keep native resolution."""
    path = Path(path)
    with Image.open(path) as image:
        original_size = image.size
        orientation = int(image.getexif().get(274, 1))
        oriented = ImageOps.exif_transpose(image)
        result = observe_image(oriented, policy=policy, source=str(path.resolve()))
        result.metrics["source_image_size"] = list(original_size)
        result.metrics["source_exif_orientation"] = orientation
        result.metrics["orientation_transform"] = "Pillow ImageOps.exif_transpose"
        return result
