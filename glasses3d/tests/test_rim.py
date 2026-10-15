"""Independent numerical/topological checks for the general closed-rim helpers."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

import numpy as np
import pytest

from glasses3d.g3d import safe_mode
from glasses3d.g3d.rim import RimError, acetate_section, sweep_rim


def circle(n=128, radius=25.0, wrap=0.0):
    angle = np.arange(n) * 2 * math.pi / n
    return np.c_[radius*np.cos(angle), radius*np.sin(angle), -wrap*(radius*np.cos(angle))**2]


def topology(vertices, triangles):
    edges = Counter((int(a), int(b)) for f in triangles for a, b in zip(f, np.roll(f, -1)))
    assert all(count == 1 and edges[(b, a)] == 1 for (a, b), count in edges.items())
    assert len(vertices) - len(edges)//2 + len(triangles) == 0  # one real opening, not capped ends
    signed = sum(np.dot(vertices[a], np.cross(vertices[b], vertices[c])) for a, b, c in triangles) / 6
    assert signed > 0
    return signed


@pytest.mark.parametrize("crown", [0.0, 1e-6, 0.4, 1.2])
def test_section_dimensions_tangency_and_round_radius(crown):
    s = acetate_section(6, 5, 0.6, crown)
    p, kinds = np.array(s["points"]), np.array(s["kind"])
    assert np.ptp(p[:, 0]) == pytest.approx(6, abs=1e-10)
    assert np.ptp(p[:, 1]) == pytest.approx(5, abs=1e-10)
    assert not any(s["sharp"])
    centers = {}
    for name in ("rear_outer_round", "front_outer_round", "front_inner_round", "rear_inner_round"):
        arc = p[kinds == name]
        # Three non-collinear points independently determine the circle.
        a, b, c = arc[0], arc[len(arc)//2], arc[-1]
        center = np.linalg.solve(2*np.vstack((b-a, c-a)), np.array([b@b-a@a, c@c-a@a]))
        centers[name] = center
        assert np.linalg.norm(arc-center, axis=1) == pytest.approx(np.full(len(arc), 0.6), abs=2e-9)
    right = p[kinds == "front_outer_round"]
    assert p[:, 1].max() - right[-1, 1] == pytest.approx(crown, abs=2e-9)
    at_join = right[-1]-centers["front_outer_round"]
    if crown >= .01:
        face = p[kinds == "crown"]
        a, b, c = right[-1], face[len(face)//2], face[-1]
        center = np.linalg.solve(2*np.vstack((b-a, c-a)), np.array([b@b-a@a, c@c-a@a]))
        normal = a-center
        assert at_join/np.linalg.norm(at_join) == pytest.approx(normal/np.linalg.norm(normal), abs=1e-9)
    elif crown == 0:
        assert at_join == pytest.approx([0, .6], abs=1e-9)
    # The tangent directions at the analytic joins agree to the sampling error;
    # no accidental straight cliff or duplicated/zero-length edge is present.
    edges = np.roll(p, -1, axis=0)-p
    assert np.linalg.norm(edges, axis=1).min() > 1e-8
    assert len(p) == len(acetate_section(6, 5, 0.6, 0.7)["points"])


def test_circle_true_hole_volume_and_no_invented_sharp_edges():
    s = acetate_section(6, 5, 0.6, 0.7)
    out = sweep_rim(circle(), s)
    v, f = out["V"], out["F"]
    signed = topology(v, f)
    assert out["closed"] and out["euler"] == 0 and not out["sharp_edges"]
    assert out["volume_mm3"] == pytest.approx(signed)
    # Pappus' volume theorem is an independent continuous-shape reference. Only
    # the sampled centreline's chord error remains, bounded here at 0.05%.
    assert signed == pytest.approx(s["area_mm2"] * 2*math.pi*25, rel=0.0005)
    # No triangle can cover the opening: every projected triangle stays >20 mm
    # from the centre in a separating radial half-plane.
    centers = v[f].mean(axis=1)[:, :2]
    normal = centers / np.linalg.norm(centers, axis=1)[:, None]
    assert np.einsum("fvc,fc->fv", v[f, :2], normal).min() > 20


def test_near_maximum_crown_keeps_corresponding_profile_vertices():
    mild = acetate_section(6, 10, .6, .7)
    steep = acetate_section(6, 10, .6, 2.9999999)
    assert len(mild["points"]) == len(steep["points"]) == 51
    assert mild["kind"] == steep["kind"]
    with pytest.raises(RimError, match="resolution"):
        acetate_section(6, 10, .6, 2.99999999999)


@pytest.mark.parametrize("reverse", [False, True])
def test_wrapped_rim_closed_seam_and_front_direction(reverse):
    q = circle(96, wrap=0.012)
    if reverse:
        q = q[::-1]
    s = acetate_section(5, 4, 0.5, 0.5)
    out = sweep_rim(q, s)
    topology(out["V"], out["F"])
    assert out["V"].shape[0] == 96*len(s["points"])
    assert np.ptp(out["V"][:, 2]) > 10
    # Last/first stations share mesh edges; no coincident caps or endpoint copy.
    seam = [(a, b) for a, b in out["F"][:, :2] if a//len(s["points"]) != b//len(s["points"])]
    assert seam


def test_named_local_facet_span_does_not_mark_whole_rim():
    q = circle(64)
    s = acetate_section(5, 4, 0.5, 0.4)
    profiles = [deepcopy(s) for _ in q]
    # A deliberately authored flat crown segment, only at stations 10..14.
    crown = [i for i, k in enumerate(s["kind"]) if k == "crown"]
    left, right = crown[3], crown[-4]
    for i in range(10, 15):
        for j in range(left, right+1):
            profiles[i]["points"][j][1] = 1.75
        profiles[i]["sharp"][left] = profiles[i]["sharp"][right] = True
    out = sweep_rim(q, s, sections=profiles)
    topology(out["V"], out["F"])
    assert set(out["sharp_spans"]) == {(i, j) for i in range(10, 14) for j in (left, right)}
    base = sweep_rim(q, s)["V"].reshape(len(q), -1, 3)
    altered = out["V"].reshape(len(q), -1, 3)
    assert np.array_equal(base[:10], altered[:10]) and np.array_equal(base[15:], altered[15:])


@pytest.mark.parametrize("args", [(6,5,0,1), (1,5,0.6,0), (6,1,0.6,0), (6,5,.6,-1),
                                  (6,5,.6,3), (6,1.3,.6,1), (6,5,.6,0,3)])
def test_bad_section_dimensions_refused(args):
    with pytest.raises(RimError):
        acetate_section(*args)


@pytest.mark.parametrize("bad", ["endpoint", "zero", "shape", "sections", "count", "winding"])
def test_bad_sweeps_refused(bad):
    q, s = circle(16), acetate_section(4, 4, .5, .4)
    kwargs = {}
    if bad == "endpoint":
        q = np.vstack((q, q[0]))
    elif bad == "zero":
        q[2] = q[1]
    elif bad == "shape":
        q = q[:, :2]
    elif bad == "sections":
        kwargs["sections"] = [s]
    elif bad == "count":
        altered = deepcopy(s)
        altered["points"] = altered["points"][:-1]
        altered["sharp"] = altered["sharp"][:-1]
        kwargs["sections"] = [altered] + [s]*15
    else:
        s["points"] = s["points"][::-1]
    with pytest.raises(RimError):
        sweep_rim(q, s, **kwargs)


def test_exact_facade_names_are_usable_without_widening_imports():
    assert safe_mode.validate("s = g3d.acetate_section(6,5,.6,.7)\nresult = g3d.sweep_rim([[0,0,0],[20,0,0],[0,20,0]],s)") == []
    assert safe_mode.validate("from g3d.lib import acetate_section, sweep_rim\nresult = acetate_section(6,5,.6,.7)") == []
    assert safe_mode.validate("result = g3d.rim_bpy.build_rim([],{})")
    assert safe_mode.validate("result = g3d.load_font('Arial')")
