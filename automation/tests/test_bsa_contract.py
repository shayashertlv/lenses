"""bsa.contract (fault injection on the synthetic export) and bsa.archeck (actual AR runtime smoke)."""
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bsa import archeck, contract, export
from bsa.core import AUTOMATION, DATA


def patch_glb(src: Path, dst: Path, edit_doc=None, edit_bin=None):
    raw = src.read_bytes()
    n = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20 + n])
    binary = bytearray(raw[20 + n + 8:])
    if edit_doc:
        edit_doc(doc)
    if edit_bin:
        edit_bin(doc, binary)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + bytes(binary)
    dst.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return dst


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.parts, cls.mats = export.synthetic_parts()
        cls.good = cls.dir / "good.glb"
        cls.receipt = export.write_glb(cls.parts, cls.mats, cls.good)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fails(self, path, *names):
        r = contract.check(path)
        self.assertFalse(r["ok"])
        for name in names:
            self.assertIn(name, r["failures"], r["failures"])
        return r

    def test_synthetic_passes(self):
        r = contract.check(self.good)
        self.assertTrue(r["ok"], r["failures"])
        self.assertEqual(r["summary"]["runtime_lens_meshes"], 2)
        self.assertEqual(r["parts"]["lens_R"]["profile"], "front_sheet")
        self.assertTrue(r["parts"]["frame"]["watertight"])

    def test_units_fault(self):
        parts = {k: dict(v, V=v["V"] * 10) for k, v in self.parts.items()}
        p = self.dir / "units.glb"
        export.write_glb(parts, self.mats, p)
        self.fails(p, "units_width_m")

    def test_backwards_fault(self):
        parts = {}
        for k, v in self.parts.items():
            V = v["V"] * np.array([-1, 1, -1])
            parts[k] = dict(v, V=V)
        parts["lens_R"], parts["lens_L"] = parts["lens_L"], parts["lens_R"]
        parts["temple_R"], parts["temple_L"] = parts["temple_L"], parts["temple_R"]
        p = self.dir / "back.glb"
        export.write_glb(parts, self.mats, p)
        self.fails(p, "front_is_plus_z")

    def test_origin_fault(self):
        o, _ = export.bridge_underside_mm(self.parts)
        p = self.dir / "origin.glb"
        export.write_glb(self.parts, self.mats, p, origin_mm=o + [0, 5.0, 0])
        self.fails(p, "origin_bridge_underside")

    def test_node_transform_fault(self):
        p = patch_glb(self.good, self.dir / "xf.glb", edit_doc=lambda d: d["nodes"][0].update(translation=[0, 0.01, 0]))
        self.fails(p, "identity_transforms")

    def test_lens_not_detected_fault(self):
        def strip(d):
            for m in d["materials"]:
                m.get("extensions", {}).pop("KHR_materials_transmission", None)
        p = patch_glb(self.good, self.dir / "nolens.glb", edit_doc=strip)
        self.fails(p, "lens_detection")

    def test_open_frame_fault(self):
        parts = dict(self.parts)
        parts["frame"] = dict(parts["frame"], F=parts["frame"]["F"][:-20])
        p = self.dir / "open.glb"
        export.write_glb(parts, self.mats, p)
        r = self.fails(p, "watertight_parts")
        self.assertGreater(r["parts"]["frame"]["boundary_edges"], 0)

    def test_nan_fault(self):
        def poison(doc, binary):
            acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
            off = doc["bufferViews"][acc["bufferView"]]["byteOffset"]
            binary[off:off + 4] = struct.pack("<f", float("nan"))
        p = patch_glb(self.good, self.dir / "nan.glb", edit_bin=poison)
        self.fails(p, "finite")

    def test_texture_size_fault(self):
        mats = dict(self.mats)
        mats["frame"] = dict(mats["frame"], base_color_texture=np.full((64, 3000, 3), 90, np.uint8))
        real = export.encode_texture
        with mock.patch.object(export, "encode_texture", lambda src, **k: real(src, max_px=4096)):
            p = self.dir / "tex.glb"
            export.write_glb(self.parts, mats, p)
        self.fails(p, "textures")

    def test_triangle_and_byte_limits(self):
        with mock.patch.object(contract, "MAX_TRIANGLES", 1000), mock.patch.object(contract, "MAX_BYTES", 1000):
            self.fails(self.good, "triangles", "bytes")

    def test_solid_lens_accepted_double_sided_reported(self):
        p = self.dir / "solid.glb"
        mats = dict(self.mats)
        mats["lens"] = dict(mats["lens"], double_sided=True)
        r = export.write_glb(self.parts, mats, p, lens_profile="solid")
        self.assertIn("lens_R_lens_material_double_sided", r["flags"])
        c = contract.check(p)
        self.assertEqual(c["parts"]["lens_R"]["profile"], "solid")
        self.assertTrue(c["parts"]["lens_R"]["double_sided"])

    def test_garbage_does_not_raise(self):
        p = self.dir / "junk.glb"
        p.write_bytes(b"not a glb at all")
        r = contract.check(p)
        self.assertEqual(r["failures"], ["parse"])

    @unittest.skipUnless((DATA / "segmented-pipeline-v1/jobs/vb/candidate.glb").exists(), "cached candidate missing")
    def test_real_candidate_smoke(self):
        r = contract.check(DATA / "segmented-pipeline-v1/jobs/vb/candidate.glb")
        self.assertTrue(r["checks"]["parse"]["pass"])
        self.assertTrue(r["checks"]["lens_detection"]["pass"])
        self.assertEqual(r["summary"]["runtime_lens_meshes"], 2)
        self.assertIn("node_names", r["failures"])     # foreign naming is not the BSA contract


def _ar_available() -> bool:
    return bool(shutil.which("node")) and (AUTOMATION.parent / "ar" / "node_modules" / "@playwright").exists()


class ArCheckTest(unittest.TestCase):
    def test_manifest_and_ids(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.glb"
            export.write_glb(*export.synthetic_parts(), p)
            m, ids = archeck.write_manifest({"BSA vb/m1": p}, Path(t))
            doc = json.loads(m.read_text())
            self.assertEqual(ids, {"bsa-vb-m1": "BSA vb/m1"})
            self.assertEqual(doc["ar_views"], [{"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35}])
            self.assertAlmostEqual(doc["cases"][0]["width_mm"], 140.0, places=1)
            self.assertNotIn("environments", doc)

    @unittest.skipUnless(_ar_available(), "node / ar playwright not installed")
    def test_synthetic_loads_in_actual_runtime(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "synthetic.glb"
            export.write_glb(*export.synthetic_parts(), p)
            r = archeck.run({"synthetic": p}, Path(t) / "ar")
            m = r["models"]["synthetic"]
            self.assertEqual(m["status"], "runtime_compatible", m.get("error"))
            self.assertEqual(m["optical_meshes_detected"], 2)
            self.assertEqual(m["lens_mesh_names"], ["lens_L", "lens_R"])
            self.assertTrue(m["synthetic_fit_ready"])
            self.assertIsNone(m["continuity_failure"])
            self.assertEqual(len(m["renders"]), 2)
            self.assertTrue(all(Path(x).exists() for x in m["renders"]))
            self.assertTrue(r["all_compatible_with_lenses"])


if __name__ == "__main__":
    unittest.main()
