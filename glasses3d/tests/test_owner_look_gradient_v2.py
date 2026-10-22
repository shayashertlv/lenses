"""Gradient v2: positioned stops, per-transition softness, per-colour tint strength, area-centroid centres."""
from __future__ import annotations

import copy
import hashlib

import numpy as np
import pytest

from glasses3d.agent import owner_look_edit as look
from glasses3d.agent import owner_look_gradient as gradient
from glasses3d.tests.test_owner_look_edit import encode, fixture, selected


def v2(direction="left_to_right", stops=((1, 0, 0, 0, 1), (0, 0, 1, 1, 1)), softness=(1,), **extra):
    return {"version": 2, "direction": direction, "softness": list(softness),
            "stops": [{"colour": list(s[:3]), "position": s[3], "strength": s[4]} for s in stops], **extra}


def test_v1_images_stay_byte_identical_to_the_committed_renderer():
    # Hashes from the committed v1 renderer (HEAD before v2): saved versions keep verifying.
    linear = {"direction": "centre_to_sides", "blend": .6, "colours": [{"colour": [.8, .1, .3], "share": 30},
              {"colour": [.1, .9, .4], "share": 45}, {"colour": [.2, .2, .9], "share": 25}]}
    radial = {"direction": "around_point", "blend": .8, "centre": [.1, -.2], "roundness": 2,
              "colours": [{"colour": [.1, .8, .4], "share": 60}, {"colour": [.5, .2, .8], "share": 40}]}
    assert hashlib.sha256(gradient.image_bytes(linear)).hexdigest() == \
        "179d7ddb0324707b78251b540a3e566e84539e5834f648fa1cdd2a2792c62d07"
    assert hashlib.sha256(gradient.image_bytes(radial)).hexdigest() == \
        "41dfc1d9cb016f809fd3a03c2960f088330d64eaaa1bbc8835babda24737bfb0"


def test_softness_one_blends_across_the_whole_gap_and_zero_is_a_hard_edge():
    smooth = v2(stops=((1, 0, 0, .2, 1), (0, 0, 1, .8, 1)), softness=(1,))
    assert np.allclose(gradient.sample_gradient(smooth, [0, .2, .5, .8, 1]),
                       [[1, 0, 0], [1, 0, 0], [.5, 0, .5], [0, 0, 1], [0, 0, 1]], atol=1e-12)
    hard = v2(stops=((1, 0, 0, .2, 1), (0, 0, 1, .8, 1)), softness=(0,))
    assert gradient.sample_gradient(hard, [.499, .5, .501]).tolist() == [[1, 0, 0], [0, 0, 1], [0, 0, 1]]


def test_each_transition_has_its_own_softness():
    value = v2(stops=((1, 0, 0, 0, 1), (0, 1, 0, .5, 1), (0, 0, 1, 1, 1)), softness=(1, 0))
    rows = gradient.sample_gradient(value, [.25, .74, .76]).tolist()
    assert rows[0] == [.5, .5, 0]                       # soft first transition, mid-gap
    assert rows[1] == [0, 1, 0] and rows[2] == [0, 0, 1]  # hard second transition at .75


def test_tint_strength_mixes_clear_to_the_chosen_colour():
    dark_top = v2("top_to_bottom", stops=((.05, .05, .05, .1, 1), (.05, .05, .05, .9, 0)))
    samples = gradient.sample_gradient(dark_top, [0, .9])
    assert np.allclose(samples[0], .05) and np.allclose(samples[1], 1)  # dark top, fully clear bottom
    half = v2(stops=((.2, .4, .6, .5, .5), (1, 1, 1, .9, 1)))
    assert np.allclose(gradient.sample_gradient(half, [0])[0], [.6, .7, .8])


@pytest.mark.parametrize("bad, message", [
    ({"stops": [{"colour": [1, 0, 0], "position": .5, "strength": 1}, {"colour": [0, 0, 1], "position": .5, "strength": 1}]}, "in order"),
    ({"softness": [1, 1]}, "own softness"),
    ({"stops": [{"colour": [1, 0, 0], "position": .1, "strength": 1}]}, "two or three"),
    ({"stops": [{"colour": [1, 0, 0], "position": .1, "strength": 2}, {"colour": [0, 0, 1], "position": .9, "strength": 1}]}, "Tint strength"),
    ({"blend": .5}, "Provide the gradient stops"),
    ({"version": 3}, "not supported"),
])
def test_invalid_v2_specs_are_refused_plainly(bad, message):
    with pytest.raises(ValueError, match=message):
        gradient.validate_gradient({**v2(), **bad})


def test_v2_round_trips_through_the_glb_patch_and_verifies_its_texture():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = v2("centre_to_sides", stops=((.3, .1, .5, .1, 1), (.2, .8, .5, .5, .7), (.3, .1, .5, .9, 1)),
                            softness=(.8, .3))
    patched, receipt = look.patch_glb(original, {0: values})
    assert receipt["changed_material_indices"] == [0]
    read = look.read_materials(patched)["materials"][0]
    assert read["gradient"] == gradient.validate_gradient(values["gradient"])
    again, _ = look.patch_glb(patched, {0: copy.deepcopy(read["values"])})
    assert again == patched  # an unchanged v2 look is a no-op


def test_radial_v2_and_area_centroid_default_centre():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    row = look.read_materials(original)["materials"][0]
    surface = row["radial_projection"]["surfaces"][0]
    assert len(surface["area_centroid"]) == 2
    low = np.array(surface["centre"]) - surface["half_extent"]
    high = np.array(surface["centre"]) + surface["half_extent"]
    assert (low <= surface["area_centroid"]).all() and (np.array(surface["area_centroid"]) <= high).all()
    values = selected(original)
    values["gradient"] = v2("around_point", stops=((.1, .8, .4, 0, 1), (.5, .2, .8, .6, 1)), softness=(.5,),
                            centre=[0, .3], roundness=1.5)
    patched, _ = look.patch_glb(original, {0: values})
    assert look.read_materials(patched)["materials"][0]["gradient"]["centre"] == [0, .3]
