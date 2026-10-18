"""Real exported float32 temple geometry, explicit cameras and first-hit visibility; no visibility stub."""
import numpy as np
import pytest

from glasses3d.agent.temple_lab import crease_ar as AR
from glasses3d.g3d.temple import fit as F
from glasses3d.g3d.temple.build import build_temple
from glasses3d.tests import glbfab
from glasses3d.tests.test_temple_visible_fit import design


@pytest.mark.parametrize("side", [1, -1])
def test_visible_target_distinguishes_outer_facet_from_hidden_inner_side(tmp_path, side):
    source = design()
    source["hinge"]["opening_deg"] = 7.
    tb = build_temple(F.fit_spec(source).spec)
    vertices, _, triangles = tb.world(side)

    def patch(doc, add_accessor):
        primitive = doc["meshes"][0]["primitives"][0]
        primitive["attributes"]["POSITION"] = add_accessor((vertices / 1000).astype("<f4"), 5126, "VEC3")
        primitive["indices"] = add_accessor(triangles.reshape(-1).astype("<u4"), 5125, "SCALAR")

    model = tmp_path / "temple.glb"
    model.write_bytes(glbfab.build({"body": ((0, 1, 0, 1, 0, 1), "frame", "temple")}, patch=patch))

    def camera(view_side):
        # The photo poses this built arm by a further 13 degrees; visible_target must undo both transforms.
        R = np.array([[0., 0., -view_side], [0., 1., 0.], [view_side, 0., 0.]])
        eye = np.array([view_side * .3, 0., -.055])
        view = np.eye(4)
        view[:3, :3], view[:3, 3] = R, -R @ eye
        axis = tb.spec.hinge["axis_xz_mm"]
        return {"view": view.ravel().tolist(), "fx": 700., "fy": 700., "cx": 160., "cy": 120.,
                "width": 320, "height": 240, "temple_pose": {
                    f"{side:+d}": {"theta_deg": 13., "axis_xz_mm": [side * axis[0], axis[1]]}}}

    visible = AR.visible_target(model, camera(side), tb, "lower", [0, 0, 320, 240], side)
    hidden = AR.visible_target(model, camera(-side), tb, "lower", [0, 0, 320, 240], side)
    missing = AR.visible_target(model, camera(side), tb, "absent", [0, 0, 320, 240], side)
    assert visible > 100
    assert hidden == missing == 0
