"""match_coating_colours: Astra's read-only coating search (audit B3), the same function as the owner's Edit page."""
from __future__ import annotations

import math

from agents.tool_context import ToolContext

from .. import coating_match


def _srgb(value, name):
    if (not isinstance(value, list) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in value)):
        raise ValueError(f"{name} must be three sRGB integers 0-255")
    return value


def _unit(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a number from 0 to 1")
    return float(value)


def tool_for(scene, tool, call):
    @tool("match_coating_colours")
    async def match_coating_colours(ctx: ToolContext, straight_on_srgb: list[int], sides_srgb: list[int],
                                    lens_ior: float, metallic: float = 0.0, tint: list[float] | None = None,
                                    strength: float = 1.0):
        """Find real thin-film coatings that reflect one colour straight on and another at the lens sides, before
        trying any. Searches every thickness 0-1200 nm and film IOR 1.2-2.4 with the AR renderer's own coating
        function; the owner's Edit page runs the same search. lens_ior (the lens body's IOR: higher reflects more
        strongly), metallic, linear tint and strength stay as given. Returns up to three distinct choices, each with
        the colours it reflects straight on (cos 1) and at the sides (cos 0.4) and the remaining colour error.
        These are the coating's own reflected colours, not a lit render or a likeness check. Try choices side by
        side with compare_lens_candidates; apply one with g3d.lens_coating(material, factor=strength, ior=film_ior,
        thickness_min_nm=thickness_nm, thickness_max_nm=thickness_nm, tint=tint), keeping the lens body IOR at
        lens_ior; then judge the worn view with compare_look_to_photos. Read-only: the scene does not change.

        Args:
            straight_on_srgb: Target coating colour seen straight on, sRGB 0-255, for example sampled from a photo.
            sides_srgb: Target coating colour at the lens sides, sRGB 0-255; repeat straight_on_srgb for one colour.
            lens_ior: The lens body's IOR, 1 or more.
            metallic: The lens material's metallic value, 0-1.
            tint: The lens's linear RGB base colour, each 0-1; null means white.
            strength: Coating factor (glTF iridescenceFactor), 0-1.
        """
        def action(cid):
            straight, sides = _srgb(straight_on_srgb, "straight_on_srgb"), _srgb(sides_srgb, "sides_srgb")
            if (isinstance(lens_ior, bool) or not isinstance(lens_ior, (int, float)) or not math.isfinite(lens_ior)
                    or lens_ior < 1):
                raise ValueError("lens_ior must be a finite number of 1 or more")
            colour = [1.0, 1.0, 1.0] if tint is None else tint
            if not isinstance(colour, list) or len(colour) != 3:
                raise ValueError("tint must be three linear RGB values 0-1")
            values = {"ior": float(lens_ior), "metallic": _unit(metallic, "metallic"),
                      "tint": [_unit(v, "tint") for v in colour], "iridescence": _unit(strength, "strength"),
                      "iridescence_ior": 1.3, "iridescence_thickness_max": 400.0}
            found = coating_match.find_coating(straight, sides, values)
            return {"status": "ok", "schema": "g3d.coating_match/1", "targets": {"straight_on_srgb": straight,
                    "sides_srgb": sides}, "kept": {key: values[key] for key in ("ior", "metallic", "tint", "iridescence")},
                    "choices": found["choices"], "reason": found["reason"],
                    "note": "Coating colours at two angles, not a lit render; compare a chosen coating before keeping it."}
        return await call(ctx, "match_coating_colours", action)

    return match_coating_colours
