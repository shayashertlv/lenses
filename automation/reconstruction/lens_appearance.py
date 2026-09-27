"""LensAppearance v1: experimental representation and reference math only.

This is neither a production shader nor recovered material parameters. RGB is
scene-linear sRGB/D65. Optical density is natural-log attenuation at normal
incidence through an effective slab, interpolated with smoothstep in lens-local
v (0 bottom, 1 top). This choice gives one consistent gradient definition; it
does not identify a unique physical material from photographs.

Coating reflectance is separate from absorption. Angular response uses an
empirical Schlick continuation or a supplied full-domain RGB keyframe table;
the latter can describe angular color changes without claiming a spectral
thin-film simulation. Air-to-lens Snell refraction controls attenuation path:
T=(1-R)*exp(-density/cos(theta_inside)). R, T and A are passive RGB fractions.
Roughness is recorded but does not broaden reflections in this local evaluator.
Multiple internal reflections, polarization, dispersion and ray displacement
are omitted. Renderers must establish parity before this contract is deployed.

References: https://www.pbr-book.org/4ed/Reflection_Models/Specular_Reflection_and_Transmission
and https://www.w3.org/TR/css-color-4/ (linear-light sRGB transfer).
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any

import numpy as np

COLOR_SPACE = "scene_linear_srgb_D65"
DENSITY_INTERPOLATION = "piecewise_smoothstep_optical_density"
VERTICAL_COORDINATE = "lens_local_bottom_0_top_1"


def _scalar(value: Any, name: str, low: float, high: float = np.inf) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a real number")
    value = float(value)
    if not np.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite in [{low}, {high}]")
    return value


def _rgb(value: Any, name: str, high: float = np.inf) -> tuple[float, float, float]:
    if (not isinstance(value, (tuple, list, np.ndarray)) or
            isinstance(value, np.ndarray) and value.ndim != 1 or len(value) != 3):
        raise ValueError(f"{name} must contain three RGB values")
    return tuple(_scalar(channel, name, 0, high) for channel in value)


def _array(value: Any, name: str, high: float = np.inf) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.kind not in "fiu" or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain finite numeric values")
    array = array.astype(np.float64)
    if np.any(array < 0) or np.any(array > high):
        raise ValueError(f"{name} must be in [0, {high}]")
    return array


def srgb_to_linear(rgb: Any) -> np.ndarray:
    """Explicitly decode normalized sRGB; no clipping or color normalization."""
    rgb = _array(rgb, "sRGB", 1)
    if rgb.shape[-1:] != (3,):
        raise ValueError("sRGB must have a final RGB dimension")
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(rgb: Any) -> np.ndarray:
    """Encode normalized linear sRGB; HDR tone mapping is outside this contract."""
    rgb = _array(rgb, "linear RGB", 1)
    if rgb.shape[-1:] != (3,):
        raise ValueError("linear RGB must have a final RGB dimension")
    return np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - 0.055)


def optical_density_from_linear_transmission(rgb: Any) -> tuple[float, float, float]:
    """Convert intrinsic normal transmission BEFORE coating loss to density.

    Zero needs infinite density, which v1 cannot serialize; it is rejected, never
    silently replaced by a small positive transmission.
    """
    rgb = np.asarray(_rgb(rgb, "intrinsic transmission", 1))
    if np.any(rgb == 0):
        raise ValueError("Finite optical density requires transmission above zero")
    return tuple(float(value) for value in -np.log(rgb))


@dataclass(frozen=True)
class DensityKeyframe:
    v: float
    optical_density_rgb: tuple[float, float, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "v", _scalar(self.v, "v", 0, 1))
        object.__setattr__(self, "optical_density_rgb", _rgb(self.optical_density_rgb, "density"))


@dataclass(frozen=True)
class ReflectanceKeyframe:
    angle_degrees: float
    reflectance_rgb: tuple[float, float, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "angle_degrees", _scalar(self.angle_degrees, "angle", 0, 90))
        object.__setattr__(self, "reflectance_rgb", _rgb(self.reflectance_rgb, "reflectance", 1))


def _keyframes(values: Any, expected: type, coordinate: str, end: float, single: bool) -> tuple:
    if not isinstance(values, (tuple, list)) or not values or any(type(v) is not expected for v in values):
        raise ValueError(f"Expected nonempty {expected.__name__} sequence")
    positions = [getattr(value, coordinate) for value in values]
    if positions[0] != 0 or any(a >= b for a, b in zip(positions, positions[1:])):
        raise ValueError("Keyframes must start at zero and be strictly increasing")
    if not (single and len(values) == 1) and positions[-1] != end:
        raise ValueError(f"Keyframes must cover the full domain through {end}")
    return tuple(values)


def _interpolate(x: np.ndarray, positions: list[float], colors: list[tuple]) -> np.ndarray:
    values = np.asarray(colors)
    if len(positions) == 1:
        return np.broadcast_to(values[0], x.shape + (3,)).copy()
    # Input domains are validated; limiting the interval index only assigns
    # the exact last endpoint to the last interval, never clamps input values.
    index = np.minimum(np.searchsorted(positions, x, side="right") - 1, len(positions) - 2)
    knots = np.asarray(positions)
    t = (x - knots[index]) / (knots[index + 1] - knots[index])
    weight = (t * t * (3 - 2 * t))[..., None]
    return values[index] * (1 - weight) + values[index + 1] * weight


@dataclass(frozen=True)
class LensSample:
    reflectance_rgb: np.ndarray
    transmission_rgb: np.ndarray
    absorption_rgb: np.ndarray
    optical_density_rgb: np.ndarray

    def compose(self, background_linear_rgb: Any, reflected_linear_rgb: Any) -> np.ndarray:
        """Combine supplied linear radiance samples; no tonemap, blur or alpha."""
        background = _array(background_linear_rgb, "background radiance")
        reflected = _array(reflected_linear_rgb, "reflected radiance")
        if background.shape[-1:] != (3,) or reflected.shape[-1:] != (3,):
            raise ValueError("Radiance must have a final RGB dimension")
        return self.transmission_rgb * background + self.reflectance_rgb * reflected


@dataclass(frozen=True)
class LensAppearance:
    optical_density_keyframes: tuple[DensityKeyframe, ...]
    normal_reflectance_rgb: tuple[float, float, float] = (0.04, 0.04, 0.04)
    refractive_index: float = 1.5
    roughness: float = 0.05
    angular_reflectance_keyframes: tuple[ReflectanceKeyframe, ...] | None = None
    # Optional effective rear response, measured from the source -Z side.
    # Transmission is reciprocal; this is a fraction of energy NOT transmitted,
    # not a second coating loss applied to the transmitted light.
    rear_reflection_fraction_rgb: tuple[float, float, float] | None = None

    def __post_init__(self) -> None:
        density = _keyframes(self.optical_density_keyframes, DensityKeyframe, "v", 1, True)
        object.__setattr__(self, "optical_density_keyframes", density)
        normal = _rgb(self.normal_reflectance_rgb, "normal reflectance", 1)
        object.__setattr__(self, "normal_reflectance_rgb", normal)
        object.__setattr__(self, "refractive_index", _scalar(self.refractive_index, "index", 1))
        object.__setattr__(self, "roughness", _scalar(self.roughness, "roughness", 0, 1))
        if self.rear_reflection_fraction_rgb is not None:
            object.__setattr__(self, "rear_reflection_fraction_rgb",
                               _rgb(self.rear_reflection_fraction_rgb, "rear reflection fraction", 1))
        if self.angular_reflectance_keyframes is not None:
            angular = _keyframes(self.angular_reflectance_keyframes, ReflectanceKeyframe, "angle_degrees", 90, False)
            if angular[0].reflectance_rgb != normal:
                raise ValueError("Angular table at zero must equal normal_reflectance_rgb")
            object.__setattr__(self, "angular_reflectance_keyframes", angular)

    def evaluate(self, v: Any, angle_degrees: Any = 0.0, *, side: str = 'front') -> LensSample:
        """Evaluate broadcastable lens-local v and incidence angles in [0, 90].

        v is attached to the lens surface, never screen height or world up; a
        head roll does not alter it. Angles are unsigned incidence from air.
        The optional rear response uses the side of the canonical source +Z
        axis, never the winding/normal of an exit surface in a closed solid.
        """
        if side not in ('front', 'rear'):
            raise ValueError('Lens side must be front or rear')
        v, angle = np.broadcast_arrays(_array(v, "v", 1), _array(angle_degrees, "angle", 90))
        density = _interpolate(v, [k.v for k in self.optical_density_keyframes],
                               [k.optical_density_rgb for k in self.optical_density_keyframes])
        radians = np.deg2rad(angle)
        if self.angular_reflectance_keyframes is None:
            normal = np.asarray(self.normal_reflectance_rgb)
            reflection = normal + (1 - normal) * ((1 - np.cos(radians)) ** 5)[..., None]
        else:
            table = self.angular_reflectance_keyframes
            reflection = _interpolate(angle, [k.angle_degrees for k in table], [k.reflectance_rgb for k in table])
        cos_inside = np.sqrt(1 - (np.sin(radians) / self.refractive_index) ** 2)
        path_density = np.divide(density, cos_inside[..., None], out=np.full_like(density, np.inf),
                                 where=cos_inside[..., None] > 0)
        # Exact grazing at index=1: a zero-density channel has zero attenuation;
        # positive density has infinite path attenuation (the limiting values).
        path_density = np.where(density == 0, 0, path_density)
        transmission = (1 - reflection) * np.exp(-path_density)
        absorption = (1 - reflection) * -np.expm1(-path_density)
        if side == 'rear' and self.rear_reflection_fraction_rgb is not None:
            fraction = np.asarray(self.rear_reflection_fraction_rgb)
            reflection = (1 - transmission) * fraction
            absorption = (1 - transmission) * (1 - fraction)
        return LensSample(reflection, transmission, absorption, density)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": 1, "color_space": COLOR_SPACE,
            "density_interpolation": DENSITY_INTERPOLATION, "vertical_coordinate": VERTICAL_COORDINATE,
            "normal_reflectance_rgb": list(self.normal_reflectance_rgb),
            "refractive_index": self.refractive_index, "roughness": self.roughness,
            "optical_density_keyframes": [{"v": k.v, "optical_density_rgb": list(k.optical_density_rgb)}
                                          for k in self.optical_density_keyframes],
            "angular_reflectance_keyframes": None if self.angular_reflectance_keyframes is None else [
                {"angle_degrees": k.angle_degrees, "reflectance_rgb": list(k.reflectance_rgb)}
                for k in self.angular_reflectance_keyframes],
        }
        if self.rear_reflection_fraction_rgb is not None:
            result['rear_reflection_fraction_rgb'] = list(self.rear_reflection_fraction_rgb)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LensAppearance":
        """Strict schema loading: unknown fields/semantics are rejected."""
        expected = {"schema_version", "color_space", "density_interpolation", "vertical_coordinate",
                    "normal_reflectance_rgb", "refractive_index", "roughness",
                    "optical_density_keyframes", "angular_reflectance_keyframes"}
        if not isinstance(value, dict) or set(value) not in (expected, expected | {'rear_reflection_fraction_rgb'}):
            raise ValueError("LensAppearance v1 requires exactly its declared fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("Unsupported LensAppearance schema_version")
        for name, literal in (("color_space", COLOR_SPACE), ("density_interpolation", DENSITY_INTERPOLATION),
                              ("vertical_coordinate", VERTICAL_COORDINATE)):
            if value[name] != literal:
                raise ValueError(f"Unsupported {name}")
        def read_keys(raw: Any, key_type: type, fields: set[str]) -> tuple:
            if not isinstance(raw, list) or any(not isinstance(k, dict) or set(k) != fields for k in raw):
                raise ValueError("Invalid keyframe fields")
            return tuple(key_type(**k) for k in raw)
        density = read_keys(value["optical_density_keyframes"], DensityKeyframe, {"v", "optical_density_rgb"})
        angular = value["angular_reflectance_keyframes"]
        if angular is not None:
            angular = read_keys(angular, ReflectanceKeyframe, {"angle_degrees", "reflectance_rgb"})
        if 'rear_reflection_fraction_rgb' in value and value['rear_reflection_fraction_rgb'] is None:
            raise ValueError('Optional rear reflection fraction must be an RGB triple when present')
        return cls(density, value["normal_reflectance_rgb"], value["refractive_index"], value["roughness"], angular,
                   value.get('rear_reflection_fraction_rgb'))
