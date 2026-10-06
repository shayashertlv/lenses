"""Set WP4 seating properties on a scene's objects, then export it with the WP3 exporter (tests only).

    blender -b --factory-startup <scene>.blend --python tests/blender/seat_extras_export.py -- <out.glb> <receipt.json>

Lens_L gets g3d_seat and g3d_hidden_x_ranges_mm as JSON strings (what the tools write) plus two properties that must
NOT reach the GLB (g3d_backups, an unrelated 'note'); Eyewire gets g3d_hidden_x_ranges_mm as a native ID-property
list (the allowlist turns it into a JSON string)."""
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.dont_write_bytecode = True
from glasses3d.g3d.export_glb import export_glb, snapshot      # noqa: E402

out, receipt_path = sys.argv[sys.argv.index("--") + 1:][:2]
lens = bpy.data.objects["Lens_L"]
lens["g3d_seat"] = json.dumps({"frame_type": "metal", "sharpened": False, "tool": "test"})
lens["g3d_hidden_x_ranges_mm"] = json.dumps([[-40.0, -20.0]])
lens["g3d_backups"] = json.dumps({"seat": "Lens_L | g3d pre-seat"})
lens["note"] = "must not be exported"
bpy.data.objects["Eyewire"]["g3d_hidden_x_ranges_mm"] = [[-40.0, -20.0]]
before = snapshot(bpy.context.scene)
try:
    receipt = export_glb(out)
except Exception as exc:
    receipt = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
receipt["snapshot_equal_after"] = snapshot(bpy.context.scene) == before
Path(receipt_path).write_text(json.dumps(receipt, indent=1, default=str), encoding="utf-8")
