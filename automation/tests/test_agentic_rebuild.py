"""``python -m modeler.agentic rebuild``: a no-inference rebuild of one revision's sealed program with the current library.

The owner saw test-pilot-002's r0006 in the live mirror; its temple facets come from a library default
(glasses_lib.tube_along_path), so a library fix reaches that delivery only through a rebuild of the SAME sealed program.
Until 2026-09-28 no such command existed: the only way was a paid author revision. These tests pin the contract of the
rebuild: the source job is opened read-only (its database and every file byte-identical afterwards), the program is the
revision's exact sealed set (every module verified against the revision's sha256 and its build operation's bundle; a
tampered module is refused), the build goes through the normal worker executor path, and the output is a preview folder
(model.glb, a 'preview_rebuild' manifest, the sheets) that modeler.tryon lists as a rebuild, never as a delivery.

Offline: a FakeWorker made to look real (as in tests/test_agentic_tools.py), a fake exporter and observer; no network, no
Blender, no Docker. Temp roots under a short temp path (tests/test_agentic_support.py, lag-rebuild).
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from PIL import Image

from modeler.agentic import cli, config, demo, executor, responses, runner
from modeler.agentic import tools as T
from modeler.agentic.artifacts import atomic_write
from modeler.agentic.config import EXIT_INVALID, EXIT_OK
from modeler.agentic.state import Store
from test_agentic_support import fresh_dir

NULL_LOG = lambda *a, **k: None   # noqa: E731
SHEET_KEYS = ["photo_match", "clay", "textured", "ar", "see_through"]
AUDIT = {"flags": ["faceted_sweep"], "parts": {"temple_R": {"hard_edge_mm": 693.2}},
         "notes": ["temple_R: 693 mm of hard edges below 60 deg; build the sweep with gl.tube_along_path rounded sections"]}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png(path: Path, colour=(20, 200, 20)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return str(path)


def tree(root: Path) -> dict[str, str]:
    """{relative path: sha256} of every file under ``root`` (the database, its WAL and shared-memory files included)."""
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}


class RealishFakeWorker(executor.FakeWorker):
    """The synthetic FakeWorker made to look real to ``build_revision``: a build names parts.npz / materials.json so the
    host reaches the export and observation branch (both faked in the tests)."""
    kind = "fake-realish"
    synthetic = False

    def describe(self) -> dict:
        return {"kind": self.kind, "synthetic": False, "note": "test worker"}

    def ingest(self, identity, staging):
        run = self.runs[identity["operation_id"]]
        out = Path(run["work_dir"]) / "out"
        super().ingest(identity, Path(run["work_dir"]) / "scratch_staging")
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        if result.get("ok") and run["operation"]["mode"] == "build":
            result.update(parts_npz="parts.npz", materials_json="materials.json",
                          inventory=[{"object": "front_plate", "part": "frame", "component": "front", "triangles": 12}])
            atomic_write(out / "parts.npz", b"not a real npz")
            atomic_write(out / "materials.json", b"{}")
            atomic_write(out / "result.json", json.dumps(result, indent=1).encode())
        ing = executor.validate_and_ingest_dir(out, staging)
        ing.synthetic = False
        return ing


def fake_export(*, audit: dict | None = AUDIT, tag: bytes = b"lib-v1"):
    """``modeler.export.export_glb`` stand-in; ``tag`` stands for the library the bytes were built with."""
    def export_glb(npz_path, materials_json, out_path, *, extras=None):
        raw = b"GLB-FAKE-" + tag + json.dumps(extras or {}, sort_keys=True).encode()
        atomic_write(Path(out_path), raw)
        receipt = {"origin": {"origin_mm": [0, 0, 0]}}
        if audit is not None:
            receipt["audit"] = audit
        return {"glb": str(out_path), "sha256": sha(raw), "bytes": len(raw), "receipt": receipt, "notes": [], "parts": {},
                "contract": {"ok": True, "checks": {"triangles": {"pass": True, "value": 52, "limit": 100000}}, "failures": []}}
    return export_glb


def fake_observe(cand_dir, build, evidence, evidence_dir, *, held_out_ids, previous_cameras=None, ar=True, glb_path=None, time_limit_s=300,
                 render_harness=None, heldout=False):
    obs = Path(cand_dir) / "observe"
    return {"seconds": 1.0, "triangles": 52, "bbox_mm": [[-72, -25, -3], [72, 25, 3]], "views": {},
            "sheets": {k: png(obs / f"sheet_{k}.png") for k in SHEET_KEYS}, "renders": {},
            "summary": {"status": "measured", "ar_runtime_compatible": True, "ar_report_valid": True, "ar_optical_meshes": 2}}


def fake_wearer(revision_dir, glb, width_mm):
    out = Path(revision_dir) / "observe" / "ar_wearer"
    return {"renders": [png(out / f"candidate__{v}.png", (203, 166, 141)) for v in ("front", "angled", "rolled")], "status": "runtime_compatible",
            "runtime_compatible": True}


def fake_wearer_sheets(renders, out_dir):
    return Path(png(Path(out_dir) / "sheet_wearer_mirror.png")), Path(png(Path(out_dir) / "sheet_wearer_detail.png"))


@contextmanager
def patched(*, audit: dict | None = AUDIT, tag: bytes = b"lib-v1"):
    """The export, observation and wearer passes replaced by fakes (the build and the executor path stay real)."""
    with mock.patch.object(T.mexport, "export_glb", fake_export(audit=audit, tag=tag)),             mock.patch("modeler.observe.observe_candidate", fake_observe),             mock.patch("modeler.agentic.evaluation.wearer_render_paths", fake_wearer),             mock.patch("modeler.evaluate.wearer_sheets", fake_wearer_sheets):
        yield


class RebuildCase(unittest.TestCase):
    """A source job with one built revision (r0001, through edit_program + build_now) and one created, never built (r0002)."""
    SOURCE_FINGERPRINTS: dict = {"test": "rebuild"}

    def setUp(self):
        self.root = fresh_dir(None, "rebuild", "rb-", env="LAG_REBUILD_TMP")
        self.src = self.root / "src"
        inputs = self.root / "src.in"
        translated = config.translate_request(demo.demo_request(inputs), inputs)
        policy = config.build_policy(owner_review=False, driver="scripted", worker="fake", intake="none", critic="none", final_evaluator="none", ar=True, budget_usd="5",
                                     max_inference_requests=12, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30,
                                     images_per_request=14)
        session = runner.Session.create(self.src, translated=translated, policy=policy, fingerprints=self.SOURCE_FINGERPRINTS, worker=RealishFakeWorker(),
                                        transport=responses.ScriptedTransport([]), worker_config=None, log=NULL_LOG)
        try:
            with patched():
                self.run_tool(session, "call_1", "edit_program", {"base_revision_id": None, "modules": demo.modules(demo.PROGRAM_A), "rationale": "source build",
                                                                  "expected_changes": ["a front"], "build_now": True, "deliver_if_compatible": False})
            self.run_tool(session, "call_2", "edit_program", {"base_revision_id": "r0001", "modules": demo.modules(demo.PROGRAM_B), "rationale": "never built",
                                                              "expected_changes": [], "build_now": False, "deliver_if_compatible": False})
            self.source_rev = session.store.revision("r0001")
            self.assertEqual(self.source_rev["state"], "compatible", self.source_rev)
            self.assertIsNone(session.store.revision("r0002")["operation_id"])
        finally:
            session.release()
            session.store.close()
        self.out = self.root / "out"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def run_tool(session, call_id, name, args):
        op = session.store.insert_operation(call_id=call_id, request_id=None, tool_name=name, schema_version=T.TOOLS_VERSION, args=args,
                                            source_revision_id=None, fence=session.fence)
        result = T.execute(session.context(), op)
        assert "error" not in result.text, result.text
        return result

    def rebuild(self, worker=None, revision="r0001", out=None, fingerprints=None, **kw):
        from modeler.agentic import rebuild
        with patched(**kw):
            return rebuild.rebuild_revision(self.src, revision, out or self.out, worker=worker or RealishFakeWorker(), fingerprints=fingerprints, log=NULL_LOG)


class PreviewLayout(RebuildCase):
    def test_the_preview_folder_holds_the_glb_the_manifest_and_the_sheets(self):
        worker = RealishFakeWorker()
        manifest = self.rebuild(worker, tag=b"lib-v2")
        on_disk = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, json.loads(json.dumps(manifest, default=str)))
        self.assertEqual(manifest["kind"], "preview_rebuild")
        self.assertFalse(manifest["delivery"])
        self.assertIn("not a delivery", manifest["label"])
        self.assertIn("src r0001", manifest["label"])
        # the asset: the new bytes, byte-bound
        glb = self.out / "model.glb"
        self.assertTrue(glb.is_file())
        self.assertEqual(manifest["asset"]["sha256"], sha(glb.read_bytes()))
        self.assertEqual(manifest["asset"]["path"], "model.glb")
        self.assertIn(b"lib-v2", glb.read_bytes())
        self.assertNotEqual(manifest["asset"]["sha256"], self.source_rev["glb_sha256"], "the rebuild is new bytes, not a copy of the delivery")
        # the source binding
        src = manifest["source"]
        self.assertEqual((src["job"], src["revision"], src["program_set_sha256"], src["glb_sha256"]),
                         ("src", "r0001", self.source_rev["program_set_sha256"], self.source_rev["glb_sha256"]))
        self.assertEqual(src["operation_id"], self.source_rev["operation_id"])
        self.assertEqual(src["modules"], {n: m["sha256"] for n, m in self.source_rev["modules"].items()})
        # the worker built exactly the sealed program set, through the executor's bundle
        ops = [r["operation"] for r in worker.runs.values() if r["operation"]["mode"] == "build"]
        self.assertEqual([o["program_set_sha256"] for o in ops], [self.source_rev["program_set_sha256"]])
        # library / harness fingerprint, exporter / contract, compatibility, observation, audit
        lib = manifest["library"]
        for key in ("glasses_lib_sha256", "harness_sha256", "source_bundle_lib_sha256", "changed"):
            self.assertIn(key, lib)
        self.assertEqual(lib["glasses_lib_sha256"], ops[0]["lib_sha256"]["glasses_lib.py"])
        self.assertIn("python_sources_sha256", manifest["fingerprints"])
        self.assertTrue(manifest["export"]["contract_ok"])
        self.assertEqual(manifest["export"]["audit"], {"flags": AUDIT["flags"], "notes": AUDIT["notes"]})
        self.assertEqual(manifest["export_audit"], AUDIT, "the manifest keeps the full receipt audit, parts included")
        self.assertTrue(manifest["compatibility"]["compatible"], manifest["compatibility"])
        self.assertEqual(manifest["observation"]["summary"]["status"], "measured")
        self.assertAlmostEqual(manifest["width_mm"], 144.0)
        self.assertEqual(manifest["wearer"]["count"], 3)
        # the sheets: the observation's, then the wearer sheets
        self.assertEqual(sorted(manifest["sheets"]), sorted(SHEET_KEYS + ["wearer_mirror", "wearer_detail"]))
        for key, rel in manifest["sheets"].items():
            self.assertTrue((self.out / rel).is_file(), rel)
            self.assertTrue(rel.startswith("sheets/"), rel)
        # no inference, no budget: the shadow job's database holds no request and no reservation
        shadow = Store.open(self.out / manifest["shadow_job"], readonly=True)
        try:
            self.assertEqual((shadow.requests(), shadow.reservations()), ([], []))
            self.assertEqual(shadow.job()["inference_operation_cap"], 0)
        finally:
            shadow.close()
        self.assertTrue(manifest["no_inference"])

    def test_the_source_job_is_byte_identical_after_a_rebuild(self):
        before = tree(self.src)
        self.assertIn("job.sqlite3", before)
        self.rebuild()
        self.assertEqual(tree(self.src), before, "a rebuild must not change one byte of the source job (its database included)")

    def test_the_cli_rebuilds_with_the_synthetic_worker_and_leaves_the_source_untouched(self):
        before = tree(self.src)
        code = cli.main(["rebuild", "--job", str(self.src), "--revision", "r0001", "--output", str(self.out), "--worker", "fake"])
        self.assertEqual(code, EXIT_OK)
        m = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((m["kind"], m["synthetic"], m["asset"]), ("preview_rebuild", True, None))
        self.assertFalse((self.out / "model.glb").exists(), "a synthetic worker yields no preview GLB")
        self.assertEqual(tree(self.src), before)


class Refusals(RebuildCase):
    def assert_refused(self, fragment: str, **kw):
        from modeler.agentic import rebuild
        with self.assertRaises(rebuild.RebuildError) as cm:
            self.rebuild(**kw)
        self.assertIn(fragment, str(cm.exception))
        self.assertFalse(self.out.exists() and any(self.out.iterdir()), "a refusal writes nothing")

    def test_a_tampered_bundle_module_is_refused(self):
        bundle = self.src / "worker" / self.source_rev["operation_id"] / "bundle" / "program" / "frame.py"
        bundle.write_text(bundle.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        self.assert_refused("frame")

    def test_a_tampered_revision_copy_is_refused(self):
        copy = self.src / "revisions" / "r0001" / "program" / "frame.py"
        copy.write_text("gl.note('other program')\n", encoding="utf-8")
        self.assert_refused("frame")

    def test_a_bundle_bound_to_another_program_set_is_refused(self):
        opj = self.src / "worker" / self.source_rev["operation_id"] / "bundle" / "operation.json"
        op = json.loads(opj.read_text(encoding="utf-8"))
        op["program_set_sha256"] = "0" * 64
        opj.write_text(json.dumps(op), encoding="utf-8")
        self.assert_refused("program_set_sha256")

    def test_a_revision_without_a_sealed_program_is_refused(self):
        self.assert_refused("no sealed program", revision="r0002")

    def test_an_unknown_revision_is_refused(self):
        self.assert_refused("r0009", revision="r0009")

    def test_a_non_empty_output_is_refused(self):
        png(self.out / "keep.png")
        from modeler.agentic import rebuild
        with self.assertRaises(rebuild.RebuildError) as cm:
            self.rebuild()
        self.assertIn("not empty", str(cm.exception))
        self.assertEqual([p.name for p in self.out.iterdir()], ["keep.png"])

    def test_an_output_inside_the_source_job_is_refused(self):
        self.assert_refused("inside the source job", out=self.src / "preview")
        self.assertFalse((self.src / "preview").exists())

    def test_the_cli_refuses_a_failing_doctor_record(self):
        cfg = self.root / "worker.json"
        cfg.write_text(json.dumps({"image_digest": "lenses-agentic-worker@sha256:" + "a" * 64, "entry": "/opt/lenses/worker_entry.py"}), encoding="utf-8")
        before = tree(self.src)
        code = cli.main(["rebuild", "--job", str(self.src), "--revision", "r0001", "--output", str(self.out), "--worker-config", str(cfg)])
        self.assertEqual(code, EXIT_INVALID)
        self.assertFalse(self.out.exists())
        self.assertEqual(tree(self.src), before)

    def test_the_cli_needs_a_worker_config_for_docker(self):
        self.assertEqual(cli.main(["rebuild", "--job", str(self.src), "--revision", "r0001", "--output", str(self.out)]), EXIT_INVALID)
        self.assertFalse(self.out.exists())


class TryOnListing(RebuildCase):
    def test_tryon_lists_a_preview_rebuild_beside_the_deliveries_with_a_clear_label(self):
        from modeler import tryon
        agentic = self.root / "agentic"
        self.out = agentic / "tf-rebuild-001"
        manifest = self.rebuild()
        rows = tryon.preview_rows(self.out, 8793)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "preview")
        self.assertIn("rebuild of src r0001 with the current library, not a delivery", row["label"])
        self.assertEqual(row["sha256"], manifest["asset"]["sha256"])
        self.assertEqual(row["path"], self.out / "model.glb")
        self.assertEqual(row["route"], "/models/tf-rebuild-001/model.glb")
        self.assertAlmostEqual(row["width_mm"], 144.0)
        self.assertIn("sha256=" + manifest["asset"]["sha256"], row["try_on"])
        with mock.patch.object(tryon, "AGENTIC", agentic), mock.patch.object(tryon, "JOBS", self.root / "no-legacy-jobs"):
            listed = tryon.catalog(None, 8793)
            named = tryon.catalog(["tf-rebuild-001"], 8793)
        self.assertEqual([r["kind"] for r in listed], ["preview"])
        self.assertEqual([r["kind"] for r in named], ["preview"])
        self.assertIn("not a delivery", tryon.index_page(listed).decode("utf-8"))

    def test_a_preview_whose_bytes_changed_is_not_listed(self):
        from modeler import tryon
        self.rebuild()
        (self.out / "model.glb").write_bytes(b"changed")
        self.assertEqual(tryon.preview_rows(self.out, 8793), [])

    def test_a_synthetic_preview_without_a_glb_is_not_listed(self):
        from modeler import tryon
        self.rebuild(worker=executor.FakeWorker())
        self.assertEqual(tryon.preview_rows(self.out, 8793), [])



# --------------------------------------------------------------------------- every changed component (review 2026-09-28: INF-12, INF-13)
# test-pilot-002's preview rebuild said only "changed since the source build: glasses_lib.py", while the observer, the
# exporter, lens_colour and see_through (13 of the 46 fingerprinted host files) and the AR runtime (63 -> 64 files) had
# changed too, and the numbers moved (lens_colour's basis, see_through 0.859 -> 0.895). The manifest names them all now.
def _h(c: str) -> str:
    return c * 64


RUN_FINGERPRINTS = {"protocol": "modeler_agentic_v1", "python_sources_sha256": _h("1"), "ar_runtime_sources_sha256": "3fb0b0a5" + "0" * 56, "ar_runtime_files": 63,
                    "python_sources": {"bsa/export.py": _h("a"), "modeler/observe.py": _h("b"), "modeler/lens_colour.py": _h("c"), "modeler/tags.py": _h("d"),
                                       "modeler/blender/glasses_lib.py": _h("e"), "modeler/agentic/runner.py": _h("f"), "modeler/gone.py": _h("9")}}
NOW_FINGERPRINTS = {"protocol": "modeler_agentic_v1", "python_sources_sha256": _h("2"), "ar_runtime_sources_sha256": "d608ae08" + "0" * 56, "ar_runtime_files": 64,
                    "python_sources": {"bsa/export.py": _h("A"), "modeler/observe.py": _h("B"), "modeler/lens_colour.py": _h("C"), "modeler/tags.py": _h("d"),
                                       "modeler/blender/glasses_lib.py": _h("E"), "modeler/agentic/runner.py": _h("f"), "modeler/see_through.py": _h("8")}}


class ChangedComponents(RebuildCase):
    SOURCE_FINGERPRINTS = RUN_FINGERPRINTS

    def test_the_manifest_names_every_changed_host_file_and_the_ar_runtime(self):
        manifest = self.rebuild(fingerprints=NOW_FINGERPRINTS)
        ch = manifest["changes"]
        self.assertEqual(ch["host_python"], {"changed": ["bsa/export.py", "modeler/blender/glasses_lib.py", "modeler/lens_colour.py", "modeler/observe.py"],
                                             "added": ["modeler/see_through.py"], "removed": ["modeler/gone.py"]})
        self.assertEqual(ch["ar_runtime"], {"changed": True, "source_sha256": RUN_FINGERPRINTS["ar_runtime_sources_sha256"],
                                            "current_sha256": NOW_FINGERPRINTS["ar_runtime_sources_sha256"], "source_files": 63, "current_files": 64})
        for component in ("host_python", "ar_runtime"):
            self.assertIn(component, ch["components"])
        note = next(n for n in manifest["notes"] if n.startswith("changed since the source build"))
        for name in ("bsa/export.py", "modeler/observe.py", "modeler/lens_colour.py", "modeler/see_through.py", "modeler/gone.py", "AR runtime", "63 -> 64"):
            self.assertIn(name, note)
        self.assertNotIn("modeler/tags.py", note)
        self.assertNotIn("runner.py", note)
        # the library block keeps its shape (cli prints library.changed); the per-file answer is changes.*
        self.assertEqual(set(manifest["library"]["changed"]), {"glasses_lib.py", "harness.py"})

    def test_the_measurement_fingerprint_is_diffed_against_the_source_bundle(self):
        opj = self.src / "worker" / self.source_rev["operation_id"] / "bundle" / "operation.json"
        recorded = json.loads(opj.read_text(encoding="utf-8"))["measurement_fingerprint"]
        self.assertEqual(recorded, executor.measurement_fingerprint(), "every source build bundle records the measurement fingerprint")
        moved = json.loads(json.dumps(recorded))
        moved["python"]["bsa/cameras.py"] = _h("7")
        moved["ar"]["qa/provider-comparison-ar.html"] = _h("6")
        moved["sha256"] = _h("5")
        from modeler.agentic import rebuild
        with mock.patch.object(rebuild, "measurement_fingerprint", return_value=moved):
            manifest = self.rebuild(fingerprints=NOW_FINGERPRINTS)
        m = manifest["changes"]["measurement"]
        self.assertEqual((m["source_sha256"], m["current_sha256"]), (recorded["sha256"], _h("5")))
        self.assertEqual(m["python"], {"changed": ["bsa/cameras.py"], "added": [], "removed": []})
        self.assertEqual(m["ar"], {"changed": ["qa/provider-comparison-ar.html"], "added": [], "removed": []})
        self.assertIn("measurement", manifest["changes"]["components"])
        note = next(n for n in manifest["notes"] if n.startswith("changed since the source build"))
        self.assertIn("bsa/cameras.py", note)
        self.assertIn("provider-comparison-ar.html", note)
        self.assertEqual(manifest["measurement_fingerprint"]["sha256"], _h("5"))

    def test_an_unchanged_checkout_reports_no_component(self):
        manifest = self.rebuild(fingerprints=RUN_FINGERPRINTS)
        ch = manifest["changes"]
        self.assertEqual(ch["host_python"], {"changed": [], "added": [], "removed": []})
        self.assertFalse(ch["ar_runtime"]["changed"])
        self.assertEqual(ch["measurement"]["python"], {"changed": [], "added": [], "removed": []})
        self.assertEqual([c for c in ch["components"] if c != "library"], [])

    def test_a_source_bundle_without_the_record_says_so(self):
        opj = self.src / "worker" / self.source_rev["operation_id"] / "bundle" / "operation.json"
        op = json.loads(opj.read_text(encoding="utf-8"))
        del op["measurement_fingerprint"]                      # a bundle written before 2026-09-28 (test-pilot-002)
        opj.write_text(json.dumps(op), encoding="utf-8")
        manifest = self.rebuild(fingerprints=NOW_FINGERPRINTS)
        m = manifest["changes"]["measurement"]
        self.assertIsNone(m["source_sha256"])
        self.assertIsNone(m["python"])
        self.assertIn("not recorded", m["note"])
        self.assertTrue(any("measurement fingerprint" in n and "not recorded" in n for n in manifest["notes"]), manifest["notes"])


class ChangedComponentsWithoutPerFileRecord(RebuildCase):
    def test_a_source_job_without_per_file_hashes_says_so(self):
        manifest = self.rebuild(fingerprints=NOW_FINGERPRINTS)          # the source job's fingerprints are {"test": "rebuild"}
        ch = manifest["changes"]
        self.assertIsNone(ch["host_python"])
        self.assertIsNone(ch["ar_runtime"]["changed"])
        self.assertTrue(any("per-file" in n for n in manifest["notes"]), manifest["notes"])


if __name__ == "__main__":
    unittest.main()
