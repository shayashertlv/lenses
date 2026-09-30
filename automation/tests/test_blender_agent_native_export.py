"""Native export checks include a real isolated Blender process, never live MCP."""
from collections import Counter
import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from blender_agent.native_export import RECEIPT_PREFIX, export_code


@pytest.mark.parametrize('scale', [0, -1, float('nan'), float('inf'), True])
def test_rejects_invalid_units(tmp_path, scale):
    with pytest.raises(ValueError):
        export_code(tmp_path / 'model.glb', scale)


@pytest.mark.parametrize('origin', [(0, 1), (0, 1, float('nan')), (0, True, 2)])
def test_rejects_invalid_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        export_code(tmp_path / 'model.glb', origin=origin)


def test_generated_code_passes_community_mcp_safe_mode(tmp_path):
    validator = pytest.importorskip('blender_mcp.safe_mode')
    code = export_code(tmp_path / "model's native.glb", origin=(0, 10.7, -0.5))
    compile(code, '<native export>', 'exec')
    validator.validate_code(code)


def read_glb(path):
    data = path.read_bytes()
    assert struct.unpack_from('<4sII', data) == (b'glTF', 2, len(data))
    length, kind = struct.unpack_from('<II', data, 12)
    assert kind == 0x4E4F534A
    return json.loads(data[20:20 + length])


def accessor_data(path, gltf, index):
    data = path.read_bytes()
    json_length = struct.unpack_from('<I', data, 12)[0]
    binary = data[28 + json_length:]
    accessor = gltf['accessors'][index]
    view = gltf['bufferViews'][accessor['bufferView']]
    kind = {5121: 'B', 5123: 'H', 5125: 'I', 5126: 'f'}[accessor['componentType']]
    width = {'SCALAR': 1, 'VEC3': 3}[accessor['type']]
    fmt = '<' + kind * width
    stride = view.get('byteStride', struct.calcsize(fmt))
    offset = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    return [struct.unpack_from(fmt, binary, offset + i * stride) for i in range(accessor['count'])]


@pytest.mark.slow
def test_real_export_preserves_solids_native_material_and_scene(tmp_path):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender is unavailable')
    output = tmp_path / 'native.glb'
    fixture = '''
import bpy
from mathutils import Matrix
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene['mdl_bridge_underside'] = [0.0, 10.7, -0.5]
bpy.ops.mesh.primitive_cube_add(size=2, location=(10, 11.7, 0.5))
lens = bpy.context.object
lens.name = 'Closed lens'
lens['part'] = 'lens_R'
lens.scale = (-2, 3, 0.5)
mat = bpy.data.materials.new('Native transmission')
mat.use_nodes = True
p = mat.node_tree.nodes.get('Principled BSDF')
p.inputs['Base Color'].default_value = (0.2, 0.3, 0.4, 1)
p.inputs['Roughness'].default_value = 0.173
p.inputs['Transmission Weight'].default_value = 0.81
p.inputs['IOR'].default_value = 1.47
mat['mdl_material'] = {'transmission': 0.02, 'roughness': 0.9}
lens.data.materials.append(mat)
mod = lens.modifiers.new('Evaluated bevel', 'BEVEL')
mod.width = 0.1
mod.segments = 2
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -100))
frame = bpy.context.object
frame.name = 'Hidden registered temple'
frame['part'] = 'temple_L'
frame['partRole'] = 'frame'
frame.hide_render = False
frame.hide_set(True)
frame.data.materials.append(mat)
bpy.ops.mesh.primitive_cube_add(size=50)
cutter = bpy.context.object
cutter.name = 'Replaced hidden lens'
cutter['part'] = 'lens_L'
cutter.hide_render = True
bpy.context.view_layer.objects.active = lens
lens.select_set(True)
original_scene = scene
'''
    script = tmp_path / 'verify.py'
    script.write_text(fixture + export_code(output) + "\nassert bpy.context.scene == original_scene\n", encoding='utf-8')
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(script)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    receipts = [json.loads(line.split(RECEIPT_PREFIX, 1)[1]) for line in result.stdout.splitlines()
                if RECEIPT_PREFIX in line]
    assert receipts and receipts[-1]['scene_unchanged'] is True
    receipt, gltf = receipts[-1], read_glb(output)
    assert receipt['object_count'] == 2
    assert receipt['excluded'] == [{'source': 'Replaced hidden lens', 'reason': 'hide_render'}]
    assert receipt['origin'] == [0.0, 10.7, -0.5]
    assert receipt['objects'][0]['triangles'] > 12  # Evaluated bevel retained.
    assert all(node.get('translation', [0, 0, 0]) == [0, 0, 0] for node in gltf['nodes'])
    assert all(node.get('scale', [1, 1, 1]) == [1, 1, 1] for node in gltf['nodes'])
    assert all(node.get('rotation', [0, 0, 0, 1]) == [0, 0, 0, 1] for node in gltf['nodes'])
    triangles = sum(gltf['accessors'][p['indices']]['count'] // 3 for mesh in gltf['meshes']
                    for p in mesh['primitives'] if p.get('mode', 4) == 4)
    assert triangles == receipt['triangle_count']
    assert all('LENSES_lens_appearance' not in mat.get('extensions', {}) for mat in gltf['materials'])
    assert all('mdl_material' not in mat.get('extras', {}) for mat in gltf['materials'])
    native = gltf['materials'][0]
    assert native['pbrMetallicRoughness']['roughnessFactor'] == pytest.approx(0.173)
    assert native['extensions']['KHR_materials_transmission']['transmissionFactor'] == pytest.approx(0.81)
    assert native['extensions']['KHR_materials_ior']['ior'] == pytest.approx(1.47)
    lens_node = next(node for node in gltf['nodes'] if node.get('extras', {}).get('part') == 'lens_R')
    primitive = gltf['meshes'][lens_node['mesh']]['primitives'][0]
    lens_bounds = gltf['accessors'][primitive['attributes']['POSITION']]
    assert lens_bounds['min'] == pytest.approx([0.008, -0.002, 0.0005], abs=1e-8)
    assert lens_bounds['max'] == pytest.approx([0.012, 0.004, 0.0015], abs=1e-8)
    vertices = accessor_data(output, gltf, primitive['attributes']['POSITION'])
    indices = [item[0] for item in accessor_data(output, gltf, primitive['indices'])]
    edges, volume = Counter(), 0.0
    for offset in range(0, len(indices), 3):
        a, b, c = [vertices[index] for index in indices[offset:offset + 3]]
        edges.update(tuple(sorted(pair)) for pair in ((a, b), (b, c), (c, a)))
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) + a[1] * (b[2] * c[0] - b[0] * c[2])
                   + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
    assert set(edges.values()) == {2}, 'Native lens must remain a closed solid'
    assert volume > 0, 'Negative source scale must retain outward winding'
    assert next(node for node in gltf['nodes'] if node.get('extras', {}).get('part') == 'temple_L')['extras']['partRole'] == 'frame'


@pytest.mark.slow
def test_export_failure_still_cleans_temporary_data(tmp_path):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender is unavailable')
    from textwrap import indent
    destination = tmp_path / 'occupied.glb'
    destination.mkdir()  # An existing directory cannot become a GLB file.
    code = export_code(destination, origin=(0, 0, 0))
    driver = tmp_path / 'failure.py'
    driver.write_text('try:\n' + indent(code, '    ') + '\nexcept Exception:\n    pass\nelse:\n    raise AssertionError("Expected export failure")\n', encoding='utf-8')
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(driver)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = next(json.loads(line.split(RECEIPT_PREFIX, 1)[1]) for line in result.stdout.splitlines()
                   if RECEIPT_PREFIX in line)
    assert receipt['status'] == 'failed'
    assert receipt['scene_unchanged'] is True


@pytest.mark.slow
@pytest.mark.parametrize('kind', ['collection', 'geometry_nodes'])
def test_rejects_unrealized_instances_without_partial_export(tmp_path, kind):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender is unavailable')
    from textwrap import indent
    output = tmp_path / 'unrealized.glb'
    setup = '''
import bpy
donor = bpy.data.collections.new('Unrealized donor')
donor.objects.link(bpy.data.objects['Cube'])
instance = bpy.data.objects.new('Donor instance', None)
instance.instance_type = 'COLLECTION'
instance.instance_collection = donor
bpy.context.scene.collection.objects.link(instance)
'''
    if kind == 'geometry_nodes':
        setup = '''
import bpy
tree = bpy.data.node_groups.new('Unrealized Geometry Nodes', 'GeometryNodeTree')
tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
output = tree.nodes.new('NodeGroupOutput')
points = tree.nodes.new('GeometryNodeMeshLine')
points.inputs['Count'].default_value = 2
source = tree.nodes.new('GeometryNodeObjectInfo')
source.inputs['Object'].default_value = bpy.data.objects['Cube']
instances = tree.nodes.new('GeometryNodeInstanceOnPoints')
tree.links.new(points.outputs['Mesh'], instances.inputs['Points'])
tree.links.new(source.outputs['Geometry'], instances.inputs['Instance'])
tree.links.new(instances.outputs['Instances'], output.inputs['Geometry'])
obj = bpy.data.objects.new('GN instancer', bpy.data.meshes.new('Empty instancer'))
bpy.context.scene.collection.objects.link(obj)
obj.modifiers.new('Unrealized output', 'NODES').node_group = tree
'''
    driver = tmp_path / 'unrealized.py'
    driver.write_text(setup + '\ntry:\n' + indent(export_code(output, origin=(0, 0, 0)), '    ')
                      + '\nexcept ValueError as error:\n    assert "Realize collection/Geometry Nodes instances" in str(error)\n'
                        'else:\n    raise AssertionError("Unrealized geometry was silently omitted")\n', encoding='utf-8')
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(driver)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not output.exists()
    receipt = next(json.loads(line.split(RECEIPT_PREFIX, 1)[1]) for line in result.stdout.splitlines()
                   if RECEIPT_PREFIX in line)
    assert receipt['status'] == 'failed'
    assert receipt['scene_unchanged'] is True
    assert receipt['object_count'] == 0
