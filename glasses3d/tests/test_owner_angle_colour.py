"""Owner exact angle colours: saved in material extras, mirrored by the AR renderer, optional for old looks."""
from __future__ import annotations

import copy
import json
import struct

import pytest

from glasses3d.agent import owner_angle_colour as angle
from glasses3d.agent import owner_look_edit as look
from glasses3d.tests.test_owner_look_edit import encode, fixture, selected


def spec(softness=(1,), stops=(([.42, .17, .58], .35), ([.1, .52, .25], .75))):
    return {"schema": angle.SCHEMA, "softness": list(softness), "reflection": True, "tint": .6,
            "stops": [{"colour": list(colour), "position": position} for colour, position in stops]}


def material_json(payload, index=0):
    length = struct.unpack_from("<I", payload, 12)[0]
    return json.loads(payload[20:20 + length])["materials"][index]


def coated(original):
    values = selected(original)
    values.update(iridescence=1, iridescence_thickness_min=400, iridescence_thickness_max=400)
    return values


def test_angle_colours_round_trip_through_extras_and_remove_cleanly():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    values = coated(original)
    values["angle_colour"] = spec()
    patched, receipt = look.patch_glb(original, {0: values})
    assert material_json(patched)["extras"][angle.KEY] == spec()
    read = look.read_materials(patched)["materials"][0]["values"]
    assert read["angle_colour"] == spec()
    again, _ = look.patch_glb(patched, {0: copy.deepcopy(read)})
    assert again == patched  # unchanged exact colours are a no-op
    removed = copy.deepcopy(read)
    del removed["angle_colour"]
    cleared, _ = look.patch_glb(patched, {0: removed})
    assert angle.KEY not in material_json(cleared).get("extras", {})
    assert "angle_colour" not in look.read_materials(cleared)["materials"][0]["values"]


def test_old_looks_without_the_field_keep_their_exact_shape():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    values = selected(original)
    assert "angle_colour" not in values
    assert "angle_colour" not in look._values(values)
    assert look._values({**values, "angle_colour": None}) == look._values(values)


def test_angle_colours_need_the_coating_on():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    values = selected(original)
    values.update(iridescence=0, angle_colour=spec())
    with pytest.raises(ValueError, match="need the coating on"):
        look.patch_glb(original, {0: values})


def test_a_lens_colour_tint_alone_works_with_the_coating_off():
    doc, binary = fixture(shield=True, existing_uv=True)
    original = encode(doc, binary)
    values = selected(original)
    values.update(iridescence=0, angle_colour={**spec(), "reflection": False, "tint": .7})
    patched, _ = look.patch_glb(original, {0: values})
    assert material_json(patched)["extras"][angle.KEY]["tint"] == .7


@pytest.mark.parametrize("bad, message", [
    ({**spec(), "schema": "other"}, "colours, positions, softness"),
    ({**spec(), "reflection": False, "tint": 0}, "reflections, the lens colour, or both"),
    ({**spec(), "tint": 2}, "Lens colour tint"),
    (spec(softness=(1, 1)), "own softness"),
    (spec(stops=(([1, 0, 0], .5), ([0, 1, 0], .5))), "in order"),
    (spec(stops=(([2, 0, 0], 0), ([0, 1, 0], 1))), "Angle colour"),
    (spec(softness=(), stops=(([1, 0, 0], 0),)), "two or three"),
])
def test_invalid_angle_colours_are_refused_plainly(bad, message):
    with pytest.raises(ValueError, match=message):
        angle.validate_angle_colour(bad)


def test_a_malformed_saved_spec_is_ignored_like_the_renderer_does():
    assert angle.read({"extras": {angle.KEY: {"schema": angle.SCHEMA, "stops": [], "softness": []}}}) is None
    assert angle.read({"extras": {angle.KEY: spec()}}) == spec()
