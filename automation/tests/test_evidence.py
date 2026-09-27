from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from reconstruction.evidence import (
    COORDINATE_SYSTEM, ComponentEvidence, ContourEvidence, Coverage, EdgeEvidence,
    EvidenceProvenance, ImagePoint, LandmarkEvidence, PhotoEvidence, SourceImage,
)


PHOTO_BYTES = b"original encoded photo identity for schema tests"
PHOTO_HASH = hashlib.sha256(PHOTO_BYTES).hexdigest()


def provenance(kind="reviewed", photo_hash=PHOTO_HASH):
    return EvidenceProvenance(kind, "reviewed-contour-protocol-v1" if kind == "reviewed" else "semantic-extractor-v3", photo_hash)


def point(x=10, y=20, sx=0.75, sy=1.25):
    return ImagePoint(x, y, sx, sy)


def present_component():
    return ComponentEvidence(
        "lens.left", "lens", "present", 0.9, provenance(),
        Coverage((0, 0, 120, 90), 0.65, 0.4, "Only the upper contour and nasal corner are observed"),
        contours=(ContourEvidence("upper_edge", (point(10, 20), point(40, 15), point(80, 25)), False, 0.85, provenance()),),
        landmarks=(LandmarkEvidence("nasal_corner", point(80, 25, 1, 2), 0.9, provenance()),),
    )


def snapshot(component=None, purpose="evaluation"):
    return PhotoEvidence(SourceImage(PHOTO_HASH, 120, 90, "front-photo-1"),
                         (component or present_component(),), purpose)


class EvidenceSchemaTests(unittest.TestCase):
    def edge(self, **changes):
        return replace(EdgeEvidence("image_edge_3", point(), (.6, -.8), .7, .8, provenance("automatic")), **changes)

    def edge_component(self, **changes):
        return replace(ComponentEvidence("image_edges", "other", "present", .8, provenance("automatic"),
                                         Coverage(None, None, None), notes="Candidate-guided image edges; semantic identity unmeasured",
                                         edges=(self.edge(),)), **changes)

    def test_edge_only_presence_round_trip_keeps_tangent_unconstrained(self):
        original = snapshot(self.edge_component(), purpose="inference")
        raw = json.loads(json.dumps(original.to_dict(), allow_nan=False))
        restored = PhotoEvidence.from_dict(raw, expected_hash=original.evidence_sha256)
        self.assertEqual(restored, original)
        edge = restored.components[0].edges[0]
        self.assertEqual(edge.normal_xy, (.6, -.8))
        self.assertEqual(edge.sigma_normal_px, .7)
        self.assertTrue(edge.tangent_unconstrained)
        self.assertEqual(edge.point.xy, (10, 20))
        self.assertEqual(restored.components[0].landmarks, ())
        self.assertEqual(restored.components[0].contours, ())
        self.assertIsNone(restored.components[0].coverage.boundary_fraction)

    def test_old_v1_without_edges_preserves_canonical_payload_and_digest(self):
        original = snapshot()
        raw = original.to_dict()
        self.assertNotIn("edges", raw["components"][0])
        restored = PhotoEvidence.from_dict(raw)
        self.assertEqual(restored.components[0].edges, ())
        self.assertEqual(restored.to_dict(), raw)
        explicit_empty = replace(original.components[0], edges=())
        self.assertEqual(snapshot(explicit_empty).evidence_sha256, original.evidence_sha256)
        # Keep one canonical representation: empty optional fields are omitted.
        raw["components"][0]["edges"] = []
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(raw)

    def test_edge_unit_normal_scalar_uncertainty_and_tangent_validation(self):
        for normal in ((0, 0), (2, 0), (.6, .7), (True, 0), (float("nan"), 0), (0, float("inf")), (1,), "xy"):
            with self.subTest(normal=normal), self.assertRaises(ValueError):
                self.edge(normal_xy=normal)
        for sigma in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.subTest(sigma=sigma), self.assertRaises(ValueError):
                self.edge(sigma_normal_px=sigma)
        for value in (False, 1, None, "true"):
            with self.subTest(tangent=value), self.assertRaises(ValueError):
                self.edge(tangent_unconstrained=value)
        for confidence in (0, -1, 1.1, True, float("nan")):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                self.edge(confidence=confidence)

    def test_edge_hash_binds_normal_uncertainty_anchor_confidence_and_provenance(self):
        edge = self.edge()
        changes = ({"normal_xy": (-.6, .8)}, {"sigma_normal_px": .9}, {"point": point(11, 20)},
                   {"confidence": .5}, {"provenance": provenance("reviewed")})
        original = snapshot(self.edge_component())
        hashes = {original.evidence_sha256}
        for change in changes:
            candidate = snapshot(self.edge_component(edges=(replace(edge, **change),)))
            hashes.add(candidate.evidence_sha256)
            with self.assertRaises(ValueError):
                candidate.assert_unchanged(original.evidence_sha256)
        self.assertEqual(len(hashes), len(changes) + 1)

    def test_edge_strict_fields_and_field_loss_are_rejected(self):
        original = snapshot(self.edge_component())
        for name in ("normal_xy", "sigma_normal_px", "tangent_unconstrained", "point", "provenance"):
            raw = original.to_dict()
            del raw["components"][0]["edges"][0][name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                PhotoEvidence.from_dict(raw)
        raw = original.to_dict()
        raw["components"][0]["edges"][0]["verified_lens_boundary"] = True
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(raw)
        raw = original.to_dict()
        raw["components"][0]["edges"][0]["normal_xy"] = [-.6, .8]
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(raw)

    def test_edges_follow_source_region_pixel_bounds_and_coverage_contract(self):
        for edge in (self.edge(provenance=provenance(photo_hash="f" * 64)), self.edge(point=point(120, 89))):
            with self.assertRaises(ValueError):
                snapshot(self.edge_component(edges=(edge,)))
        with self.assertRaises(ValueError):
            snapshot(self.edge_component(coverage=Coverage((30, 30, 80, 80), None, None)))
        for coverage in (Coverage(None, 0, None), Coverage(None, None, 0)):
            with self.assertRaises(ValueError):
                self.edge_component(coverage=coverage)
        for state in ("unknown", "inconsistent", "occluded", "verified_absent"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.edge_component(state=state)

    def test_edge_ids_share_namespace_with_other_feature_types(self):
        with self.assertRaises(ValueError):
            self.edge_component(landmarks=(LandmarkEvidence("image_edge_3", point(), .8, provenance()),))
        with self.assertRaises(ValueError):
            self.edge_component(edges=(self.edge(), self.edge()))

    def test_edge_normal_and_container_sequences_are_owned_and_frozen(self):
        normal = [.6, -.8]
        edge = self.edge(normal_xy=normal)
        edges = [edge]
        original = snapshot(self.edge_component(edges=edges))
        normal[0] = 0
        edges.clear()
        self.assertEqual(original.components[0].edges[0].normal_xy, (.6, -.8))
        with self.assertRaises(FrozenInstanceError):
            edge.sigma_normal_px = 5
        raw = original.to_dict()
        raw["components"][0]["edges"][0]["normal_xy"][0] = 0
        original.assert_unchanged(original.evidence_sha256)

    def test_pixel_coordinates_uncertainty_coverage_and_provenance_round_trip(self):
        original = snapshot()
        raw = json.loads(json.dumps(original.to_dict(), allow_nan=False))
        restored = PhotoEvidence.from_dict(raw, expected_hash=original.evidence_sha256)
        self.assertEqual(restored, original)
        self.assertEqual(restored.to_dict()["coordinate_system"], COORDINATE_SYSTEM)
        lens = restored.component("lens.left")
        self.assertEqual(lens.contours[0].points[0].xy, (10, 20))
        self.assertEqual(lens.contours[0].points[0].sigma_xy_px, (0.75, 1.25))
        self.assertEqual(lens.coverage.visible_fraction, 0.65)
        self.assertEqual(lens.provenance.kind, "reviewed")
        self.assertEqual(restored.purpose, "evaluation")
        restored.assert_source_bytes(PHOTO_BYTES)

    def test_unknown_coverage_stays_explicitly_unknown_for_present_geometry(self):
        component = replace(present_component(), coverage=Coverage(None, None, None, "Cannot estimate hidden boundary length"))
        evidence = snapshot(component)
        self.assertIsNone(evidence.component("lens.left").coverage.visible_fraction)
        self.assertIsNone(PhotoEvidence.from_dict(evidence.to_dict()).component("lens.left").coverage.boundary_fraction)

    def test_empty_or_unobserved_components_never_become_verified_absent(self):
        with self.assertRaises(ValueError):
            replace(present_component(), contours=(), landmarks=())
        unknown = ComponentEvidence("rim.lower", "rim", "unknown", None, provenance("automatic"),
                                    Coverage(None, None, None), notes="The clear lower edge cannot be resolved")
        evidence = snapshot(unknown)
        self.assertEqual(PhotoEvidence.from_dict(evidence.to_dict()).component("rim.lower").state, "unknown")
        empty = PhotoEvidence(evidence.source, (), "evaluation")
        self.assertEqual(PhotoEvidence.from_dict(empty.to_dict()).components, ())
        with self.assertRaises(KeyError):
            empty.component("rim.lower")

    def test_verified_absence_is_explicit_region_scoped_and_requires_reason(self):
        absent = ComponentEvidence("rim.lower", "rim", "verified_absent", 0.98, provenance(),
                                   Coverage((10, 30, 90, 60), None, None), notes="Reviewed unobstructed lower lens edge in this image")
        read = PhotoEvidence.from_dict(snapshot(absent).to_dict()).component("rim.lower")
        self.assertEqual(read.state, "verified_absent")
        self.assertEqual(read.coverage.assessed_region_xyxy, (10, 30, 90, 60))
        for change in ({"coverage": Coverage(None, None, None)}, {"notes": " "}, {"confidence": None},
                       {"coverage": Coverage((10, 30, 90, 60), 0, 0)}, {"landmarks": present_component().landmarks}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(absent, **change)

    def test_occluded_inconsistent_and_unknown_have_distinct_nonfit_states(self):
        for state, confidence, coverage in (
            ("occluded", 0.9, Coverage((30, 0, 120, 80), 0, 0)),
            ("unknown", None, Coverage(None, None, None)),
            ("inconsistent", None, Coverage(None, None, None)),
        ):
            component = ComponentEvidence("temple.right", "temple", state, confidence, provenance(), coverage,
                                          notes="Visibility review result for this source image")
            self.assertEqual(PhotoEvidence.from_dict(snapshot(component).to_dict()).components[0].state, state)
            with self.assertRaises(ValueError):
                replace(component, contours=present_component().contours)
        with self.assertRaises(ValueError):
            ComponentEvidence("temple", "temple", "occluded", 0.9, provenance(), Coverage(None, None, None), notes="Hidden")
        with self.assertRaises(ValueError):
            ComponentEvidence("temple", "temple", "unknown", 0.5, provenance(), Coverage(None, None, None), notes="Unknown")

    def test_landmark_only_presence_is_valid_and_zero_coverage_cannot_hide_visible_data(self):
        component = replace(present_component(), contours=(), coverage=Coverage(None, 0.05, None))
        self.assertEqual(snapshot(component).components[0].state, "present")
        for coverage in (Coverage(None, 0, None), Coverage(None, None, 0)):
            with self.assertRaises(ValueError):
                replace(present_component(), coverage=coverage)

    def test_contours_require_nonzero_extent_and_explicit_closure_without_repeated_endpoints(self):
        for points, closed in (((), False), ((point(),), False), ((point(), point()), False),
                               ((point(), point(30, 40)), True),
                               ((point(), point(30, 40), point()), True),
                               ((point(), point(30, 40), point(30, 40), point(50, 20)), False)):
            with self.subTest(points=points, closed=closed), self.assertRaises(ValueError):
                ContourEvidence("outline", points, closed, 0.8, provenance())
        closed = ContourEvidence("outline", (point(), point(30, 40), point(50, 20)), True, 0.8, provenance())
        self.assertTrue(closed.closed)
        with self.assertRaises(ValueError):
            replace(closed, closed=1)

    def test_coordinates_are_original_pixel_centers_and_regions_are_image_edges(self):
        full = Coverage((0, 0, 120, 90), None, None)
        component = replace(present_component(), contours=(), coverage=full,
                            landmarks=(LandmarkEvidence("last_center", point(119, 89), 0.9, provenance()),))
        snapshot(component)
        for bad_point in (point(120, 89), point(119, 90)):
            bad = replace(component, landmarks=(LandmarkEvidence("bad", bad_point, 0.9, provenance()),))
            with self.assertRaises(ValueError):
                snapshot(bad)
        for region in ((0, 0, 121, 90), (0, 0, 120, 91), (0, 0, 5, 5)):
            with self.assertRaises(ValueError):
                snapshot(replace(present_component(), coverage=Coverage(region, None, None)))
        for region in ((1, 1, 1, 2), (1, 1, 2, 1), (-1, 0, 2, 2), (0, 0, 1)):
            with self.assertRaises(ValueError):
                Coverage(region, None, None)

    def test_every_feature_must_be_bound_to_exact_source_photo(self):
        wrong = provenance(photo_hash="f" * 64)
        for component in (
            replace(present_component(), provenance=wrong),
            replace(present_component(), contours=(replace(present_component().contours[0], provenance=wrong),)),
            replace(present_component(), landmarks=(replace(present_component().landmarks[0], provenance=wrong),)),
        ):
            with self.assertRaises(ValueError):
                snapshot(component)
        with self.assertRaises(ValueError):
            snapshot().assert_source_bytes(PHOTO_BYTES + b"resized or edited")
        with self.assertRaises(ValueError):
            snapshot().assert_source_bytes(bytearray(PHOTO_BYTES))

    def test_mutable_constructor_sequences_are_copied_and_nested_values_are_frozen(self):
        points = [point(), point(30, 40)]
        contour = ContourEvidence("edge", points, False, 0.8, provenance())
        contours = [contour]
        component = replace(present_component(), contours=contours)
        components = [component]
        evidence = PhotoEvidence(snapshot().source, components, "evaluation")
        pinned = evidence.evidence_sha256
        points.clear()
        contours.clear()
        components.clear()
        self.assertEqual(len(evidence.components[0].contours[0].points), 2)
        evidence.assert_unchanged(pinned)
        for obj, field, value in ((evidence, "purpose", "inference"), (component, "state", "unknown"),
                                  (contour.points[0], "x", 50), (component.coverage, "boundary_fraction", 1)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)

    def test_serialized_copy_cannot_mutate_snapshot_and_changed_payload_fails_embedded_hash(self):
        evidence = snapshot()
        raw = evidence.to_dict()
        raw["components"][0]["contours"][0]["points"][0]["x"] += 1
        self.assertEqual(evidence.components[0].contours[0].points[0].x, 10)
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(raw)
        evidence.assert_unchanged(evidence.evidence_sha256)

    def test_external_pin_detects_legitimately_rehashed_replacement(self):
        original = snapshot()
        changed = snapshot(replace(original.components[0], confidence=0.5))
        self.assertNotEqual(changed.evidence_sha256, original.evidence_sha256)
        PhotoEvidence.from_dict(changed.to_dict())
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(changed.to_dict(), expected_hash=original.evidence_sha256)
        with self.assertRaises(ValueError):
            changed.assert_unchanged(original.evidence_sha256)

    def test_hash_binds_uncertainty_coverage_provenance_state_and_purpose(self):
        original = snapshot()
        component = original.components[0]
        alternatives = (
            snapshot(replace(component, coverage=replace(component.coverage, boundary_fraction=0.3))),
            snapshot(replace(component, provenance=provenance("automatic"))),
            snapshot(replace(component, contours=(replace(component.contours[0], points=(point(10, 20, 2, 3), *component.contours[0].points[1:])),))),
            snapshot(purpose="inference"),
        )
        self.assertEqual(len({original.evidence_sha256, *(item.evidence_sha256 for item in alternatives)}), 5)
        for candidate in alternatives:
            with self.assertRaises(ValueError):
                candidate.assert_unchanged(original.evidence_sha256)

    def test_duplicate_ids_are_rejected_but_component_roles_are_not_topology_templates(self):
        component = present_component()
        with self.assertRaises(ValueError):
            PhotoEvidence(snapshot().source, (component, component))
        with self.assertRaises(ValueError):
            replace(component, landmarks=(replace(component.landmarks[0], id=component.contours[0].id),))
        custom = replace(component, id="clip_on.decorative_member.3", role="other", notes="Detachable decorative member")
        self.assertEqual(snapshot(custom).components[0].id, custom.id)
        with self.assertRaises(ValueError):
            replace(component, role="other", notes="")
        with self.assertRaises(ValueError):
            replace(component, role="arbitrary_unversioned_role")

    def test_strict_json_schema_rejects_unknown_semantics_and_fields(self):
        for key, value in (("schema_version", 2), ("schema_version", True), ("coordinate_system", "normalized_uv"),
                           ("uncertainty_model", "confidence_is_sigma"), ("purpose", "quality_pass"), ("components", {})):
            raw = snapshot().to_dict()
            raw[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                PhotoEvidence.from_dict(raw)
        raw = snapshot().to_dict()
        raw["components"][0]["pass"] = True
        with self.assertRaises(ValueError):
            PhotoEvidence.from_dict(raw)
        for key in ("evidence_sha256", "coordinate_system", "uncertainty_model"):
            raw = snapshot().to_dict()
            del raw[key]
            with self.assertRaises(ValueError):
                PhotoEvidence.from_dict(raw)

    def test_uncertainty_and_confidence_never_become_nan_infinite_or_implicit(self):
        for sigma in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.assertRaises(ValueError):
                point(sx=sigma)
        for coordinate in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                point(x=coordinate)
        for confidence in (0, -0.1, 1.1, float("nan"), float("inf"), True, "high"):
            with self.assertRaises(ValueError):
                replace(present_component(), confidence=confidence)
            with self.assertRaises(ValueError):
                replace(present_component().contours[0], confidence=confidence)
        for fraction in (-0.1, 1.1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                Coverage(None, fraction, None)

    def test_source_hash_and_dimensions_require_explicit_exact_values(self):
        for digest in ("", "x" * 64, PHOTO_HASH.upper(), None):
            with self.assertRaises(ValueError):
                SourceImage(digest, 120, 90, "source")
        for width in (0, -1, 120.0, True, "120"):
            with self.assertRaises(ValueError):
                SourceImage(PHOTO_HASH, width, 90, "source")
        with self.assertRaises(ValueError):
            EvidenceProvenance("guessed", "method", PHOTO_HASH)


if __name__ == "__main__":
    unittest.main()
