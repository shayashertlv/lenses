"""The `g3d` facade that Astra's snippets see (WP2_DESIGN §4): pure geometry helpers only, no file I/O inside.

    g3d.seat_lens, g3d.hide_eyewire, g3d.check_lens_seating, g3d.unseat_lens, g3d.unhide_eyewire   (WP4, seat_bpy)
    g3d.loft_sections, g3d.polygon_section, g3d.check_round_corners                              (WP6, temple_bpy)

`seat_bpy` does no I/O (inventory F4). Helpers with internal file I/O (export_glb, print_uv, attach_print,
faceted_temple with a spec path) are trusted worker methods, never facade names. The facade is a fresh module object
holding only these function objects, so nothing else of the g3d package is reachable from a snippet; the safe-mode
rules deny every `_`-prefixed attribute (a function's __globals__ included) statically and at run time.

Runs inside Blender only. `build()` never raises: a helper whose module fails to import is missing from the facade
and named in the returned errors (the worker reports them in its receipt).
"""
from __future__ import annotations

import types

SEAT_NAMES = ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire")
TEMPLE_NAMES = ("loft_sections", "polygon_section", "check_round_corners")
NAMES = SEAT_NAMES + TEMPLE_NAMES


def build() -> tuple[types.ModuleType, dict]:
    """(facade module, {group: import error}) — `g3d` and `g3d.lib` both resolve to this module in a snippet."""
    facade = types.ModuleType("g3d.lib", "glasses3d geometry helpers for scene code (no file I/O)")
    errors: dict = {}
    try:
        from . import seat_bpy
        for name in SEAT_NAMES:
            setattr(facade, name, getattr(seat_bpy, name))
    except Exception as exc:  # noqa: BLE001 - reported in the worker receipt, never fatal
        errors["seat"] = f"{type(exc).__name__}: {exc}"
    try:
        from . import temple_bpy
        for name in TEMPLE_NAMES:
            setattr(facade, name, getattr(temple_bpy, name))
    except Exception as exc:  # noqa: BLE001
        errors["temple"] = f"{type(exc).__name__}: {exc}"
    facade.lib = facade
    facade.__all__ = [n for n in NAMES if hasattr(facade, n)]
    return facade, errors
