"""Owner exact angle colours: the colour a lens reflects by viewing angle, as drawn on the edit page.

A host effect, mirrored exactly by ``arview/src/eyewear/angle-colour.ts`` (the AR renderer, edit preview and production
capture): the material's ``extras.g3dAngleColour`` replaces only three's thin-film colour term; the GLB keeps its
standard ``KHR_materials_iridescence`` (strength, and a fallback coating for viewers without this effect). Positions run
0 (seen at an angle, the lens sides; cos <= 0.4) to 1 (seen straight on). Each transition is centred on the midpoint
between its two stops with width ``softness * gap``. Colours are linear-RGB reflectance, as the coating bar shows them.
``reflection`` replaces the coating's reflected colour (needs the coating on); ``tint`` (0-1) also tints what is seen
through the lens by angle with the same hues at the lens's own brightness.
"""
from __future__ import annotations

from . import owner_lens_edit as lens

KEY = "g3dAngleColour"
SCHEMA = "g3d.angle_colour/1"


def validate_angle_colour(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"schema", "stops", "softness", "reflection", "tint"}
            or value["schema"] != SCHEMA):
        raise ValueError("Exact angle colours need their colours, positions, softness, reflection and tint settings.")
    stops = value["stops"]
    if not isinstance(stops, list) or not 2 <= len(stops) <= 3:
        raise ValueError("Exact angle colours need two or three colours.")
    clean = []
    for row in stops:
        if not isinstance(row, dict) or set(row) != {"colour", "position"}:
            raise ValueError("Each angle colour needs a colour and a position on the bar.")
        rgb = row["colour"]
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError("Each angle colour needs three linear red, green and blue values.")
        clean.append({"colour": [lens._number(x, 0, 1, "Angle colour") for x in rgb],
                      "position": lens._number(row["position"], 0, 1, "Angle colour position")})
    if any(b["position"] - a["position"] < 1e-3 for a, b in zip(clean, clean[1:])):
        raise ValueError("Angle colours must be in order along the bar, with a little space between them.")
    softness = value["softness"]
    if not isinstance(softness, list) or len(softness) != len(clean) - 1:
        raise ValueError("Each transition between two angle colours needs its own softness.")
    if type(value["reflection"]) is not bool:
        raise ValueError("Choose whether the colours show in the reflections.")
    tint = lens._number(value["tint"], 0, 1, "Lens colour tint")
    if not value["reflection"] and tint == 0:
        raise ValueError("Show the angle colours in the reflections, the lens colour, or both.")
    return {"schema": SCHEMA, "stops": clean, "softness": [lens._number(x, 0, 1, "Softness") for x in softness],
            "reflection": value["reflection"], "tint": tint}


def read(material):
    """The saved spec, or None. A malformed saved spec is ignored, exactly as the renderer ignores it."""
    extras = material.get("extras")
    if not isinstance(extras, dict) or KEY not in extras:
        return None
    try:
        return validate_angle_colour(extras[KEY])
    except (ValueError, TypeError, KeyError):
        return None
