"""Inverse fitting of CALIBRATED optical samples, not raw product photographs.

Known lens-local v, front incidence angle, linear transmitted background and
reflected illumination are required. The caller supplies calibration provenance,
unclipped observations, noise standard deviations and confidence. Camera pose,
illumination, exposure, clipping and spectral properties are never inferred.

The fitted family is LensAppearance v1 with fixed IOR/roughness and Schlick
reflection. Robust multistart fitting is followed by a separate *data* Jacobian
audit: optimizer success or a tiny residual alone cannot identify a material.
Rank, noise-scaled parameter precision, knot coverage and distinct plausible
solutions can make the result ambiguous. ``identified`` additionally requires
repeated identical (v, angle) measurements whose independent illumination solves
R/T directly, at sufficient precision, and spans the density basis. This is a
sufficient separation certificate; its absence does not prove that angular-only
data could never identify a model. Local uncertainty remains conditional on the
supplied calibration/model, not a global uniqueness proof or physical recovery.
Statuses concern parameter identification, not AR appearance acceptance. For
example, density hidden by a total mirror may have no effect on rendered R/T;
downstream prediction uncertainty, not parameter ambiguity alone, must govern
appearance acceptance or whether any further input is necessary.

SciPy's robust optimizer modifies its returned Jacobian; observability below
uses our unregularized analytic data Jacobian instead:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from numbers import Real
from pathlib import Path
from typing import Any, Literal

import numpy as np
import scipy
from scipy.optimize import least_squares

from .lens_appearance import COLOR_SPACE, DensityKeyframe, LensAppearance


def _numeric(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.kind not in "fiu" or not np.isfinite(array).all():
        raise ValueError(f"{name} must contain finite numeric values")
    return array.astype(np.float64, copy=True)


@dataclass(frozen=True)
class CalibratedLensSamples:
    v: Any
    angle_degrees: Any
    observed_linear_rgb: Any
    background_linear_rgb: Any
    reflected_linear_rgb: Any
    noise_sigma: Any
    confidence: Any
    calibration_id: str | None
    calibration_verified: bool = False
    clipped: Any = False
    source: str | None = None
    color_space: str = COLOR_SPACE

    def __post_init__(self) -> None:
        observed = _numeric(self.observed_linear_rgb, "observed_linear_rgb")
        if observed.ndim != 2 or observed.shape[1] != 3 or not len(observed):
            raise ValueError("observed_linear_rgb must be a nonempty Nx3 array")
        # Calibrated noise can make dark-subtracted observations negative; never
        # clip them. The known illumination/background themselves are nonnegative.
        shape = observed.shape
        for name, target in (("v", (shape[0],)), ("angle_degrees", (shape[0],)),
                             ("confidence", (shape[0],)), ("noise_sigma", shape),
                             ("background_linear_rgb", shape), ("reflected_linear_rgb", shape)):
            try:
                value = np.broadcast_to(_numeric(getattr(self, name), name), target).copy()
            except ValueError as error:
                raise ValueError(f"Invalid {name} shape or values") from error
            if name == "noise_sigma" and np.any(value <= 0):
                raise ValueError("noise_sigma must be strictly positive")
            if name != "noise_sigma" and np.any(value < 0):
                raise ValueError(f"{name} cannot be negative")
            if name in ("v", "confidence") and np.any(value > 1):
                raise ValueError(f"{name} must be in [0,1]")
            if name == "angle_degrees" and np.any(value >= 90):
                raise ValueError("Calibrated incidence angles must be in [0,90)")
            value.setflags(write=False)
            object.__setattr__(self, name, value)
        clipped = np.asarray(self.clipped)
        if clipped.dtype != np.bool_:
            raise ValueError("clipped must be an explicit boolean or Nx3 boolean array")
        clipped = np.broadcast_to(clipped, shape).copy()
        clipped.setflags(write=False)
        observed.setflags(write=False)
        object.__setattr__(self, "clipped", clipped)
        object.__setattr__(self, "observed_linear_rgb", observed)
        if type(self.calibration_verified) is not bool:
            raise ValueError("calibration_verified must be boolean")
        if self.calibration_id is not None and not isinstance(self.calibration_id, str):
            raise ValueError("calibration_id must be a string or None")
        if self.source is not None and not isinstance(self.source, str):
            raise ValueError("source must be a string or None")


@dataclass(frozen=True)
class LensFitConfig:
    knot_positions: tuple[float, ...] = (0.0, 1.0)
    fixed_refractive_index: float = 1.5
    fixed_roughness: float = 0.0
    maximum_optical_density: float = 12.0
    starts: int = 8
    seed: int = 1729
    max_nfev: int = 500
    robust_scale_sigma: float = 1.0
    inlier_sigma: float = 4.0
    minimum_inlier_fraction: float = 0.90
    maximum_median_residual_sigma: float = 2.0
    jacobian_relative_tolerance: float = 1e-7
    maximum_density_standard_error: float = 0.20
    maximum_reflectance_standard_error: float = 0.03
    minimum_scaled_singular_value: float = 1.0
    minimum_knot_basis_support: float = 0.80
    plausible_cost_delta: float = 2.0

    def __post_init__(self) -> None:
        # Reuse the canonical domain constraints; positions are never sorted or
        # filled in silently, and uniform density is a single knot at v=0.
        dummy = LensAppearance(tuple(DensityKeyframe(v, (0, 0, 0)) for v in self.knot_positions),
                               refractive_index=self.fixed_refractive_index, roughness=self.fixed_roughness)
        object.__setattr__(self, "knot_positions", tuple(k.v for k in dummy.optical_density_keyframes))
        for name in ("starts", "max_nfev", "seed"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "seed" else 1):
                raise ValueError(f"{name} must be a valid integer")
        if not 3 <= self.starts <= 64:
            raise ValueError("Use 3 to 64 distinct optimization starts")
        for name in ("maximum_optical_density", "robust_scale_sigma", "inlier_sigma",
                     "maximum_median_residual_sigma", "jacobian_relative_tolerance",
                     "maximum_density_standard_error", "maximum_reflectance_standard_error",
                     "minimum_scaled_singular_value", "plausible_cost_delta"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("minimum_inlier_fraction", "minimum_knot_basis_support"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0,1]")


@dataclass(frozen=True)
class FitAlternative:
    appearance: LensAppearance
    robust_cost: float
    standardized_rmse: float
    converged: bool
    evaluations: int
    start_index: int

    def to_report(self) -> dict[str, Any]:
        return {"appearance": self.appearance.to_dict(), "robust_cost": self.robust_cost,
                "standardized_rmse": self.standardized_rmse, "converged": self.converged,
                "evaluations": self.evaluations, "start_index": self.start_index}


@dataclass(frozen=True)
class LensFitResult:
    status: Literal["identified", "ambiguous", "nonconverged", "rejected"]
    best: LensAppearance | None
    alternatives: tuple[FitAlternative, ...]
    reasons: tuple[str, ...]
    evidence: dict[str, Any]

    @property
    def usable_appearance(self) -> LensAppearance | None:
        """Identified parameter point estimate only; this is NOT an AR gate.

        Ambiguous candidates remain in best/alternatives for downstream
        prediction-uncertainty analysis; irrelevant unknown parameters need not
        prevent acceptance of their rendered appearance.
        """
        return self.best if self.status == "identified" else None

    def to_report(self) -> dict[str, Any]:
        return {"schema_version": 1, "status": self.status,
                "best": self.best.to_dict() if self.best is not None else None,
                "alternatives": [alternative.to_report() for alternative in self.alternatives],
                "reasons": list(self.reasons), "evidence": self.evidence,
                "ar_appearance_acceptance": "unmeasured; parameter ambiguity alone is not appearance failure",
                "scope": "parameter identification from calibrated samples under a fixed Schlick/optical-density model; not raw-photo recovery"}


def _basis(v: np.ndarray, knots: tuple[float, ...]) -> np.ndarray:
    if len(knots) == 1:
        return np.ones((len(v), 1))
    positions = np.asarray(knots)
    index = np.minimum(np.searchsorted(positions, v, side="right") - 1, len(knots) - 2)
    t = (v - positions[index]) / (positions[index + 1] - positions[index])
    smooth = t * t * (3 - 2 * t)
    basis = np.zeros((len(v), len(knots)))
    basis[np.arange(len(v)), index] = 1 - smooth
    basis[np.arange(len(v)), index + 1] = smooth
    return basis


class _Model:
    def __init__(self, samples: CalibratedLensSamples, config: LensFitConfig):
        self.samples, self.config = samples, config
        self.basis = _basis(samples.v, config.knot_positions)
        angle = np.deg2rad(samples.angle_degrees)
        self.schlick = ((1 - np.cos(angle)) ** 5)[:, None]
        inverse_index = 1 / config.fixed_refractive_index
        # Equivalent Snell cosine without subtracting nearly equal numbers for
        # index=1 and near-grazing incidence.
        cosine_inside = np.sqrt((1 - inverse_index) * (1 + inverse_index) + (np.cos(angle) * inverse_index) ** 2)
        self.path = (1 / cosine_inside)[:, None]
        self.weight = np.sqrt(samples.confidence[:, None]) / samples.noise_sigma
        self.parameter_count = 3 * (len(config.knot_positions) + 1)

    def predict_and_jacobian(self, parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k, samples = len(self.config.knot_positions), self.samples
        density = self.basis @ parameters[:3 * k].reshape(k, 3)
        intrinsic = np.exp(-density * self.path)
        reflection = self.schlick + (1 - self.schlick) * parameters[-3:]
        transmission = (1 - reflection) * intrinsic
        prediction = reflection * samples.reflected_linear_rgb + transmission * samples.background_linear_rgb
        jacobian = np.zeros((len(density), 3, self.parameter_count))
        density_derivative = -transmission * samples.background_linear_rgb * self.path
        reflection_derivative = (1 - self.schlick) * (samples.reflected_linear_rgb - intrinsic * samples.background_linear_rgb)
        for channel in range(3):
            jacobian[:, channel, channel:3 * k:3] = density_derivative[:, channel, None] * self.basis
            jacobian[:, channel, 3 * k + channel] = reflection_derivative[:, channel]
        return prediction, jacobian

    def residual(self, parameters: np.ndarray) -> np.ndarray:
        return ((self.predict_and_jacobian(parameters)[0] - self.samples.observed_linear_rgb) * self.weight).ravel()

    def jacobian(self, parameters: np.ndarray) -> np.ndarray:
        return (self.predict_and_jacobian(parameters)[1] * self.weight[:, :, None]).reshape(-1, self.parameter_count)

    def appearance(self, parameters: np.ndarray) -> LensAppearance:
        density = parameters[:-3].reshape(-1, 3)
        return LensAppearance(tuple(DensityKeyframe(v, tuple(row)) for v, row in zip(self.config.knot_positions, density)),
                              tuple(parameters[-3:]), self.config.fixed_refractive_index, self.config.fixed_roughness)


def _spectrum(jacobian: np.ndarray, parameter_count: int, relative_tolerance: float) -> tuple[dict[str, Any], np.ndarray | None]:
    _, singular, vt = np.linalg.svd(jacobian, full_matrices=False)
    singular = np.pad(singular, (0, max(0, parameter_count - len(singular))))
    threshold = max(1e-10, float(singular[0]) * relative_tolerance) if len(singular) else 1e-10
    rank = int(np.sum(singular > threshold))
    std = np.sqrt(np.sum((vt.T / singular) ** 2, axis=1)) if rank == parameter_count else None
    return {"rank": rank, "parameter_count": parameter_count, "singular_values": singular.tolist(),
            "rank_threshold": threshold,
            "condition_number": float(singular[0] / singular[-1]) if rank == parameter_count else None}, std


def _provenance(samples: CalibratedLensSamples, config: LensFitConfig) -> dict[str, Any]:
    digest = hashlib.sha256()
    for name in ("v", "angle_degrees", "observed_linear_rgb", "background_linear_rgb", "reflected_linear_rgb", "noise_sigma", "confidence", "clipped"):
        digest.update(name.encode())
        digest.update(np.asarray(getattr(samples, name), dtype="<f8").tobytes())
    digest.update(json.dumps({"calibration_id": samples.calibration_id, "color_space": samples.color_space,
                              "calibration_verified": samples.calibration_verified, "source": samples.source}, sort_keys=True).encode())
    return {"samples_sha256": digest.hexdigest(), "fitter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "numpy_version": np.__version__, "scipy_version": scipy.__version__, "calibration_id": samples.calibration_id,
            "calibration_verification": "caller_asserted; not independently verified by fitter", "source": samples.source,
            "sample_count": len(samples.v), "positive_confidence_sample_count": int(np.sum(samples.confidence > 0)),
            "config": asdict(config), "fixed_not_inferred": {"refractive_index": config.fixed_refractive_index,
                                                              "roughness": config.fixed_roughness},
            "angular_model": "Schlick only", "loss": "soft_l1", "priors_or_regularization_in_data_rank": False,
            "uncertainty_scope": "local linearization conditional on supplied independent noise and correct calibration/model"}


def _separation_certificate(model: _Model, inlier: np.ndarray) -> dict[str, Any]:
    """Sufficient direct R/T separation, independent of nonlinear solver basins.

    At exactly shared v/angle, radiance is linear in [T,R]. Rank-two controlled
    illumination determines them. Finite T and 1-R then determine density; the
    supplied interpolation basis determines all knots if its rank is full.
    Certifying groups also need direct propagated noise precision, so almost
    proportional illuminations cannot create a merely algebraic certificate.
    """
    samples, config = model.samples, model.config
    groups: dict[tuple[float, float], list[int]] = {}
    for index, (v, angle) in enumerate(zip(samples.v, samples.angle_degrees)):
        groups.setdefault((float(v), float(angle)), []).append(index)
    certified_basis: list[list[np.ndarray]] = [[], [], []]
    records = []
    for (v, angle), indices in groups.items():
        record: dict[str, Any] = {"v": v, "angle_degrees": angle, "illumination_rank_rgb": [],
                                  "transmission_standard_error_rgb": [], "normal_reflectance_standard_error_rgb": [],
                                  "density_standard_error_rgb": [], "certified_channels": []}
        for channel in range(3):
            selected = [index for index in indices if inlier[index, channel]]
            design = np.column_stack((samples.background_linear_rgb[selected, channel], samples.reflected_linear_rgb[selected, channel]))
            design *= model.weight[selected, channel, None]
            information, standard_errors = _spectrum(design, 2, config.jacobian_relative_tolerance)
            record["illumination_rank_rgb"].append(information["rank"])
            transmission_error = reflection_error = density_error = None
            certified = False
            if information["rank"] == 2:
                inverse = np.linalg.pinv(design, rcond=config.jacobian_relative_tolerance)
                covariance = inverse @ inverse.T
                measured = samples.observed_linear_rgb[selected, channel] * model.weight[selected, channel]
                transmission, reflection = inverse @ measured
                schlick = float(model.schlick[indices[0], 0])
                transmission_error = float(standard_errors[0])
                reflection_error = float(standard_errors[1] / (1 - schlick))
                # Three-sigma separation from zero transmission or unit mirror
                # reflectance keeps the logarithmic density transform observable.
                if transmission > 3 * standard_errors[0] and 1 - reflection > 3 * standard_errors[1]:
                    cosine_inside = 1 / float(model.path[indices[0], 0])
                    derivative = -cosine_inside * np.array([1 / transmission, 1 / (1 - reflection)])
                    variance = float(derivative @ covariance @ derivative)
                    if np.isfinite(variance) and variance >= 0:
                        density_error = float(np.sqrt(variance))
                        certified = (density_error <= config.maximum_density_standard_error and
                                     reflection_error <= config.maximum_reflectance_standard_error)
            record["transmission_standard_error_rgb"].append(transmission_error)
            record["normal_reflectance_standard_error_rgb"].append(reflection_error)
            record["density_standard_error_rgb"].append(density_error)
            record["certified_channels"].append(bool(certified))
            if certified:
                certified_basis[channel].append(model.basis[indices[0]])
        records.append(record)
    k = len(config.knot_positions)
    ranks = [_spectrum(np.asarray(rows).reshape(-1, k), k, config.jacobian_relative_tolerance)[0]["rank"]
             for rows in certified_basis]
    return {"established": all(rank == k for rank in ranks), "density_basis_rank_rgb": ranks,
            "required_density_basis_rank": k, "certified_group_count_rgb": [len(rows) for rows in certified_basis],
            "groups": records,
            "interpretation": "sufficient direct R/T separation at repeated identical poses; absence is not proof of non-identifiability",
            "noise_policy": "each certified group separates T and 1-R from zero by 3 sigma and meets configured local density/R0 precision"}


def fit_lens_appearance(samples: CalibratedLensSamples, config: LensFitConfig | None = None) -> LensFitResult:
    """Fit the stipulated material family, preserving ambiguity and alternatives.

    Noise may be a scalar, RGB triplet or Nx3 array. Confidence is scalar or N.
    Its square root scales inverse-noise residuals. Zero-confidence rows supply
    no information. Reported local precision does not include calibration error.
    """
    config = config or LensFitConfig()
    evidence = _provenance(samples, config)
    rejection = []
    if not samples.calibration_verified or not samples.calibration_id or not samples.calibration_id.strip():
        rejection.append("verified_calibration_and_provenance_required")
    if samples.color_space != COLOR_SPACE:
        rejection.append("known_scene_linear_srgb_calibration_required")
    if np.any(samples.clipped):
        rejection.append("clipped_measurements_not_supported")
    if not np.any(samples.confidence > 0):
        rejection.append("no_positive_confidence_measurements")
    if rejection:
        return LensFitResult("rejected", None, (), tuple(rejection), evidence)
    model = _Model(samples, config)
    p, k = model.parameter_count, len(config.knot_positions)
    rng = np.random.default_rng(config.seed)
    starts = [np.r_[np.full(3 * k, fraction * config.maximum_optical_density), np.full(3, reflection)]
              for fraction, reflection in ((0.02, 0.04), (0.10, 0.50), (0.30, 0.90))]
    for _ in range(config.starts - 3):
        starts.append(np.r_[np.exp(rng.uniform(np.log(0.005), np.log(0.8), 3 * k)) * config.maximum_optical_density,
                            rng.uniform(0.01, 0.99, 3)])
    results = [least_squares(model.residual, start, jac=model.jacobian, bounds=(np.zeros(p), np.r_[np.full(3 * k, config.maximum_optical_density), np.ones(3)]),
                             loss="soft_l1", f_scale=config.robust_scale_sigma, max_nfev=config.max_nfev,
                             xtol=1e-11, ftol=1e-11, gtol=1e-11, method="trf") for start in starts]
    ordering = sorted(range(len(results)), key=lambda index: results[index].cost)
    best_index, best = ordering[0], results[ordering[0]]
    residual = model.residual(best.x)
    active = np.repeat(samples.confidence > 0, 3)
    inlier = (np.abs(residual) <= config.inlier_sigma) & active
    jacobian = model.jacobian(best.x)
    raw_information, _ = _spectrum(jacobian[active], p, config.jacobian_relative_tolerance)
    information, std = _spectrum(jacobian[inlier], p, config.jacobian_relative_tolerance)
    parameter_scales = np.r_[np.full(3 * k, config.maximum_density_standard_error), np.full(3, config.maximum_reflectance_standard_error)]
    scaled_information, _ = _spectrum(jacobian[inlier] * parameter_scales, p, config.jacobian_relative_tolerance)
    support = np.zeros((k, 3))
    illumination_ranks = []
    channel_inliers = inlier.reshape(-1, 3)
    for channel in range(3):
        useful = channel_inliers[:, channel] & (samples.background_linear_rgb[:, channel] > 0)
        if np.any(useful):
            support[:, channel] = model.basis[useful].max(axis=0)
        valid = channel_inliers[:, channel]
        design = np.column_stack((samples.background_linear_rgb[valid, channel], samples.reflected_linear_rgb[valid, channel]))
        design *= model.weight[valid, channel, None]
        illumination_ranks.append(_spectrum(design, 2, config.jacobian_relative_tolerance)[0]["rank"])
    separation = _separation_certificate(model, channel_inliers)
    alternatives, preserved = [], [best.x]
    for index in ordering[1:]:
        result = results[index]
        if result.cost > best.cost + config.plausible_cost_delta or not result.success:
            continue
        if any(np.max(np.abs(result.x - vector) / parameter_scales) < 0.10 for vector in preserved):
            continue
        preserved.append(result.x)
        alternatives.append(FitAlternative(model.appearance(result.x), float(result.cost),
                                            float(np.sqrt(np.mean(model.residual(result.x)[active] ** 2))),
                                            bool(result.success), int(result.nfev), index))
    evidence.update({"parameter_order": [f"density_v{v:g}_{channel}" for v in config.knot_positions for channel in "rgb"] + [f"normal_reflectance_{channel}" for channel in "rgb"],
                     "data_jacobian": raw_information, "inlier_data_jacobian": information,
                     "uncertainty_scaled_data_jacobian": scaled_information,
                     "parameter_standard_errors": std.tolist() if std is not None else None,
                     "knot_basis_support_per_channel": support.tolist(), "illumination_design_rank_per_channel": illumination_ranks,
                     "direct_separation_certificate": separation,
                     "knot_coverage_policy": "minimum basis support is an explicit anti-extrapolation guard, separate from algebraic rank or noise precision",
                     "illumination_rank_interpretation": "diagnostic only; known angular variation may supply additional information",
                     "standardized_rmse": float(np.sqrt(np.mean(residual[active] ** 2))),
                     "median_absolute_standardized_residual": float(np.median(np.abs(residual[active]))),
                     "maximum_absolute_standardized_residual": float(np.max(np.abs(residual[active]))),
                     "inlier_fraction": float(np.sum(inlier) / np.sum(active)), "outlier_channel_count": int(np.sum(active & ~inlier)),
                     "best_start_index": best_index, "best_robust_cost": float(best.cost), "distinct_plausible_candidate_count": 1 + len(alternatives),
                     "starts": [{"index": index, "converged": bool(result.success), "robust_cost": float(result.cost),
                                 "evaluations": int(result.nfev), "termination": result.message} for index, result in enumerate(results)],
                     "global_uniqueness": "not_proven; multistart and local data information only"})
    failure = []
    if not best.success:
        failure.append("optimizer_did_not_converge")
    if (evidence["inlier_fraction"] < config.minimum_inlier_fraction or
            evidence["median_absolute_standardized_residual"] > config.maximum_median_residual_sigma):
        failure.append("observations_not_explained_at_declared_noise")
    ambiguity = []
    if not separation["established"]:
        ambiguity.append("global_separation_certificate_not_established")
    if any(not result.success for result in results):
        ambiguity.append("exploratory_optimization_starts_unresolved")
    if information["rank"] < p:
        ambiguity.append("data_jacobian_rank_deficient")
    if std is not None and np.any(std > parameter_scales):
        ambiguity.append("parameter_uncertainty_exceeds_requested_precision")
    if scaled_information["singular_values"][-1] < config.minimum_scaled_singular_value:
        ambiguity.append("weak_parameter_direction_below_noise_precision")
    if np.any(support < config.minimum_knot_basis_support):
        ambiguity.append("density_knot_positions_lack_observed_coverage")
    if np.any(best.x[:-3] > config.maximum_optical_density * (1 - 1e-5)):
        ambiguity.append("density_reaches_explicit_fitting_bound")
    if alternatives:
        ambiguity.append("distinct_plausible_material_alternatives")
    status = "nonconverged" if failure else "ambiguous" if ambiguity else "identified"
    return LensFitResult(status, model.appearance(best.x), tuple(alternatives), tuple(failure + ambiguity), evidence)
