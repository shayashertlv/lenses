"""One translucency decision on exported glTF material semantics, shared by both seating readers."""
from __future__ import annotations


def translucent(material: dict | None) -> bool:
    """Bulk transmission/volume or blended alpha; MASK/OPAQUE alpha is not translucent material.

    Texture content is not sampled: an effective transmission factor or a blended base-colour texture is
    conservatively eligible. A transmission texture alone has the glTF default factor zero and has no effect.
    """
    if not isinstance(material, dict):
        return False
    ext = material.get("extensions") or {}
    pbr = material.get("pbrMetallicRoughness") or {}
    try:
        if float((ext.get("KHR_materials_transmission") or {}).get("transmissionFactor", 0)) > .05:
            return True
        alpha = float((pbr.get("baseColorFactor") or [1, 1, 1, 1])[3])
    except (TypeError, ValueError, IndexError):
        return False
    return ("KHR_materials_volume" in ext or
            (material.get("alphaMode", "OPAQUE") == "BLEND" and
             (alpha < .98 or pbr.get("baseColorTexture") is not None)))
