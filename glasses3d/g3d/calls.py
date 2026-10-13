"""JSON-only contracts for trusted worker operations, outside the snippet facade.

The allowlist is deliberately explicit. Values identify scene objects by name; a caller cannot supply a bpy
object, a scene, executable text or a function name outside the allowlist. Geometry rules remain in WP4/WP5.
This module imports without Blender so the same boundary can be tested on the host.
"""
from __future__ import annotations

import math

SEAT_OPS = frozenset({"seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire"})
PRINT_OPS = frozenset({"print_uv", "attach_print", "detach_print"})
OPS = SEAT_OPS | PRINT_OPS
READ_OPS = frozenset({"check_lens_seating"})
FRAME_TYPES = frozenset({"acetate", "metal", "browline", "semi_rimless", "rimless"})
SUCCESS = {
    "seat_lens": frozenset({"seated", "already_seated", "partial"}),
    "hide_eyewire": frozenset({"lifted", "already_hidden"}),
    "unseat_lens": frozenset({"restored", "nothing_to_restore"}),
    "unhide_eyewire": frozenset({"restored", "nothing_to_restore"}),
    **{op: frozenset({"ok"}) for op in sorted(PRINT_OPS)},
}


def _name(value):
    return type(value) is str and bool(value.strip()) and "\0" not in value


def _names(value):
    return _name(value) or type(value) is list and all(_name(x) for x in value)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _ranges(value):
    return type(value) is list and all(type(pair) is list and len(pair) == 2 and all(_number(x) for x in pair)
                                      for pair in value)


def _nullable(check):
    return lambda value: value is None or check(value)


def _frame_type(value):
    return type(value) is str and value in FRAME_TYPES


def _lower_edge(value):
    return type(value) is str and value in ("plain", "nylor")


def _boolean(value):
    return type(value) is bool


COMMON = {"frame_parts": _nullable(_names), "frame_type": _nullable(_frame_type),
          "lower_edge": _nullable(_lower_edge)}
ARGUMENTS = {
    "seat_lens": ({"lens"}, {**COMMON, "lens": _name, "capture_mm": _nullable(_number),
                            "translucent": _nullable(_boolean), "uv": lambda v: type(v) is str and v == "keep",
                            "dry_run": _boolean}),
    "hide_eyewire": ({"eyewire", "under", "hidden_x_range_mm"}, {
        "eyewire": _name, "under": _names, "hidden_x_range_mm": _ranges, "lens": _nullable(_name),
        "margin_mm": _number, "ramp_mm": _number, "lift_max_mm": _number, "dry_run": _boolean}),
    "check_lens_seating": (set(), {**COMMON, "lenses": _nullable(_names),
                                   "hidden_x_range_mm": _nullable(_ranges), "stations_table": _boolean}),
    "unseat_lens": ({"lens"}, {"lens": _name}),
    "unhide_eyewire": ({"eyewire"}, {"eyewire": _name, "lens": _nullable(_name)}),
    "print_uv": ({"parts"}, {"parts": _names, "density": lambda v: v == "auto" if type(v) is str else
                            _number(v) and v > 0, "layer": lambda v: v == "g3d_print",
                            "dry_run": _boolean, "replace": _boolean, "bake": _nullable(_name)}),
    "attach_print": ({"report"}, {"report": _name, "parts": _nullable(_names),
                                  "gain": lambda v: type(v) is list and len(v) == 3 and
                                  all(_number(x) and x > 0 for x in v),
                                  "transmission_map": lambda v: v is False}),
    "detach_print": ({"parts"}, {"parts": _names}),
}


def validate(params: dict) -> tuple[str, dict]:
    """Validate the entire RPC payload before running any operation. Raises ValueError on refusal."""
    if type(params) is not dict or any(type(k) is not str for k in params):
        raise ValueError("g3d_call parameters must be a JSON object")
    extra = set(params) - {"op", "kwargs", "expect_fingerprint"}
    if extra:
        raise ValueError(f"unknown g3d_call arguments: {sorted(extra)}")
    op = params.get("op")
    if type(op) is not str or op not in OPS:
        raise ValueError(f"op must be one of {sorted(OPS)}")
    if "expect_fingerprint" in params and not _name(params["expect_fingerprint"]):
        raise ValueError("expect_fingerprint must be a nonempty string")
    kwargs = params.get("kwargs")
    if type(kwargs) is not dict or any(type(k) is not str for k in kwargs):
        raise ValueError("kwargs must be a JSON object")
    required, allowed = ARGUMENTS[op]
    missing, extra = required - set(kwargs), set(kwargs) - set(allowed)
    if missing or extra:
        raise ValueError(f"{op} arguments: missing={sorted(missing)}, unknown={sorted(extra)}")
    for key, value in kwargs.items():
        if not allowed[key](value):
            raise ValueError(f"invalid type or value for {op}.{key}")
    return op, dict(kwargs)


def invoke(op: str, kwargs: dict) -> dict:
    """Use fixed function references, never getattr or a caller-controlled import path."""
    from . import seat_bpy
    from . import print_bpy
    functions = {"seat_lens": seat_bpy.seat_lens, "hide_eyewire": seat_bpy.hide_eyewire,
                 "check_lens_seating": seat_bpy.check_lens_seating, "unseat_lens": seat_bpy.unseat_lens,
                 "unhide_eyewire": seat_bpy.unhide_eyewire, "print_uv": print_bpy.print_uv,
                 "attach_print": print_bpy.attach_print, "detach_print": print_bpy.detach_print}
    return functions[op](**kwargs)


def envelope(op: str, result: dict) -> dict:
    """Keep the complete domain report and distinguish an applied partial result from a refused mutation.

    A completed check with ok=False is a valid read, never a successful quality verdict. A partial seating result
    is saved by the journal so Astra can inspect and correct it; its original partial status stays in result.
    """
    if type(result) is not dict:
        return {"status": "error", "op": op, "code": "E_RESULT", "message": "trusted operation returned no report",
                "result": result}
    if op in READ_OPS or result.get("status") in SUCCESS[op]:
        return {"status": "ok", "op": op, "result": result}
    return {"status": "refused" if result.get("status") == "refused" else "error", "op": op,
            "code": result.get("code") or "E_RESULT", "message": result.get("message") or
            f"unexpected {op} result status {result.get('status')!r}", "result": result}
