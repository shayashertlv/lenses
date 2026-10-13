"""Real rimless GLB → construction evidence → optional fit → frozen camera evidence."""
import json

import numpy as np
from PIL import Image

from glasses3d.g3d_host import cameras, compare as C, fit_views, fitmesh, matte as M, raster
from glasses3d.tests.fixtures import seat_fixtures as SF
from glasses3d.tests.test_cameras_fit import bar
from glasses3d.tests.test_fit_views_synthetic import PPM, SHAPE, TRUTH, photo


def test_rimless_photo_fit_preserves_raw_and_effective_metrics(tmp_path):
    nodes = []
    for sign, name in ((-1, "Lens_R"), (1, "Lens_L")):
        lens = SF.lens(SF.ellipse(cx=sign * 32., n=48), nring=5)
        nodes.append({"name": name, "mesh": lens, "role": "lens",
                      "extras": {"g3d_seat": {"frame_type": "rimless"}},
                      "material": {"name": "Clear lens", "transmission": 1.}})
    # Rimless construction: bridge/endpiece mounts and ear-bent temples, no
    # eyewire enclosing a lens opening that the photo matte could fill.
    solids = [("Bridge", "frame", (-9., 9., -1.), (9., 9., -1.), 3., 4.)]
    for sign, side in ((-1, "R"), (1, "L")):
        solids += [(f"Mount_{side}", "frame", (sign * 53., 6., -2.), (sign * 66., 6., -2.), 4., 8.),
                   (f"Temple_{side}", "temple", (sign * 66., 6., -4.), (sign * 69., 5., -110.), 3., 6.),
                   (f"Ear_{side}", "temple", (sign * 69., 5., -110.), (sign * 68., -18., -138.), 3., 5.)]
    for name, role, start, end, width, height in solids:
        vertices, triangles = bar(start, end, width, height)
        nodes.append({"name": name, "mesh": SF.Mesh(vertices, triangles), "role": role})
    glb = tmp_path / "rimless.glb"
    SF.write_glb(glb, nodes)
    model = fitmesh.load_fit_mesh(glb)
    optional = np.isin(model.part, [i for i, lens in enumerate(model.lens_parts) if lens])
    # Render the actual hard frame through transparent lenses. Use the existing
    # untuned front-camera truth, construction-selected matte, unchanged seeds and gates.
    hard = raster.get_scene(model.V, model.F[~optional], model.frame)
    kind, yaw, pitch, roll, perspective = TRUTH["front.png"]
    truth = raster.view_camera(model.frame, yaw, pitch, PPM, SHAPE, roll=roll, perspective=perspective)
    image = tmp_path / "front.png"
    Image.fromarray(photo(hard.render(truth, SHAPE)["mask"])).save(image)
    digests = {path: C.sha256_file(path) for path in (glb, image)}
    result = fit_views.fit_image(glb, image, kind)
    (tmp_path / "fit-result.json").write_text(json.dumps(result, indent=1, default=fit_views._json))
    assert result["status"] == "ok", result
    assert result["matte_mode"] == "open_frame"
    policy = result["lens_loss"]
    assert policy["mode"] == "optional" and policy["optional_nodes"] == ["Lens_L", "Lens_R"]
    assert policy["optional_faces"] == int(optional.sum())
    assert len(policy["construction"]) == 2
    assert all(row["frame_type"] == "rimless" and row["frame_type_source"] == "g3d_seat"
               and not row["provisional"] for row in policy["construction"])
    assert result["iou_lens_optional"] > .98, result
    assert result["iou"] + .2 < result["iou_lens_optional"], result
    assert result["contour_mean_px_lens_optional"] < 1., result
    assert abs(result["camera"]["yaw"] - yaw) < 2., result
    assert abs(result["camera"]["pitch"] - pitch) < 2., result
    foreground = M.matte(M.load_photo(image), mode=result["matte_mode"])
    native = cameras.native_metrics(model.scene(), C.Camera.from_dict(result["camera"]), foreground,
                                    cameras.matte_roi(foreground))
    assert result["iou"] == native["iou"] and result["contour_mean_px"] == native["contour_mean_px"]
    folder = tmp_path / "cameras/basis-01"
    folder.mkdir(parents=True)
    rec = {key: value for key, value in result.items() if key != "model"}
    (folder / "cameras.json").write_text(json.dumps({"model": result["model"], "images": {image.name: rec}},
                                                    default=fit_views._json))
    basis = C.freeze_basis(tmp_path, folder, {image.name: image}, reason="real rimless fit proof",
                           call_id=None, source="test")
    assert basis.refs["front"]["lens_loss"] == policy
    assert basis.refs["front"]["matte_mode"] == result["matte_mode"]
    np.testing.assert_array_equal(basis.matte("front"), foreground)
    for key in ("iou", "contour_mean_px", "iou_lens_optional", "lens_pixels_optional",
                "contour_mean_px_lens_optional", "contour_p95_px_lens_optional", "render_only_px_lens_optional"):
        assert basis.refs["front"]["fit"][key] == result[key]
    assert C.load_basis(tmp_path).camera("front") == C.Camera.from_dict(result["camera"])
    assert all(C.sha256_file(path) == digest for path, digest in digests.items())
