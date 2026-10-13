"""Explicit copied migration of the historical Studio FT fixture to exact object identities.

The old GLB wrote every authored object name with '.001'. Historical WP5 reports and seeds use the authored
names. This test-only adapter renames those exact 30 nodes, keeps every binary byte and every other JSON value,
and rebinds copied camera/registration receipts. Runtime code has no legacy-name fallback.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile

from glasses3d.g3d.printuv import glbmesh as GM

REPO = Path(__file__).resolve().parents[3]
CAMERAS = REPO / 'glasses3d/data/wp3b/ft1119/cameras.json'
STEP0 = REPO / 'glasses3d/data/wp5/ft1119_strip_null/report.json'
MODEL_SHA = 'af6e9d100fcf7a30ee3862884bff6418f7166a7395c34834559c2da6fcad6cd7'
APPROVED_PRINT_SHA = 'ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4'
NAMES = ('Brow_Left', 'Brow_Right', 'Eyewire_Left', 'Eyewire_Right', 'Front_hinge_Left', 'Front_hinge_Right',
         'Hinge_screw_Left', 'Hinge_screw_Right', 'Left_inner_brand_print', 'Lens_Left', 'Lens_Right',
         'Nose_pad_Left', 'Nose_pad_Right', 'Pad_mount_Left', 'Pad_mount_Right', 'Pad_stalk_Left', 'Pad_stalk_Right',
         'Right_tip_medallion', 'Right_tip_TOM_FORD', 'Saddle_bridge', 'T_arm_Left', 'T_arm_Right',
         'T_return_Left', 'T_return_Right', 'T_vertical_Left', 'T_vertical_Right', 'Temple_hinge_Left',
         'Temple_hinge_Right', 'Temple_Left', 'Temple_Right')
NAME_MAP = {name + '.001': name for name in NAMES}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2), encoding='utf-8')


def rename_glb(source, destination):
    """Change only the explicit legacy node names; refuse a collision or unrelated name set."""
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError('preserve source and existing migration outputs')
    raw = source.read_bytes()
    doc, binary = GM.chunks(raw)
    names = [node['name'] for node in doc['nodes'] if 'mesh' in node]
    if not set(names) <= set(NAME_MAP) | set(NAME_MAP.values()):
        raise ValueError('not the explicit historical FT node set')
    changed = deepcopy(doc)
    for node in changed['nodes']:
        if node.get('name') in NAME_MAP:
            node['name'] = NAME_MAP[node['name']]
    renamed = [node['name'] for node in changed['nodes'] if 'mesh' in node]
    if len(renamed) != len(set(renamed)):
        raise ValueError('legacy migration would collapse distinct objects')
    body = json.dumps(changed, separators=(',', ':')).encode('utf-8')
    body += b' ' * (-len(body) % 4)
    chunks = [struct.pack('<II', len(body), 0x4e4f534a) + body]
    offset = 12
    while offset + 8 <= len(raw):
        length, kind = struct.unpack_from('<II', raw, offset)
        if kind != 0x4e4f534a:
            chunks.append(raw[offset:offset + 8 + length])
        offset += 8 + length
    data = b''.join(chunks)
    destination.write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(data)) + data)
    after, after_binary = GM.chunks(destination.read_bytes())
    assert after == changed and after_binary == binary
    reverse = deepcopy(after)
    for old, new in zip(doc['nodes'], reverse['nodes']):
        if 'name' in old:
            new['name'] = old['name']
    assert reverse == doc
    return {'source': str(source), 'source_sha256': sha(source), 'output': str(destination),
            'output_sha256': sha(destination), 'name_map': NAME_MAP,
            'binary_unchanged': True, 'only_node_names_changed': True}


def prepare(destination):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=False)
    cameras = json.loads(CAMERAS.read_bytes())
    original_model = REPO / Path(cameras['model']['path'])
    if sha(original_model) != MODEL_SHA or cameras['model']['sha256'] != MODEL_SHA:
        raise ValueError('historical FT source model changed')
    sources = {str(p): sha(p) for p in (CAMERAS, original_model, STEP0)}
    model = root / 'model.glb'
    migration = rename_glb(original_model, model)
    cameras['model'].update(path=str(model), sha256=sha(model))
    camera_path = root / 'cameras.json'
    write(camera_path, cameras)
    step0 = json.loads(STEP0.read_bytes())
    if (step0['inputs']['glb_sha256'] != MODEL_SHA
            or step0['inputs']['cameras_sha256'] != sha(CAMERAS)):
        raise ValueError('historical registration receipt no longer binds the original inputs')
    step0['inputs'].update(glb=str(model), glb_sha256=sha(model), cameras_json=str(camera_path),
                           cameras_sha256=sha(camera_path))
    step0_path = root / 'step0.json'
    write(step0_path, step0)
    receipt = {'schema': 'g3d.test_fixture_migration/1', 'model': migration, 'sources': sources,
               'cameras': str(camera_path), 'step0': str(step0_path),
               'rebound_fields': ['model.path', 'model.sha256', 'inputs.glb', 'inputs.glb_sha256',
                                  'inputs.cameras_json', 'inputs.cameras_sha256']}
    assert all(sha(path) == digest for path, digest in sources.items())
    write(root / 'migration.json', receipt)
    return {'root': root, 'model': model, 'cameras': camera_path, 'step0': step0_path, 'receipt': receipt}


def copy_bake(source, destination, fixture):
    """Rebind a copied historical bake to the name-only migrated source; its pixels/layout stay exact."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('layout.json', 'print.png', 'classes.png', 'source.png', 'tier.png', 'bake.json'):
        if (source / name).is_file():
            shutil.copyfile(source / name, destination / name)
    report = json.loads((source / 'report.json').read_bytes())
    identities = report['inputs']['sources']
    if identities.get('glb_sha256') != MODEL_SHA or identities.get('cameras_sha256') != sha(CAMERAS):
        raise ValueError('historical bake does not bind the explicit original FT fixture')
    report['inputs']['sources'].update(glb=str(fixture['model']), glb_sha256=sha(fixture['model']),
                                       cameras_json=str(fixture['cameras']), cameras_sha256=sha(fixture['cameras']))
    # The old report's explicit coverage decision is preserved; only missing redundant complements are derived.
    if 'no_texture_parts' not in report and 'texture_parts' in report:
        report['no_texture_parts'] = [part for part in report['parts'] if part not in report['texture_parts']]
    write(destination / 'report.json', report)
    from glasses3d.g3d_host.photoprint.manifest import write_manifest
    write_manifest(destination)
    return destination


def bake_inputs(source, destination):
    """Bind a legacy wrapper to migrated copies, or verify an explicitly supplied prior migration.

    Only the pinned original model/camera pair is migrated. An existing migration must still match its actual
    files and the original camera values; unrelated bakes are refused, never rebound as historical evidence.
    """
    from glasses3d.g3d_host.photoprint import inputs, production
    source = Path(source).resolve()
    report = json.loads((source / 'report.json').read_bytes())
    identities = report['inputs']['sources']
    fixture = cached_fixture()
    if identities.get('glb_sha256') == MODEL_SHA:
        bake = copy_bake(source, destination, fixture)
        job = inputs.load_job(fixture['cameras'])
    else:
        model = Path(identities['glb'])
        cameras_path = Path(identities['cameras_json'])
        model = model if model.is_absolute() else REPO / model
        cameras_path = cameras_path if cameras_path.is_absolute() else REPO / cameras_path
        if (identities.get('glb_sha256') != sha(fixture['model']) or sha(model) != sha(fixture['model'])
                or identities.get('cameras_sha256') != sha(cameras_path)):
            raise ValueError('bake is not a bound explicit historical FT migration')
        cameras, original = json.loads(cameras_path.read_bytes()), json.loads(CAMERAS.read_bytes())
        actual_model = dict(cameras['model'])
        actual_model.update(path=original['model']['path'], sha256=original['model']['sha256'])
        if {**cameras, 'model': actual_model} != original:
            raise ValueError('migrated camera values differ from the historical FT fixture')
        bake, job = source, inputs.load_job(cameras_path)
    production.validate_registration_inputs(job, bake / 'report.json')
    return job, bake


@lru_cache(maxsize=1)
def cached_fixture():
    return prepare(Path(tempfile.mkdtemp(prefix='g3d-ft1119-test-')) / 'inputs')


def load_job(*, with_astra=True):
    from glasses3d.g3d_host.photoprint import inputs
    return inputs.load_job(cached_fixture()['cameras'], with_astra=with_astra)
