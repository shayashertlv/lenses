"""The export-time winding repair orients on the position-welded topology (test-pilot-002, AT-03 / F3).

Both paid contract failures of run 2 were closed, orientable solids: r0001's 'Crystal one piece front' (2,123 misoriented
edges, one welded component) and r0003's 'Tip inscription L/R' (373 each, seven glyph components). The old repair ran
bmesh's recalc on the UNWELDED source (gl.apply_modifiers had baked shade_smooth's edge split into it), oriented every
disconnected island on its own and turned r0001's 4 bad edges into 2,127; its note counted 12,800 boundary edges where the
contract's welded topology has none. Here the repair welds by position at the contract's 1e-3 mm, orients by BFS on the
welded graph, writes the winding back to the split faces (no weld of the mesh itself), and never writes a result that is
not better; the note's counts, the inventory and the host's exported topology agree. Everything runs in host Blender
through the harness on copies of the run-2 parts; the module skips when Blender or the job data is absent."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import numpy as np

from modeler.paths import blender_executable
from modeler.worker import run_harness

ROOT = Path(__file__).resolve().parents[1]
RUN2 = ROOT / "data" / "modeler" / "agentic" / "test-pilot-002" / "revisions"
R0001 = RUN2 / "r0001" / "build" / "parts.npz"
R0003 = RUN2 / "r0003" / "build" / "parts.npz"
FRONT = "Crystal one piece front"
TIPS = ("Tip inscription L", "Tip inscription R")

LOAD_PROGRAM = '''
import json
before = {}
for npz, name in LOADS:
    z = np.load(npz)
    V, F = z[name + "__V"].astype(float), z[name + "__F"].astype(np.int64)
    # the inventory's counts on the parts as delivered (they must equal the contract's)
    before[name] = gl.inventory({name: {"part": "frame", "component": name, "V": V, "F": F, "materials": [None]}})[0]
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], F.tolist())         # as delivered: split vertices, not welded
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    gl.register(obj, "frame", name)
gl.note("before " + json.dumps(before))
'''

SYNTH_PROGRAM = '''
import json
import bmesh as _bm


def link(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return gl.register(obj, "frame", name)


def split_sphere(name, patch_x=4.0, inside_out=False, cap_z=None):
    """An icosphere (80 faces, r 10 mm) split into disconnected faces, with the faces beyond x = patch_x flipped."""
    bm = _bm.new()
    _bm.ops.create_icosphere(bm, subdivisions=2, radius=10.0)
    if cap_z is not None:
        _bm.ops.delete(bm, geom=[f for f in bm.faces if f.calc_center_median().z > cap_z], context="FACES")
    _bm.ops.split_edges(bm, edges=list(bm.edges))
    if inside_out:
        _bm.ops.reverse_faces(bm, faces=list(bm.faces))
    _bm.ops.reverse_faces(bm, faces=[f for f in bm.faces if f.calc_center_median().x > patch_x])
    return link(name, bm)


split_sphere("split_closed")
split_sphere("split_inside_out", inside_out=True)
split_sphere("split_open", cap_z=7.0)

# a Moebius strip: one edge is misoriented however it is wound; a re-orientation cannot improve it
bm = _bm.new()
M = 48
top, bot = [], []
for i in range(M):
    t = 2 * math.pi * i / M
    c = Vector((30 * math.cos(t), 30 * math.sin(t), 0.0))
    radial = Vector((math.cos(t), math.sin(t), 0.0))
    w = radial * math.cos(t / 2) * 4.0 + Vector((0.0, 0.0, 1.0)) * math.sin(t / 2) * 4.0
    top.append(bm.verts.new(c + w))
    bot.append(bm.verts.new(c - w))
for i in range(M):
    j = i + 1
    b1, t1 = (bot[j], top[j]) if j < M else (top[0], bot[0])      # the half twist joins the ends swapped
    bm.faces.new((bot[i], b1, t1))
    bm.faces.new((bot[i], t1, top[i]))
link("mobius", bm)

# the r0001 chain: plate_with_holes (shade_smooth's edge split) + bevel + apply_modifiers, then one flipped face
plate = gl.plate_with_holes(gl.rounded_rect(60.0, 40.0, 8.0, n=64), [gl.rounded_rect(40.0, 24.0, 6.0, n=64)], 0.0, 4.0,
                            "plate", "frame", "front")
gl.bevel(plate, 0.5)
gl.apply_modifiers(plate)
bm = gl.bmesh_of(plate)
src = {"modifiers": [m.name for m in plate.modifiers], "boundary": sum(1 for e in bm.edges if e.is_boundary)}
bm.faces.ensure_lookup_table()
_bm.ops.reverse_faces(bm, faces=[bm.faces[0]])
bm.to_mesh(plate.data)
bm.free()
plate.data.update()
gl.note("source " + json.dumps(src))
'''


def build(program: str, root: Path) -> dict:
    (root / "p.py").write_text(program, encoding="utf-8")
    r = run_harness({"modules": [{"name": "p", "path": str(root / "p.py")}], "export": True, "save_blend": False,
                     "renders": []}, root / "out", time_limit_s=300)
    return r


def failure_text(r: dict) -> str:
    return json.dumps({"module_results": r.get("module_results"), "error": r.get("error"),
                       "stderr_tail": (r.get("process") or {}).get("stderr_tail")}, indent=1)[:4000]


def object_note(r: dict, name: str) -> str:
    notes = [n for n in r.get("notes") or [] if n.startswith(name + ":")]
    return " | ".join(notes)


def tagged(r: dict, tag: str) -> dict:
    return json.loads(next(n for n in r["notes"] if n.startswith(tag + " "))[len(tag) + 1:])


def contract_topology(V, F) -> dict:
    from bsa import contract
    return contract.topology(np.asarray(V, float) / 1000.0, np.asarray(F, np.int64))


def component_volumes(V, F) -> np.ndarray:
    """Signed volume of every position-welded (1e-3 mm) edge-connected component."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    key = np.round(V / 1e-3).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    W = inv.ravel()[F]
    e = np.r_[W[:, [0, 1]], W[:, [1, 2]]]
    n = int(W.max()) + 1
    _, lab = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)), directed=False)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    vol = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    return np.bincount(lab[W[:, 0]], weights=vol)[np.unique(lab[W[:, 0]])]


def load_out(r: dict, name: str):
    z = np.load(r["parts_npz"])
    return z[name + "__V"].astype(float), z[name + "__F"].astype(np.int64)


@unittest.skipIf(blender_executable() is None or not (R0001.is_file() and R0003.is_file()),
                 "Blender or the test-pilot-002 r0001/r0003 builds not present")
class Run2FailedParts(unittest.TestCase):
    """The two parts that failed run 2's contract, loaded from copies of their parts.npz exactly as delivered (split
    vertices), repaired by the export: watertight, outward, and the counts in the note equal the contract's."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        shutil.copy2(R0001, root / "r0001.npz")
        shutil.copy2(R0003, root / "r0003.npz")
        loads = [(str(root / "r0001.npz"), FRONT)] + [(str(root / "r0003.npz"), n) for n in TIPS]
        cls.inputs = {}
        for npz, name in loads:
            z = np.load(npz)
            cls.inputs[name] = (z[name + "__V"].astype(float), z[name + "__F"].astype(np.int64))
        cls.r = build("LOADS = " + json.dumps(loads) + "\n" + LOAD_PROGRAM, root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_inputs_are_the_delivered_failures(self):
        self.assertEqual(contract_topology(*self.inputs[FRONT])["misoriented_edges"], 2123)
        for name in TIPS:
            self.assertEqual(contract_topology(*self.inputs[name])["misoriented_edges"], 373)

    def test_the_inventory_counts_what_the_contract_counts(self):
        # delivered: the inventory said 0 boundary / 6 nonmanifold / 2135 misoriented, the contract 0 / 0 / 2123
        self.assertTrue(self.r["ok"], failure_text(self.r))
        before = tagged(self.r, "before")
        for name, (V, F) in self.inputs.items():
            topo = contract_topology(V, F)
            row = before[name]
            self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]),
                             (topo["boundary_edges"], topo["nonmanifold_edges"], topo["misoriented_edges"]), name)

    def test_the_front_is_repaired_watertight_and_outward(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        row = {x["object"]: x for x in self.r["inventory"]}[FRONT]
        self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0), row)
        self.assertTrue(row["closed"], row)
        self.assertGreater(row["signed_volume_mm3"], 0.0)
        self.assertEqual(row["vertices"], len(self.inputs[FRONT][0]), "the source mesh is re-wound, not welded")
        topo = contract_topology(*load_out(self.r, FRONT))
        self.assertTrue(topo["watertight"], topo)
        note = object_note(self.r, FRONT)
        self.assertIn("2123 inconsistently wound edges", note)
        self.assertIn("normals made consistent at export", note)
        self.assertNotIn("remain", note)
        self.assertNotIn("open", note)

    def test_the_inscriptions_are_repaired_and_every_glyph_points_outward(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        rows = {x["object"]: x for x in self.r["inventory"]}
        for name in TIPS:
            row = rows[name]
            self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0), row)
            V, F = load_out(self.r, name)
            self.assertTrue(contract_topology(V, F)["watertight"], name)
            vols = component_volumes(V, F)
            self.assertEqual(len(vols), 7, name)
            self.assertTrue((vols > 0).all(), (name, vols))
            note = object_note(self.r, name)
            self.assertIn("373 inconsistently wound edges", note)
            self.assertNotIn("remain", note)


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class SyntheticWelds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.r = build(SYNTH_PROGRAM, cls.root)
        cls.rows = {x["object"]: x for x in cls.r.get("inventory") or []}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def hosted(self, name: str) -> dict:
        """The host's exported topology row (what the build reply's repair hints are built from)."""
        from modeler.agentic import tools
        rows = tools.exported_object_topology(self.r["parts_npz"], self.r["materials_json"])
        return next(x for x in rows if x["object"] == name)

    def test_a_closed_solid_in_disconnected_faces_is_repaired_without_a_weld(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        for name in ("split_closed", "split_inside_out"):
            row = self.rows[name]
            self.assertTrue(row["closed"], row)
            self.assertEqual(row["misoriented_edges"], 0, row)
            self.assertGreater(row["signed_volume_mm3"], 0.0, name)
            self.assertEqual(row["vertices"], 3 * row["triangles"], "the split vertices (and their UVs, normals) are kept")
            self.assertTrue(self.hosted(name)["watertight"], name)
            note = object_note(self.r, name)
            self.assertIn("normals made consistent at export", note)
            self.assertNotIn("open", note)
            self.assertNotIn("remain", note)

    def test_an_open_mesh_is_rewound_and_still_reported_open_with_one_count(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        row = self.rows["split_open"]
        self.assertEqual(row["misoriented_edges"], 0, row)
        self.assertGreater(row["boundary_edges"], 0)
        self.assertFalse(row["closed"])
        hosted = self.hosted("split_open")
        self.assertEqual(hosted["boundary_edges"], row["boundary_edges"])
        note = object_note(self.r, "split_open")
        m = re.search(r"open \((\d+) boundary edges", note)
        self.assertIsNotNone(m, note)
        self.assertEqual(int(m.group(1)), row["boundary_edges"], note)
        # the majority (outward) orientation of the open sheet is kept: every face points away from the centre
        V, F = load_out(self.r, "split_open")
        n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        self.assertTrue((np.einsum("ij,ij->i", n, V[F].mean(1)) > 0).all())

    def test_a_non_orientable_mesh_is_left_unchanged_and_says_so(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        row = self.rows["mobius"]
        self.assertEqual(row["misoriented_edges"], 1, row)
        self.assertEqual(self.hosted("mobius")["misoriented_edges"], 1)
        note = object_note(self.r, "mobius")
        self.assertIn("1 inconsistently wound edges", note)
        self.assertIn("left unchanged", note)
        self.assertNotIn("normals made consistent", note)

    def test_apply_modifiers_keeps_the_smoothing_split_live(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        src = tagged(self.r, "source")
        self.assertEqual(src["boundary"], 0, "apply_modifiers baked shade_smooth's edge split into the source mesh")
        self.assertIn("mdl_smooth", src["modifiers"])
        row = self.rows["plate"]
        self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0), row)
        self.assertGreater(row["signed_volume_mm3"], 0.0)
        self.assertTrue(self.hosted("plate")["watertight"])


if __name__ == "__main__":
    unittest.main()
