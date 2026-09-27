"""Pinned pre-fit composition diagnostics; no articulation or material inference.

Candidate rear support is compared with unlabeled photographic structure. A
temple label, a stable SAM mask or a dark pixel does not establish a hinge,
articulation state or an optical coefficient. No source geometry is modified.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image
from scipy import ndimage

from .camera import Camera
from .edge_correspondence import match_contour_edges
from .mesh import TriangleMesh, load_glb
from .raster import Raster, rasterize
from .region_proposals import _lens_parts, _native_grid


@dataclass(frozen=True)
class CompositionPolicy:
    maximum_photos: int = 32
    maximum_source_faces: int = 1_000_000
    maximum_native_pixels: int = 16_000_000
    maximum_boundary_probes: int = 160
    optical_interior_margin_working_px: int = 2
    edge_explanation_radius_working_px: float = 2.
    image_edge_sigma_native_px: float = 1.
    image_edge_minimum_rgb_rms: float = 8.
    minimum_unexplained_edge_pixels: int = 20
    residual_fraction_threshold: float = .5

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name in ('edge_explanation_radius_working_px', 'image_edge_sigma_native_px',
                        'image_edge_minimum_rgb_rms', 'residual_fraction_threshold'):
                if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                    raise ValueError(f'{name} must be finite and positive')
            elif type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.residual_fraction_threshold >= 1:
            raise ValueError('residual_fraction_threshold must lie in (0,1)')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _implementation():
    return {name: _sha(Path(__file__).parent/name) for name in
            ('composition_diagnostic.py', 'camera.py', 'mesh.py', 'raster.py', 'edge_correspondence.py', 'region_proposals.py')}


def _inside(base, name):
    path = (base / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(base.resolve()):
        raise ValueError('Artifact path escapes region evidence')
    return path


def _read_pin(path, pins, expected=None):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise ValueError(f'Input hash mismatch: {path}')
    pins[str(path.resolve())] = digest
    return raw


def _decode(raw, *, shape=None, mask=False):
    with Image.open(io.BytesIO(raw)) as image:
        if getattr(image, 'n_frames', 1) != 1 or image.getexif().get(274, 1) != 1:
            raise ValueError('Inputs require one normalized image frame')
        if not mask and (image.mode not in ('RGB', 'RGBA') or image.info.get('icc_profile')):
            raise ValueError('Photo requires normalized RGB/sRGB input')
        if shape is not None and image.size != tuple(shape[::-1]):
            raise ValueError('Evidence pixel grid mismatch')
        if mask:
            array = np.asarray(image.convert('L'))
            if not np.isin(array, [0, 255]).all():
                raise ValueError('Region masks must be binary')
            return array > 0
        rgba = np.asarray(image.convert('RGBA'))
        if not np.all(rgba[..., 3] == 255):
            raise ValueError('Photo transparency is unsupported')
        return rgba[..., :3].copy()


def _boundary(mask):
    result = mask & ~ndimage.binary_erosion(mask)
    result[[0, -1], :] = False
    result[:, [0, -1]] = False
    return result


def _empty(shape):
    return Raster(np.full(shape, -1, np.int32), np.zeros((*shape, 3)), np.full(shape, np.inf))


def project_composition(mesh, camera, shape, normalization, *, view_scene=None):
    """Nearest optical event and nearest opaque hit, without volume counting.

    All optical part identities remain candidate hypotheses. No grouping of
    material layers is required to ask whether ANY optical event precedes the
    first opaque surface. Opaque surfaces inside closed lens geometry are kept.
    """
    articulation = None
    if view_scene is not None:
        from .view_scene import pose_mesh_from_contract
        posed = pose_mesh_from_contract(mesh, view_scene['contract'], view_scene['view_id'],
                                       photo_sha256=view_scene.get('source_image_sha256'))
        mesh, articulation = posed['mesh'], posed['report']
    selected, ledger = _lens_parts(mesh)
    center = np.asarray(normalization['center'], float)
    extent = normalization['extent']
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
        raise ValueError('Invalid camera normalization')
    normalized = TriangleMesh((mesh.vertices-center)/extent, mesh.faces, mesh.parts)
    face_part = np.full(len(mesh.faces), -1, np.int64)
    for index, part in enumerate(mesh.parts):
        if np.any(face_part[part['face_start']:part['face_start']+part['face_count']] >= 0):
            raise ValueError('Overlapping source primitive face ranges')
        face_part[part['face_start']:part['face_start']+part['face_count']] = index
    if np.any(face_part < 0):
        raise ValueError('Source primitive face ranges are incomplete')
    optical_faces = np.isin(face_part, selected)

    def render(selection):
        ids = np.flatnonzero(selection)
        return (rasterize(TriangleMesh(normalized.vertices, normalized.faces[ids], []), camera, shape)
                if len(ids) else _empty(shape)), ids

    optical, _ = render(optical_faces)
    opaque, opaque_ids = render(~optical_faces)
    hit_face = np.full(shape, -1, int)
    hit_face[opaque.mask] = opaque_ids[opaque.face_index[opaque.mask]]
    behind = optical.mask & opaque.mask & (optical.depth + 1e-9 < opaque.depth)
    tie = optical.mask & opaque.mask & (np.abs(np.where(optical.mask, optical.depth, 0.)-
                                             np.where(opaque.mask, opaque.depth, 0.)) <= 1e-9)
    front = opaque.mask & (~optical.mask | (opaque.depth < optical.depth-1e-9))
    named = [i for i, p in enumerate(mesh.parts) if re.search(r'(^|[^a-z])(temple|earpiece|hinge)([^a-z]|$)',
              f"{p['name']} {p['material']}".lower()) and i not in selected]
    named_mask = opaque.mask & np.isin(face_part[np.maximum(hit_face, 0)], named)
    posterior = np.zeros(shape, bool)
    plane = None
    if selected:
        plane = float(mesh.vertices[np.unique(mesh.faces[optical_faces]), 2].min())
        centroids = mesh.vertices[mesh.faces].mean(axis=1)
        posterior = opaque.mask & (centroids[np.maximum(hit_face, 0), 2] < plane)
    visible_optical = optical.mask & ~front & ~tie
    return {'optical': optical.mask, 'visible_optical': visible_optical, 'opaque': opaque.mask, 'opaque_in_front': front,
            'opaque_behind_optical': behind, 'depth_tie': tie, 'named_rear': named_mask,
            'posterior_depth_proxy': posterior, 'posterior_plane_source_z': plane,
            'parts': ledger, 'named_rear_parts': named, 'optical_parts': selected, 'articulation': articulation}


def _edge_matches(rgb, mask, policy):
    rows, cols = np.nonzero(_boundary(mask))
    if not len(rows):
        return {'status': 'no_projected_boundary', 'probes': 0, 'correspondences': None}
    picks = np.linspace(0, len(rows)-1, min(len(rows), policy.maximum_boundary_probes), dtype=int)
    rows, cols = rows[picks], cols[picks]
    signed = ndimage.distance_transform_edt(~mask)-ndimage.distance_transform_edt(mask)
    gy, gx = np.gradient(ndimage.gaussian_filter(signed, 1.))
    normals = np.column_stack([gx[rows, cols], gy[rows, cols]])
    norm = np.linalg.norm(normals, axis=1)
    keep = norm > 1e-12
    normals, rows, cols = normals[keep]/norm[keep, None], rows[keep], cols[keep]
    if not len(rows):
        return {'status': 'undefined_boundary_normals', 'probes': 0, 'correspondences': None}
    sy, sx = np.asarray(rgb.shape[:2])/mask.shape
    xy = np.column_stack([(cols+.5)*sx-.5, (rows+.5)*sy-.5])
    normals /= [sx, sy]
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    matches = match_contour_edges(rgb, xy, normals)
    selected = matches.selected_mask
    distances = np.linalg.norm(matches.matched_xy[selected]-xy[selected], axis=1)
    return {'status': 'unlabeled_candidate_boundary_comparison', 'probes': len(rows),
            'statuses': {status: matches.status.count(status) for status in sorted(set(matches.status))},
            'selected_offset_native_px': {'p50': float(np.median(distances)), 'p95': float(np.quantile(distances, .95))}
                if len(distances) else None,
            'sampling_uncertainty_native_px': float(np.hypot(sx, sy)/2),
            'correspondences': matches.to_report()}


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run_composition_diagnostic(model: Path, region_report: Path, output: Path, *, policy=CompositionPolicy()):
    model, region_report, output = Path(model).resolve(), Path(region_report).resolve(), Path(output).resolve()
    if not isinstance(policy, CompositionPolicy):
        raise TypeError('policy must be CompositionPolicy')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output must be an empty directory')
    implementation = _implementation()
    pins = {}
    region_raw = _read_pin(region_report, pins)
    region = json.loads(region_raw)
    _read_pin(model, pins, region.get('candidate_sha256'))
    model_hash = pins[str(model)]
    if region.get('candidate_sha256') != model_hash:
        raise ValueError('Region evidence requires an exact candidate binding')
    mesh = load_glb(model)
    if _sha(model) != model_hash:
        raise ValueError('Model changed while loading')
    if len(mesh.faces) > policy.maximum_source_faces:
        raise ValueError('Source triangle capacity exceeded')
    photos = region.get('photos', [])
    if not 1 <= len(photos) <= policy.maximum_photos:
        raise ValueError('Unsupported photograph count')
    ids, photo_hashes, prepared, skipped_views = set(), set(), [], []
    for photo in photos:
        identity = photo['id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', identity) or identity.casefold() in ids:
            raise ValueError('Photos require unique safe IDs')
        ids.add(identity.casefold())
        source = Path(photo['source'])
        source = source.resolve() if source.is_absolute() else (region_report.parent/source).resolve()
        raw = _read_pin(source, pins, photo['source_sha256'])
        if photo['source_sha256'] in photo_hashes:
            raise ValueError('Duplicate photograph bytes do not provide independent views')
        photo_hashes.add(photo['source_sha256'])
        with Image.open(io.BytesIO(raw)) as image:
            if not np.all(np.asarray(image.convert('RGBA'))[..., 3] == 255):
                skipped_views.append({'id': identity, 'status': 'alpha_geometry_only_no_observed_background',
                                      'photo_sha256': photo['source_sha256']})
                continue
        if photo.get('candidate_projection', {}).get('status') == 'no_fitted_camera_for_photo':
            skipped_views.append({'id': identity, 'status': 'no_fitted_camera', 'photo_sha256': photo['source_sha256']})
            continue
        rgb = _decode(raw)
        if rgb.shape[0]*rgb.shape[1] > policy.maximum_native_pixels or photo.get('image_size') != list(rgb.shape[1::-1]):
            raise ValueError('Photo pixel grid or capacity mismatch')
        projection = photo.get('candidate_projection', {})
        if projection.get('candidate_sha256') != model_hash or projection.get('camera_sha256') != _digest(projection.get('camera')):
            raise ValueError('Camera/model binding mismatch')
        size = projection.get('working_size')
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 2 <= v <= 768 for v in size):
            raise ValueError('Unsupported camera working grid')
        camera = Camera(**projection['camera'])
        if not np.isfinite(list(camera.to_dict().values())).all() or camera.scale <= 0 or camera.perspective < 0:
            raise ValueError('Invalid camera')
        masks = []
        for candidate in photo.get('regions', []):
            base = _inside(region_report.parent, identity+'/'+candidate['directory'])
            entries = [('decoder', variant, alt['decoder_index'], alt['mask'])
                       for variant, alternatives in enumerate(candidate.get('alternatives', [])) for alt in alternatives]
            entries += [(kind, hypothesis['index'], None, hypothesis[kind])
                        for hypothesis in candidate.get('hypotheses', []) for kind in ('intersection', 'union')]
            for kind, variant, alternative, artifact in entries:
                path = _inside(base, artifact['path'])
                mask = _decode(_read_pin(path, pins, artifact['sha256']), shape=rgb.shape[:2], mask=True)
                masks.append(({'region': candidate['id'], 'kind': kind, 'variant_or_hypothesis': variant,
                               'alternative': alternative, 'path': str(path), 'sha256': artifact['sha256']}, mask))
        prepared.append((photo, rgb, camera, tuple(size[::-1]), masks))

    output.mkdir(parents=True, exist_ok=True)
    (output/'region-report.json').write_bytes(region_raw)
    report = {'schema_version': 1, 'method': 'candidate_composition_preflight_v1', 'status': 'diagnostic_complete',
              'accepted': False, 'quality_verdict': 'unmeasured', 'articulation_identification': 'unmeasured',
              'source_model': str(model), 'source_model_sha256': model_hash,
              'source_region_report': str(region_report), 'source_region_report_sha256': pins[str(region_report)],
              'policy': asdict(policy), 'views': skipped_views, 'source_pins': pins,
              'limitations': ['Candidate labels and posterior depth are hypotheses, not verified temple identity.',
                  'Supplied automatic articulation remains a geometric/photo-edge hypothesis, not verified mechanical identity.',
                  'Unexplained optical-region edges may be frame, texture, reflection, shadow, camera error or geometry mismatch.',
                  'A stable SAM mask or selected image edge does not establish semantic identity.',
                  'Candidate cameras were fitted from these photographs; this is not held-out or calibrated quality evidence.',
                  'The posterior proxy assumes the existing AR authored +Z front direction; it is not a physical hinge plane.',
                  'No optical material, source geometry, camera or original evidence is modified.']}
    for photo, rgb, camera, shape, masks in prepared:
        projection = photo['candidate_projection']
        fields = project_composition(mesh, camera, shape, projection['normalization'], view_scene=projection.get('view_scene'))
        folder = output/photo['id']
        folder.mkdir()
        mask_receipts = {}
        for name in ('optical', 'visible_optical', 'opaque_in_front', 'opaque_behind_optical', 'depth_tie', 'named_rear', 'posterior_depth_proxy'):
            path = folder/(name+'.png')
            Image.fromarray(fields[name].astype(np.uint8)*255).save(path)
            mask_receipts[name] = {'path': path.relative_to(output).as_posix(), 'sha256': _sha(path), 'pixels': int(fields[name].sum())}
        native_optical = _native_grid(ndimage.binary_erosion(fields['visible_optical'], iterations=policy.optical_interior_margin_working_px), rgb.shape[:2])
        gradient = []
        for channel in range(3):
            gy, gx = np.gradient(ndimage.gaussian_filter(rgb[..., channel].astype(float), policy.image_edge_sigma_native_px))
            gradient.append(gx*gx+gy*gy)
        observed = np.sqrt(np.mean(gradient, axis=0)) >= policy.image_edge_minimum_rgb_rms
        edges = observed & native_optical
        boundary = _boundary(fields['opaque_behind_optical']) | _boundary(fields['opaque_in_front'])
        explained = ndimage.distance_transform_edt(~boundary) <= policy.edge_explanation_radius_working_px if boundary.any() else np.zeros(shape, bool)
        unexplained = edges & ~_native_grid(explained, rgb.shape[:2])
        total, residual = int(edges.sum()), int(unexplained.sum())
        fraction = residual/total if total else None
        status = ('insufficient_evidence' if not native_optical.any() or total < policy.minimum_unexplained_edge_pixels else
                  'unexplained_image_structure' if residual >= policy.minimum_unexplained_edge_pixels and fraction > policy.residual_fraction_threshold else
                  'candidate_compatible_residuals')
        comparisons = []
        native_rear = _native_grid(fields['opaque_behind_optical'], rgb.shape[:2])
        for metadata, mask in masks:
            comparisons.append({**metadata, 'mask_pixels': int(mask.sum()), 'opaque_behind_optical_intersection_pixels': int((mask&native_rear).sum()),
                                'unexplained_edge_intersection_pixels': int((mask&unexplained).sum()), 'semantic_identity': 'unverified'})
        matches = {name: _edge_matches(rgb, fields[name], policy) for name in ('named_rear', 'posterior_depth_proxy')}
        _write(folder/'edge-correspondences.json', matches)
        overlay = rgb.astype(float)
        for mask, color in [(_native_grid(_boundary(fields['optical']), rgb.shape[:2]), [255, 160, 0]),
                            (_native_grid(_boundary(fields['posterior_depth_proxy']), rgb.shape[:2]), [0, 170, 255]),
                            (unexplained, [255, 0, 170])]:
            overlay[mask] = .25*overlay[mask]+.75*np.asarray(color)
        Image.fromarray(overlay.astype(np.uint8)).save(folder/'overlay.png')
        path = folder/'unexplained-optical-edges.png'
        Image.fromarray(unexplained.astype(np.uint8)*255).save(path)
        report['views'].append({'id': photo['id'], 'status': status, 'photo_sha256': photo['source_sha256'],
            'camera_sha256': projection['camera_sha256'], 'camera': camera.to_dict(), 'normalization': projection['normalization'],
            'working_size': projection['working_size'], 'native_size': list(rgb.shape[1::-1]), 'masks': mask_receipts,
            'parts': fields['parts'], 'named_rear_parts': fields['named_rear_parts'], 'optical_parts': fields['optical_parts'],
            'posterior_proxy_plane_source_z': fields['posterior_plane_source_z'],
            'pivot_status': 'automatic_geometric_hypothesis' if fields['articulation'] else 'unverified_not_supplied',
            'rigid_arm_partition_status': 'source_bound_automatic_hypothesis' if fields['articulation'] else 'unverified_not_supplied',
            'articulation': fields['articulation'],
            'optical_region_edge_pixels': total, 'unexplained_optical_region_edge_pixels': residual, 'unexplained_edge_fraction': fraction,
            'all_region_alternatives': comparisons, 'selected_region_alternative': None,
            'edge_correspondences': {'path': (folder/'edge-correspondences.json').relative_to(output).as_posix(), 'sha256': _sha(folder/'edge-correspondences.json')},
            'unexplained_edges': {'path': path.relative_to(output).as_posix(), 'sha256': _sha(path)},
            'overlay': {'path': (folder/'overlay.png').relative_to(output).as_posix(), 'sha256': _sha(folder/'overlay.png'),
                        'legend': {'orange': 'candidate optical boundary', 'blue': 'candidate posterior depth-proxy boundary', 'magenta': 'unexplained unlabeled optical-region edge'}}})
    if any(_sha(path) != digest for path, digest in pins.items()):
        raise ValueError('Input evidence changed during diagnostic')
    report['source_snapshot_stable'] = True
    if _implementation() != implementation:
        raise ValueError('Implementation changed during diagnostic')
    report['implementation'] = implementation
    _write(output/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run_composition_diagnostic(args.model, args.regions, args.output)
    print(json.dumps({'status': report['status'], 'accepted': False, 'views': [{'id': v['id'], 'status': v['status']} for v in report['views']]}))


if __name__ == '__main__':
    main()
