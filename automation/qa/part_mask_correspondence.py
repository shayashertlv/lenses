"""Project render-space mask evidence onto original Tripo face IDs, without edits.

Silhouette agreement checks the declared renderer camera/normalization. Pixel hits
are visible-surface evidence only: unseen faces receive no semantic assignment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image
from scipy import ndimage

from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh, load_glb_bytes
from reconstruction.raster import rasterize

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'data/provider-comparison-v1/render-final/report.json'
MASK_ROOT = ROOT / 'data/render-mask-probes-v1'
OUTPUT = ROOT / 'data/part-mask-correspondence-v1'
DIRECTIONS = {'front': [0, 0, 1], 'angled': [.68, .22, 1]}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def camera_for(direction, width, height, vertical_span):
    direction = np.asarray(direction, float)
    direction /= np.linalg.norm(direction)
    return Camera(yaw=math.degrees(math.atan2(direction[0], direction[2])),
                  pitch=math.degrees(math.asin(direction[1])), roll=0, perspective=0,
                  scale=height / vertical_span, center_x=(width-1)/2, center_y=(height-1)/2)


def normalize(vertices, normalization):
    if normalization['rotation_order'] != 'XYZ' or normalization['rotation_degrees'] != [0, -90, 0]:
        raise ValueError('This frozen probe only supports the declared Tripo Y -90 orientation')
    angle = -math.pi / 2
    rotation = np.array([[math.cos(angle), 0, math.sin(angle)], [0, 1, 0],
                         [-math.sin(angle), 0, math.cos(angle)]])
    return (vertices @ rotation.T + np.asarray(normalization['translation'])) * normalization['uniform_scale']


def raster_source(mesh, camera, shape, exclude_face_mask=None):
    """Match raw Tripo FrontSide culling and return original, unfiltered face IDs."""
    yaw, pitch = np.radians([camera.yaw, camera.pitch])
    toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
    keep = np.empty(len(mesh.faces), bool)
    for begin in range(0, len(mesh.faces), 100_000):
        triangles = mesh.vertices[mesh.faces[begin:begin+100_000]]
        keep[begin:begin+len(triangles)] = np.cross(triangles[:, 1]-triangles[:, 0],
                                                triangles[:, 2]-triangles[:, 0]) @ toward > 0
    if exclude_face_mask is not None:
        if exclude_face_mask.shape != keep.shape:
            raise ValueError('Excluded face mask size differs from source')
        keep &= ~exclude_face_mask
    source_ids = np.flatnonzero(keep)
    result = rasterize(TriangleMesh(mesh.vertices, mesh.faces[source_ids], []), camera, shape,
                       max_candidates=250_000)
    face_ids = result.face_index.copy()
    occupied = face_ids >= 0
    face_ids[occupied] = source_ids[face_ids[occupied]]
    return face_ids, result.depth


def mask_hits(face_ids, mask):
    if mask.shape != face_ids.shape:
        raise ValueError('Mask size differs from frozen renderer')
    visible, visible_counts = np.unique(face_ids[face_ids >= 0], return_counts=True)
    ids, counts = np.unique(face_ids[mask & (face_ids >= 0)], return_counts=True)
    return visible, visible_counts, ids, counts


def original_part_labels(audit_path, original_sha, face_count):
    audit = read(audit_path)
    if audit['original']['sha256'] != original_sha or audit['original']['face_count'] != face_count:
        raise ValueError('Part audit is for another original asset')
    bounded_path = audit_path.parent / 'bounded-correspondence.json'
    if bounded_path.exists():
        bounded = read(bounded_path)
        if bounded['original_sha256'] != original_sha or bounded['candidate_sha256'] != audit['candidate']['sha256']:
            raise ValueError('Bounded correspondence is for another asset')
        if bounded['complete_bijection'] and bounded['original_faces'] == face_count:
            labels = np.load(audit_path.parent / bounded['original_part_labels_file'])
            if labels.shape != (face_count,) or np.any(labels < 0):
                raise ValueError('Bounded bijection contains missing face labels')
            audit['correspondence_used'] = dict(kind='bounded_bijection',
                maximum_corner_error_world=bounded['maximum_corner_error_world'],
                sha256=sha(bounded_path.read_bytes()), exact=False)
            return audit, labels
    audit['correspondence_used'] = dict(kind='partial_bit_exact', exact=True)
    mapping = np.load(audit_path.parent / 'candidate-to-original-face.npy', mmap_mode='r')
    labels = np.full(face_count, -1, np.int32)
    for part in audit['candidate']['parts']:
        matches = np.asarray(mapping[part['face_start']:part['face_start']+part['face_count']])
        matches = np.unique(matches[matches >= 0])
        prior = labels[matches]
        conflict = (prior >= 0) & (prior != part['index']) | (prior == -2)
        labels[matches] = np.where(conflict, -2, part['index'])
    return audit, labels


def score_parts(face_ids, mask, labels, parts):
    pixel_parts = np.full(face_ids.shape, -1, np.int32)
    hit = face_ids >= 0
    pixel_parts[hit] = labels[face_ids[hit]]
    known = pixel_parts >= 0
    rows = []
    for part in parts:
        region = pixel_parts == part['index']
        intersection = int((region & mask).sum())
        visible = int(region.sum())
        rows.append(dict(part_id=part['id'], part_index=part['index'],
                         matched_visible_pixels=visible, mask_intersection_pixels=intersection,
                         precision_on_matched_pixels=intersection/visible if visible else None,
                         mask_recall_all_pixels=intersection/int(mask.sum()) if mask.any() else None))
    return dict(mask_pixels=int(mask.sum()), mapped_mask_pixels=int((known & mask).sum()),
                mapped_mask_fraction=float((known & mask).sum()/mask.sum()) if mask.any() else None,
                mapped_visible_fraction=float(known.sum()/hit.sum()) if hit.any() else None,
                parts=rows, selected_parts=[],
                verdict='visible_evidence_only; whole_part_selection_requires_multiview_purity_and_hidden_face_checks')


def run(output=OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    report_raw = REPORT.read_bytes()
    report = json.loads(report_raw)
    width, height = report['renderer']['width'], report['renderer']['height']
    records = []
    for product in ('oakley', 'miu'):
        case = next(c for c in report['cases'] if c['id'] == product+'-tripo')
        if any(m['side'] != 0 for m in case['materials']):
            raise ValueError('Raw material culling contract changed')
        path = ROOT / 'data/provider-comparison-v1/runs' / (product+'-tripo') / 'artifacts/model.glb'
        raw = path.read_bytes()
        if sha(raw) != case['model_sha256']:
            raise ValueError('Model differs from rendered source')
        mesh = load_glb_bytes(raw)
        original_face_count = len(mesh.faces)
        mesh.vertices = normalize(mesh.vertices, case['normalization'])
        proposals = []
        for operation in ('segment-auto', 'native-parts', 'segment-guided'):
            audit_path = ROOT / 'data/part-probes-v1/audits' / f'{product}-{operation}' / 'audit.json'
            if audit_path.exists():
                audit, labels = original_part_labels(audit_path, case['model_sha256'], original_face_count)
                proposals.append((f'{product}-{operation}', audit_path, audit, labels))
        for view, direction in DIRECTIONS.items():
            started = time.time()
            name = product+'-'+view
            destination = output / name
            destination.mkdir(exist_ok=True)
            mask_request = read(MASK_ROOT / name / 'request.json')
            image_path = Path(mask_request['image']['path'])
            if sha(image_path.read_bytes()) != mask_request['image']['sha256'] or mask_request['model_sha256'] != case['model_sha256']:
                raise ValueError('Mask evidence and source renderer differ')
            if mask_request['render_report']['sha256'] != sha(report_raw):
                raise ValueError('Renderer report changed since mask submission')
            camera = camera_for(direction, width, height, case['orthographic_vertical_span'])
            face_ids, depth = raster_source(mesh, camera, (height, width))
            np.save(destination / 'face-index.npy', face_ids)
            np.save(destination / 'depth.npy', depth)
            silhouette = face_ids >= 0
            Image.fromarray(silhouette.astype(np.uint8)*255).save(destination / 'silhouette.png')
            normal_row = next(r for r in case['renders'] if r['mode']=='normals' and r['background']=='light' and r['view']==view)
            normal_path = REPORT.parent / normal_row['filename']
            if sha(normal_path.read_bytes()) != normal_row['sha256']:
                raise ValueError('Normal validation render changed')
            normals = np.asarray(Image.open(normal_path).convert('RGB'))
            background = normals[0, 0].astype(int)
            gpu = np.max(np.abs(normals.astype(int)-background), axis=2) > 3
            union, intersection = int((gpu | silhouette).sum()), int((gpu & silhouette).sum())
            iou = intersection / union if union else 1.
            boundary = silhouette ^ ndimage.binary_erosion(silhouette)
            overlay = np.array(Image.open(image_path).convert('RGB'))
            overlay[boundary] = [0, 255, 255]
            overlay[gpu & ~ndimage.binary_dilation(silhouette, iterations=2)] = [255, 0, 255]
            Image.fromarray(overlay).save(destination / 'silhouette-overlay.png')
            alignment = dict(iou=iou, cpu_pixels=int(silhouette.sum()), gpu_foreground_pixels=int(gpu.sum()),
                gpu_outside_cpu_two_pixel_tolerance=int((gpu & ~ndimage.binary_dilation(silhouette, iterations=2)).sum()),
                cpu_outside_gpu_two_pixel_tolerance=int((silhouette & ~ndimage.binary_dilation(gpu, iterations=2)).sum()),
                validation='Normal render background difference; WebGL antialiasing differs from CPU pixel centers')
            if iou < .97:
                write(destination / 'alignment-failed.json', alignment)
                raise ValueError(f'{name} silhouette alignment failed: {iou}')
            record = dict(case=name, model_sha256=case['model_sha256'], render_sha256=mask_request['image']['sha256'],
                render_report_sha256=sha(report_raw), original_face_count=original_face_count, camera=camera.to_dict(),
                normalization=case['normalization'], face_index_path=str((destination/'face-index.npy').resolve()),
                silhouette_overlay=str((destination/'silhouette-overlay.png').resolve()), alignment=alignment, masks=[])
            visible, visible_counts = np.unique(face_ids[silhouette], return_counts=True)
            np.savez_compressed(destination/'visible-faces.npz', face_ids=visible, pixel_counts=visible_counts)
            artifacts = read(MASK_ROOT / name / 'artifacts.json')
            for mask_item in artifacts['masks']:
                mask_path = Path(mask_item['path'])
                if sha(mask_path.read_bytes()) != mask_item['sha256']:
                    raise ValueError('Provider mask changed')
                mask = np.asarray(Image.open(mask_path).convert('L')) > 127
                _, _, ids, counts = mask_hits(face_ids, mask)
                evidence = destination / f"mask-{mask_item['index']:02d}-faces.npz"
                np.savez_compressed(evidence, face_ids=ids, pixel_counts=counts)
                row = dict(index=mask_item['index'], mask_sha256=mask_item['sha256'], mask_pixels=int(mask.sum()),
                           mesh_hit_pixels=int((mask & silhouette).sum()), background_pixels=int((mask & ~silhouette).sum()),
                           hit_faces=len(ids), evidence_path=str(evidence.resolve()), proposal_scores={})
                for probe_name, audit_path, audit, labels in proposals:
                    row['proposal_scores'][probe_name] = score_parts(face_ids, mask, labels, audit['candidate']['parts'])
                    row['proposal_scores'][probe_name]['audit_sha256'] = sha(audit_path.read_bytes())
                    row['proposal_scores'][probe_name]['correspondence'] = audit['correspondence_used']
                record['masks'].append(row)
            record['visible_faces'] = len(visible)
            record['source_faces_unseen_fraction'] = 1-len(visible)/original_face_count
            record['elapsed_seconds'] = round(time.time()-started, 3)
            record['production_accepted'] = False
            write(destination/'report.json', record)
            records.append(record)
            write(output/'report.json', dict(schema_version=1, cases=records,
                policy='Visible pixel evidence only; source face IDs preserved; no unseen face labels or production changes'))
            print(json.dumps(dict(case=name, alignment_iou=iou, visible_faces=len(visible), masks=len(record['masks']))), flush=True)
    return records


def hidden_diagnostic(output=OUTPUT):
    """Measure geometry exposed by making manually inspected auto parts 0/1 optical.

    This is an explicitly pinned intervention, not an automatic semantic label.
    It explains why a part absent from the opaque-view mask can become visible.
    """
    report = read(REPORT)
    records = []
    for product in ('oakley', 'miu'):
        source = next(c for c in report['cases'] if c['id'] == product+'-tripo')
        path = ROOT/'data/provider-comparison-v1/runs'/f'{product}-tripo'/'artifacts/model.glb'
        raw = path.read_bytes()
        if sha(raw) != source['model_sha256']:
            raise ValueError('Source model changed')
        mesh = load_glb_bytes(raw)
        mesh.vertices = normalize(mesh.vertices, source['normalization'])
        audit_path = ROOT/'data/part-probes-v1/audits'/f'{product}-segment-auto'/'audit.json'
        audit, labels = original_part_labels(audit_path, source['model_sha256'], len(mesh.faces))
        if audit['correspondence_used']['kind'] != 'bounded_bijection':
            raise ValueError('Hidden diagnostic requires verified complete mapping')
        for view, direction in DIRECTIONS.items():
            name = product+'-'+view
            artifacts = read(MASK_ROOT/name/'artifacts.json')
            if not artifacts['masks']:
                continue
            camera = camera_for(direction, report['renderer']['width'], report['renderer']['height'],
                                source['orthographic_vertical_span'])
            face_ids, depth = raster_source(mesh, camera, (480, 640), np.isin(labels, [0, 1]))
            mask = np.zeros(face_ids.shape, bool)
            for item in artifacts['masks']:
                raw_mask = Path(item['path']).read_bytes()
                if sha(raw_mask) != item['sha256']:
                    raise ValueError('Mask changed')
                mask |= np.asarray(Image.open(Path(item['path'])).convert('L')) > 127
            scores = score_parts(face_ids, mask, labels, audit['candidate']['parts'])
            destination = output/name
            np.save(destination/'without-auto-parts-0-1-face-index.npy', face_ids)
            np.save(destination/'without-auto-parts-0-1-depth.npy', depth)
            front_depth = np.load(destination/'depth.npy')
            for part in scores['parts']:
                points = (face_ids >= 0) & mask
                points[points] &= labels[face_ids[points]] == part['part_index']
                gap = depth[points]-front_depth[points]
                gap = gap[np.isfinite(gap)]
                part['normalized_depth_gap_from_opaque_surface_p05_p50_p95'] = (
                    np.quantile(gap, [.05, .5, .95]).tolist() if len(gap) else None)
                part['depth_units'] = 'Normalized source coordinates where full model width is 2'
            request = read(MASK_ROOT/name/'request.json')
            overlay = np.array(Image.open(request['image']['path']).convert('RGB'))
            palette = np.array([[230, 60, 70], [55, 200, 80], [50, 100, 245], [240, 180, 20],
                                [150, 50, 220], [20, 200, 210], [240, 110, 30]])
            hit = (face_ids >= 0) & mask
            overlay[hit] = palette[labels[face_ids[hit]] % len(palette)]
            Image.fromarray(overlay).save(destination/'without-auto-parts-0-1-overlay.png')
            row = dict(case=name, excluded_parts=[0, 1], exclusion_reason='Root visually identified auto lens proposals',
                       correspondence=audit['correspondence_used'], scores=scores,
                       revealed_parts_in_mask=[r for r in scores['parts'] if r['mask_intersection_pixels'] > 0],
                       semantic_conclusion='None: projected rear hardware and omitted optical geometry both appear here')
            records.append(row)
    write(output/'hidden-diagnostic.json', dict(cases=records,
          policy='Diagnostic exclusion only. No source geometry/material edits or unseen semantic assignments.'))
    return records


def propose_visible_parts(output=OUTPUT):
    """Rank whole-part hypotheses from reliable visible mask-union evidence.

    A proposed role is not propagated as accepted truth onto unobserved faces.
    Empty provider output is missing evidence, never an all-negative mask.
    """
    report = read(output/'report.json')
    records = []
    policy = dict(minimum_visible_pixels_per_view=50, minimum_mask_hits_all_views=100,
                  minimum_per_view_purity=.8, purpose='Exploratory visible-role hypotheses, not production acceptance')
    for product in ('oakley', 'miu'):
        cases = [c for c in report['cases'] if c['case'].startswith(product+'-')]
        for operation in ('segment-auto', 'segment-guided'):
            audit_path = ROOT/'data/part-probes-v1/audits'/f'{product}-{operation}'/'audit.json'
            if not audit_path.exists():
                continue
            audit, labels = original_part_labels(audit_path, cases[0]['model_sha256'], cases[0]['original_face_count'])
            if audit['correspondence_used']['kind'] != 'bounded_bijection':
                continue
            scores = []
            for case in cases:
                artifacts = read(MASK_ROOT/case['case']/'artifacts.json')
                if not artifacts['masks']:
                    continue
                face_ids = np.load(case['face_index_path'])
                union = np.zeros(face_ids.shape, bool)
                for item in artifacts['masks']:
                    raw = Path(item['path']).read_bytes()
                    if sha(raw) != item['sha256']:
                        raise ValueError('Provider mask changed')
                    union |= np.asarray(Image.open(Path(item['path'])).convert('L')) > 127
                scores.append(dict(case=case['case'], **score_parts(face_ids, union, labels, audit['candidate']['parts'])))
            rows = []
            for part in audit['candidate']['parts']:
                evidence = [s['parts'][part['index']] for s in scores]
                visible_views = [e for e in evidence if e['matched_visible_pixels'] >= policy['minimum_visible_pixels_per_view']]
                minimum_purity = min((e['precision_on_matched_pixels'] for e in visible_views), default=None)
                hits = sum(e['mask_intersection_pixels'] for e in evidence)
                candidate = bool(visible_views and minimum_purity >= policy['minimum_per_view_purity'] and
                                 hits >= policy['minimum_mask_hits_all_views'])
                rows.append(dict(part_index=part['index'], mask_hits_across_views=hits,
                                 reliable_view_count=len(visible_views), minimum_view_purity=minimum_purity,
                                 visible_role_hypothesis='optical_candidate' if candidate else
                                     'unobserved_or_unsupported' if not visible_views else 'not_selected',
                                 whole_part_semantics_accepted=False))
            selected = [p['part_index'] for p in rows if p['visible_role_hypothesis']=='optical_candidate']
            coverage = [dict(case=s['case'], mask_pixels=s['mask_pixels'],
                            selected_mask_pixels=sum(p['mask_intersection_pixels'] for p in s['parts'] if p['part_index'] in selected),
                            coverage_fraction=sum(p['mask_intersection_pixels'] for p in s['parts'] if p['part_index'] in selected)/s['mask_pixels'])
                        for s in scores]
            records.append(dict(case=product+'-'+operation, candidate_part_indices=selected,
                                correspondence=audit['correspondence_used'], parts=rows, coverage=coverage,
                                production_accepted=False, missing_evidence='Hidden surfaces and omitted interposed geometry; SAM boundary correctness'))
    write(output/'visible-part-hypotheses.json', dict(policy=policy, cases=records))
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--hidden-diagnostic', action='store_true')
    parser.add_argument('--propose-visible-parts', action='store_true')
    args = parser.parse_args()
    (hidden_diagnostic if args.hidden_diagnostic else propose_visible_parts if args.propose_visible_parts else run)(args.output)
