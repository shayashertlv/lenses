"""A fresh synthetic scene exercises the export contract, never a live MCP session."""
from collections import Counter
import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from blender_agent.native_export import RECEIPT_PREFIX, export_code


def _accessor(document, binary, index):
    accessor = document['accessors'][index]
    view = document['bufferViews'][accessor['bufferView']]
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3}[accessor['type']]
    kind = {5121: 'B', 5123: 'H', 5125: 'I', 5126: 'f'}[accessor['componentType']]
    fmt = '<' + width * kind
    start = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    stride = view.get('byteStride', struct.calcsize(fmt))
    return [struct.unpack_from(fmt, binary, start + i * stride) for i in range(accessor['count'])]


@pytest.mark.slow
def test_scratch_meshes_curves_materials_roles_and_origin_export_without_seed_metadata(tmp_path):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender is unavailable')
    glb = tmp_path / 'scratch.glb'
    fixture = r'''
import bpy
from mathutils import Vector
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
assert 'mdl_bridge_underside' not in scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = .001

def material(name, color, transmission, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    bsdf.inputs['Roughness'].default_value = .173
    bsdf.inputs['Transmission Weight'].default_value = transmission
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['IOR'].default_value = 1.49
    bsdf.inputs['Coat Weight'].default_value = .6
    return mat

crystal = material('Scratch crystal', (.91, .86, .72), .8)
brown = material('Scratch brown lenses', (.35, .19, .08), .95)
gold = material('Scratch gold', (.8, .5, .12), 0, 1)
image = bpy.data.images.new('Scratch texture', width=2, height=2)
image.pixels[:] = [.35, .19, .08, 1] * 4
texture = brown.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
brown.node_tree.links.new(texture.outputs['Color'], brown.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])

def curve(name, points, radius, role, cyclic=False):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    data.bevel_depth = radius
    data.bevel_resolution = 2
    data.use_fill_caps = True
    spline = data.splines.new('POLY')
    spline.points.add(len(points) - 1)
    for point, xyz in zip(spline.points, points): point.co = (*xyz, 1)
    spline.use_cyclic_u = cyclic
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj['partRole'] = role
    data.materials.append(crystal)
    return obj

front = curve('New curved frame', [(-64, -15, 0), (64, -15, 0), (64, 24, 0), (-64, 24, 0)], 2, 'frame', True)
for sign in (-1, 1):
    curve('New temple ' + str(sign), [(64 * sign, 16, 0), (66 * sign, 14, -70), (70 * sign, 7, -140)], 2, 'temple')
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=(32 * sign, 4, 1))
    lens = bpy.context.object
    lens.name = 'New closed curved lens ' + str(sign)
    lens.scale = (25, 18, 1.1)
    lens['partRole'] = 'lens'
    lens.data.materials.append(brown)
    for polygon in lens.data.polygons: polygon.use_smooth = True
bpy.ops.mesh.primitive_cube_add(size=1, location=(-61, 17, 2))
hardware = bpy.context.object
hardware.name = 'Gold hardware'
hardware.scale = (-5, 2, 1)
hardware.rotation_euler.z = .13
hardware['partRole'] = 'frame'
hardware.data.materials.append(gold)
bpy.ops.mesh.primitive_plane_add(size=1000, location=(0, 0, -500))
reference = bpy.context.object
reference.name = 'Render hidden reference plane'
reference.hide_render = True
hidden_collection = bpy.data.collections.new('Render hidden references')
scene.collection.children.link(hidden_collection)
hidden_collection.hide_render = True
nested_collection = bpy.data.collections.new('Nested references')
hidden_collection.children.link(nested_collection)
collection_reference = bpy.data.objects.new('Collection hidden reference plane', reference.data.copy())
nested_collection.objects.link(collection_reference)
# A product can also be linked into a hidden collection without becoming hidden on its original visible path.
hidden_collection.objects.link(hardware)
donor = bpy.data.collections.new('Reference instance donor')
donor.objects.link(hardware)
hidden_instance = bpy.data.objects.new('Hidden reference instance', None)
hidden_instance.instance_type = 'COLLECTION'
hidden_instance.instance_collection = donor
nested_collection.objects.link(hidden_instance)
camera = bpy.data.objects.new('Inspection camera', bpy.data.cameras.new('Inspection camera'))
light = bpy.data.objects.new('Inspection light', bpy.data.lights.new('Inspection light', 'AREA'))
image_empty = bpy.data.objects.new('Image reference empty', None)
for obj in (camera, light, image_empty): scene.collection.objects.link(obj)
lens.select_set(True)
bpy.context.view_layer.objects.active = lens
bpy.context.view_layer.update()
original_vertices = [tuple(v.co) for v in lens.data.vertices]
original_uv = [tuple(loop.uv) for loop in lens.data.uv_layers.active.data]
original_curve = [tuple(point.co) for point in front.data.splines[0].points]
'''
    script = tmp_path / 'scratch-fixture.py'
    script.write_text(fixture + export_code(glb, origin=(0, 0, 0)) + '''
assert [tuple(v.co) for v in lens.data.vertices] == original_vertices
assert [tuple(loop.uv) for loop in lens.data.uv_layers.active.data] == original_uv
assert [tuple(point.co) for point in front.data.splines[0].points] == original_curve
assert 'mdl_bridge_underside' not in scene
''', encoding='utf-8')
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(script)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = next(json.loads(line.split(RECEIPT_PREFIX, 1)[1]) for line in result.stdout.splitlines()
                   if RECEIPT_PREFIX in line)
    assert receipt['status'] == 'exported' and receipt['scene_unchanged'] is True
    assert receipt['origin_source'] == 'explicit' and receipt['origin'] == [0, 0, 0]
    assert receipt['object_count'] == 6
    assert all(obj['part'] == '' for obj in receipt['objects']), 'No seed part identifiers are needed'
    assert Counter(obj['partRole'] for obj in receipt['objects']) == {'frame': 2, 'temple': 2, 'lens': 2}
    assert {row['source']: row['reason'] for row in receipt['excluded']} == {
        'Render hidden reference plane': 'hide_render',
        'Collection hidden reference plane': 'collection.hide_render',
    }
    raw = glb.read_bytes()
    length = struct.unpack_from('<I', raw, 12)[0]
    document, binary = json.loads(raw[20:20 + length]), raw[28 + length:]
    assert not document.get('cameras')
    assert 'KHR_lights_punctual' not in document.get('extensionsUsed', [])
    assert 'LENSES_lens_appearance' not in document.get('extensionsUsed', [])
    assert 'images' in document and document['images'][0].get('bufferView') is not None
    assert all('matrix' not in node and 'translation' not in node and 'rotation' not in node and 'scale' not in node
               for node in document['nodes'])
    for node in document['nodes']:
        role = node['extras']['partRole']
        for primitive in document['meshes'][node['mesh']]['primitives']:
            assert primitive.get('mode', 4) == 4
            assert document['accessors'][primitive['indices']]['count'] > 0
            if role != 'lens':
                continue
            vertices = _accessor(document, binary, primitive['attributes']['POSITION'])
            uv = _accessor(document, binary, primitive['attributes']['TEXCOORD_0'])
            normals = _accessor(document, binary, primitive['attributes']['NORMAL'])
            indices = [row[0] for row in _accessor(document, binary, primitive['indices'])]
            edges = Counter()
            for offset in range(0, len(indices), 3):
                a, b, c = [vertices[index] for index in indices[offset:offset + 3]]
                edges.update(tuple(sorted(pair)) for pair in ((a, b), (b, c), (c, a)))
            assert set(edges.values()) == {2}, 'Full closed curved lens, including its back and sides'
            assert min(n[2] for n in normals) < -.8 and max(n[2] for n in normals) > .8
            assert max(v[2] for v in vertices) - min(v[2] for v in vertices) == pytest.approx(.0022, abs=1e-8)
            assert len(uv) == len(vertices) and max(abs(v[0]) for v in vertices) < .06
    crystal = next(mat for mat in document['materials'] if mat['name'].startswith('Scratch crystal'))
    assert crystal['pbrMetallicRoughness']['roughnessFactor'] == pytest.approx(.173)
    assert crystal['extensions']['KHR_materials_transmission']['transmissionFactor'] == pytest.approx(.8)
    assert crystal['extensions']['KHR_materials_clearcoat']['clearcoatFactor'] == pytest.approx(.6)
    assert crystal['extensions']['KHR_materials_ior']['ior'] == pytest.approx(1.49)
