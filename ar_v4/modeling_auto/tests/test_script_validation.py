import math

import pytest

from app.script_validation import (
    ALLOWED_IMPORTS, ALLOWED_OPERATORS, MAX_SCRIPT_BYTES, guarded_getattr,
    guarded_hasattr, guarded_import, guarded_setattr, safe_builtins, validate_script,
)


@pytest.mark.parametrize("script", [
    "import bpy\nfor obj in bpy.data.objects:\n    obj.select_set(False)",
    "import bmesh\nbm = bmesh.new()\nbmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))",
    "from mathutils.bvhtree import BVHTree\nfrom mathutils.kdtree import KDTree",
    "from mathutils import Vector, Matrix, Quaternion, Euler, Color, geometry",
    "import mathutils.geometry as geo\nimport math as m\nx=m.sqrt(2)",
    "def _lens(points):\n    return [p for _i,p in enumerate(points)]",
    "import bpy as b\nb.ops.object.mode_set(mode='OBJECT')",
    "import bpy\nmaterial=bpy.data.materials.new('lens')\nmaterial.use_nodes=True\nmaterial.node_tree.nodes.get('Principled BSDF').inputs.get('Roughness').default_value=0.2",
    "import bpy\nobj = bpy.data.objects.get('lens')\nobj['auto_role'] = 'lens_left'",
    "import bpy\nassert hasattr(bpy.context.scene, 'objects')",
])
def test_documented_edit_scripts(script):
    validate_script(script)


@pytest.mark.parametrize("operator", sorted(ALLOWED_OPERATORS))
def test_all_documented_operators_validate(operator):
    validate_script("import bpy\n" + operator + "()")


@pytest.mark.parametrize("module", sorted(ALLOWED_IMPORTS))
def test_exact_documented_imports_validate(module):
    validate_script("import " + module)


@pytest.mark.parametrize("script", [
    "import os", "import pathlib", "import subprocess", "import socket",
    "import sys", "import numpy", "import bpy.app", "from . import x",
    "from mathutils.bvhtree import *", "from mathutils import interpolate",
    "from bpy import app", "from bmesh import ops",
    "bpy.ops.wm.save_as_mainfile(filepath='evil.blend')",
    "import bpy as b\nb.ops.script.python_file_run(filepath='evil.py')",
    "operators = bpy.ops", "bpy.app.handlers.load_post.append(lambda x: 0)",
    "bpy.data.libraries.load('outside.blend')", "bpy.data.texts.new('evil')",
    "bpy.context.preferences.filepaths.script_directories",
    "obj.driver_add('location')", "obj.__class__", "().__class__.__base__",
    "exec('pass')", "eval('1')", "open('outside')", "globals()", "locals()",
    "__import__('os')", "getattr(obj, '__class__')", "setattr(obj, 'filepath', 'x')",
    "node=nodes.new('ShaderNodeScript')", "global x\nx=1",
    "class X: pass", "async def task(): pass", "import math\nx=math.__dict__",
    "getattr(bpy, 'ops').wm.save_as_mainfile()", "getattr(obj, 'load')('file')",
    "image.save()", "image.reload()", "node.filepath='outside'",
    "bpy.context.window_manager", "image.unpack()",
    "```python\npass\n```", "", "if",
])
def test_unsafe_or_unavailable_capabilities_rejected(script):
    with pytest.raises(ValueError):
        validate_script(script)


def test_size_bounded():
    with pytest.raises(ValueError, match="200 KB"):
        validate_script("#" * (MAX_SCRIPT_BYTES + 1))


def test_runtime_import_same_rule_and_python_none_fromlist():
    assert guarded_import("math", {}, {}, None) is math
    for name in ("os", "sys", "pathlib", "mathutils.unknown"):
        with pytest.raises(ImportError):
            guarded_import(name)
    with pytest.raises(ImportError):
        guarded_import("math", fromlist=["__dict__"])
    with pytest.raises(ImportError):
        guarded_import("math", level=1)


def test_dynamic_attributes_guarded_at_runtime():
    for value in ("__class__", "filepath", "app", "load", "ops"):
        with pytest.raises(ValueError):
            guarded_getattr(math, value)
        with pytest.raises(ValueError):
            guarded_setattr(math, value, None)
    assert guarded_getattr(math, "pi") == math.pi


def test_runtime_builtins_contract():
    context = {"__builtins__": safe_builtins()}
    script = "import math\nvalues = [math.sqrt(v) for v in range(5)]\nresult=sum(values)\nassert isinstance(result, float)"
    validate_script(script)
    exec(compile(script, "<test-edit>", "exec"), context, context)
    assert context["result"] == sum(math.sqrt(v) for v in range(5))
    assert not {"open", "eval", "exec", "globals", "object", "id", "memoryview", "super"} & safe_builtins().keys()


def test_single_argument_type_and_blender_exceptions_are_available():
    script = '''
kinds = [type(v) is int for v in (1, 2.0, "x")]
caught = []
try:
    raise ReferenceError("StructRNA of type Object has been removed")
except ReferenceError as error:
    caught.append(str(error))
try:
    undefined_helper
except NameError:
    caught.append("NameError")
'''
    validate_script(script)
    context = {"__builtins__": safe_builtins()}
    exec(compile(script, "<blender-exceptions>", "exec"), context, context)
    assert context["kinds"] == [True, False, False]
    assert context["caught"] == ["StructRNA of type Object has been removed", "NameError"]
    with pytest.raises(TypeError, match=r"only as type\(value\)"):
        exec(compile("Made = type('Made', (), {})", "<class-definition>", "exec"), context, context)


def test_prompt_lists_exactly_the_runtime_builtins():
    from app.prompts import API_GUIDE
    from app.script_validation import builtin_names
    listed = API_GUIDE.split("is exactly: ", 1)[1].split(". No other builtin", 1)[0].split(", ")
    assert listed == builtin_names()
    assert set(listed) == {name for name in safe_builtins() if not name.startswith("_")}
    assert "type" in listed and "ReferenceError" in listed and "id" not in listed


@pytest.mark.parametrize("script", [
    "# see ``` fenced example in the docs\nimport bpy\n",
    "note = 'never wrap output in ```python fences'\n",
])
def test_backticks_inside_comments_or_strings_do_not_reject_a_valid_script(script):
    validate_script(script)


def test_fenced_script_is_named_as_the_reason_it_does_not_parse():
    with pytest.raises(ValueError, match="Markdown fences"):
        validate_script("```python\nimport bpy\n```\n")


def test_readonly_length_query_handles_scalar_and_array_socket_values():
    script = '''
values = [0.5, (0.1, 0.2, 0.3, 1.0), [1, 2], None]
copied = [tuple(v) if hasattr(v, "__len__") else v for v in values]
'''
    validate_script(script)
    context = {"__builtins__": safe_builtins()}
    exec(compile(script, '<socket-inspection>', 'exec'), context, context)
    assert context['copied'] == [0.5, (0.1, 0.2, 0.3, 1.0), (1, 2), None]


@pytest.mark.parametrize('script', [
    "v.__len__()", "method = v.__len__", "getattr(v, '__len__')",
    "setattr(v, '__len__', None)", "hasattr(v, '__class__')",
    "hasattr(v, '__dict__')", "name = '__len__'", "check = hasattr(v, '__len__', None)",
    "hasattr(v, '__len__', default=None)", "hasattr('__len__', '__len__')",
])
def test_length_query_does_not_expose_private_attributes(script):
    with pytest.raises(ValueError):
        validate_script(script)


def test_dynamic_private_access_remains_blocked_except_boolean_length_query():
    assert guarded_hasattr([1], '__len__') is True
    assert guarded_hasattr(1.0, '__len__') is False
    for name in ('__len__', '__class__', '__dict__'):
        with pytest.raises(ValueError):
            guarded_getattr([], name)
        with pytest.raises(ValueError):
            guarded_setattr([], name, None)
    with pytest.raises(ValueError):
        guarded_hasattr([], '__class__')


def test_pointer_identity_deduplicates_shared_meshes_without_mutating_them():
    class MeshView:
        def __init__(self, identity):
            self.identity = identity

        def as_pointer(self):
            return self.identity

    meshes = [MeshView(101), MeshView(101), MeshView(202)]
    script = '''
processed = set()
unique = []
for mesh in meshes:
    if mesh.as_pointer() in processed:
        continue
    processed.add(mesh.as_pointer())
    unique.append(mesh)
'''
    validate_script(script)
    context = {'__builtins__': safe_builtins(), 'meshes': meshes}
    exec(compile(script, '<shared-mesh-identity>', 'exec'), context, context)
    assert context['unique'] == [meshes[0], meshes[2]]
    assert [mesh.identity for mesh in meshes] == [101, 101, 202]


@pytest.mark.parametrize('script', [
    'method = mesh.as_pointer', 'mesh.as_pointer = value', 'del mesh.as_pointer',
    'mesh.as_pointer(1)', 'mesh.as_pointer(value=1)', 'mesh.as_pointer(*args)',
    'mesh.as_pointer(**kwargs)', "getattr(mesh, 'as_pointer')()",
    "setattr(mesh, 'as_pointer', value)", "hasattr(mesh, 'as_pointer')",
    'mesh.from_address(123)', 'mesh.from_pointer(123)',
    "getattr(mesh, 'from_address')(123)", 'import ctypes',
])
def test_pointer_identity_does_not_allow_capture_rebinding_or_address_access(script):
    with pytest.raises(ValueError):
        validate_script(script)


@pytest.mark.parametrize('name', ['as_pointer', 'from_address', 'from_pointer'])
def test_pointer_methods_cannot_be_obtained_with_dynamic_attribute_helpers(name):
    class MeshView:
        def as_pointer(self):
            return 101

    mesh = MeshView()
    for getter in (guarded_getattr, guarded_hasattr):
        with pytest.raises(ValueError):
            getter(mesh, name)
    with pytest.raises(ValueError):
        guarded_setattr(mesh, name, None)
