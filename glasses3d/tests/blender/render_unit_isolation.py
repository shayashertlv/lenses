"""Synthetic proof that temporary scene construction never changes any user's unit setting."""
import itertools
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import checkpoint, render


def units(scene):
    value = scene.unit_settings
    return {p.identifier: getattr(value, p.identifier) for p in value.bl_rna.properties
            if p.type in {"ENUM", "FLOAT", "INT", "BOOLEAN", "STRING"} and p.identifier != "rna_type"}


def configure(scene, system):
    with bpy.context.temp_override(scene=scene):
        unit = scene.unit_settings
        unit.system = system
        unit.scale_length = .001
        unit.length_unit = "MILLIMETERS" if system == "METRIC" else "INCHES"
        unit.mass_unit = "GRAMS" if system == "METRIC" else "OUNCES"
        unit.time_unit = "MILLISECONDS"
        unit.temperature_unit = "CELSIUS" if system == "METRIC" else "FAHRENHEIT"
        unit.system_rotation = "RADIANS"
        unit.use_separate = True


def case(system, other_active, fail):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    user = bpy.context.scene
    other = bpy.data.scenes.new("Other authored scene")
    configure(user, system)
    configure(other, "IMPERIAL" if system == "METRIC" else "METRIC")
    bpy.context.window.scene = other if other_active else user
    active = bpy.context.scene
    before = {scene.name: units(scene) for scene in (user, other)}
    fingerprint_before = checkpoint.fingerprint()
    renderer = render.TempRender(user, [], {"center": [0, 0, 0], "radius": 100}, 1)
    if fail:
        def refused():
            raise RuntimeError("injected failure after unit copying")
        renderer._rig = refused
    raised = False
    try:
        with renderer:
            assert bpy.context.scene == active
            assert renderer.scene.unit_settings.system == before[user.name]["system"]
            assert renderer.scene.unit_settings.scale_length == before[user.name]["scale_length"]
            assert renderer.scene.unit_settings.length_unit == before[user.name]["length_unit"]
    except RuntimeError as exc:
        assert fail and str(exc) == "injected failure after unit copying"
        raised = True
    after = {scene.name: units(scene) for scene in (user, other)}
    fingerprint_after = checkpoint.fingerprint()
    assert raised == fail
    assert before == after, {"before": before, "after": after}
    assert fingerprint_before == fingerprint_after
    assert bpy.context.scene == active
    assert checkpoint.RENDER_SCENE not in bpy.data.scenes
    assert renderer.made == []
    return {"system": system, "other_active": other_active, "exception": fail, "before": before, "after": after,
            "fingerprint_before": fingerprint_before, "fingerprint_after": fingerprint_after, "ok": True}


out = Path(sys.argv[sys.argv.index("--") + 1])
out.mkdir(parents=True, exist_ok=True)
cases = [case(*args) for args in itertools.product(("METRIC", "IMPERIAL"), (False, True), (False, True))]
(out / "report.json").write_text(json.dumps({"ok": True, "cases": cases}, indent=2), encoding="utf-8")
print(json.dumps({"ok": True, "cases": len(cases)}))
