"""Blender-side glasses3d package. Runs inside Blender 5.2's Python (numpy available; no cv2/scipy/PIL).
Never import host-only modules (glasses3d.g3d_host, glasses3d.agent) from here.

The WP4 seating tools and the WP5 print tools are exposed lazily, so the host can import `glasses3d.g3d.seat` and
`glasses3d.g3d.printuv` (numpy only) without bpy:

    g3d.seat_lens, g3d.hide_eyewire, g3d.check_lens_seating, g3d.unseat_lens, g3d.unhide_eyewire     (seat_bpy)
    g3d.print_uv, g3d.attach_print, g3d.detach_print, g3d.remove_print_uv                           (print_bpy)

On the host (no bpy) these names are not attributes: `hasattr(g3d, "seat_lens")` is False and getattr raises
AttributeError (its __cause__ is the ImportError), never ModuleNotFoundError. `SEAT_TOOLS` and `PRINT_TOOLS` list them
without bpy.
"""
SEAT_TOOLS = ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire")
PRINT_TOOLS = ("print_uv", "attach_print", "detach_print", "remove_print_uv")
# the tools stay reachable as attributes inside Blender (__getattr__), but a star import must work on the host too,
# so __all__ names only what exists everywhere (review fix round 2: `from glasses3d.g3d import *` raised there)
__all__ = ["SEAT_TOOLS", "PRINT_TOOLS"]


def __getattr__(name):
    if name in SEAT_TOOLS or name in PRINT_TOOLS:
        try:
            if name in SEAT_TOOLS:
                from . import seat_bpy as mod
            else:
                from . import print_bpy as mod
        except ImportError as exc:                     # the host: no bpy / mathutils / bmesh
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} outside Blender "
                                 f"({type(exc).__name__}: {exc})") from exc
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
