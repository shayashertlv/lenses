"""Build the empty job template in Blender; no product geometry and no network.

Usage: blender -b --factory-startup --python job_seed.py -- /absolute/path/seed.blend
Collections organize future objects; exported surfaces still require an explicit
``partRole`` on the object or its mesh, as export_glb.py requires.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy


def create(path: str | Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene["blender_agent_axes"] = "+Y up; +Z front"
    scene["mdl_bridge_underside"] = [0.0, 0.0, 0.0]
    marker = bpy.data.objects.new("mdl_bridge_underside", None)
    marker.empty_display_type = "PLAIN_AXES"
    marker.empty_display_size = 5.0
    scene.collection.objects.link(marker)
    for name in ("frame", "lenses", "temples"):
        scene.collection.children.link(bpy.data.collections.new(name))
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 1:
        raise ValueError("job_seed.py needs one output .blend path")
    create(args[0])
