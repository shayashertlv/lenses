"""Transfer the textured generation's surface parameterization onto its split parts.

A provider split delivers named closed parts with positions and per-vertex
colours only, while the textured generation of the same task carries the
photographic surface (UVs, base colour, roughness/metallic and normal maps)
on one fused mesh whose outer surface coincides with the parts' outer faces.
The generation's texture atlas is a set of islands: patches of triangles that
share one continuous parameterization, separated in the atlas by padding and
by other islands. Texture coordinates are only meaningful within one island,
so every split triangle must sample exactly one island.

For every split vertex this stage finds the closest point on the generation's
surface and the island it lies on. A face whose corners all lie on one island
keeps the exact interpolated coordinates. A face that straddles islands takes
one of them: among the islands under its corners and its centroid, the one
whose nearest triangles need the least extrapolation for the corners off it
(ties go to the island holding more corners). Corners on the chosen island
keep their exact coordinates; a corner off it is extrapolated through the
affine map of that island's nearest triangle, so the face samples a little
beyond the island edge, into its padding, instead of sweeping across the atlas.
The extrapolation is recorded in barycentric units of that triangle. Corners
of one source vertex that ended up with different coordinates become separate
output vertices at the same position; equal ones are welded. Every other
vertex attribute is gathered along, split-provider vertex colours are replaced
by the generation's interpolated colors when present (glTF multiplies them
into the base colour) and the generation's material, textures
and images attach to every primitive.

Both models must share the provider's coordinate frame, so this runs before
any scaling. Closest-point lookup uses Open3D's float32 raycasting scene; the
receipt records distance statistics, how many vertices lie farther than a
stated fraction of the generation's extent (interior and cut faces of a closed
part have no counterpart on the outer surface and take the nearest one), how
many faces straddled an island boundary and how far their corners were
extrapolated. Downstream connectivity is position based, so the duplicated
seam vertices keep every part in one component. Nothing here identifies a
lens or a frame; declared optical groups receive their fitted material later
regardless of the texture transferred here.
"""
from __future__ import annotations

import hashlib
import json
import struct

import numpy as np

from .mesh import _node_matrix


METHOD = 'island_consistent_uv_color_transfer_v3'
UV_WELD_DECIMALS = 6
_DTYPES = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_WIDTHS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _chunks(raw: bytes):
    if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
        raise ValueError('Expected an intact GLB version 2')
    offset, chunks = 12, []
    while offset < len(raw):
        size, kind = struct.unpack_from('<II', raw, offset)
        offset += 8
        if size % 4 or offset + size > len(raw):
            raise ValueError('Invalid GLB chunk extent/alignment')
        chunks.append((kind, raw[offset:offset + size]))
        offset += size
    if [c[0] for c in chunks] != [0x4E4F534A, 0x004E4942]:
        raise ValueError('Expected a self-contained JSON/BIN GLB')
    doc = json.loads(chunks[0][1])
    if len(doc.get('buffers', [])) != 1 or 'uri' in doc['buffers'][0]:
        raise ValueError('Expected one embedded buffer')
    return doc, chunks[1][1]


def _pack(doc: dict, binary: bytes) -> bytes:
    text = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
    text += b' ' * (-len(text) % 4)
    binary = bytes(binary) + b'\0' * (-len(binary) % 4)
    body = struct.pack('<II', len(text), 0x4E4F534A) + text + struct.pack('<II', len(binary), 0x004E4942) + binary
    return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body


def _rows(doc, binary, index):
    """An accessor's rows in their stored dtype, with the accessor record; normalized data is kept as stored."""
    acc = doc['accessors'][index]
    if 'sparse' in acc or 'bufferView' not in acc:
        raise ValueError('Sparse or bufferless accessors require preprocessing')
    view = doc['bufferViews'][acc['bufferView']]
    dtype, width, count = np.dtype(_DTYPES[acc['componentType']]), _WIDTHS[acc['type']], acc['count']
    start = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
    stride = view.get('byteStride', width * dtype.itemsize)
    end = start + max(0, count - 1) * stride + width * dtype.itemsize
    if count <= 0 or stride < width * dtype.itemsize or end > view.get('byteOffset', 0) + view['byteLength'] or end > len(binary):
        raise ValueError('Invalid accessor range')
    return acc, np.ndarray((count, width), dtype=dtype, buffer=binary, offset=start, strides=(stride, dtype.itemsize)).copy()


def _accessor(doc, binary, index):
    acc, rows = _rows(doc, binary, index)
    if acc.get('normalized'):
        raise ValueError('Normalized geometry accessors require preprocessing')
    return rows


def _primitives(doc, binary):
    """World-space triangles per primitive of the default scene, with node and mesh identity."""
    scene = doc['scenes'][doc.get('scene', 0)]
    result = []

    def visit(index, parent):
        node = doc['nodes'][index]
        matrix = parent @ _node_matrix(node)
        if 'mesh' in node:
            for pi, primitive in enumerate(doc['meshes'][node['mesh']]['primitives']):
                if primitive.get('mode', 4) != 4:
                    raise ValueError('Only triangle primitives are supported')
                positions = _accessor(doc, binary, primitive['attributes']['POSITION']).astype(float)
                world = positions @ matrix[:3, :3].T + matrix[:3, 3]
                indices = (_accessor(doc, binary, primitive['indices']).ravel().astype(np.int64)
                           if 'indices' in primitive else np.arange(len(positions)))
                if len(indices) % 3 or (len(indices) and (indices.min() < 0 or indices.max() >= len(positions))):
                    raise ValueError('Invalid triangle indices')
                result.append({'node': index, 'mesh': node['mesh'], 'primitive': pi, 'record': primitive,
                               'world': world, 'faces': indices.reshape(-1, 3), 'vertex_count': len(positions)})
        for child in node.get('children', []):
            visit(child, matrix)

    for root in scene.get('nodes', []):
        visit(root, np.eye(4))
    if not result:
        raise ValueError('The selected scene has no triangle primitives')
    return result


def _uv_islands(sources, face_offsets):
    """Label every generation triangle by its UV island.

    Triangles that share a vertex index share that vertex's texture coordinate,
    so they lie in one continuous parameterization; a seam in glTF is a vertex
    stored twice with different coordinates, which never joins two islands.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    labels, next_label = np.empty(int(face_offsets[-1]), np.int64), 0
    for si, source in enumerate(sources):
        faces, nv = source['faces'], source['vertex_count']
        nf = len(faces)
        if not nf:
            continue
        graph = coo_matrix((np.ones(3 * nf, np.int8), (np.repeat(np.arange(nf), 3), faces.ravel() + nf)), shape=(nf + nv, nf + nv))
        _, component = connected_components(graph, directed=False)
        _, face_labels = np.unique(component[:nf], return_inverse=True)
        labels[face_offsets[si]:face_offsets[si + 1]] = face_labels + next_label
        next_label += int(face_labels.max()) + 1
    return labels, next_label


def _barycentric(points, triangles):
    """Unclamped barycentric coordinates of each point's projection onto its triangle's plane."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    v0, v1, v2 = b - a, c - a, points - a
    d00, d01, d11 = np.sum(v0 * v0, axis=1), np.sum(v0 * v1, axis=1), np.sum(v1 * v1, axis=1)
    d20, d21 = np.sum(v2 * v0, axis=1), np.sum(v2 * v1, axis=1)
    denominator = d00 * d11 - d01 * d01
    safe = np.abs(denominator) > 1e-20
    denominator = np.where(safe, denominator, 1.)
    v = np.where(safe, (d11 * d20 - d01 * d21) / denominator, 0.)
    w = np.where(safe, (d00 * d21 - d01 * d20) / denominator, 0.)
    return np.stack([1 - v - w, v, w], axis=1)


def _append_view(binary, views, payload, target):
    binary.extend(b'\0' * (-len(binary) % 4))
    views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(payload), 'target': target})
    binary.extend(payload)
    return len(views) - 1


def _copy_images(doc, source_binary, binary, views, images):
    """Append a document's embedded images to ``binary``; returns old-to-new image indices."""
    result = {}
    for ii, image in enumerate(doc.get('images', [])):
        if 'bufferView' not in image:
            raise ValueError('Images must be embedded buffer views')
        view = doc['bufferViews'][image['bufferView']]
        blob = source_binary[view.get('byteOffset', 0):view.get('byteOffset', 0) + view['byteLength']]
        binary.extend(b'\0' * (-len(binary) % 4))
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(blob)})
        binary.extend(blob)
        copied = {k: v for k, v in image.items() if k != 'bufferView'}
        copied['bufferView'] = len(views) - 1
        images.append(copied)
        result[ii] = len(images) - 1
    return result


def transfer_surface(split_raw: bytes, generation_raw: bytes, *, far_fraction: float = .02) -> tuple[bytes, dict]:
    """Return the split GLB with island-consistent UVs and the generation's material, plus a receipt."""
    if isinstance(far_fraction, bool) or not isinstance(far_fraction, (int, float)) or not 0 < far_fraction < 1:
        raise ValueError('far_fraction must lie in (0, 1)')
    try:
        import open3d as o3d
    except ImportError as error:  # pragma: no cover - environment
        raise ValueError('Surface transfer needs Open3D') from error
    gen_doc, gen_bin = _chunks(generation_raw)
    split_doc, split_bin = _chunks(split_raw)
    sources = _primitives(gen_doc, gen_bin)
    has_generation_colors = False
    for source in sources:
        attributes = source['record']['attributes']
        if 'TEXCOORD_0' not in attributes or 'material' not in source['record']:
            raise ValueError('Every generation primitive needs TEXCOORD_0 and a material')
        source['uv'] = _accessor(gen_doc, gen_bin, attributes['TEXCOORD_0']).astype(float)
        if source['uv'].shape[1] != 2 or len(source['uv']) != source['vertex_count']:
            raise ValueError('TEXCOORD_0 must be one VEC2 per vertex')
        source['color'] = np.ones((source['vertex_count'], 4))
        if 'COLOR_0' in attributes:
            acc, color = _rows(gen_doc, gen_bin, attributes['COLOR_0'])
            if acc.get('normalized') and color.dtype.kind in 'ui':
                color = color.astype(float) / np.iinfo(color.dtype).max
            if (color.shape not in ((source['vertex_count'],3),(source['vertex_count'],4))
                    or not np.isfinite(color).all() or np.any(color<0) or np.any(color>1)):
                raise ValueError('Generation vertex colors must be finite normalized RGB or RGBA')
            source['color'][:,:color.shape[1]] = color
            has_generation_colors = True
    # One triangle soup with per-triangle owner, corner UVs and island label.
    face_offsets = np.cumsum([0] + [len(s['faces']) for s in sources])
    soup = np.concatenate([s['world'][s['faces']] for s in sources]).reshape(-1, 3, 3)
    owner = np.concatenate([np.full(len(s['faces']), si, np.int64) for si, s in enumerate(sources)])
    tri_uv = np.concatenate([s['uv'][s['faces']] for s in sources]).reshape(-1, 3, 2)
    tri_color = np.concatenate([s['color'][s['faces']] for s in sources]).reshape(-1,3,4)
    islands, island_count = _uv_islands(sources, face_offsets)
    extent = float(np.max(soup.reshape(-1, 3).max(axis=0) - soup.reshape(-1, 3).min(axis=0)))
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError('The generation has no finite extent')
    def raycasting_scene(triangles):
        built = o3d.t.geometry.RaycastingScene()
        built.add_triangles(o3d.core.Tensor(triangles.reshape(-1, 3).astype(np.float32)),
                            o3d.core.Tensor(np.arange(len(triangles) * 3, dtype=np.uint32).reshape(-1, 3)))
        return built

    scene = raycasting_scene(soup)
    island_order = np.argsort(islands, kind='stable')
    island_sorted, island_scenes = islands[island_order], {}

    def nearest_on_island(label, points):
        """Global ids of the island's triangles nearest to ``points``; one scene per island, built on demand."""
        if label not in island_scenes:
            members = island_order[np.searchsorted(island_sorted, label, 'left'):np.searchsorted(island_sorted, label, 'right')]
            island_scenes[label] = (raycasting_scene(soup[members]), members)
        built, members = island_scenes[label]
        return members[built.compute_closest_points(o3d.core.Tensor(points.astype(np.float32)))['primitive_ids'].numpy().astype(np.int64)]

    targets = _primitives(split_doc, split_bin)
    # A fresh buffer: every referenced attribute is re-emitted, so old views and accessors are dropped.
    binary, views, accessors, images = bytearray(), [], [], []
    split_image_map = _copy_images(split_doc, split_bin, binary, views, images)
    for texture in split_doc.get('textures', []):
        if 'source' in texture:
            texture['source'] = split_image_map[texture['source']]
    image_map = _copy_images(gen_doc, gen_bin, binary, views, images)
    sampler_base = len(split_doc.setdefault('samplers', []))
    split_doc['samplers'].extend(json.loads(json.dumps(gen_doc.get('samplers', []))))
    texture_map = {}
    for ti, texture in enumerate(gen_doc.get('textures', [])):
        copied = dict(texture)
        if 'source' in copied:
            copied['source'] = image_map[copied['source']]
        if 'sampler' in copied:
            copied['sampler'] = sampler_base + copied['sampler']
        split_doc.setdefault('textures', []).append(copied)
        texture_map[ti] = len(split_doc['textures']) - 1
    material_map = {}
    for mi, material in enumerate(gen_doc.get('materials', [])):
        copied = json.loads(json.dumps(material))

        def remap(node):
            if isinstance(node, dict):
                if 'index' in node and set(node) <= {'index', 'texCoord', 'scale', 'strength', 'extensions', 'extras'}:
                    node['index'] = texture_map[node['index']]
                for value in node.values():
                    remap(value)
            elif isinstance(node, list):
                for value in node:
                    remap(value)
        remap(copied)
        copied['name'] = f"{material.get('name', 'material')} (transferred)"
        split_doc.setdefault('materials', []).append(copied)
        material_map[mi] = len(split_doc['materials']) - 1
    if gen_doc.get('extensionsUsed'):
        split_doc['extensionsUsed'] = sorted(set(split_doc.get('extensionsUsed', [])) | set(gen_doc['extensionsUsed']))
    quantum = 10 ** UV_WELD_DECIMALS
    distances_all, excursions_all, per_primitive = [], [], []
    totals = {'vertices': 0, 'output_vertices': 0, 'faces': 0, 'straddling_faces': 0, 'extrapolated_corners': 0, 'far_vertices': 0}
    for target in targets:
        world, faces = target['world'], target['faces']
        hit = scene.compute_closest_points(o3d.core.Tensor(world.astype(np.float32)))
        points = hit['points'].numpy().astype(float)
        ids = hit['primitive_ids'].numpy().astype(np.int64)
        distance = np.linalg.norm(world - points, axis=1)
        bary = np.clip(_barycentric(points, soup[ids]), 0, 1)
        bary /= np.maximum(bary.sum(axis=1, keepdims=True), 1e-300)
        uv_vertex = np.einsum('ni,nij->nj', bary, tri_uv[ids])
        island_vertex = islands[ids]
        # One island per face. Faces whose corners agree keep exact coordinates; a straddling face takes the candidate
        # island (its corners' and its centroid's) whose nearest triangles need the least extrapolation.
        corner_islands = island_vertex[faces]
        straddling = ~((corner_islands[:, 0] == corner_islands[:, 1]) & (corner_islands[:, 0] == corner_islands[:, 2]))
        corner_uv = uv_vertex[faces].copy()
        color_vertex = np.einsum('ni,nij->nj',bary,tri_color[ids])
        corner_color = color_vertex[faces].copy()
        straddlers = np.flatnonzero(straddling)
        excursion, extrapolated = np.zeros(0), 0
        if len(straddlers):
            corners_of = faces[straddlers]
            points = world[corners_of]
            candidate_islands = corner_islands[straddlers]
            centroids = points.mean(axis=1)
            centroid_ids = scene.compute_closest_points(o3d.core.Tensor(centroids.astype(np.float32)))['primitive_ids'].numpy().astype(np.int64)
            candidates = np.concatenate([candidate_islands, islands[centroid_ids][:, None]], axis=1)
            excursions = np.zeros((len(straddlers), 4, 3))
            candidate_uv = np.zeros((len(straddlers), 4, 3, 2))
            candidate_color = np.zeros((len(straddlers),4,3,4))
            for slot in range(4):
                label_of = candidates[:, slot]
                for corner in range(3):
                    exact = candidate_islands[:, corner] == label_of
                    candidate_uv[exact, slot, corner] = uv_vertex[corners_of[exact, corner]]
                    candidate_color[exact,slot,corner] = color_vertex[corners_of[exact,corner]]
                    off = np.flatnonzero(~exact)
                    if not len(off):
                        continue
                    wanted, nearest = label_of[off], np.empty(len(off), np.int64)
                    for label in np.unique(wanted):
                        selected = wanted == label
                        nearest[selected] = nearest_on_island(label, points[off[selected], corner])
                    outside = _barycentric(points[off, corner], soup[nearest])
                    candidate_uv[off, slot, corner] = np.einsum('ni,nij->nj', outside, tri_uv[nearest])
                    candidate_color[off,slot,corner] = np.clip(np.einsum('ni,nij->nj',outside,tri_color[nearest]),0,1)
                    excursions[off, slot, corner] = np.maximum(0., -outside.min(axis=1))
            worst = excursions.max(axis=2)
            exact_corners = (candidate_islands[:, None, :] == candidates[:, :, None]).sum(axis=2)
            slot = np.lexsort((candidates, -exact_corners, worst), axis=-1)[:, 0]
            rows = np.arange(len(straddlers))
            corner_uv[straddlers] = candidate_uv[rows, slot]
            corner_color[straddlers] = candidate_color[rows,slot]
            off_island = candidate_islands != candidates[rows, slot][:, None]
            excursion = excursions[rows, slot][off_island]
            extrapolated = int(np.count_nonzero(off_island))
        # Weld corners of one source vertex that agree in UV; split the rest.
        quantized = np.round(corner_uv * quantum).astype(np.int64)
        keys = np.stack([faces, quantized[..., 0], quantized[..., 1]], axis=-1).reshape(-1, 3)
        if has_generation_colors:
            keys = np.column_stack((keys,np.round(corner_color.reshape(-1,4)*quantum).astype(np.int64)))
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        new_faces = inverse.reshape(-1, 3)
        old_index = unique[:, 0]
        uv_out = unique[:, 1:3].astype(float) / quantum
        record = target['record']
        material_votes = {}
        for si in np.unique(owner[ids]):
            material = sources[si]['record']['material']
            material_votes[material] = material_votes.get(material, 0) + int(np.count_nonzero(owner[ids] == si))
        chosen_material = max(sorted(material_votes), key=material_votes.get)
        new_attributes = {}
        for name, index in record['attributes'].items():
            if name in ('COLOR_0', 'TEXCOORD_0'):
                continue
            acc, rows = _rows(split_doc, split_bin, index)
            gathered = np.ascontiguousarray(rows[old_index])
            accessor = {'bufferView': _append_view(binary, views, gathered.tobytes(), _ARRAY_BUFFER),
                        'componentType': acc['componentType'], 'count': len(gathered), 'type': acc['type']}
            if acc.get('normalized'):
                accessor['normalized'] = True
            if name == 'POSITION':
                accessor['min'] = [float(x) for x in gathered.min(axis=0)]
                accessor['max'] = [float(x) for x in gathered.max(axis=0)]
            accessors.append(accessor)
            new_attributes[name] = len(accessors) - 1
        uv_payload = uv_out.astype('<f4')
        accessors.append({'bufferView': _append_view(binary, views, uv_payload.tobytes(), _ARRAY_BUFFER), 'componentType': 5126,
                          'count': len(uv_payload), 'type': 'VEC2',
                          'min': [float(x) for x in uv_payload.min(axis=0)], 'max': [float(x) for x in uv_payload.max(axis=0)]})
        new_attributes['TEXCOORD_0'] = len(accessors) - 1
        if has_generation_colors:
            color_payload = (unique[:,3:7].astype(float)/quantum).astype('<f4')
            accessors.append({'bufferView':_append_view(binary,views,color_payload.tobytes(),_ARRAY_BUFFER),
                              'componentType':5126,'count':len(color_payload),'type':'VEC4'})
            new_attributes['COLOR_0'] = len(accessors)-1
        accessors.append({'bufferView': _append_view(binary, views, new_faces.astype('<u4').tobytes(), _ELEMENT_ARRAY_BUFFER),
                          'componentType': 5125, 'count': int(new_faces.size), 'type': 'SCALAR'})
        removed = record['attributes'].get('COLOR_0')
        record['attributes'] = new_attributes
        record['indices'] = len(accessors) - 1
        record['material'] = material_map[chosen_material]
        far = int(np.count_nonzero(distance > far_fraction * extent))
        counts = {'vertices': len(distance), 'output_vertices': len(old_index), 'faces': len(faces),
                  'straddling_faces': int(len(straddlers)), 'extrapolated_corners': extrapolated, 'far_vertices': far}
        for key, value in counts.items():
            totals[key] += value
        distances_all.append(distance); excursions_all.append(excursion)
        per_primitive.append({'node_index': target['node'], 'mesh_index': target['mesh'], 'primitive_index': target['primitive'], **counts,
                              'median_distance_units': float(np.median(distance)), 'p95_distance_units': float(np.quantile(distance, .95)),
                              'dropped_color_accessor': removed, 'material_index': record['material']})
    all_distances, all_excursions = np.concatenate(distances_all), np.concatenate(excursions_all)
    split_doc['bufferViews'], split_doc['accessors'] = views, accessors
    if images:
        split_doc['images'] = images
    split_doc['buffers'][0]['byteLength'] = len(binary)
    receipt = {'schema_version': 1, 'method': METHOD, 'split_sha256': _sha(split_raw), 'generation_sha256': _sha(generation_raw),
               'generation_extent_units': extent, 'far_fraction': far_fraction, **totals, 'uv_islands': island_count,
               'median_distance_units': float(np.median(all_distances)), 'p95_distance_units': float(np.quantile(all_distances, .95)),
               'maximum_distance_units': float(all_distances.max()),
               'barycentric_excursion': ({'p95': float(np.quantile(all_excursions, .95)), 'maximum': float(all_excursions.max())}
                                         if len(all_excursions) else None),
               'uv_weld_decimals': UV_WELD_DECIMALS, 'primitives': per_primitive,
               'copied': {'images': len(image_map), 'textures': len(texture_map), 'materials': len(material_map)},
               'vertex_colors': 'split-provider colors replaced by generation colors when present; otherwise removed to avoid multiplying unrelated bakes',
               'generation_vertex_colors': 'interpolated alongside transferred UVs' if has_generation_colors else 'absent',
               'lookup': 'Open3D float32 raycasting scene closest point; not exact source-surface arithmetic',
               'accepted': False, 'quality_verdict': 'unmeasured',
               'limitations': ['Interior and cut faces of closed parts have no outer-surface counterpart and take the nearest surface UV.',
                               'A face straddling an island boundary samples one island and extrapolates the other corners a little past '
                               'its edge into the atlas padding; the excursion is bounded by the face size and is recorded here.',
                               'Corners of one source vertex with differing coordinates become separate output vertices at one position.',
                               'The transferred texture is the provider\'s photographic bake, not a measured material.']}
    split_doc.setdefault('extras', {})['lensesSurfaceTransfer'] = {k: receipt[k] for k in ('method', 'generation_sha256', 'median_distance_units',
                                                                                             'far_vertices', 'vertices', 'output_vertices', 'straddling_faces')}
    output = _pack(split_doc, binary)
    receipt['output_sha256'] = _sha(output)
    return output, receipt


def run_surface_transfer_stage(split_model, generation_model, output) -> dict:
    """Write ``transferred.glb`` and ``report.json``; ``generation_model`` may be None (nothing applied)."""
    from pathlib import Path
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    split_raw = Path(split_model).read_bytes()
    report = {'schema_version': 1, 'method': METHOD, 'split': {'path': str(Path(split_model).resolve()), 'sha256': _sha(split_raw)},
              'accepted': False, 'quality_verdict': 'unmeasured'}
    if generation_model is None:
        report.update(status='not_applicable', model=None, reason='No retained textured generation beside this model.')
    else:
        generation_raw = Path(generation_model).read_bytes()
        transferred, receipt = transfer_surface(split_raw, generation_raw)
        (output / 'transferred.glb').write_bytes(transferred)
        report.update(status='transferred', generation={'path': str(Path(generation_model).resolve()), 'sha256': _sha(generation_raw)},
                      model={'path': 'transferred.glb', 'sha256': receipt['output_sha256'], 'bytes': len(transferred)}, receipt=receipt)
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report
