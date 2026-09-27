"""Pinned actual-AR observations for the canonical editing session.

The renderer is the product's TryOnRenderer. CPU part pictures are explicitly
opaque geometry evidence, never a substitute for its canonical optical response.
No inference/provider API is called here, and source assets are never edited.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .camera import Camera, project
from .mesh import TriangleMesh, load_glb_bytes
from .optical_group_asset import read_optical_group_candidate
from .raster import rasterize

ROOT = Path(__file__).resolve().parents[1]
AR = ROOT.parent / 'ar'
VIEWS = ({'id': 'front', 'yaw_degrees': 0}, {'id': 'angled', 'yaw_degrees': 35},
         {'id': 'angled-opposite', 'yaw_degrees': -35}, {'id': 'back', 'type': 'asset-back'},
         {'id': 'rolled', 'roll_degrees': 25})
ENVIRONMENTS = ({'id': 'room', 'preset': 'room', 'intensity': .8},
                {'id': 'broad', 'preset': 'broad_studio', 'intensity': .8},
                {'id': 'side', 'preset': 'side_studio', 'intensity': .8})
_SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_CAVEAT = 'Uncalibrated source photos. Synthetic AR pose and checker; lighting differs from the photo.'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _pin(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {'path': str(path), 'sha256': _sha(raw), 'bytes': len(raw)}


def _read_pin(value, label):
    if not isinstance(value, dict) or not isinstance(value.get('path'), (str, Path)):
        raise ValueError(f'{label} requires a path and SHA-256')
    if not isinstance(value.get('sha256'), str) or not _SHA.fullmatch(value['sha256']):
        raise ValueError(f'{label} requires a valid SHA-256')
    path = Path(value['path']).resolve()
    raw = path.read_bytes()
    if _sha(raw) != value['sha256']:
        raise ValueError(f'{label} bytes differ from their pin')
    return path, raw


def renderer_fingerprint() -> dict:
    """Fingerprint all runtime sources, observer math, QA and dependency locks."""
    files = list((AR / 'src').rglob('*'))
    files += [AR / 'qa' / name for name in ('provider-comparison.mjs', 'provider-comparison.html',
              'provider-comparison-ar.html', 'provider-comparison-lighting.mjs')]
    files += [AR / 'package.json', AR / 'package-lock.json', AR / 'public/models/canonical-face.json']
    files += [Path(__file__), ROOT / 'reconstruction/camera.py', ROOT / 'reconstruction/raster.py',
              ROOT / 'reconstruction/mesh.py', ROOT / 'reconstruction/optical_group_asset.py',
              ROOT / 'reconstruction/optical_group_runtime.py', ROOT / 'reconstruction/compact_glb.py']
    hashes = {str(p.relative_to(ROOT.parent)).replace('\\', '/'): _sha(p.read_bytes())
              for p in sorted(set(files)) if p.is_file()}
    required = [AR / 'package-lock.json', AR / 'public/models/canonical-face.json']
    if any(not p.is_file() for p in required):
        raise ValueError('Renderer lockfile or canonical face fixture is missing')
    node = shutil.which('node')
    if node is None:
        raise ValueError('Node.js is unavailable')
    version = subprocess.run([node, '--version'], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    import PIL
    import cv2
    import scipy
    runtime = {'node': version, 'python': sys.version, 'numpy': np.__version__, 'pillow': PIL.__version__,
               'opencv': cv2.__version__, 'scipy': scipy.__version__}
    payload = {'schema_version': 1, 'files': hashes, 'runtime': runtime}
    return {**payload, 'sha256': _sha(_json_bytes(payload))}


def _verify_optical(value, label):
    path, raw = _read_pin(value, label)
    receipt_path, receipt_raw = _read_pin(value.get('export'), label + ' export')
    receipt = json.loads(receipt_raw)
    loaded = read_optical_group_candidate(path, receipt, expected_sha256=_sha(raw))
    return {'path': str(path), 'sha256': _sha(raw), 'export': _pin(receipt_path)}, loaded['mesh']


def _run_ar(manifest, output):
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is unavailable')
    command = [node, str(AR / 'qa/provider-comparison.mjs'), f'--manifest={manifest}',
               f'--output={output}', '--stage=ar']
    result = subprocess.run(command, cwd=AR, capture_output=True, timeout=900)
    (output.parent / 'renderer.log').write_bytes(result.stdout + b'\n' + result.stderr)
    if result.returncode:
        raise ValueError('Actual AR observation failed; see renderer.log and retained renderer report')


def _font(size):
    for path in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _image_record(path, image_id, label, **extra):
    return {'id': image_id, 'label': label, **_pin(path), **extra}


def _matrix(value, label):
    a = np.asarray(value, dtype=float)
    if a.shape != (16,) or not np.isfinite(a).all():
        raise ValueError(f'Invalid {label} camera matrix')
    matrix = a.reshape((4, 4), order='F')
    if np.linalg.matrix_rank(matrix) != 4:
        raise ValueError(f'Singular {label} camera matrix')
    return matrix


def _project_bounds(bounds, render):
    """Canonical bounds through the exact reported AR camera, with no fitting."""
    lo, hi = np.asarray(bounds, dtype=float)
    points = np.array([[x, y, z, 1] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    camera = render['camera']
    matrix = (_matrix(camera['projection_matrix'], 'projection') @ _matrix(camera['view_matrix'], 'view')
              @ _matrix(render['spatial']['asset_to_world'], 'asset'))
    clip = points @ matrix.T
    if np.any(clip[:, 3] <= 0):
        raise ValueError('Focus crosses the AR camera plane')
    ndc = clip[:, :2] / clip[:, 3, None]
    pixels = np.column_stack(((ndc[:, 0] + 1) * camera['width'] / 2,
                              (1 - ndc[:, 1]) * camera['height'] / 2))
    return pixels.min(axis=0), pixels.max(axis=0)


def _crop(bounds, renders, padding=.08):
    projected = [_project_bounds(bounds, r) for r in renders]
    lo = np.min([p[0] for p in projected], axis=0)
    hi = np.max([p[1] for p in projected], axis=0)
    pad = np.maximum((hi - lo) * padding, 6)
    width, height = renders[0]['camera']['width'], renders[0]['camera']['height']
    box = [max(0, math.floor(lo[0] - pad[0])), max(0, math.floor(lo[1] - pad[1])),
           min(width, math.ceil(hi[0] + pad[0])), min(height, math.ceil(hi[1] + pad[1]))]
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('Requested region is outside the recorded AR camera')
    return box


def _put(sheet, path, box, crop=None):
    with Image.open(path) as source:
        source = source.convert('RGB')
        if crop is not None:
            source = source.crop(crop)
        image = ImageOps.contain(source, (box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
        sheet.paste(image, (box[0]+(box[2]-box[0]-image.width)//2, box[1]+(box[3]-box[1]-image.height)//2))


def _photo_crop(path):
    with Image.open(path) as image:
        array = np.asarray(image.convert('RGB'))
    rows, cols = np.nonzero(np.min(array, axis=2) < 245)
    if not len(rows):
        return [0, 0, array.shape[1], array.shape[0]]
    pad = max(4, int(max(array.shape[:2])*.015))
    return [max(0, int(cols.min())-pad), max(0, int(rows.min())-pad),
            min(array.shape[1], int(cols.max())+pad+1), min(array.shape[0], int(rows.max())+pad+1)]


def _sheets(output, rows, meshes, focus_bounds=None, focus_padding=.15):
    images, crops = [], []
    full_bounds = np.array([np.min([m.vertices.min(axis=0) for m in meshes], axis=0),
                            np.max([m.vertices.max(axis=0) for m in meshes], axis=0)])
    for environment in (e['id'] for e in ENVIRONMENTS):
        for focused in ([False, True] if focus_bounds is not None else [False]):
            # The driver supplies original source photos separately. Do not
            # duplicate five photo tiles in each lighting sheet.
            labels = ['baseline', 'candidate']
            sheet = Image.new('RGB', (1940, 110+230*len(labels)), '#eef1f5')
            draw = ImageDraw.Draw(sheet)
            title = f'{environment} | ' + ('Requested region' if focused else 'Baseline / candidate; source photos supplied separately')
            draw.text((12, 10), title, font=_font(25), fill='#203040')
            draw.text((12, 43), _CAVEAT, font=_font(18), fill='#46556c')
            draw.text((12, 69), 'Rear: asset inspection; face occluders hidden, other AR temple logic remains active.', font=_font(16), fill='#46556c')
            for column, view in enumerate(v['id'] for v in VIEWS):
                draw.text((140+column*360, 90), view, font=_font(16), fill='#203040')
                render_rows = [rows[(kind, environment, view)] for kind in ('baseline', 'candidate')]
                crop = _crop(focus_bounds if focused else full_bounds, render_rows, focus_padding if focused else .08)
                crops.append({'environment': environment, 'view': view, 'focus': focused, 'crop_xyxy': crop,
                              'projection': 'Exact recorded asset/view/projection matrices; conservative canonical bounds. Runtime temple deformation may differ.'})
                for row, label in enumerate(labels):
                    y = 110+row*230
                    if column == 0:
                        draw.text((12, y+12), label, font=_font(19), fill='#203040')
                    box = (140+column*360, y+4, 140+(column+1)*360, y+225)
                    _put(sheet, rows[(label, environment, view)]['path'], box, crop)
            image_id = ('focus-' if focused else 'comparison-')+environment
            path = output / (image_id+'.png'); sheet.save(path)
            images.append(_image_record(path, image_id, title, kind='actual_ar_comparison',
                                       environment=environment, columns=[v['id'] for v in VIEWS], rows=labels))
    return images, crops


def _parts(output, source, groups, focus):
    if source is None:
        if groups is not None or focus is not None:
            raise ValueError('Part groups/focus require a canonical part_source')
        return None, None, []
    pin = _pin(source); mesh = load_glb_bytes(Path(pin['path']).read_bytes())
    if not 0 < len(mesh.parts) <= 256 or len(mesh.faces) > 500_000:
        raise ValueError('Observer requires a reduced source with 1..256 parts and at most500000 faces')
    if not np.isfinite(mesh.vertices).all() or np.ptp(mesh.vertices, axis=0).max() > 1:
        raise ValueError('part_source must be finite canonical metre geometry')
    groups = {} if groups is None else groups
    if not isinstance(groups, dict):
        raise ValueError('groups must map group IDs to source part indices')
    assigned = {}
    for gid, members in groups.items():
        if not isinstance(gid, str) or not _SAFE.fullmatch(gid) or not isinstance(members, list) or not members:
            raise ValueError('Invalid optical group mapping')
        for i in members:
            if type(i) is not int or not 0 <= i < len(mesh.parts) or i in assigned:
                raise ValueError('Unknown or repeated optical part index')
            assigned[i] = gid
    focus_bounds = None
    if focus is not None:
        if not isinstance(focus, dict) or set(focus)-{'part_ids', 'padding_fraction'} or 'part_ids' not in focus:
            raise ValueError('focus requires part_ids and optional padding_fraction')
        padding = focus.get('padding_fraction', .15)
        if isinstance(padding, bool) or not isinstance(padding, (int, float)) or not math.isfinite(padding) or not 0 <= padding <= 1:
            raise ValueError('Invalid focus padding_fraction')
        ids = focus['part_ids']
        if not isinstance(ids, list) or not ids or any(type(i) is not int or not 0 <= i < len(mesh.parts) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Invalid focus part IDs')
        points = np.vstack([mesh.vertices[p['vertex_start']:p['vertex_start']+p['vertex_count']]
                            for i, p in enumerate(mesh.parts) if i in ids])
        focus_bounds = np.array([points.min(axis=0), points.max(axis=0)])
    labels = np.full(len(mesh.faces), -1, np.int32); inventory = []
    colors = np.array([np.array(colorsys.hsv_to_rgb((i*.61803398875)%1, .65, .82))*255 for i in range(len(mesh.parts))], np.uint8)
    for i, p in enumerate(mesh.parts):
        labels[p['face_start']:p['face_start']+p['face_count']] = i
        points = mesh.vertices[p['vertex_start']:p['vertex_start']+p['vertex_count']]
        inventory.append({'part_id': i, 'stable_binding': {'source_sha256': pin['sha256'],
                          **{k: p[k] for k in ('node_index', 'mesh_index', 'primitive_index')}},
                          'name': p['name'], 'bounds_m': [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                          'triangles': p['face_count'], 'supplied_optical_group': assigned.get(i),
                          'role_verified': False, 'color_rgb': colors[i].tolist()})
    if np.any(labels < 0):
        raise ValueError('Incomplete source face inventory')
    lo, hi = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0); center = (lo+hi)*.5
    centered = TriangleMesh(mesh.vertices-center, mesh.faces, mesh.parts)
    sheet = Image.new('RGB', (1440, 100+480*2), '#eef1f5'); draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), 'Source part IDs | opaque geometry only', font=_font(25), fill='#203040')
    draw.text((12, 45), 'Canonical source SHA binds IDs. Colors are labels, not materials. Hidden parts remain in inventory.', font=_font(18), fill='#46556c')
    cameras = []
    for index, (name, yaw, pitch) in enumerate((('front', 0, 0), ('angled', 35, 12), ('angled-opposite', -35, 12), ('back', 180, 0))):
        unit = project(centered.vertices, Camera(yaw, pitch, 0, 0, 1, 0, 0))
        extent = np.maximum(np.ptp(unit, axis=0), 1e-8)
        scale = min(600/extent[0], 320/extent[1])
        offset = np.array([359.5, 239.5]) - (unit.min(axis=0)+unit.max(axis=0))*.5*scale
        cam = Camera(yaw, pitch, 0, 0, float(scale), float(offset[0]), float(offset[1]))
        result = rasterize(centered, cam, (480, 720), max_candidates=150_000)
        hit = result.face_index >= 0; pixel = np.full((480, 720), -1, np.int32); pixel[hit] = labels[result.face_index[hit]]
        image = np.full((480, 720, 3), 245, np.uint8); image[hit] = colors[pixel[hit]]
        tile = Image.fromarray(image); td = ImageDraw.Draw(tile); visible, occupied = [], []
        for i in range(len(mesh.parts)):
            yy, xx = np.nonzero(pixel == i)
            if len(xx) < 8:
                continue
            chosen = np.argmin((xx-xx.mean())**2+(yy-yy.mean())**2); x, y = int(xx[chosen]), int(yy[chosen])
            text = str(i)
            # Keep each ID anchored to a verified visible pixel; move only its
            # callout when small adjacent hardware would make the IDs overlap.
            offsets = [(0, 0)]+[(int(radius*np.cos(a)), int(radius*np.sin(a)))
                               for radius in range(24, 145, 24) for a in np.linspace(0, 2*np.pi, 12, endpoint=False)]
            for dx, dy in offsets:
                label_x, label_y = x+dx, y+dy
                rect = td.textbbox((label_x, label_y), text, font=_font(16))
                rect = (rect[0]-3, rect[1]-3, rect[2]+3, rect[3]+3)
                if rect[0] < 4 or rect[1] < 35 or rect[2] > 716 or rect[3] > 476:
                    continue
                if not any(rect[0] < r[2]+2 and rect[2] > r[0]-2 and rect[1] < r[3]+2 and rect[3] > r[1]-2 for r in occupied):
                    break
            else:
                # Inventory always retains every ID even when a dense source
                # cannot accommodate a readable callout in this fixed image.
                visible.append({'part_id': i, 'pixels': len(xx), 'anchor_pixel_xy': [x, y], 'label_omitted': 'crowded'})
                continue
            occupied.append(rect)
            if (dx, dy) != (0, 0):
                td.line((x, y, (rect[0]+rect[2])/2, (rect[1]+rect[3])/2), fill='#333333', width=1)
                td.ellipse((x-2, y-2, x+2, y+2), fill='#333333')
            td.rectangle(rect, fill='white'); td.text((label_x, label_y), text, font=_font(16), fill='black')
            visible.append({'part_id': i, 'pixels': len(xx), 'anchor_pixel_xy': [x, y], 'label_pixel_xy': [label_x, label_y]})
        x, y = (index%2)*720, 100+(index//2)*480
        sheet.paste(tile, (x, y)); draw.text((x+10, y+8), name, font=_font(20), fill='#203040')
        npz = output / ('parts-'+name+'.npz')
        np.savez_compressed(npz, face_index=result.face_index, part_index=pixel)
        cameras.append({'view': name, 'camera': cam.to_dict(), 'image_size': [720, 480],
                        'source_to_centered_translation_m': (-center).tolist(), 'orthographic_vertical_span_m': 480/scale,
                        'visible_parts': visible, 'raster': _pin(npz), 'backface_culling': False})
    path = output / 'labelled-source-parts.png'; sheet.save(path)
    report = {'source': pin, 'parts': inventory, 'groups': groups, 'cameras': cameras,
              'scope': 'Opaque first-hit CPU geometry, no optical simulation, no runtime face/temple occlusion. IDs are bound to this source revision, not semantic truth.',
              'focus_bounds_m': focus_bounds.tolist() if focus_bounds is not None else None}
    _save(output / 'parts.json', report)
    return report, focus_bounds, [_image_record(path, 'labelled-source-parts', 'Stable source part IDs; opaque geometry only', kind='geometry_labels')]


def observe_candidate(candidate: dict, baseline: dict, photos: list, output: Path, *,
                      product_id: str, width_mm: float = 145, part_source: Path | None = None,
                      groups: dict[str, list[int]] | None = None, focus: dict | None = None) -> dict:
    """Produce one fresh, completely pinned host observation, without paid calls."""
    if not isinstance(product_id, str) or not _SAFE.fullmatch(product_id):
        raise ValueError('product_id must be a safe identifier')
    if isinstance(width_mm, bool) or not isinstance(width_mm, (float, int)) or not math.isfinite(width_mm) or not 60 <= width_mm <= 250:
        raise ValueError('width_mm must be finite in60..250')
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Observation output must be new or empty')
    assets, meshes = {}, []
    for name, value in (('baseline', baseline), ('candidate', candidate)):
        assets[name], mesh = _verify_optical(value, name); meshes.append(mesh)
    if not isinstance(photos, list) or not 1 <= len(photos) <= 24:
        raise ValueError('Supply1..24 pinned source photos')
    references, photo_ids = [], set()
    for photo in photos:
        if not isinstance(photo, dict) or not isinstance(photo.get('id'), str) or not _SAFE.fullmatch(photo['id']) or photo['id'] in photo_ids:
            raise ValueError('Photo IDs must be unique safe strings')
        if not isinstance(photo.get('view'), str):
            raise ValueError('Each source photo requires a view label')
        path, _ = _read_pin(photo, 'photo'); photo_ids.add(photo['id'])
        references.append({'id': photo['id'], 'view': photo['view'], **_pin(path), 'display_crop_xyxy': _photo_crop(path)})
    inputs = [a for a in assets.values()]+[a['export'] for a in assets.values()]+references
    if part_source is not None:
        inputs.append(_pin(part_source))
    if any(Path(p['path']).is_relative_to(output) for p in inputs):
        raise ValueError('Observation output must not contain an input')
    before = renderer_fingerprint()
    output.mkdir(parents=True, exist_ok=True)
    request = {'schema_version': 1, 'product_id': product_id, 'assets': assets, 'photos': references,
               'part_source': _pin(part_source) if part_source is not None else None, 'groups': groups, 'focus': focus,
               'width_mm': width_mm, 'renderer_fingerprint': before}
    _save(output / 'request.json', request)
    try:
        part_report, focus_bounds, geometry_images = _parts(output, part_source, groups, focus)
        manifest = {'input_description': 'Canonical editing observation of exact baseline and candidate. Prepared optical descriptors remain authoritative. No Blender fallback is used.',
                    'cases': [{'id': k, 'product': product_id, 'provider': 'canonical-session',
                               'path': a['path'], 'model_sha256': a['sha256'], 'width_mm': width_mm,
                               'input_provenance': a['export']} for k, a in assets.items()],
                    'environments': list(ENVIRONMENTS), 'ar_views': list(VIEWS),
                    'background_fixture': 'checker', 'background_audit': False, 'shadows': True}
        manifest_path = output / 'manifest.json'; _save(manifest_path, manifest)
        render_dir = output / 'renders'
        _run_ar(manifest_path, render_dir)
        render_path = render_dir / 'report.json'; render = json.loads(render_path.read_bytes())
        if (render.get('status') != 'inspected' or render.get('source_snapshot_stable') is not True
                or render.get('manifest_sha256') != _pin(manifest_path)['sha256']
                or any(render.get(k) for k in ('errors', 'failed_responses', 'blocked_external'))):
            raise ValueError('Actual AR report failed integrity/runtime checks')
        if render.get('ar_views') != list(VIEWS) or render.get('background_fixture') != 'controlled-checker':
            raise ValueError('Actual AR requested view/background policy mismatch')
        lighting = render.get('environments', [])
        if (len(lighting) != len(ENVIRONMENTS) or
                any(any(found.get(k) != v for k, v in expected.items()) or found.get('explicit') is not True
                    for found, expected in zip(lighting, ENVIRONMENTS))):
            raise ValueError('Actual AR explicit environment policy mismatch')
        rows, render_images = {}, []
        if len(render.get('cases', [])) != 2:
            raise ValueError('Actual AR must inspect exactly baseline and candidate')
        for case in render['cases']:
            kind = case['id']
            if kind not in assets or case.get('model_sha256') != assets[kind]['sha256'] or case.get('status') != 'runtime_compatible':
                raise ValueError('Actual AR case model or compatibility mismatch')
            for row in case.get('renders', []):
                key = (kind, row.get('environment'), row.get('view'))
                filename = row.get('filename')
                if not isinstance(filename, str) or Path(filename).name != filename or '/' in filename or '\\' in filename:
                    raise ValueError('Invalid render image filename')
                path, _ = _read_pin({'path': render_dir / filename, 'sha256': row.get('sha256')}, 'render image')
                if row.get('mode') != 'actual-ar' or key in rows:
                    raise ValueError('Unexpected or duplicate rendered view')
                view = next((v for v in VIEWS if v['id'] == row.get('view')), None)
                pose = row.get('requested_pose', {})
                if (view is None or row.get('environment_explicit') is not True or
                        row.get('background') != 'controlled-checker' or
                        any(pose.get(k) != view.get(k, 0) for k in ('yaw_degrees', 'pitch_degrees', 'roll_degrees'))):
                    raise ValueError('Actual AR row pose/environment policy mismatch')
                inspection = row.get('inspection', {})
                if view.get('type') == 'asset-back':
                    if (inspection.get('type') != 'rear_asset_inspection' or inspection.get('asset_rotation_y_degrees') != 180
                            or inspection.get('synthetic_face_occluders_hidden') is not True):
                        raise ValueError('Actual AR rear inspection policy mismatch')
                elif inspection.get('type') != 'synthetic_front_face_pose':
                    raise ValueError('Actual AR ordinary pose inspection mismatch')
                if row.get('camera', {}).get('matrix_layout') != 'column-major' or row.get('spatial', {}).get('matrix_layout') != 'column-major':
                    raise ValueError('Missing exact rendered camera/spatial metadata')
                _project_bounds([meshes[0].vertices.min(axis=0), meshes[0].vertices.max(axis=0)], row)
                with Image.open(path) as image:
                    if image.size != (row['camera']['width'], row['camera']['height']):
                        raise ValueError('Render camera dimensions differ from image')
                rows[key] = {**row, 'path': str(path)}
                render_images.append({'case': kind, **_pin(path), 'environment': key[1], 'view': key[2]})
        expected = {(k, e['id'], v['id']) for k in assets for e in ENVIRONMENTS for v in VIEWS}
        if set(rows) != expected:
            raise ValueError('Incomplete baseline/candidate environment/view matrix')
        for environment in ENVIRONMENTS:
            for view in VIEWS:
                pair = [rows[(kind, environment['id'], view['id'])] for kind in ('baseline', 'candidate')]
                if pair[0]['camera'] != pair[1]['camera'] or pair[0]['requested_pose'] != pair[1]['requested_pose']:
                    raise ValueError('Baseline and candidate must use identical camera and requested pose')
        images, crops = _sheets(output, rows, meshes, focus_bounds,
                               .15 if focus is None else focus.get('padding_fraction', .15))
        images += geometry_images
        after = renderer_fingerprint()
        if before != after:
            raise ValueError('Renderer implementation/runtime changed during observation')
        for item in inputs:
            _read_pin(item, 'observation input')
        for item in images+render_images:
            _read_pin(item, 'observation image')
        report = {'schema_version': 1, 'status': 'runtime_compatible', 'accepted': False,
                  'quality_verdict': 'unmeasured', 'candidate_sha256': assets['candidate']['sha256'],
                  'baseline_sha256': assets['baseline']['sha256'], 'images': images,
                  'render_report': _pin(render_path), 'renderer_fingerprint': before,
                  'renderer_fingerprint_after': after, 'source_snapshot_stable': True,
                  'source_photos': references, 'render_images': render_images, 'crops': crops,
                  'part_evidence': _pin(output / 'parts.json') if part_report else None,
                  'request': _pin(output / 'request.json'), 'manifest': _pin(manifest_path),
                  'limitations': [_CAVEAT, 'Runtime compatibility does not certify product accuracy or optical/part semantic identity.',
                                 'Rear rotates the asset with synthetic face occluders hidden; other runtime temple/pose logic remains.',
                                 'CPU labelled geometry is opaque first-hit source geometry, not canonical optical material behavior.',
                                 'Focus is a crop through exact recorded cameras, not a new render or a calibrated source-photo camera.']}
        _save(output / 'report.json', report)
        return report
    except Exception as error:
        _save(output / 'failure.json', {'schema_version': 1, 'status': 'failed', 'accepted': False,
                                      'error': f'{type(error).__name__}: {error}', 'candidate_sha256': assets['candidate']['sha256']})
        raise
