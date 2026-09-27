"""Immutable, source-bound geometric observations; not reconstruction verdicts.

Coordinates are on the ORIGINAL decoded photo pixel grid before EXIF rotation,
cropping or resizing: pixel centers range from (0, 0) to (width-1, height-1),
with x right and y down. Extractors using another grid must transform their
coordinates and marginal standard deviations back before constructing evidence.
No precision or semantic confidence is inferred from image contrast here.
EdgeEvidence additionally carries a unit image normal and scalar normal-direction
uncertainty. Its tangent is explicitly unconstrained; the ImagePoint anchor is
not an observed material-point correspondence or verified lens boundary.

``present`` means some component geometry was visibly observed. Partial occlusion
is represented by present observations plus partial/unknown coverage. ``occluded``
means no visible geometry is asserted. ``verified_absent`` is a positive absence
assertion only inside its assessed image region, never a whole-product topology
claim. Empty features are never interpreted as absence. Unknown/inconsistent
components retain explanations, not coordinates fitters might accidentally use.

Frozen dataclasses own immutable nested values. Canonical hashes include geometry,
uncertainty, states, coverage, confidence, provenance, purpose and photo identity.
Loading checks the embedded digest; consumers pin an expected digest to detect
replacement/re-hashing too. Hashes bind evidence, not certify its truth or author.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from numbers import Real
import re
from typing import Any, Literal


SCHEMA_VERSION = 1
COORDINATE_SYSTEM = "original_decoded_pixel_centers_xy_before_exif"
UNCERTAINTY_MODEL = "per_axis_marginal_standard_deviation_px"
COMPONENT_ROLES = frozenset({"frame", "rim", "lens", "lens_opening", "bridge", "temple", "hinge", "nose_pad", "attachment", "other"})
OBSERVATION_STATES = frozenset({"present", "verified_absent", "occluded", "unknown", "inconsistent"})
ObservationState = Literal["present", "verified_absent", "occluded", "unknown", "inconsistent"]
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _text(value: Any, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{name} must be {'a nonempty' if nonempty else 'a'} string")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _number(value: Any, name: str, *, low: float = 0, high: float = math.inf, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a real number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(number) or not low <= number <= high or (positive and number <= 0):
        raise ValueError(f"{name} is outside its finite allowed range")
    return number if number else 0.0


def _tuple(value: Any, expected: type, name: str) -> tuple:
    if not isinstance(value, (tuple, list)) or any(type(item) is not expected for item in value):
        raise ValueError(f"{name} must be a sequence of {expected.__name__}")
    return tuple(value)


def _fields(value: Any, keys: tuple[str, ...], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{name} requires exactly its declared fields")
    return value


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return value


@dataclass(frozen=True)
class SourceImage:
    image_sha256: str
    width: int
    height: int
    image_id: str

    def __post_init__(self) -> None:
        _hash(self.image_sha256, "image_sha256")
        _text(self.image_id, "image_id")
        for name in ("width", "height"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def assert_source_bytes(self, data: bytes) -> None:
        """Verify original encoded file identity; this does not decode dimensions."""
        if not isinstance(data, bytes):
            raise ValueError("source data must be immutable bytes")
        if hashlib.sha256(data).hexdigest() != self.image_sha256:
            raise ValueError("Source image bytes do not match frozen evidence")

    def to_dict(self) -> dict:
        return {"image_sha256": self.image_sha256, "width": self.width, "height": self.height, "image_id": self.image_id}


@dataclass(frozen=True)
class EvidenceProvenance:
    kind: Literal["automatic", "reviewed"]
    method: str
    source_sha256: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("automatic", "reviewed"):
            raise ValueError("Provenance kind must be automatic or reviewed")
        _text(self.method, "provenance method")
        _hash(self.source_sha256, "provenance source_sha256")
        _text(self.notes, "provenance notes", nonempty=False)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "method": self.method, "source_sha256": self.source_sha256, "notes": self.notes}


@dataclass(frozen=True)
class ImagePoint:
    """Position with marginal x/y standard deviations, all in original pixels.

    Correlation is not represented: a consumer must not claim independent samples
    merely because a contour has many points. Zero/infinite certainty is rejected.
    """
    x: float
    y: float
    sigma_x_px: float
    sigma_y_px: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "sigma_x_px", "sigma_y_px"):
            object.__setattr__(self, name, _number(getattr(self, name), name, positive=name.startswith("sigma")))

    @property
    def xy(self) -> tuple[float, float]:
        return self.x, self.y

    @property
    def sigma_xy_px(self) -> tuple[float, float]:
        return self.sigma_x_px, self.sigma_y_px

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "sigma_x_px": self.sigma_x_px, "sigma_y_px": self.sigma_y_px}


@dataclass(frozen=True)
class ContourEvidence:
    id: str
    points: tuple[ImagePoint, ...]
    closed: bool
    confidence: float
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        _text(self.id, "contour id")
        points = _tuple(self.points, ImagePoint, "contour points")
        object.__setattr__(self, "points", points)
        if type(self.closed) is not bool:
            raise ValueError("Contour closed must be boolean")
        minimum = 3 if self.closed else 2
        if len(points) < minimum or len({point.xy for point in points}) < minimum:
            raise ValueError(f"Contour requires at least {minimum} distinct points")
        if any(a.xy == b.xy for a, b in zip(points, points[1:])) or (self.closed and points[0].xy == points[-1].xy):
            raise ValueError("Contour cannot repeat adjacent/end points; closure is implicit")
        object.__setattr__(self, "confidence", _number(self.confidence, "contour confidence", high=1, positive=True))
        if type(self.provenance) is not EvidenceProvenance:
            raise ValueError("Contour requires typed provenance")

    def to_dict(self) -> dict:
        return {"id": self.id, "points": [point.to_dict() for point in self.points], "closed": self.closed,
                "confidence": self.confidence, "provenance": self.provenance.to_dict()}


@dataclass(frozen=True)
class LandmarkEvidence:
    id: str
    point: ImagePoint
    confidence: float
    provenance: EvidenceProvenance

    def __post_init__(self) -> None:
        _text(self.id, "landmark id")
        if type(self.point) is not ImagePoint or type(self.provenance) is not EvidenceProvenance:
            raise ValueError("Landmark requires typed point and provenance")
        object.__setattr__(self, "confidence", _number(self.confidence, "landmark confidence", high=1, positive=True))

    def to_dict(self) -> dict:
        return {"id": self.id, "point": self.point.to_dict(), "confidence": self.confidence,
                "provenance": self.provenance.to_dict()}


@dataclass(frozen=True)
class EdgeEvidence:
    """One local image-edge constraint, with no tangential correspondence.

    ``point`` locates the chosen edge anchor in the original photo. Its marginal
    pixel uncertainties describe that anchor's coordinate bookkeeping, not two
    independent displacement constraints. Fitters must use only the scalar
    normal residual and ``sigma_normal_px``. The normal orientation may follow
    an outward candidate-contour normal; it does not establish semantic identity.

    When native coordinates equal diag(ratio) times working coordinates plus an
    offset, transform the normal as normalize(normal_work / ratio), and transform
    sigma as sigma_work / norm(normal_work / ratio). This is a covector transform,
    so multiplying the normal by the resize ratio is incorrect.
    """
    id: str
    point: ImagePoint
    normal_xy: tuple[float, float]
    sigma_normal_px: float
    confidence: float
    provenance: EvidenceProvenance
    tangent_unconstrained: bool = True

    def __post_init__(self) -> None:
        _text(self.id, "edge id")
        if type(self.point) is not ImagePoint or type(self.provenance) is not EvidenceProvenance:
            raise ValueError("Edge requires typed point and provenance")
        if not isinstance(self.normal_xy, (tuple, list)) or len(self.normal_xy) != 2:
            raise ValueError("Edge normal must have two unit-vector coordinates")
        normal = tuple(_number(value, "edge normal", low=-1, high=1) for value in self.normal_xy)
        if not math.isclose(math.hypot(*normal), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Edge normal must already be a unit vector")
        object.__setattr__(self, "normal_xy", normal)
        object.__setattr__(self, "sigma_normal_px", _number(self.sigma_normal_px, "edge normal sigma", positive=True))
        object.__setattr__(self, "confidence", _number(self.confidence, "edge confidence", high=1, positive=True))
        if type(self.tangent_unconstrained) is not bool or not self.tangent_unconstrained:
            raise ValueError("Image-edge evidence requires an explicitly unconstrained tangent")

    def to_dict(self) -> dict:
        return {"id": self.id, "point": self.point.to_dict(), "normal_xy": list(self.normal_xy),
                "sigma_normal_px": self.sigma_normal_px, "confidence": self.confidence,
                "provenance": self.provenance.to_dict(), "tangent_unconstrained": True}


@dataclass(frozen=True)
class Coverage:
    """Explicit assessment scope, with unknown fractions represented by None.

    Region uses image-edge coordinates [left, top, right, bottom), bounded by
    [0,width] and [0,height]. It states where an assertion applies; it is not a
    segmentation mask. visible_fraction concerns the component; boundary_fraction
    concerns its complete projected boundary. These fractions are supplied claims,
    not inferred from the bounding box or number of recorded contour vertices.
    """
    assessed_region_xyxy: tuple[float, float, float, float] | None
    visible_fraction: float | None
    boundary_fraction: float | None
    notes: str = ""

    def __post_init__(self) -> None:
        region = self.assessed_region_xyxy
        if region is not None:
            if not isinstance(region, (tuple, list)) or len(region) != 4:
                raise ValueError("Assessed region must have four coordinates")
            region = tuple(_number(value, "assessed region") for value in region)
            if region[0] >= region[2] or region[1] >= region[3]:
                raise ValueError("Assessed region must have positive area")
            object.__setattr__(self, "assessed_region_xyxy", region)
        for name in ("visible_fraction", "boundary_fraction"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _number(value, name, high=1))
        _text(self.notes, "coverage notes", nonempty=False)

    def to_dict(self) -> dict:
        return {"assessed_region_xyxy": list(self.assessed_region_xyxy) if self.assessed_region_xyxy is not None else None,
                "visible_fraction": self.visible_fraction, "boundary_fraction": self.boundary_fraction, "notes": self.notes}


@dataclass(frozen=True)
class ComponentEvidence:
    id: str
    role: str
    state: ObservationState
    confidence: float | None
    provenance: EvidenceProvenance
    coverage: Coverage
    contours: tuple[ContourEvidence, ...] = ()
    landmarks: tuple[LandmarkEvidence, ...] = ()
    notes: str = ""
    edges: tuple[EdgeEvidence, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "component id")
        _text(self.role, "component role")
        _text(self.state, "component state")
        if self.role not in COMPONENT_ROLES:
            raise ValueError(f"Unknown component role {self.role!r}; use other with notes for extensions")
        if self.state not in OBSERVATION_STATES:
            raise ValueError("Unknown component observation state")
        if type(self.provenance) is not EvidenceProvenance or type(self.coverage) is not Coverage:
            raise ValueError("Component requires typed provenance and coverage")
        object.__setattr__(self, "contours", _tuple(self.contours, ContourEvidence, "contours"))
        object.__setattr__(self, "landmarks", _tuple(self.landmarks, LandmarkEvidence, "landmarks"))
        object.__setattr__(self, "edges", _tuple(self.edges, EdgeEvidence, "edges"))
        _text(self.notes, "component notes", nonempty=False)
        if self.role == "other" and not self.notes.strip():
            raise ValueError("Other component roles require descriptive notes")
        features = self.contours + self.landmarks + self.edges
        if len({feature.id for feature in features}) != len(features):
            raise ValueError("Feature IDs must be unique within a component")
        if self.confidence is not None:
            object.__setattr__(self, "confidence", _number(self.confidence, "component confidence", high=1, positive=True))
        if self.state in ("present", "verified_absent", "occluded") and self.confidence is None:
            raise ValueError("Asserted component states require explicit positive confidence")
        if self.state == "present":
            if not features:
                raise ValueError("Present components require visible contours, landmarks or edges")
            if self.coverage.visible_fraction == 0 or ((self.contours or self.edges) and self.coverage.boundary_fraction == 0):
                raise ValueError("Visible observations contradict zero coverage")
        else:
            if features:
                raise ValueError("Only present components may supply visible coordinates")
            if not self.notes.strip():
                raise ValueError("Non-present states require an explicit explanation")
        if self.state == "verified_absent":
            if self.coverage.assessed_region_xyxy is None:
                raise ValueError("Verified absence requires an explicit assessed image region")
            if self.coverage.visible_fraction is not None or self.coverage.boundary_fraction is not None:
                raise ValueError("An absent component has undefined visible/boundary fractions")
        if self.state == "occluded":
            if self.coverage.visible_fraction != 0 or self.coverage.boundary_fraction != 0:
                raise ValueError("Fully occluded components require explicit zero visible/boundary coverage")
        if self.state in ("unknown", "inconsistent"):
            if self.confidence is not None or self.coverage.visible_fraction is not None or self.coverage.boundary_fraction is not None:
                raise ValueError("Unknown/inconsistent geometry must not assert confidence or coverage fractions")

    def to_dict(self) -> dict:
        result = {"id": self.id, "role": self.role, "state": self.state, "confidence": self.confidence,
                "provenance": self.provenance.to_dict(), "coverage": self.coverage.to_dict(),
                "contours": [contour.to_dict() for contour in self.contours],
                "landmarks": [landmark.to_dict() for landmark in self.landmarks], "notes": self.notes}
        # Optional additive v1 field: old documents retain exactly their hashes.
        if self.edges:
            result["edges"] = [edge.to_dict() for edge in self.edges]
        return result


def _read_provenance(value: Any) -> EvidenceProvenance:
    return EvidenceProvenance(**_fields(value, ("kind", "method", "source_sha256", "notes"), "Provenance"))


def _read_point(value: Any) -> ImagePoint:
    return ImagePoint(**_fields(value, ("x", "y", "sigma_x_px", "sigma_y_px"), "ImagePoint"))


def _read_component(value: Any) -> ComponentEvidence:
    keys = ("id", "role", "state", "confidence", "provenance", "coverage", "contours", "landmarks", "notes")
    if isinstance(value, dict) and "edges" in value:
        keys += ("edges",)
    data = _fields(value, keys, "Component")
    contours = []
    for raw in _list(data["contours"], "contours"):
        contour = _fields(raw, ("id", "points", "closed", "confidence", "provenance"), "Contour")
        contours.append(ContourEvidence(contour["id"], tuple(_read_point(p) for p in _list(contour["points"], "points")),
                                        contour["closed"], contour["confidence"], _read_provenance(contour["provenance"])))
    landmarks = []
    for raw in _list(data["landmarks"], "landmarks"):
        landmark = _fields(raw, ("id", "point", "confidence", "provenance"), "Landmark")
        landmarks.append(LandmarkEvidence(landmark["id"], _read_point(landmark["point"]), landmark["confidence"],
                                         _read_provenance(landmark["provenance"])))
    edges = []
    if "edges" in data:
        raw_edges = _list(data["edges"], "edges")
        if not raw_edges:
            raise ValueError("Canonical empty edges must omit the optional edges field")
        for raw in raw_edges:
            edge = _fields(raw, ("id", "point", "normal_xy", "sigma_normal_px", "confidence", "provenance", "tangent_unconstrained"), "Edge")
            edges.append(EdgeEvidence(edge["id"], _read_point(edge["point"]), edge["normal_xy"], edge["sigma_normal_px"],
                                      edge["confidence"], _read_provenance(edge["provenance"]), edge["tangent_unconstrained"]))
    coverage = Coverage(**_fields(data["coverage"], ("assessed_region_xyxy", "visible_fraction", "boundary_fraction", "notes"), "Coverage"))
    return ComponentEvidence(data["id"], data["role"], data["state"], data["confidence"], _read_provenance(data["provenance"]),
                             coverage, tuple(contours), tuple(landmarks), data["notes"], tuple(edges))


@dataclass(frozen=True)
class PhotoEvidence:
    source: SourceImage
    components: tuple[ComponentEvidence, ...]
    purpose: Literal["inference", "evaluation"] = "inference"
    evidence_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.source) is not SourceImage:
            raise ValueError("PhotoEvidence requires a typed source image")
        object.__setattr__(self, "components", _tuple(self.components, ComponentEvidence, "components"))
        if self.purpose not in ("inference", "evaluation"):
            raise ValueError("Evidence purpose must be inference or evaluation")
        if len({component.id for component in self.components}) != len(self.components):
            raise ValueError("Component IDs must be unique within a photo")
        for component in self.components:
            for provenance in (component.provenance, *(feature.provenance for feature in component.contours + component.landmarks + component.edges)):
                if provenance.source_sha256 != self.source.image_sha256:
                    raise ValueError("Every observation must refer to the bound source photo hash")
            region = component.coverage.assessed_region_xyxy
            if region is not None and (region[2] > self.source.width or region[3] > self.source.height):
                raise ValueError("Assessed region lies outside original photo dimensions")
            for point in (*[point for contour in component.contours for point in contour.points],
                          *[landmark.point for landmark in component.landmarks], *[edge.point for edge in component.edges]):
                if point.x > self.source.width - 1 or point.y > self.source.height - 1:
                    raise ValueError("Observation coordinates lie outside original photo pixel centers")
                if region is not None and not (region[0] <= point.x < region[2] and region[1] <= point.y < region[3]):
                    raise ValueError("Observation lies outside its declared assessed region")
        object.__setattr__(self, "evidence_sha256", self._digest())

    def _payload(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "coordinate_system": COORDINATE_SYSTEM, "uncertainty_model": UNCERTAINTY_MODEL,
                "source": self.source.to_dict(), "purpose": self.purpose,
                "components": [component.to_dict() for component in self.components]}

    def _digest(self) -> str:
        canonical = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        """Return detached JSON values; mutating this copy cannot change evidence."""
        return {**self._payload(), "evidence_sha256": self.evidence_sha256}

    def assert_unchanged(self, expected_hash: str) -> None:
        """Fit/evaluation consumers pin this digest BEFORE modifying a candidate.

        This detects a replacement evidence object with a newly computed digest,
        unlike merely trusting the document's own embedded digest on every call.
        """
        _hash(expected_hash, "expected evidence hash")
        if self.evidence_sha256 != expected_hash or self._digest() != expected_hash:
            raise ValueError("Evidence changed after its digest was pinned")

    def assert_source_bytes(self, data: bytes) -> None:
        self.source.assert_source_bytes(data)

    def component(self, component_id: str) -> ComponentEvidence:
        """Explicit lookup: missing components raise rather than becoming absent."""
        for component in self.components:
            if component.id == component_id:
                return component
        raise KeyError(component_id)

    @classmethod
    def from_dict(cls, value: Any, *, expected_hash: str | None = None) -> "PhotoEvidence":
        """Strict schema/hash loading; optionally enforce an externally pinned hash."""
        data = _fields(value, ("schema_version", "coordinate_system", "uncertainty_model", "source", "purpose", "components", "evidence_sha256"), "PhotoEvidence")
        if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported evidence schema_version")
        if data["coordinate_system"] != COORDINATE_SYSTEM or data["uncertainty_model"] != UNCERTAINTY_MODEL:
            raise ValueError("Unsupported coordinate or uncertainty semantics")
        source = SourceImage(**_fields(data["source"], ("image_sha256", "width", "height", "image_id"), "SourceImage"))
        result = cls(source, tuple(_read_component(item) for item in _list(data["components"], "components")), data["purpose"])
        result.assert_unchanged(data["evidence_sha256"])
        if expected_hash is not None:
            result.assert_unchanged(expected_hash)
        return result
