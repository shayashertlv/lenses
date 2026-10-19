"""WP2 stage A: safe mode for Astra's execute_blender_code (g3d/safe_mode.py, glasses3d/plan/archive/20261019/WP2_DESIGN.md §4). Host only.

* the community suite (automation/.../mcp-for-blender/tests/test_safe_mode.py, MIT) inverted for the glasses3d
  policy: its render/save/import/open/preferences "legitimate" scripts are refused here, its escapes stay refused;
* the union of the three panel designs' adversarial sets (worker_angle 33, worker 47, loop 33): every one refused,
  statically or by the runtime layer (a computed getattr passes the static walk by design and is refused at run time);
* legitimate numpy/bmesh/BVH/g3d snippets pass; every violation is collected with its line and the replacement tool;
* the runtime layers on the host: restricted builtins, guarded import, guarded getattr family, the audit hook.
The corpus gate over the 214 Studio snippets lives in test_wp2_corpus.py (it needs the ignored corpus folder).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import types

import numpy as np
import pytest

from glasses3d.g3d import safe_mode as S


def denied(code: str) -> bool:
    return bool(S.validate(code))


# ------------------------------------------------------------------------------------------------ community suite
COMMUNITY_STILL_ALLOWED = {
    "create_cube": "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))\n"
                   "cube = bpy.context.active_object\ncube.name = 'MyCube'\ncube.location.x += 1.5\n",
    "materials_and_nodes": "import bpy\nmat = bpy.data.materials.new(name='Red')\nmat.use_nodes = True\n"
                           "bsdf = mat.node_tree.nodes['Principled BSDF']\n"
                           "bsdf.inputs['Base Color'].default_value = (1, 0, 0, 1)\n"
                           "bpy.context.active_object.data.materials.append(mat)\n",
    "bmesh_and_math": "import bpy\nimport bmesh\nimport math\nfrom mathutils import Vector\nbm = bmesh.new()\n"
                      "bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.0)\nfor v in bm.verts:\n"
                      "    v.co += Vector((0, 0, math.sin(v.co.x)))\nmesh = bpy.data.meshes.new('Wavy')\n"
                      "bm.to_mesh(mesh)\nbm.free()\n",
    "functions_loops_fstrings": "import bpy\ndef grid(n):\n    for i in range(n):\n        for j in range(n):\n"
                                "            bpy.ops.mesh.primitive_cube_add(location=(i * 2, j * 2, 0))\n"
                                "            bpy.context.active_object.name = f'cube_{i}_{j}'\ngrid(3)\n"
                                "print(f'{len(bpy.data.objects)} objects')\n",
    "scene_iteration": "import bpy\nmeshes = [o for o in bpy.data.objects if o.type == 'MESH']\n"
                       "meshes.sort(key=repr)\nfor obj in meshes:\n    obj.select_set(True)\n"
                       "sub = bpy.data.scenes[0].render.resolution_x\nprint(sub)\n",
    "collection_link": "import bpy\nlight_data = bpy.data.lights.new(name='Sun', type='SUN')\n"
                       "light = bpy.data.objects.new(name='Sun', object_data=light_data)\n"
                       "bpy.context.collection.objects.link(light)\n",
    "try_except": "import bpy\ntry:\n    obj = bpy.data.objects['Cube']\nexcept KeyError as e:\n"
                  "    print('missing:', e)\n",
}
# allowed by the community policy, refused by glasses3d (file I/O and preferences are host tools)
COMMUNITY_NOW_REFUSED = {
    "render_to_file": "import bpy\nscene = bpy.context.scene\nscene.render.filepath = '/tmp/render.png'\n"
                      "scene.render.resolution_x = 1920\nbpy.ops.render.render(write_still=True)\n",
    "save_and_export": "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='/tmp/scene.blend')\n"
                       "bpy.ops.wm.obj_export(filepath='/tmp/scene.obj')\nbpy.ops.export_scene.fbx(filepath='/tmp/scene.fbx')\n",
    "import_and_load": "import bpy\nbpy.ops.wm.obj_import(filepath='/tmp/model.obj')\nimg = bpy.data.images.load('/tmp/tex.png')\n",
    "open_mainfile": "import bpy\nbpy.ops.wm.open_mainfile(filepath='/tmp/other.blend')\n",
    "read_preferences": "import bpy\nprefs = bpy.context.preferences\nprint(prefs.filepaths.temporary_directory)\n",
}
COMMUNITY_BLOCKED = {
    "eval": "eval('1+1')", "exec": "exec('import os')", "open_builtin": "data = open('/etc/passwd').read()\nprint(data)",
    "dunder_ladder": "x = ().__class__.__bases__[0].__subclasses__()",
    "computed_getattr_module": "import bpy\nm = getattr(bpy, 'ap' + 'p')", "type_factory": "C = type('C', (), {})",
    "import_os": "import os\nprint(os.listdir('/'))", "import_subprocess": "import subprocess\nsubprocess.run(['ls'])",
    "import_socket_mod": "import socket", "import_alias": "import bpy as b\nprint(b.data)",
    "from_os": "from os import system", "numpy_load": "import numpy\nnumpy.load('/tmp/x.npy')",
    "handlers": "import bpy\nbpy.app.handlers.frame_change_post.clear()",
    "timers": "import bpy\nbpy.app.timers.register(print)",
    "driver_add": "import bpy\nbpy.context.object.driver_add('location', 0)",
    "driver_expression": "import bpy\nfc = bpy.context.object.animation_data.drivers[0]\nfc.driver.expression = '1+1'\n",
    "register_class": "import bpy\nbpy.utils.register_class(None)", "rna_assign": "import bpy\nbpy.types.Scene.evil = None",
    "ops_script": "import bpy\nbpy.ops.script.python_file_run(filepath='/tmp/x.py')",
    "ops_text": "import bpy\nbpy.ops.text.run_script()",
    "addon_install": "import bpy\nbpy.ops.preferences.addon_install(filepath='/tmp/x.zip')",
    "extension_install_files": "import bpy\nbpy.ops.extensions.package_install_files(filepath='/tmp/x.zip', "
                               "repo='user_default', enable_on_install=True)\n",
    "extension_install_remote": "import bpy\nbpy.ops.extensions.package_install(repo_index=0, pkg_id='x')",
    "script_directories_new": "import bpy\nsd = bpy.context.preferences.filepaths.script_directories.new()\n"
                              "sd.directory = '/tmp/evil_scripts'\n",
    "script_directories_via_alias": "import bpy\nprefs = bpy.context.preferences\n"
                                    "prefs.filepaths.script_directories.new().directory = '/tmp/evil_scripts'\n",
    "script_directory_legacy": "import bpy\nbpy.context.preferences.filepaths.script_directory = '/tmp/evil'",
    "scripts_auto_execute": "import bpy\nbpy.context.preferences.filepaths.use_scripts_auto_execute = True",
    "texts": "import bpy\nt = bpy.data.texts.new('x')", "external_blend": "import bpy\nbpy.ops.wm.append(filepath='/tmp/evil.blend')",
    "libraries": "import bpy\nprint(bpy.data.libraries)", "url_open": "import bpy\nbpy.ops.wm.url_open(url='http://evil.example')",
    "save_homefile": "import bpy\nbpy.ops.wm.save_homefile()",
    "container_wrap": "import bpy\n[bpy][0].ops.script.python_file_run(filepath='/tmp/x.py')",
    "alias_walk": "import bpy\nd = bpy.data\nt = d.texts",
    "from_bpy_import": "from bpy import ops\nops.wm.append(filepath='/tmp/evil.blend')",
    "alias_ops_namespace": "import bpy\no = bpy.ops\no.wm.append(filepath='/tmp/evil.blend')",
    "namespace_as_argument": "import bpy\ndef f(m):\n    m.wm.link(filepath='/tmp/evil.blend')\nf(bpy.ops)\n",
    "module_in_container": "import bpy\nx = [bpy]\nx[0].ops.wm.append(filepath='/tmp/evil.blend')\n",
    "getattr_namespace": "import bpy\no = getattr(bpy, 'ops')", "shadow_bpy": "import bpy\nbpy = None",
    "shadow_builtin": "print = None", "lambda_call": "f = lambda: 1\nf()", "unknown_name": "mystery_helper()",
    "class_def": "class Evil:\n    pass",
}


@pytest.mark.parametrize("name", sorted(COMMUNITY_STILL_ALLOWED))
def test_community_legitimate_scripts_still_pass(name):
    assert S.validate(COMMUNITY_STILL_ALLOWED[name]) == []


@pytest.mark.parametrize("name", sorted(COMMUNITY_NOW_REFUSED))
def test_community_file_io_scripts_are_now_refused(name):
    assert denied(COMMUNITY_NOW_REFUSED[name])


@pytest.mark.parametrize("name", sorted(COMMUNITY_BLOCKED))
def test_community_escapes_stay_refused(name):
    assert denied(COMMUNITY_BLOCKED[name])


# ------------------------------------------------------------------------------------------------ panel sets
WORKER_ANGLE = {     # g3d-wp2-design/worker_angle/run_corpus.py ATTACKS
    "open": "f = open('C:/x.txt','w')", "os": "import os\nos.remove('x')", "pickle": "import pickle",
    "np.load": "import numpy as np\nnp.load('x.npy', allow_pickle=True)",
    "np.save alias": "import numpy\nnumpy.save('x', numpy.zeros(3))", "from numpy import load": "from numpy import load",
    "arr.tofile": "import numpy as np\na=np.zeros(3)\na.tofile('x')",
    "np.lib.npyio": "import numpy as np\nnp.lib.npyio.savetxt('x', [1])",
    "np._core": "import numpy as np\nnp._core.multiarray", "ctypes via np": "import numpy as np\np = np.zeros(3).ctypes.data",
    "save_as_mainfile": "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='x.blend')",
    "open_mainfile": "import bpy\nbpy.ops.wm.open_mainfile(filepath='x.blend')",
    "factory reset": "import bpy\nbpy.ops.wm.read_factory_settings(use_empty=True)",
    "render.filepath": "import bpy\nbpy.context.scene.render.filepath='C:/x.png'",
    "images.load": "import bpy\nbpy.data.images.load('C:/secret.png')",
    "image.save_render": "import bpy\nimg=bpy.data.images['x']\nimg.save_render('C:/x.png')",
    "export gltf": "import bpy\nbpy.ops.export_scene.gltf(filepath='x.glb')",
    "dunder ladder": "x = ().__class__.__bases__[0].__subclasses__()",
    "format ladder": "s = '{0.__class__.__mro__}'.format(1)", "computed format": "fmt='{0.__class__}'\ns = fmt.format(1)",
    "eval": "eval('1+1')",
    "getattr computed": "import bpy\nn='te'+'xts'\nt=getattr(bpy.data, n)",          # runtime layer
    "handlers": "import bpy\nbpy.app.handlers.load_post.append(print)",
    "driver": "import bpy\nd=bpy.context.object.driver_add('location',0)\nd.driver.expression='__import__(\"os\")'",
    "bpy alias": "import bpy as b\nb.ops.wm.save_mainfile()",
    "namespace laundering": "import bpy\no=bpy.ops.wm\no.append(directory='x')", "class": "class A: pass",
    "lambda call target": "f = lambda: 1\nf()", "subprocess": "import subprocess",
    "preferences": "import bpy\nbpy.context.preferences.filepaths.temporary_directory='C:/'",
    "kw filepath anywhere": "import bpy\nbpy.ops.object.bake(type='COMBINED', save_mode='EXTERNAL', filepath='C:/x.png')",
    "bpy.utils": "import bpy\nbpy.utils.register_class(None)", "importlib": "import importlib",
}
WORKER = {           # g3d-wp2-design/worker/corpus_static.py ADVERSARIAL
    "open": "f = open('C:/x.txt', 'w')", "np.save": "import numpy as np\nnp.save('x.npy', np.zeros(3))",
    "np.load alias": "import numpy as n\nn.load('x.npy', allow_pickle=True)",
    "from numpy import load": "from numpy import load\nload('x.npy')",
    "np.lib.format": "import numpy as np\nnp.lib.format.open_memmap('x', mode='w+')",
    "ndarray.tofile": "import numpy as np\nnp.zeros(3).tofile('x.bin')",
    "np.fromfile": "import numpy as np\nnp.fromfile('C:/Windows/win.ini')",
    "np.ctypeslib": "import numpy as np\nnp.ctypeslib.as_ctypes(np.zeros(3))",
    "arr.ctypes": "import numpy as np\np = np.zeros(3).ctypes.data", "numpy.testing import": "import numpy.testing",
    "save_mainfile": "import bpy\nbpy.ops.wm.save_mainfile(filepath='C:/x.blend')",
    "save_as alias": "import bpy as b\nb.ops.wm.save_as_mainfile(filepath='C:/x.blend', copy=True)",
    "open_mainfile": "import bpy\nbpy.ops.wm.open_mainfile(filepath='C:/x.blend')",
    "gltf export": "import bpy\nbpy.ops.export_scene.gltf(filepath='C:/x.glb')",
    "obj import": "import bpy\nbpy.ops.wm.obj_import(filepath='C:/x.obj')",
    "image load": "import bpy\nbpy.data.images.load('C:/x.png')",
    "image save": "import bpy\nimg = bpy.data.images.new('a', 4, 4)\nimg.filepath_raw = 'C:/x.png'\nimg.save()",
    "render write_still": "import bpy\nbpy.ops.render.render(write_still=True)",
    "render filepath": "import bpy\nbpy.context.scene.render.filepath = 'C:/x.png'",
    "libraries.write": "import bpy\nbpy.data.libraries.write('C:/x.blend', set())",
    "os": "import os\nos.system('calc')", "subprocess": "import subprocess", "socket": "import socket",
    "importlib": "import importlib", "__import__": "__import__('os')",
    "dunder ladder": "x = ().__class__.__bases__[0].__subclasses__()",
    "getattr computed": "import bpy\ngetattr(bpy, 'o' + 'ps')", "exec": "exec('import os')", "eval": "eval('1')",
    "pickle": "import pickle", "pathlib": "import pathlib", "io": "import io", "shutil": "import shutil",
    "ctypes": "import ctypes", "sys": "import sys", "timers": "import bpy\nbpy.app.timers.register(lambda: None)",
    "handlers": "import bpy\nbpy.app.handlers.load_post.append(lambda *a: None)",
    "driver expression": "import bpy\nd = bpy.context.object.driver_add('location', 0)\nd.driver.expression = '__import__(\"os\")'",
    "text datablock": "import bpy\nbpy.data.texts.new('x')", "launder module": "import bpy\no = bpy.ops\no.wm.save_mainfile()",
    "launder via lambda": "import bpy\nf = lambda m: m.wm.save_mainfile()\nf(bpy.ops)",
    "bpy.path": "import bpy\nbpy.path.abspath('//x')", "g3d trusted": "import g3d.export_glb as e\ne.export_glb('C:/x.glb')",
    "g3d root": "import g3d\ng3d.export_glb('x.glb')", "file pack": "import bpy\nbpy.ops.file.pack_all()",
    "image pack": "import bpy\nbpy.data.images['x'].pack()", "json + open": "import json\njson.dump({}, open('x', 'w'))",
}
LOOP = {             # g3d-wp2-design/adversarial.py DENY
    "np.load": "import numpy as np\nnp.load('x.npy', allow_pickle=True)", "np.save": "import numpy as np\nnp.save('x.npy', np.zeros(3))",
    "tofile": "import numpy as np\nnp.zeros(3).tofile('x.bin')", "ndarray.dump": "import numpy as np\nnp.zeros(3).dump('x.pkl')",
    "np.lib.npyio": "import numpy as np\nnp.lib.npyio.loadtxt('x')", "getattr np load": "import numpy as np\ngetattr(np, 'load')('x.npy')",
    "computed getattr": "import numpy as np\ngetattr(np, 'lo' + 'ad')('x.npy')", "alias launder": "import numpy as np\nm = np\nm.load('x')",
    "container launder": "import numpy as np\n[np][0].load('x')", "from numpy import load": "from numpy import load\nload('x')",
    "memmap": "import numpy as np\nnp.memmap('x', mode='w+', shape=(3,))", "ctypes": "import numpy as np\np = np.zeros(3).ctypes.data",
    "os": "import os\nos.remove('x')", "__import__": "__import__('os').system('calc')", "open": "open('x', 'w').write('y')",
    "save_as_mainfile": "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='x.blend')",
    "export": "import bpy\nbpy.ops.export_scene.gltf(filepath='x.glb')",
    "render write": "import bpy\nbpy.context.scene.render.filepath='x.png'\nbpy.ops.render.render(write_still=True)",
    "image save": "import bpy\nbpy.data.images['a'].save()", "image load": "import bpy\nbpy.data.images.load('C:/x.png')",
    "font load": "import bpy\nbpy.data.fonts.load('C:/Windows/Fonts/arial.ttf')", "texts": "import bpy\nbpy.data.texts.new('x')",
    "handlers": "import bpy\nbpy.app.handlers.load_post.append(print)",
    "dunder ladder": "x = ().__class__.__bases__[0].__subclasses__()",
    "g3d export": "from g3d.export_glb import export_glb\nexport_glb('x.glb')", "import g3d": "import g3d\ng3d.export_glb",
    "g3d lib private": "from g3d.lib import _blender", "lambda call": "(lambda: 1)()", "lambda bound call": "f = lambda: 1\nf()",
    "unpack": "import bpy\nbpy.data.images['a'].unpack()",
    "prefs": "import bpy\nbpy.context.preferences.filepaths.temporary_directory = 'C:/'",
    "np private": "import numpy as np\nnp._core.records.fromfile('x')", "read factory": "import bpy\nbpy.ops.wm.read_factory_settings()",
}
EXTRA = {            # holes found while porting (each measured or reasoned in glasses3d/plan/archive/20261019/PORT_REVIEW.md §11)
    "typing.sys": "import typing\ntyping.sys.modules['os'].system('calc')",
    "enum builtins": "import enum\nenum.bltns.open('x', 'w')", "json.codecs": "import json\njson.codecs.open('x', 'w')",
    "dataclasses.inspect": "import dataclasses\ndataclasses.inspect.currentframe()",
    "object_utils.bpy re-root": "import bpy_extras.object_utils as ou\nou.bpy.ops.wm.save_mainfile()",
    "io_utils": "import bpy_extras\nbpy_extras.io_utils.ExportHelper",
    "from typing import sys": "from typing import sys", "from json import codecs": "from json import codecs",
    "get_type_hints eval": "import typing\ndef f(x: \"__import__('os')\"): pass\ntyping.get_type_hints(f, {}, {})",
    "string.Formatter get_field": "import string\nstring.Formatter().get_field('0.__class__', (1,), {})",
    "as_strided": "import numpy as np\nfrom numpy.lib.stride_tricks import as_strided\nas_strided(np.zeros(1), (10**9,), (8,))",
    "operator namespace value": "import bpy\no = bpy.ops.object\no.bake_image()",
    "np.random module value": "import numpy as np\nr = np.random\nr.mtrand",
    "np.core": "import numpy as np\nnp.core.multiarray", "path_resolve": "import bpy\nbpy.data.path_resolve('texts')",
    "draw handler": "import bpy\nbpy.types.SpaceView3D.draw_handler_add(print, (), 'WINDOW', 'POST_VIEW')",
    "clipboard": "import bpy\nbpy.context.window_manager.clipboard = 'x'",
    "fluid bake": "import bpy\nbpy.ops.fluid.bake_all()", "gn bake": "import bpy\nbpy.ops.object.geometry_node_bake_single()",
    "bake save_mode": "import bpy\nbpy.ops.object.bake(type='NORMAL', save_mode='EXTERNAL')",
    "format_map": "s = '{x}'.format_map({'x': 1})", "walrus": "if (n := 3):\n    pass",
    "async": "async def f():\n    pass", "global": "def f():\n    global x\n    x = 1",
    "yield": "def g():\n    yield 1", "match": "match 1:\n    case 1:\n        pass",
    "decorator": "def d(f):\n    return f\n@d\ndef g():\n    pass",
    "relative import": "from . import x", "star import": "from math import *",
    "np rebind module": "import numpy as np\nnp = 3", "alias two modules": "import numpy as m\nimport bmesh as m",
    "loop var call": "fns = [print]\nfor f in fns:\n    f('x')", "subscript call": "fns = [print]\nfns[0]('x')",
}
RUNTIME_ONLY = {"getattr computed (worker_angle)", "computed getattr bpy.data"}


def _runtime_refuses(code: str) -> bool:
    """Run `code` with the runtime builtins against fake bpy/numpy modules; True when a layer refuses."""
    data = types.SimpleNamespace(texts="TEXTS")
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = data
    guard = S.RuntimeGuard()
    ns = {"__builtins__": guard.builtins(), "bpy": fake_bpy, "np": np}
    had = sys.modules.get("bpy")
    sys.modules["bpy"] = fake_bpy                          # `import bpy` resolves to the fake on the host
    try:
        exec(compile(code, "<snippet>", "exec"), ns)
    except PermissionError:
        return True
    finally:
        if had is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = had
    return bool(guard.refusals)


@pytest.mark.parametrize("source,name", [("worker_angle", k) for k in WORKER_ANGLE] + [("worker", k) for k in WORKER]
                         + [("loop", k) for k in LOOP] + [("extra", k) for k in EXTRA])
def test_every_adversarial_snippet_is_refused(source, name):
    code = {"worker_angle": WORKER_ANGLE, "worker": WORKER, "loop": LOOP, "extra": EXTRA}[source][name]
    if denied(code):
        return
    # the only snippets the static walk passes are computed getattr names: the runtime layer must refuse them
    assert "getattr" in code and _runtime_refuses(code), f"{source}/{name} passed every layer"


def test_adversarial_union_size():
    union = {c for d in (WORKER_ANGLE, WORKER, LOOP) for c in d.values()}
    assert len(WORKER_ANGLE) == 33 and len(WORKER) == 47 and len(LOOP) == 33
    assert len(union) == 96                       # 113 snippets, 17 shared between the panel designs


# ------------------------------------------------------------------------------------------------ legitimate code
LEGIT = {
    "numpy foreach": "import bpy\nimport numpy as np\nme = bpy.data.objects['Lens_L'].data\n"
                     "co = np.empty(len(me.vertices) * 3, dtype=np.float64)\nme.vertices.foreach_get('co', co)\n"
                     "co = co.reshape(-1, 3)\nco[:, 2] += 0.2 * np.clip((co[:, 1] - 10.0) / 5.0, 0.0, 1.0)\n"
                     "me.vertices.foreach_set('co', co.ravel())\nme.update()\nresult = {'moved': int((co[:, 2] != 0).sum())}\n",
    "numpy linalg + lambda key": "import numpy as np\nfrom numpy.linalg import svd\n"
                                 "pts = np.random.default_rng(0).normal(size=(50, 3))\nu, s, vt = svd(pts - pts.mean(0))\n"
                                 "order = sorted(range(3), key=lambda i: -s[i])\nprint(order, np.round(vt[2], 3))\n",
    "prebound np without import": "a = np.zeros((3, 3))\nresult = float(np.linalg.det(a + np.eye(3)))",
    "from numpy import linalg": "from numpy import linalg\nresult = linalg.norm([3, 4])",
    "polynomial": "import numpy as np\nc = np.polynomial.polynomial.polyfit([0, 1, 2], [1, 3, 5], 1)\nresult = c.tolist()",
    "sliding window": "import numpy as np\nfrom numpy.lib.stride_tricks import sliding_window_view\n"
                      "result = sliding_window_view(np.arange(5), 3).tolist()",
    "g3d facade": "r = g3d.polygon_section([(0, 0), (1, 0), (0, 1)])\nresult = r",
    "g3d lib import": "from g3d.lib import loft_sections, check_round_corners\nresult = check_round_corners([])\n",
    "generated texture": "import bpy\nimg = bpy.data.images.new('grain', 256, 256)\n"
                         "img.pixels.foreach_set([0.5] * (256 * 256 * 4))\nimg.update()\n",
    "json dumps": "import json, bpy\nprint(json.dumps({'objects': [o.name for o in bpy.data.objects]}))\n",
    "bvh": "import bpy\nfrom mathutils.bvhtree import BVHTree\nfrom mathutils import Vector\nob = bpy.data.objects['Brow_Left']\n"
           "dg = bpy.context.evaluated_depsgraph_get()\nt = BVHTree.FromObject(ob, dg)\n"
           "hit = t.ray_cast(Vector((0, 0, 50)), Vector((0, 0, -1)))\n",
    "bmesh ops": "import bpy, bmesh\nbm = bmesh.new()\nbmesh.ops.create_cube(bm, size=10)\nme = bpy.data.meshes.new('Box')\n"
                 "bm.to_mesh(me)\nbm.free()\nob = bpy.data.objects.new('Box', me)\n"
                 "bpy.context.scene.collection.objects.link(ob)\nob['partRole'] = 'frame'\n",
    "getattr literal default": "import bpy\nm = bpy.data.materials.new('M')\nnt = getattr(m, 'node_tree', None)",
    "setattr computed": "import bpy\nm = bpy.data.materials.new('M')\nfor k, v in {'roughness': 0.2, 'metallic': 0.0}.items():\n"
                        "    setattr(m, k, v)\n",
    "hasattr private": "x = [1]\nresult = hasattr(x, '__len__')",
    "f-string and literal format": "x = 1.23456\nresult = [f'{x:.2f}', '{:.3f} mm'.format(x)]",
    "param call (Studio uvx)": "def uv_rot(a, b):\n    return (b, a)\ndef sweep(pts, uvx=None):\n"
                               "    return [uvx(p[0], p[1]) for p in pts] if uvx else pts\nresult = sweep([(1, 2)], uvx=uv_rot)",
    "module function alias (Studio nz)": "from mathutils import noise, Vector\nnz = noise.noise\nresult = nz(Vector((0.1, 0.2, 0.3)))",
    "own np variable (Studio corpus)": "from mathutils import Vector\nxy = Vector((1, 2))\nnp = xy + Vector((1, 1))\n"
                                       "result = [np.x, np.y]",
    "temp_override": "import bpy\nwith bpy.context.temp_override(scene=bpy.context.scene):\n    pass",
    "scene notes": "import bpy\nbpy.context.scene['notes'] = 'built from the photos'",
    "bake into image": "import bpy\nbpy.ops.object.bake(type='NORMAL')",
}


@pytest.mark.parametrize("name", sorted(LEGIT))
def test_legitimate_snippets_pass(name):
    assert S.validate(LEGIT[name]) == [], [str(v) for v in S.validate(LEGIT[name])]


@pytest.mark.parametrize("name", sorted(S.G3D_LIB_NAMES))
def test_every_registered_facade_helper_passes_static_and_runtime_routes(name):
    from glasses3d.g3d import lib
    assert S.G3D_LIB_NAMES == frozenset(lib.NAMES)
    for code in (f"result = g3d.{name}()",
                 f"from g3d import {name}\nresult = {name}()",
                 f"from g3d.lib import {name} as helper\nresult = helper()"):
        assert S.validate(code) == [], code
    facade = types.ModuleType("g3d.lib")
    marker = object()
    setattr(facade, name, marker)
    guard = S.RuntimeGuard(g3d_facade=facade)
    builtins = guard.builtins()
    for module in ("g3d", "g3d.lib"):
        assert builtins["__import__"](module, None, None, (name,)) is facade
    assert builtins["getattr"](facade, name) is marker
    assert f"g3d.{name}" in S.HINTS["g3d"]


@pytest.mark.parametrize("name", ["quadratic_lens", "mesh_cleanup", "consistent_normals",
                                  "symmetrize_mesh", "controlled_decimate"])
def test_new_facade_helpers_do_not_expose_implementation_or_function_globals(name):
    assert S.validate(f"result = g3d.{name}.__globals__")
    assert S.validate(f"from g3d.{name} import build")
    facade = types.ModuleType("g3d.lib")
    guard = S.RuntimeGuard(g3d_facade=facade)
    with pytest.raises(PermissionError):
        guard.builtins()["__import__"](f"g3d.{name}")
    with pytest.raises(PermissionError):
        guard.builtins()["getattr"](lambda: None, "__globals__")


@pytest.mark.parametrize("name", ["seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire"])
def test_seating_operations_require_the_trusted_host_tools(name):
    for code in (f"g3d.{name}()", f"from g3d.lib import {name}", f"getattr(g3d, '{name}')"):
        assert S.validate(code), code
    facade = types.ModuleType("g3d.lib")
    guard = S.RuntimeGuard(g3d_facade=facade)
    with pytest.raises(PermissionError):
        guard.builtins()["__import__"]("g3d.lib", None, None, (name,))
    with pytest.raises(PermissionError):
        guard.builtins()["getattr"](facade, name)


def test_prebound_names_and_own_variables():
    assert S.analyse("x = np.zeros(2)").prebound["np"] == "numpy"
    assert "np" not in S.analyse("np = 3\nresult = np + 1").prebound      # the snippet's own variable
    assert S.analyse("np = 3").violations == []
    assert S.analyse("import numpy as np\nnp = 3").violations           # an imported module is never rebound
    assert S.analyse("bpy = 1").violations                               # bpy can never be rebound


# ------------------------------------------------------------------------------------------------ messages
def test_all_violations_are_collected_with_lines():
    code = "import os\nx = 1\neval('1')\nimport bpy\nbpy.ops.wm.save_mainfile()\n"
    lines = sorted({v.line for v in S.validate(code)})
    assert lines == [1, 3, 5]


@pytest.mark.parametrize("code,hint", [
    ("import bpy\nbpy.ops.wm.save_as_mainfile(filepath='x.blend', copy=True)", "save_checkpoint"),
    ("import bpy\nbpy.ops.render.render(write_still=True)", "render_views"),
    ("import bpy\nbpy.context.scene.render.filepath = 'x.png'", "render_views"),
    ("import bpy\nbpy.ops.export_scene.gltf(filepath='x.glb')", "export_glb"),
    ("import bpy\nf = bpy.data.fonts.load('C:/Windows/Fonts/arial.ttf')", "load_font"),
    ("import bpy\nt = bpy.data.texts.new('notes')", "scene custom properties"),
    ("import bpy\nimg = bpy.data.images['a']\nimg.save()", "packed into the next checkpoint"),
    ("import bpy\nbpy.data.images['a'].pack()", "packed into the next checkpoint"),
    ("import numpy as np\nnp.save('x', np.zeros(3))", "keep arrays in memory"),
    ("import bpy\nbpy.ops.ed.undo()", "restore_checkpoint"),
])
def test_refusals_name_the_replacement_tool(code, hint):
    found = S.validate(code)
    assert found and any(hint in (v.use_instead or "") for v in found), [str(v) for v in found]


def test_limits():
    assert "bytes" in S.validate("x = 1\n" * 40_000)[0].message
    assert "AST nodes" in S.validate("x = [" + "1," * 40_001 + "]")[0].message
    nested = "x = " + "[" * 40 + "1" + "]" * 40
    assert "nests deeper" in S.validate(nested)[0].message
    assert S.validate("def broken(:\n    pass")[0].message.startswith("syntax error")
    assert S.validate(3)[0].message.startswith("code must be a string")
    assert (S.MAX_CODE_BYTES, S.MAX_AST_NODES, S.MAX_NESTING_DEPTH) == (200_000, 40_000, 32)


def test_escape_edge_table_names_only_allowed_modules():
    for mod, attrs in S.ESCAPE_EDGES.items():
        assert mod in S.MODULE_PATHS
        for a in attrs:
            assert S.path_rule(f"{mod}.{a}") is not None


# ------------------------------------------------------------------------------------------------ runtime layer 1-2
def test_runtime_builtins_have_no_escape_hatches():
    b = S.RuntimeGuard().builtins()
    for name in ("open", "eval", "exec", "compile", "globals", "vars", "dir", "memoryview", "super", "object",
                 "input", "breakpoint", "__build_class__", "exit", "quit", "help"):
        assert name not in b
    assert b["getattr"] is not getattr and b["__import__"] is not __import__


def test_runtime_guard_refuses_computed_names_and_records_them():
    guard = S.RuntimeGuard()
    b = guard.builtins()
    data = types.SimpleNamespace(texts=1, objects=2)
    assert b["getattr"](data, "obj" + "ects") == 2
    with pytest.raises(PermissionError):
        b["getattr"](data, "te" + "xts")
    with pytest.raises(PermissionError):
        b["getattr"](data, "__" + "class__")
    with pytest.raises(PermissionError):
        b["setattr"](data, "file" + "path", "x")
    import json
    with pytest.raises(PermissionError):
        b["getattr"](json, "co" + "decs")                # a module edge out of the allowlist
    with pytest.raises(PermissionError):
        b["getattr"](np.random, "mt" + "rand")
    holder = types.SimpleNamespace(m=os)
    with pytest.raises(PermissionError):
        b["getattr"](holder, "m")                         # a computed lookup that yields a foreign module
    assert b["hasattr"](data, "__dict__") is True         # a bool grants nothing
    assert len(guard.refusals) == 6


def test_runtime_import_guard():
    facade = types.ModuleType("g3d.lib")
    guard = S.RuntimeGuard(g3d_facade=facade)
    imp = guard.builtins()["__import__"]
    assert imp("numpy") is np and imp("math").sqrt(4) == 2
    assert imp("g3d") is facade and imp("g3d.lib", None, None, ("polygon_section",)) is facade
    for bad, fromlist in (("os", ()), ("numpy", ("load",)), ("json", ("codecs",)), ("numpy.testing", ()),
                          ("importlib", ()), ("numpy", ("_core",))):
        with pytest.raises(PermissionError):
            imp(bad, None, None, fromlist)
    with pytest.raises(PermissionError):
        imp("math", None, None, (), 1)                     # relative


def test_runtime_layer_refuses_code_that_skipped_the_validator():
    assert _runtime_refuses("import os")
    assert _runtime_refuses("n = 'te' + 'xts'\nt = getattr(bpy.data, n)")
    assert _runtime_refuses("from numpy import load")
    assert not _runtime_refuses("import math\nresult = math.sqrt(2)")


# ------------------------------------------------------------------------------------------------ runtime layer 3
@pytest.fixture(scope="module")
def audit(tmp_path_factory):
    allowed = tmp_path_factory.mktemp("allowed")
    (allowed / "ok.txt").write_text("fine")
    g = S.AuditGuard([allowed, os.path.dirname(os.__file__)])
    g.install()
    return g, allowed


def test_audit_hook_refuses_writes_reads_outside_and_processes(audit, tmp_path):
    g, allowed = audit
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    g.arm()
    try:
        assert open(allowed / "ok.txt").read() == "fine"
        with pytest.raises(PermissionError):
            open(allowed / "new.txt", "w")
        with pytest.raises(PermissionError):
            open(outside).read()
        with pytest.raises(PermissionError):
            os.open(str(allowed / "flags.txt"), os.O_WRONLY | os.O_CREAT)
        with pytest.raises(PermissionError):
            subprocess.run([sys.executable, "-c", "pass"])
        with pytest.raises(PermissionError):
            os.listdir(allowed)
        with pytest.raises(PermissionError):
            import socket
            socket.getaddrinfo("example.com", 80)
    finally:
        refusals = g.disarm()
    assert len(refusals) >= 6
    assert not (allowed / "new.txt").exists()
    assert open(outside).read() == "secret"               # disarmed: the hook is inert


def test_audit_hook_is_armed_only_on_its_thread(audit, tmp_path):
    g, _ = audit
    target = tmp_path / "thread.txt"
    out = {}

    def other():
        try:
            target.write_text("from another thread")
            out["ok"] = True
        except PermissionError:
            out["ok"] = False
    g.arm()
    try:
        t = threading.Thread(target=other)
        t.start()
        t.join()
    finally:
        g.disarm()
    assert out["ok"] is True and target.read_text() == "from another thread"


@pytest.mark.parametrize("field", ["0.gi_frame", "0.gi_code", "0.gi_frame.f_globals", "0.mro",
                                  "0.co_names", "0.load", "0.as_pointer", "0[gi_frame]", "0[x].mro"])
def test_format_fields_use_the_attribute_policy(field):
    code = "result = " + repr("{" + field + "}") + ".format((i for i in range(1)))"
    assert S.validate(code)


def test_nested_format_spec_fields_use_the_attribute_policy():
    assert S.validate("result = '{0:{1.gi_frame}}'.format(1, (i for i in range(1)))")
    assert not S.validate("result = '{0:0{1}d}'.format(3, 4)")


@pytest.mark.parametrize("attribute", ["gi_frame", "gi_code"])
def test_armed_audit_guard_blocks_generator_frame_and_code(audit, attribute):
    guard, _ = audit
    template = "{0." + attribute + "}"
    generator = (i for i in range(1))
    guard.arm()
    try:
        with pytest.raises(PermissionError, match=attribute):
            template.format(generator)
    finally:
        refusals = guard.disarm()
    assert f"reading {attribute!r}" in refusals
