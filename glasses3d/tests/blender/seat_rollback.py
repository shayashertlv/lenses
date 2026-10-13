"""Force post-profile failures on a COPY and report exact mesh/scene restoration (tests only)."""
import json
from pathlib import Path
import sys
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.dont_write_bytecode = True
from glasses3d.g3d import export_glb as EX, seat_bpy as SB  # noqa: E402


def snapshot():
    return {
        "meshes": {m.name: {"digest": EX._mesh_digest(m), "fake_user": m.use_fake_user}
                   for m in bpy.data.meshes},
        "objects": {o.name: {"mesh": o.data.name if o.type == "MESH" else None,
                              "props": {k: o[k] for k in o.keys()},
                              "modifiers": [(m.name, m.type, getattr(getattr(m, "object", None), "name", None))
                                            for m in o.modifiers]} for o in bpy.data.objects},
    }


def profile_objects(lens):
    cutter = bpy.data.objects.get(SB.CUTTER_PREFIX + lens.name)
    cord = bpy.data.objects.get(lens.name + SB.CORD_SUFFIX)
    return {"cutter": cutter.name if cutter is not None else None,
            "modifiers": [f"{o.name}/{m.name}" for o in bpy.data.objects for m in o.modifiers
                          if m.type == "BOOLEAN" and cutter is not None and m.object == cutter],
            "cord": cord.name if cord is not None else None}


def run(mode, kind):
    lens = bpy.data.objects["Lens_L"]
    # Existing unrelated backup metadata and mesh must survive the failed seat exactly.
    SB._keep_backup(lens, "hide", {"test_existing": "preserve"})
    lens[SB.PROP_SEAT] = json.dumps({"profile": None, "test_existing": "preserve"})
    before = snapshot()
    observed = {}
    original_profile, original_verify, original_check = SB._apply_profile, SB._verify_seat, SB.check_lens_seating

    def apply(*args, **kwargs):
        report = original_profile(*args, **kwargs)
        assert report["status"] == "ok", report
        observed.update(profile_objects(lens))
        if mode == "exception":
            raise RuntimeError("forced failure after a successful profile")
        if mode == "lookup_exception":
            raise KeyError("forced lookup failure after a successful profile")
        return report

    def verify(*args, **kwargs):
        if mode == "rollback":
            return {"failures": ["forced post-profile verification failure"]}
        return original_verify(*args, **kwargs)

    def check(*args, **kwargs):
        if mode == "late_exception":
            observed["seat_property_written"] = "digest_after" in json.loads(lens[SB.PROP_SEAT])
            raise RuntimeError("forced failure after the seat property was written")
        return original_check(*args, **kwargs)

    SB._apply_profile, SB._verify_seat, SB.check_lens_seating = apply, verify, check
    try:
        kwargs = {"lens": lens.name, "frame_type": "acetate" if kind == "crystal" else "semi_rimless"}
        if kind != "crystal":
            kwargs["lower_edge"] = "nylor"
        result = SB.seat_lens(**kwargs)
    finally:
        SB._apply_profile, SB._verify_seat, SB.check_lens_seating = original_profile, original_verify, original_check
    return {"result": result, "observed": observed, "before": before, "after": snapshot(),
            "remaining": profile_objects(lens)}


def main():
    mode, kind, output = sys.argv[sys.argv.index("--") + 1:]
    try:
        result = run(mode, kind)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    Path(output).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


main()
