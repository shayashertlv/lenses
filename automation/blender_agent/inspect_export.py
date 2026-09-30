"""Read-only inspection of the exact native GLB an agent has exported."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import uuid

from agents import function_tool


def glb_summary(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("Expected a binary glTF file")
    version, length, chunk_length, chunk_type = struct.unpack_from("<4I", data, 4)
    if version != 2 or length != len(data) or chunk_type != 0x4E4F534A or chunk_length > len(data) - 20:
        raise ValueError("Invalid glTF 2 binary header")
    document = json.loads(data[20:20 + chunk_length])
    meshes = []
    accessors = document.get("accessors", [])
    for mesh in document.get("meshes", []):
        primitives = []
        for primitive in mesh.get("primitives", []):
            position = accessors[primitive["attributes"]["POSITION"]]
            indices = accessors[primitive["indices"]] if "indices" in primitive else position
            primitives.append({"material": primitive.get("material"), "mode": primitive.get("mode", 4),
                               "vertices": position["count"], "indices": indices["count"],
                               "min": position.get("min"), "max": position.get("max")})
        meshes.append({"name": mesh.get("name"), "extras": mesh.get("extras"), "primitives": primitives})
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "extensions_used": document.get("extensionsUsed", []),
            "materials": document.get("materials", []), "meshes": meshes,
            "nodes": document.get("nodes", [])}


def make_inspect_export(output: Path, *, evidence_registry=None):
    @function_tool
    def inspect_export(path: str) -> dict:
        """Inspect an exported GLB's ACTUAL material values/extensions and geometry metadata.

        Read this to check whether Blender node changes survived export. In particular,
        closed geometry and a Thickness socket do not prove KHR_materials_volume exists.
        Returns native glTF materials, extension values, role tags, bounds and counts.
        The file must be inside your assigned output directory. Does not modify Blender.
        """
        candidate = Path(path).resolve()
        if not candidate.is_relative_to(output.resolve()):
            raise ValueError("GLB must be inside the output directory")
        result = glb_summary(candidate)
        if evidence_registry is not None:
            directory = output.resolve() / "inspections"
            directory.mkdir(parents=True, exist_ok=True)
            receipt = directory / f"export-{uuid.uuid4().hex}.json"
            receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
            evidence_registry.register(receipt, "inspection")
            result["metadata_path"] = str(receipt)
        return result

    return inspect_export
