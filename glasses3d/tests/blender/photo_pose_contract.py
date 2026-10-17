"""Synthetic no-render Blender proof of the same finite-pose contract as host/AR."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np

from glasses3d.g3d import checkpoint, render


def main(fixture_path, receipt_path):
    fixture = json.loads(Path(fixture_path).read_text())
    original = copy.deepcopy(fixture)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene, objects = bpy.context.scene, []
    faces = [(0,1,3,2), (4,6,7,5), (0,4,5,1), (2,3,7,6), (0,2,6,4), (1,5,7,3)]
    for name, row in fixture["parts"].items():
        mesh = bpy.data.meshes.new(name + "-mesh")
        mesh.from_pydata(row["vertices_mm"], [], [tuple(reversed(face)) for face in faces] if row["side"] < 0 else faces)
        obj = bpy.data.objects.new(name, mesh)
        obj["partRole"] = row["role"]
        scene.collection.objects.link(obj)
        objects.append(obj)
    bpy.context.view_layer.update()
    fingerprint = checkpoint.fingerprint()
    maximum = 0.
    for case in fixture["cases"]:
        parsed = render.parse_pose(case["pose"])
        for side, (theta, axis) in parsed.items():
            assert theta == case["pose"][f"{side:+d}"]["theta_deg"]
        with render.TempRender(scene, objects, {"center": [0., 0., -60.], "radius": 100.}, 1) as temp:
            # Repeated configure must start from the authored matrix, never accumulate a rotation.
            for _ in range(2):
                temp.configure(parsed, {}, "native")
                for obj in objects:
                    rendered = temp.copies[obj.name]
                    assert rendered is not obj and rendered.data is obj.data
                    matrix = np.array(rendered.matrix_world)
                    vertices = np.array([tuple(v.co) for v in rendered.data.vertices])
                    actual = vertices @ matrix[:3, :3].T + matrix[:3, 3]
                    maximum = max(maximum, float(np.max(np.abs(actual-np.array(case["expected_mm"][obj.name])))))
                    assert maximum < 2e-5
                    # Normals are transformed by the same rigid matrix; no mesh-normal data is rewritten.
                    np.testing.assert_allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=2e-7, rtol=0)
            temp.configure({}, {}, "native")
            for obj in objects:
                assert temp.copies[obj.name].matrix_world == obj.matrix_world
        assert checkpoint.fingerprint() == fingerprint
    bad = [[], {"+2": {"theta_deg": 0., "axis_xz_mm": [64., -9.]}},
           {"+1": {"theta_deg": float("nan"), "axis_xz_mm": [64., -9.]}},
           {"+1": {"theta_deg": float("inf"), "axis_xz_mm": [64., -9.]}},
           {"+1": {"theta_deg": 0., "axis_xz_mm": [64., float("nan")]}},
           {"+1": {"theta_deg": 0., "axis_xz_mm": [-64., -9.]}},
           {"+1": {"theta_deg": 0., "axis_xz_mm": [64.]}},
           {"+1": {"theta_deg": 0.}},
           {"+1": {"theta_deg": 0., "axis_xz_mm": [64., -9.]},
            "1": {"theta_deg": 0., "axis_xz_mm": [64., -9.]}}]
    for value in bad:
        try:
            render.parse_pose(value)
        except render.RenderRefused:
            pass
        else:
            raise AssertionError(f"malformed pose accepted: {value}")
    assert fixture == original
    Path(receipt_path).write_text(json.dumps({"status": "ok", "cases": len(fixture["cases"]),
        "max_component_error_mm": maximum, "retained_refusals": len(bad),
        "unchanged": checkpoint.fingerprint() == fingerprint, "raw_poses_unchanged": fixture == original,
        "temporary_scene_removed": checkpoint.RENDER_SCENE not in bpy.data.scenes}), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[sys.argv.index("--") + 1:])
