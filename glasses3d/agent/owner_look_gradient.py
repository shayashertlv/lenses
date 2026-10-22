"""Deterministic image gradients in the exported model's canonical coordinates.

Shares are positive region widths summing to 100. At each region boundary,
``blend * min(adjacent shares)`` is the transition width, centred on the boundary.
The transition is smoothstep in linear RGB; the embedded PNG stores sRGB bytes.
Centre-to-sides uses an ordinary linear X UV and a mirrored image, so even a
single triangle crossing the bridge interpolates correctly (unlike abs(X) UVs).

Around-a-point uses affine XY coordinates, never interpolated vertex distances.
Its circle radius is half the projected lens height. Roundness (1..4) multiplies
the horizontal radius only. Both UV axes use the SAME scale, radius * roundness;
the square PNG evaluates the elliptical distance. Every edge of this square is
at radius >= 1, so clamp-to-edge preserves the end colour even on very wide lenses
or when the centre is moved to an edge. Centre offsets (-1..1) are fractions of
the projected half-width/half-height; +Y is up. Disconnected surfaces on both
sides of X=0 get their own extent centre and mirrored horizontal offsets (+X
means outwards). Front/back sheets and material splits on the same side share
one logical lens's union extent. Otherwise all lens surfaces share their union
extent centre.
"""
from __future__ import annotations

import io
from functools import lru_cache
import math
import struct

import numpy as np
from PIL import Image

from . import owner_lens_edit as lens

DIRECTIONS = {"centre_to_sides", "top_to_bottom", "left_to_right", "around_point"}
WIDTH = 1024
KEY = "g3dOwnerGradient"
SCHEMA = "g3d.owner_gradient/1"


def validate_gradient(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get("direction"), str)
            or value["direction"] not in DIRECTIONS):
        raise ValueError("Choose Centre to sides, Top to bottom, Left to right or Around a point for the gradient.")
    radial = value["direction"] == "around_point"
    expected = {"direction", "colours", "blend"} | ({"centre", "roundness"} if radial else set())
    if set(value) != expected:
        raise ValueError("Provide the gradient controls, including centre and roundness only for Around a point.")
    colours = value["colours"]
    if not isinstance(colours, list) or not 2 <= len(colours) <= 3:
        raise ValueError("A gradient needs two or three colours; use Solid for one colour.")
    result = []
    for row in colours:
        if not isinstance(row, dict) or set(row) != {"colour", "share"}:
            raise ValueError("Each gradient colour needs a colour and a takes share.")
        rgb = row["colour"]
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError("Each gradient colour needs three linear RGB values.")
        share = lens._number(row["share"], 0, 100, "Colour share")
        if share == 0:
            raise ValueError("Each colour must take a positive share; remove an unused middle colour.")
        result.append({"colour": [lens._number(x, 0, 1, "Gradient colour") for x in rgb], "share": share})
    if not math.isclose(sum(row["share"] for row in result), 100, rel_tol=0, abs_tol=1e-7):
        raise ValueError("The colour takes shares must add up to 100%.")
    clean = {"direction": value["direction"], "colours": result,
             "blend": lens._number(value["blend"], 0, 1, "Blend")}
    if radial:
        if not isinstance(value["centre"], list) or len(value["centre"]) != 2:
            raise ValueError("The gradient centre needs Left / right and Up / down values.")
        clean.update(centre=[lens._number(x, -1, 1, "Gradient centre") for x in value["centre"]],
                     roundness=lens._number(value["roundness"], 1, 4, "Roundness"))
    return clean


def sample_gradient(value, positions):
    """Return linear RGB at one or more normalised colour-region positions."""
    value = validate_gradient(value)
    if value is None:
        raise ValueError("A gradient is required.")
    t = np.clip(np.asarray(positions, dtype=np.float64), 0, 1)
    rows = value["colours"]
    bounds = np.cumsum([row["share"] for row in rows]) / 100
    colours = np.array([row["colour"] for row in rows])
    result = colours[np.minimum(np.searchsorted(bounds, t, side="right"), len(rows) - 1)].copy()
    for i, boundary in enumerate(bounds[:-1]):
        half = value["blend"] * min(rows[i]["share"], rows[i + 1]["share"]) / 200
        if half:
            z = np.clip((t - (boundary - half)) / (2 * half), 0, 1)
            z = z * z * (3 - 2 * z)
            candidate = colours[i] * (1 - z[..., None]) + colours[i + 1] * z[..., None]
            result = np.where(((t >= boundary - half) & (t <= boundary + half))[..., None], candidate, result)
    return result


def image_bytes(value):
    value = validate_gradient(value)
    # Centre moves only UVs. Keep a small cache of immutable encoded PNGs so live
    # dragging and authenticated rereads do not rasterize the same million pixels.
    return _image_bytes(value["direction"], tuple((tuple(row["colour"]), row["share"])
                        for row in value["colours"]), value["blend"], value.get("roundness"))


@lru_cache(maxsize=16)
def _image_bytes(direction, colours, blend, roundness):
    value = {"direction": direction, "colours": [{"colour": list(rgb), "share": share} for rgb, share in colours],
             "blend": blend}
    if direction == "around_point":
        value.update(centre=[0, 0], roundness=roundness)
    u = (np.arange(WIDTH, dtype=np.float64) + .5) / WIDTH
    if value["direction"] == "centre_to_sides":
        u = np.abs(2 * u - 1)
    elif value["direction"] == "around_point":
        # Ellipse maths belongs in the image. Affine, isotropic UVs then work
        # inside even a two-triangle lens with no vertex near the centre.
        x = 2 * u - 1
        u = np.hypot(x[None, :], value["roundness"] * x[:, None])
        # Clamp samples the outer texel centres, just inside the square. Keep
        # those perimeter texels at the endpoint even for sub-texel end shares.
        u[[0, -1], :] = 1
        u[:, [0, -1]] = 1
    linear = sample_gradient(value, u)
    srgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
    rgba = np.full((WIDTH if value["direction"] == "around_point" else 1, WIDTH, 4), 255, dtype=np.uint8)
    rgba[:, :, :3] = np.floor(srgb * 255 + .5).astype(np.uint8)
    output = io.BytesIO()
    Image.fromarray(rgba).save(output, format="PNG", compress_level=9)
    return output.getvalue()


def _identity_nodes(doc):
    defaults = {"matrix": np.eye(4).T.reshape(-1).tolist(), "translation": [0, 0, 0],
                "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}
    active = lens._active_nodes(doc)
    for index, node in enumerate(doc.get("nodes", [])):
        if index not in active:
            continue
        for key, default in defaults.items():
            if key in node:
                array = np.asarray(node[key], dtype=np.float64)
                if array.shape != (len(default),) or not np.allclose(array, default, rtol=0, atol=1e-12):
                    raise ValueError("Gradient needs the model's baked canonical coordinates; this part has a transform.")


def _accessor(doc, binary, index):
    from glasses3d.g3d.printuv.glbmesh import accessor
    try:
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        if view.get("byteOffset", 0) + view["byteLength"] > len(binary):
            raise ValueError("The model's vertex data is incomplete.")
        result = accessor(doc, binary, index)
        if not np.isfinite(result).all():
            raise ValueError("The model has a non-finite vertex value.")
        return result
    except (KeyError, IndexError, TypeError, OverflowError) as exc:
        raise ValueError("This model's vertex data cannot be used for a gradient.") from exc


def projection_data(doc, binary, lens_indices):
    """Read all lens extents once; connected surfaces keep their own Y extent.

    Exact duplicate positions are welded only for the component analysis, never
    in the model. This joins normal/UV seams and separate primitives of one lens
    while distinguishing two disconnected lenses even inside one mesh.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    _identity_nodes(doc)
    rows, points, faces, count = [], [], [], 0
    active_meshes = {node.get("mesh") for i, node in enumerate(doc.get("nodes", []))
                     if i in lens._active_nodes(doc)}
    for mi, mesh in enumerate(doc.get("meshes", [])):
        if mi not in active_meshes:
            continue
        for pi, primitive in enumerate(mesh.get("primitives", [])):
            if primitive.get("material") not in lens_indices:
                continue
            if primitive.get("mode", 4) != 4 or primitive.get("targets"):
                raise ValueError("Gradient needs a static triangle lens mesh.")
            if "KHR_draco_mesh_compression" in primitive.get("extensions", {}):
                raise ValueError("This compressed lens needs an uncompressed export before adding a gradient.")
            p = _accessor(doc, binary, primitive["attributes"]["POSITION"]).astype(np.float64)
            if p.ndim != 2 or p.shape[1] != 3 or not len(p):
                raise ValueError("Gradient needs finite lens positions.")
            indices = (_accessor(doc, binary, primitive["indices"]).reshape(-1)
                       if "indices" in primitive else np.arange(len(p)))
            if len(indices) % 3 or (indices < 0).any() or (indices >= len(p)).any():
                raise ValueError("Gradient needs valid lens triangles.")
            rows.append({"mesh": mi, "primitive": pi, "material": primitive["material"],
                         "positions": p, "offset": count})
            points.append(p)
            faces.append(indices.reshape(-1, 3).astype(np.int64) + count)
            count += len(p)
    if not rows:
        raise ValueError("The model has no usable lens surface for a gradient.")
    all_points, triangles = np.vstack(points), np.vstack(faces)
    unique, weld = np.unique(all_points, axis=0, return_inverse=True)
    triangles = weld[triangles]
    a, b = triangles[:, [0, 1, 2]].reshape(-1), triangles[:, [1, 2, 0]].reshape(-1)
    graph = coo_matrix((np.ones(len(a), dtype=np.uint8), (a, b)), shape=(len(unique), len(unique)))
    n, labels = connected_components(graph, directed=False)
    low, high = np.full((n, 2), np.inf), np.full((n, 2), -np.inf)
    np.minimum.at(low, labels, unique[:, :2])
    np.maximum.at(high, labels, unique[:, :2])
    # Surface topology, including material boundaries, decides pair or shield.
    # Front/back sheets may be disconnected. A side's sheets/materials share one
    # logical lens extent; a shield's sheets share the complete union extent.
    sides = np.where(high[:, 0] <= 0, -1, np.where(low[:, 0] >= 0, 1, 0))
    pair = bool((sides != 0).all() and (sides == -1).any() and (sides == 1).any())
    radial_low = np.broadcast_to(low.min(axis=0), low.shape).copy()
    radial_high = np.broadcast_to(high.max(axis=0), high.shape).copy()
    if pair:
        for side in [-1, 1]:
            mask = sides == side
            radial_low[mask] = low[mask].min(axis=0)
            radial_high[mask] = high[mask].max(axis=0)
    for row in rows:
        start, size = row["offset"], len(row["positions"])
        ids = labels[weld[start:start + size]]
        row.update(ymin=low[ids, 1], ymax=high[ids, 1], components=ids,
                   radial_mode="pair" if pair else "single",
                   radial_centre=(radial_low[ids] + radial_high[ids]) / 2,
                   radial_half_extent=(radial_high[ids] - radial_low[ids]) / 2,
                   radial_mirror_x=sides[ids] if pair else np.ones(size, dtype=int))
    return rows, float(all_points[:, 0].min()), float(all_points[:, 0].max())


def radial_projection(rows, material_index):
    """JSON layout for converting a picked canonical triangle into centre controls."""
    surfaces = []
    for row in rows:
        if row["material"] != material_index:
            continue
        for component in np.unique(row["components"]):
            indices = np.flatnonzero(row["components"] == component)
            first = indices[0]
            breaks = np.flatnonzero(np.diff(indices) > 1)
            starts = indices[np.r_[0, breaks + 1]]
            ends = indices[np.r_[breaks, len(indices) - 1]]
            surfaces.append({"mesh_index": row["mesh"], "primitive_index": row["primitive"],
                "component": int(component), "vertex_ranges": np.column_stack((starts, ends)).tolist(),
                "lens_id": int(row["radial_mirror_x"][first]) if row["radial_mode"] == "pair" else 0,
                "centre": row["radial_centre"][first].tolist(),
                "half_extent": row["radial_half_extent"][first].tolist(),
                "mirror_x": int(row["radial_mirror_x"][first])})
    return {"mode": rows[0]["radial_mode"], "surfaces": surfaces}


def coordinates(row, direction, xmin, xmax, spec=None):
    p = row["positions"]
    if direction == "around_point":
        spec = validate_gradient(spec)
        if spec is None or spec["direction"] != direction:
            raise ValueError("Around a point needs its centre and roundness controls.")
        centre = row["radial_centre"].copy()
        half = row["radial_half_extent"]
        if not (half > 0).all():
            raise ValueError("Around a point needs a lens with projected width and height.")
        centre[:, 0] += spec["centre"][0] * half[:, 0] * row["radial_mirror_x"]
        centre[:, 1] += spec["centre"][1] * half[:, 1]
        scale = half[:, 1] * spec["roundness"]
        uv = .5 + (p[:, :2] - centre) * np.array([1, -1]) / (2 * scale[:, None])
        return uv.astype("<f4").tobytes()
    if direction == "left_to_right":
        denominator = xmax - xmin
        u = (p[:, 0] - xmin) / denominator if denominator > 0 else None
    elif direction == "centre_to_sides":
        extent = max(abs(xmin), abs(xmax))
        u = (p[:, 0] + extent) / (2 * extent) if extent > 0 else None
    else:
        height = row["ymax"] - row["ymin"]
        u = (row["ymax"] - p[:, 1]) / height if (height > 0).all() else None
    if u is None:
        raise ValueError("This lens has no extent in the selected gradient direction.")
    return np.column_stack((u, np.full(len(p), .5))).astype("<f4").tobytes()


class Appender:
    def __init__(self, doc, chunks):
        self.doc, self.chunks, self.added = doc, chunks, []
        binaries = [(i, body) for i, (kind, body, _) in enumerate(chunks) if kind == 0x004E4942]
        if len(binaries) != 1 or len(doc.get("buffers", [])) != 1 or "uri" in doc["buffers"][0]:
            raise ValueError("Gradient needs a self-contained GLB with one embedded buffer.")
        self.chunk_index, original = binaries[0]
        self.original, self.binary = original, bytearray(original)

    def item(self, array, value):
        target = self.doc.setdefault(array, [])
        index = len(target)
        target.append(value)
        self.added.append({"kind": array, "index": index, "value": value})
        return index

    def view(self, payload, *, target=None, label):
        self._pad()
        offset = len(self.binary)
        self.binary.extend(payload)
        value = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target:
            value["target"] = target
        index = self.item("bufferViews", value)
        self.added.append({"kind": "bytes", "label": label, "buffer": 0, "byteOffset": offset,
                           "byteLength": len(payload), "sha256": lens._sha(payload)})
        return index

    def _pad(self):
        padding = b"\0" * (-len(self.binary) % 4)
        if padding:
            self.added.append({"kind": "bytes", "label": "alignment padding", "buffer": 0,
                               "byteOffset": len(self.binary), "byteLength": len(padding),
                               "sha256": lens._sha(padding)})
            self.binary.extend(padding)

    def texture(self, spec):
        image = self.item("images", {"bufferView": self.view(image_bytes(spec), label="gradient PNG"),
                                      "mimeType": "image/png", "name": "Owner gradient"})
        sampler = self.item("samplers", {"magFilter": 9729, "minFilter": 9729,
                                          "wrapS": 33071, "wrapT": 33071})
        return self.item("textures", {"source": image, "sampler": sampler})

    def uv(self, payload):
        return self.item("accessors", {"bufferView": self.view(payload, target=34962, label="gradient UV"),
                                        "componentType": 5126, "count": len(payload) // 8, "type": "VEC2"})

    def chunks_bytes(self):
        self.doc["buffers"][0]["byteLength"] = len(self.binary)
        self._pad()
        return [struct.pack("<II", len(self.binary), 0x004E4942) + bytes(self.binary)
                if i == self.chunk_index else raw for i, (_, _, raw) in enumerate(self.chunks) if i]
