"""Blender-side glasses3d package. Runs inside Blender 5.2's Python (numpy available; no cv2/scipy/PIL).
Never import host-only modules (glasses3d.g3d_host, glasses3d.agent) from here.

The WP4 seating tools are exposed lazily, so the host can import `glasses3d.g3d.seat` (numpy only) without bpy:

    g3d.seat_lens, g3d.hide_eyewire, g3d.check_lens_seating, g3d.unseat_lens, g3d.unhide_eyewire

On the host (no bpy) the five names are not attributes: `hasattr(g3d, "seat_lens")` is False and getattr raises
AttributeError (its __cause__ is the ImportError), never ModuleNotFoundError. `SEAT_TOOLS` lists them without bpy.
"""
SEAT_TOOLS = ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire")
_SEAT_TOOLS = SEAT_TOOLS
# the tools stay reachable as attributes inside Blender (__getattr__), but a star import must work on the host too,
# so __all__ names only what exists everywhere (review fix round 2: `from glasses3d.g3d import *` raised there)
__all__ = ["SEAT_TOOLS"]


def __getattr__(name):
    if name in SEAT_TOOLS:
        try:
            from . import seat_bpy
        except ImportError as exc:                     # the host: no bpy / mathutils
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} outside Blender "
                                 f"({type(exc).__name__}: {exc})") from exc
        return getattr(seat_bpy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
