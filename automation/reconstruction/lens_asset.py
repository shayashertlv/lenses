"""Experimental GLB fixture transport for LensAppearance v1 conformance.

LENSES_lens_appearance is an UNREGISTERED project material extension, not a
Khronos standard. Its envelope is {schema_version:1, texcoord:0, appearance:...}.
The appearance payload is the exact canonical LensAppearance v1 descriptor;
TEXCOORD_0.v is lens-local bottom=0/top=1. No image texture transform applies.
Nodes carry extras.partRole='lens'. The extension is used, never required, so
generic glTF viewers can load the deliberately approximate standard material.
Loading that fallback does not establish lens appearance fidelity.

The rectangular curved sheet is a test surface in meters with +Z front normals,
not reconstructed eyewear. Roughness is recorded, not simulated by the CPU
oracle. The dedicated reader verifies THIS narrow fixture contract, not general
glTF conformance. No live AR files or provider services are involved.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

import numpy as np

from .lens_appearance import (
    COLOR_SPACE, VERTICAL_COORDINATE, DensityKeyframe, LensAppearance,
    ReflectanceKeyframe, optical_density_from_linear_transmission,
)

EXTENSION = "LENSES_lens_appearance"
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _descriptor_hash(appearance: LensAppearance) -> str:
    return hashlib.sha256(_json_bytes(appearance.to_dict())).hexdigest()


def capability_report() -> dict[str, Any]:
    return {
        "schema_version": 1, "extension": EXTENSION, "extension_registration": "unregistered_project_extension",
        "canonical_descriptor_transport": "implemented", "production_renderer_integration": "unmeasured",
        "generic_gltf_fallback": "approximate_only", "fallback_establishes_fidelity": False,
        "fallback_limitations": ["flat midpoint tint", "no optical-density gradient", "no angular coating table",
                                 "metallic/alpha approximation is not canonical R/T/A"],
        "roughness": "recorded_only_in_reference_math",
        "physical_parameter_reconstruction": "unknown", "fixture_geometry": "procedural_test_sheet_not_eyewear",
    }


def _pack_glb(document: dict[str, Any], binary: bytes) -> bytes:
    encoded = _json_bytes(document)
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, length) + struct.pack("<II", len(encoded), JSON_CHUNK) + encoded
            + struct.pack("<II", len(binary), BIN_CHUNK) + binary)


def build_lens_fixture_glb(appearance: LensAppearance, *, columns: int = 32, rows: int = 20) -> bytes:
    """Build one curved rectangular sheet, with analytic normals and native UVs."""
    if not isinstance(appearance, LensAppearance):
        raise ValueError("appearance must be a LensAppearance")
    if any(type(value) is not int or not 2 <= value <= 256 for value in (columns, rows)):
        raise ValueError("Fixture rows and columns must be integers between 2 and 256")
    u, v = np.meshgrid(np.linspace(0, 1, columns + 1), np.linspace(0, 1, rows + 1))
    x, y = (u - 0.5) * 0.060, (v - 0.5) * 0.045
    sag = 0.006
    z = sag * (1 - (x / 0.030) ** 2 - (y / 0.0225) ** 2)
    positions = np.column_stack((x.ravel(), y.ravel(), z.ravel())).astype("<f4")
    normals = np.column_stack((2 * sag * x.ravel() / 0.030**2,
                               2 * sag * y.ravel() / 0.0225**2, np.ones(x.size)))
    normals = (normals / np.linalg.norm(normals, axis=1, keepdims=True)).astype("<f4")
    uv = np.column_stack((u.ravel(), v.ravel())).astype("<f4")
    triangles = []
    for row in range(rows):
        for col in range(columns):
            a = row * (columns + 1) + col
            b, c, d = a + 1, a + columns + 1, a + columns + 2
            triangles.extend(((a, b, c), (b, d, c)))
    indices = np.asarray(triangles, dtype="<u4").ravel()
    arrays = (positions, normals, uv, indices)
    views, accessors, binary = [], [], bytearray()
    for index, (array, kind) in enumerate(zip(arrays, ("VEC3", "VEC3", "VEC2", "SCALAR"))):
        block = array.tobytes()
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(block),
                      "target": 34963 if index == 3 else 34962})
        accessor = {"bufferView": index, "componentType": 5125 if index == 3 else 5126,
                    "count": len(array), "type": kind}
        if index == 0:
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        accessors.append(accessor)
        binary.extend(block)
    sample = appearance.evaluate(0.5, 0)
    fallback_color = sample.reflectance_rgb + sample.transmission_rgb
    material = {
        "name": "Approximate fallback; canonical appearance in project extension", "doubleSided": True,
        "alphaMode": "BLEND",
        "pbrMetallicRoughness": {"baseColorFactor": [*fallback_color.tolist(), float(1 - sample.transmission_rgb.mean())],
                                 "metallicFactor": float(np.mean(appearance.normal_reflectance_rgb)),
                                 "roughnessFactor": appearance.roughness},
        "extensions": {EXTENSION: {"schema_version": 1, "texcoord": 0, "appearance": appearance.to_dict()}},
        "extras": {"fallbackIsApproximate": True, "fallbackEstablishesFidelity": False},
    }
    document = {
        "asset": {"version": "2.0", "generator": "Lenses experimental lens conformance v1"},
        "extensionsUsed": [EXTENSION], "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Lens conformance sheet", "mesh": 0,
                   "extras": {"partRole": "lens", "lensUVConvention": VERTICAL_COORDINATE,
                              "lensAppearanceSha256": _descriptor_hash(appearance)}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                    "indices": 3, "material": 0, "mode": 4}]}],
        "materials": [material], "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views, "accessors": accessors,
        "extras": {"lensConformanceFixtureVersion": 1, "capabilities": capability_report()},
    }
    return _pack_glb(document, bytes(binary))


@dataclass(frozen=True)
class LensAsset:
    appearance: LensAppearance
    positions: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    indices: np.ndarray
    document: dict[str, Any]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_lens_fixture_glb(source: bytes | str | Path) -> LensAsset:
    """Reject missing/modified descriptors, lost UV semantics and broken buffers."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if len(data) < 28:
        raise ValueError("Truncated GLB")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError("Invalid GLB header")
    offset, chunks = 12, []
    while offset < length:
        if offset + 8 > length:
            raise ValueError("Truncated chunk header")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > length:
            raise ValueError("Invalid chunk extent")
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    if [kind for kind, _ in chunks] != [JSON_CHUNK, BIN_CHUNK]:
        raise ValueError("Fixture requires exactly JSON then BIN chunks")
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Nonfinite JSON constant: {value}")
    document = json.loads(chunks[0][1], object_pairs_hook=_unique_object, parse_constant=invalid_constant)
    binary = chunks[1][1]
    try:
        used, required = document["extensionsUsed"], document.get("extensionsRequired", [])
        if (document["asset"]["version"] != "2.0" or not isinstance(used, list) or
                not all(isinstance(name, str) for name in used) or used.count(EXTENSION) != 1):
            raise ValueError("Missing fixture extension declaration")
        if not isinstance(required, list) or EXTENSION in required:
            raise ValueError("Fixture must permit approximate generic fallback")
        if (type(document["scene"]) is not int or document["scene"] != 0 or document["scenes"] != [{"nodes": [0]}] or
                any(len(document[name]) != 1 for name in ("nodes", "meshes", "materials", "buffers"))):
            raise ValueError("Expected one lens node, mesh, material and embedded buffer")
        node, material = document["nodes"][0], document["materials"][0]
        if node["mesh"] != 0 or any(name in node for name in ("matrix", "translation", "rotation", "scale")):
            raise ValueError("Fixture must preserve native lens-local coordinates")
        if node["extras"]["partRole"] != "lens" or node["extras"]["lensUVConvention"] != VERTICAL_COORDINATE:
            raise ValueError("Missing lens role or local UV convention")
        extension = material["extensions"][EXTENSION]
        if (set(extension) != {"schema_version", "texcoord", "appearance"} or
                type(extension["schema_version"]) is not int or extension["schema_version"] != 1 or
                type(extension["texcoord"]) is not int or extension["texcoord"] != 0):
            raise ValueError("Unsupported lens extension envelope")
        appearance = LensAppearance.from_dict(extension["appearance"])
        if node["extras"]["lensAppearanceSha256"] != _descriptor_hash(appearance):
            raise ValueError("Canonical descriptor digest mismatch")
        if material["extras"]["fallbackIsApproximate"] is not True or material["extras"]["fallbackEstablishesFidelity"] is not False:
            raise ValueError("Fallback capability claims missing or unsupported")
        if (type(document["extras"]["lensConformanceFixtureVersion"]) is not int or
                document["extras"]["lensConformanceFixtureVersion"] != 1):
            raise ValueError("Unknown fixture version")
        buffer = document["buffers"][0]
        if "uri" in buffer or not 0 <= len(binary) - buffer["byteLength"] <= 3:
            raise ValueError("Fixture requires a valid embedded buffer")
        primitives = document["meshes"][0]["primitives"]
        if len(primitives) != 1:
            raise ValueError("Expected one lens primitive")
        primitive = primitives[0]
        if primitive["material"] != 0 or primitive.get("mode", 4) != 4:
            raise ValueError("Expected triangle lens material")
        arrays = []
        for semantic, kind, components, dtype, component_type in (
            ("POSITION", "VEC3", 3, "<f4", 5126), ("NORMAL", "VEC3", 3, "<f4", 5126),
            ("TEXCOORD_0", "VEC2", 2, "<f4", 5126), ("indices", "SCALAR", 1, "<u4", 5125),
        ):
            accessor_index = primitive["indices"] if semantic == "indices" else primitive["attributes"][semantic]
            if type(accessor_index) is not int or accessor_index < 0:
                raise ValueError("Invalid accessor index")
            accessor = document["accessors"][accessor_index]
            if accessor["type"] != kind or accessor["componentType"] != component_type or accessor.get("normalized", False) or "sparse" in accessor:
                raise ValueError("Unsupported fixture accessor encoding")
            count = accessor["count"]
            if type(count) is not int or count <= 0:
                raise ValueError("Invalid accessor count")
            view_index = accessor["bufferView"]
            if type(view_index) is not int or view_index < 0:
                raise ValueError("Invalid buffer view index")
            view = document["bufferViews"][view_index]
            start, relative, extent = view.get("byteOffset", 0), accessor.get("byteOffset", 0), view["byteLength"]
            if any(type(v) is not int or v < 0 for v in (start, relative, extent)):
                raise ValueError("Invalid buffer byte extent")
            needed = count * components * 4
            if (view["buffer"] != 0 or "byteStride" in view or start % 4 or relative % 4 or
                    relative + needed > extent or start + extent > buffer["byteLength"]):
                raise ValueError("Accessor exceeds or misaligns embedded buffer")
            arrays.append(np.frombuffer(binary, dtype=dtype, count=count * components, offset=start + relative).copy().reshape(count, components))
        positions, normals, uv, indices = arrays
        if len(indices) % 3 or len(normals) != len(positions) or len(uv) != len(positions):
            raise ValueError("Attribute or triangle count mismatch")
        indices = indices.reshape(-1, 3)
        if not all(np.isfinite(array).all() for array in (positions, normals, uv)) or indices.max() >= len(positions):
            raise ValueError("Nonfinite geometry or invalid triangle index")
        if not np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-5) or np.any(normals[:, 2] <= 0):
            raise ValueError("Fixture requires unit front-facing normals")
        xy_extent = np.ptp(positions[:, :2], axis=0)
        if np.any(xy_extent <= 0) or not np.allclose(uv, (positions[:, :2] - positions[:, :2].min(axis=0)) / xy_extent, atol=1e-6):
            raise ValueError("UVs no longer preserve local bottom-to-top convention")
        faces = positions[indices]
        crosses = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
        if np.any(np.sum(crosses * normals[indices].mean(axis=1), axis=1) <= 0):
            raise ValueError("Degenerate or incorrectly wound lens triangles")
        return LensAsset(appearance, positions, normals, uv, indices, document)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError(f"Incomplete or malformed fixture: {error}") from error


def conformance_cases() -> dict[str, LensAppearance]:
    """Hand-specified synthetic materials; these are not photo-inferred lenses."""
    def density(v: float, transmission: tuple) -> DensityKeyframe:
        return DensityKeyframe(v, optical_density_from_linear_transmission(transmission))
    def uniform(transmission: tuple, reflection: tuple = (0.04, 0.04, 0.04)) -> LensAppearance:
        return LensAppearance((density(0, transmission),), reflection, roughness=0)
    gradient = (density(0, (0.8, 0.85, 0.9)), density(1, (0.05, 0.16, 0.28)))
    angular = (ReflectanceKeyframe(0, (0.1, 0.65, 0.85)), ReflectanceKeyframe(45, (0.75, 0.1, 0.55)),
               ReflectanceKeyframe(90, (1, 1, 1)))
    return {
        "clear": uniform((1, 1, 1)), "neutral_tint": uniform((0.45, 0.45, 0.45)),
        "saturated_tint": uniform((0.08, 0.38, 0.72)), "vertical_gradient": LensAppearance(gradient, roughness=0),
        "multi_stop_gradient": LensAppearance((density(0, (0.85, 0.7, 0.3)), density(0.35, (0.5, 0.6, 0.25)),
                                                density(0.65, (0.3, 0.22, 0.7)), density(1, (0.02, 0.08, 0.2))), roughness=0),
        "strong_neutral_mirror": uniform((0.8, 0.8, 0.8), (0.9, 0.9, 0.9)),
        "total_mirror": uniform((0.8, 0.8, 0.8), (1, 1, 1)),
        "strong_colored_mirror": uniform((0.7, 0.8, 0.9), (0.8, 0.55, 0.15)),
        "angular_coating": LensAppearance((density(0, (0.4, 0.5, 0.6)),), angular[0].reflectance_rgb,
                                           roughness=0, angular_reflectance_keyframes=angular),
        "mirrored_gradient": LensAppearance(gradient, (0.8, 0.55, 0.15), roughness=0),
    }


def write_conformance_bundle(output: str | Path) -> Path:
    """Write fixtures and independently usable CPU samples; return cases.json."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    environments = [
        {"id": name, "background_linear_rgb": background, "reflected_linear_rgb": reflected}
        for name, background, reflected in (
            ("neutral_gray", [0.18] * 3, [0.18] * 3), ("dark_mirror", [0.0] * 3, [1.0] * 3),
            ("transmission_only", [1.0] * 3, [0.0] * 3), ("black", [0.0] * 3, [0.0] * 3),
            ("white", [1.0] * 3, [1.0] * 3), ("colored_studio", [0.12, 0.24, 0.4], [1.0, 0.4, 0.08]),
        )
    ]
    cases = []
    for name, appearance in conformance_cases().items():
        case_dir = output / name
        case_dir.mkdir(exist_ok=True)
        glb = build_lens_fixture_glb(appearance)
        (case_dir / "model.glb").write_bytes(glb)
        samples = []
        for v in np.linspace(0, 1, 9):
            for angle in (0, 15, 30, 45, 60, 75, 85):
                sample = appearance.evaluate(float(v), angle)
                samples.append({"v": float(v), "angle_degrees": angle,
                                "R": sample.reflectance_rgb.tolist(), "T": sample.transmission_rgb.tolist(),
                                "A": sample.absorption_rgb.tolist(), "compositions": {
                                    environment["id"]: sample.compose(environment["background_linear_rgb"], environment["reflected_linear_rgb"]).tolist()
                                    for environment in environments}})
        cases.append({"id": name, "label": name.replace("_", " "), "model": f"{name}/model.glb",
                      "model_sha256": hashlib.sha256(glb).hexdigest(), "appearance": appearance.to_dict(), "samples": samples})
    manifest = {"schema_version": 1, "color_space": COLOR_SPACE, "fixture_only": True,
                "environments": environments, "cases": cases}
    manifest_path = output / "cases.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    (output / "capabilities.json").write_bytes(_json_bytes(capability_report()))
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "lens-conformance")
    args = parser.parse_args()
    print(write_conformance_bundle(args.output).resolve())


if __name__ == "__main__":
    main()
