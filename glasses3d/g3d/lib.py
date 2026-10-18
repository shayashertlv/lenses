"""The `g3d` facade that Astra's snippets see (WP2_DESIGN §4): in-memory scene helpers, no file I/O inside.

    g3d.loft_sections, g3d.polygon_section, g3d.check_round_corners                              (WP6, temple_bpy)
    g3d.lens_coating                                                                         (coating)
    g3d.acetate_section, g3d.sweep_rim                                                        (rim_bpy)
    g3d.quadratic_lens                                                                      (lens_surface_bpy)
    g3d.mesh_cleanup, g3d.consistent_normals, g3d.symmetrize_mesh, g3d.controlled_decimate      (mesh_health_bpy)

Seating runs through the trusted g3d_call worker protocol. Helpers with internal file I/O (export_glb, print_uv,
attach_print, faceted_temple with a spec path) are never facade names. The facade is a fresh module object
holding only these function objects, so nothing else of the g3d package is reachable from a snippet; the safe-mode
rules deny every `_`-prefixed attribute (a function's __globals__ included) statically and at run time.

Runs inside Blender only. `build()` never raises: a helper whose module fails to import is missing from the facade
and named in the returned errors (the worker reports them in its receipt).
"""
from __future__ import annotations

import types

TEMPLE_NAMES = ("loft_sections", "polygon_section", "check_round_corners")
COATING_NAMES = ("lens_coating",)
RIM_NAMES = ("acetate_section", "sweep_rim")
SURFACE_NAMES = ("quadratic_lens",)
HEALTH_NAMES = ("mesh_cleanup", "consistent_normals", "symmetrize_mesh", "controlled_decimate")
NAMES = TEMPLE_NAMES + COATING_NAMES + RIM_NAMES + SURFACE_NAMES + HEALTH_NAMES


def build() -> tuple[types.ModuleType, dict]:
    """(facade module, {group: import error}) — `g3d` and `g3d.lib` both resolve to this module in a snippet."""
    facade = types.ModuleType("g3d.lib", "glasses3d in-memory scene helpers (no file I/O)")
    errors: dict = {}
    try:
        from . import temple_bpy
        for name in TEMPLE_NAMES:
            setattr(facade, name, getattr(temple_bpy, name))
    except Exception as exc:  # noqa: BLE001
        errors["temple"] = f"{type(exc).__name__}: {exc}"
    try:
        from . import coating
        for name in COATING_NAMES:
            setattr(facade, name, getattr(coating, name))
    except Exception as exc:  # noqa: BLE001
        errors["coating"] = f"{type(exc).__name__}: {exc}"
    try:
        from . import rim_bpy
        for name in RIM_NAMES:
            setattr(facade, name, getattr(rim_bpy, name))
    except Exception as exc:  # noqa: BLE001
        errors["rim"] = f"{type(exc).__name__}: {exc}"
    try:
        from . import lens_surface_bpy
        for name in SURFACE_NAMES:
            setattr(facade, name, getattr(lens_surface_bpy, name))
    except Exception as exc:
        errors["surface"] = f"{type(exc).__name__}: {exc}"
    try:
        # Health planners import these lazily to avoid their internal cycles.
        # Resolve them while building the trusted facade, before the worker arms
        # its audit guard; importing bytecode under that guard is rightly denied.
        from . import owner_normal_plan, owner_thickness, owner_edge_radius, owner_intersections
        from . import mesh_health_bpy
        for name in HEALTH_NAMES:
            setattr(facade, name, getattr(mesh_health_bpy, name))
    except Exception as exc:
        errors["mesh_health"] = f"{type(exc).__name__}: {exc}"
    facade.lib = facade
    facade.__all__ = [n for n in NAMES if hasattr(facade, n)]
    return facade, errors
