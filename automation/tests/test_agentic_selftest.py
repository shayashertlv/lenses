"""The worker self-test (doctor --self-test, modeler/agentic/selftest.py) proves what it claims.

Review 2026-09-28: the self-test built one frame and rendered nothing, so a pass proved neither the worker's GL
renderer record (INF-17) nor its fonts (MVP-10), and its passed_utc was local time labelled UTC (INF-14). The fixture
now renders one small view and prints one lens mark in the 'sans' style; the fixture check fails without a GL renderer
record, on a font fallback note, without the render, or when the font file used is not in the image's inventory.
The Docker path runs only when the integrator runs the doctor; these tests drive the same checks through a stub worker
and, once, through the native fixture worker on the host Blender.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

import pytest

from modeler.agentic import executor, selftest
from modeler.agentic.executor import WorkerOutcome, atomic_write, synthetic_png, validate_and_ingest_dir
from modeler.paths import blender_executable
from test_agentic_support import fresh_dir

CFG = {"image_digest": "sha256:" + "0" * 64, "image_ref": "stub"}
WORKER_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
GL = {"renderer": "llvmpipe (LLVM 15.0.6, 256 bits)", "vendor": "Mesa", "version": "4.5 (Core Profile) Mesa 22.3.6", "backend": "OPENGL"}


class StubWorker(executor.Worker):
    """The worker contract without Blender: the fixture 'builds' with the notes, renders, GL record and font inventory given;
    every negative control fails as it must in the real worker."""
    kind = "stub"

    def __init__(self, *, gl=None, fonts=None, notes=None, render=True, render_error=None, parts=True):
        self.gl, self.fonts, self.render, self.render_error, self.parts = gl, fonts, render, render_error, parts
        self.notes = ["self-test fixture built", f"font 'sans': {WORKER_FONT}"] if notes is None else list(notes)
        self.ops: dict[str, dict] = {}

    def launch(self, bundle_dir, operation, *, work_dir, deadline_s, identity):
        program = (Path(bundle_dir) / "program" / "frame.py").read_text(encoding="utf-8")
        self.ops[operation["operation_id"]] = {"operation": operation, "work": Path(work_dir), "fixture": "self-test fixture built" in program}
        return dict(identity, kind=self.kind)

    def await_outcome(self, identity, *, deadline_s, cancel_check=None):
        run = self.ops[identity["operation_id"]]
        ok = run["fixture"]
        rendered = ok and self.render and bool(run["operation"]["renders"])
        return WorkerOutcome("completed" if ok else "failed", 0 if ok else 1, 0.01, identity, gl=self.gl if rendered else None, fonts=self.fonts)

    def ingest(self, identity, staging):
        run = self.ops[identity["operation_id"]]
        out = run["work"] / "out"
        out.mkdir(parents=True, exist_ok=True)
        result = {"ok": run["fixture"], "notes": self.notes if run["fixture"] else [], "renders": []}
        if run["fixture"] and self.render:
            for spec in run["operation"]["renders"]:
                if self.render_error:
                    result["renders"].append({"id": spec["id"], "path": None, "error": self.render_error})
                    continue
                atomic_write(out / f"{spec['id']}.png", synthetic_png(spec["id"], identity["operation_id"], spec["width"], spec["height"]))
                result["renders"].append({"id": spec["id"], "path": f"/work/out/{spec['id']}.png", "error": None})
        if run["fixture"] and self.parts:
            atomic_write(out / "parts.npz", b"stub parts")
            atomic_write(out / "materials.json", b"{}")
        atomic_write(out / "result.json", json.dumps(result).encode())
        return validate_and_ingest_dir(out, staging)


class SelfTestCase(unittest.TestCase):
    def setUp(self):
        self.root = fresh_dir(None, "selftest", "st-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_stub(self, name: str, **kw) -> dict:
        return selftest.run_self_test(CFG, self.root / name, worker=StubWorker(**kw))

    @staticmethod
    def fixture_check(report: dict) -> dict:
        return next(c for c in report["checks"] if c["name"] == "fixture-build")


class FixtureCoversRenderAndText(SelfTestCase):
    def test_the_fixture_renders_one_small_view_and_prints_text_in_a_worker_font(self):
        self.assertEqual(len(selftest.FIXTURE_RENDERS), 1)
        spec = selftest.FIXTURE_RENDERS[0]
        self.assertLessEqual(spec["width"] * spec["height"], 320 * 240, "one SMALL render: the self-test proves the GL context, not quality")
        self.assertIn("lens_print(", selftest.FIXTURE_FRAME)
        self.assertIn(f"font={selftest.FIXTURE_FONT!r}", selftest.FIXTURE_FRAME)
        self.assertIn("sans", selftest.FIXTURE_FONT, "a style the image's DejaVu/Liberation fonts serve (script/handwritten have no face there)")

    def test_a_complete_fixture_passes_its_check(self):
        report = self.run_stub("ok", gl=GL, fonts={WORKER_FONT: "a" * 64})
        check = self.fixture_check(report)
        self.assertTrue(check["passed"], check)
        self.assertEqual(check["problems"], [])
        self.assertEqual(check["gl"]["renderer"], GL["renderer"])
        self.assertEqual(check["font_used"], WORKER_FONT)
        self.assertTrue(all(c["passed"] for c in report["checks"]), report["checks"])

    def test_no_gl_renderer_record_fails_the_fixture(self):
        for gl in (None, {"backend": "OPENGL"}, {"renderer": "", "vendor": "x"}):
            with self.subTest(gl=gl):
                report = self.run_stub(f"gl-{len(str(gl))}", gl=gl)
                check = self.fixture_check(report)
                self.assertFalse(check["passed"])
                self.assertTrue(any("GL renderer" in p for p in check["problems"]), check["problems"])
                self.assertFalse(report["passed"])

    def test_a_gl_probe_error_fails_the_fixture(self):
        check = self.fixture_check(self.run_stub("glerr", gl=dict(GL, error="RuntimeError: no context")))
        self.assertFalse(check["passed"])
        self.assertTrue(any("no context" in p for p in check["problems"]), check["problems"])

    def test_a_font_fallback_note_fails_the_fixture(self):
        fallbacks = ["font 'sans' not found (['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'arial.ttf']); Blender's default font is used as the last resort",
                     "Blender's default font was the last resort for 'LENSES'"]
        for i, bad in enumerate(fallbacks):
            with self.subTest(note=bad[:30]):
                check = self.fixture_check(self.run_stub(f"font-{i}", gl=GL, notes=["self-test fixture built", f"font 'sans': {WORKER_FONT}", bad]))
                self.assertFalse(check["passed"])
                self.assertTrue(any("font fallback" in p for p in check["problems"]), check["problems"])

    def test_text_that_was_never_built_fails_the_fixture(self):
        check = self.fixture_check(self.run_stub("notext", gl=GL, notes=["self-test fixture built"]))
        self.assertFalse(check["passed"])
        self.assertTrue(any("no note names the font file" in p for p in check["problems"]), check["problems"])

    def test_a_font_outside_the_image_inventory_fails_the_fixture(self):
        check = self.fixture_check(self.run_stub("inv", gl=GL, fonts={"/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf": "b" * 64}))
        self.assertFalse(check["passed"])
        self.assertTrue(any("inventory" in p for p in check["problems"]), check["problems"])
        # an image without an inventory (older image) cannot be cross-checked; the note check still holds
        self.assertTrue(self.fixture_check(self.run_stub("noinv", gl=GL, fonts=None))["passed"])

    def test_a_missing_or_failed_render_fails_the_fixture(self):
        for name, kw in (("norender", {"render": False}), ("renderr", {"render_error": "RuntimeError: EGL"})):
            with self.subTest(name=name):
                check = self.fixture_check(self.run_stub(name, gl=GL, **kw))
                self.assertFalse(check["passed"])
                self.assertTrue(any("render" in p for p in check["problems"]), check["problems"])

    def test_the_negative_controls_carry_no_render_and_still_must_fail(self):
        report = self.run_stub("controls", gl=GL)
        for c in report["checks"]:
            if c["name"].startswith("control-"):
                self.assertTrue(c["passed"], c)
                self.assertFalse(c["worker_ok"])


class DoctorRecord(SelfTestCase):
    def patched_export(self, ok: bool = True):
        rec = {"contract": {"ok": ok, "failures": [] if ok else ["x"]}, "sha256": "c" * 64}
        return (mock.patch("modeler.export.export_glb", return_value=rec), mock.patch("bsa.archeck.run", return_value={}),
                mock.patch("bsa.archeck.validate_ar_result", return_value={"ok": True, "reasons": []}))

    def test_passed_utc_is_utc_with_its_zone(self):
        a, b, c = self.patched_export()
        with a, b, c:
            report = self.run_stub("utc", gl=GL, fonts={WORKER_FONT: "a" * 64})
        self.assertTrue(report["passed"], report)
        stamped = json.loads(Path(report["worker_config_with_doctor_record"]).read_text(encoding="utf-8"))
        passed = datetime.fromisoformat(stamped["doctor"]["passed_utc"])
        self.assertEqual(passed.utcoffset(), timedelta(0), "labelled UTC and actually UTC (INF-14: it was local time)")
        self.assertLess(abs((datetime.now(timezone.utc) - passed).total_seconds()), 300)
        self.assertEqual(stamped["doctor"]["fingerprint"], executor.worker_config_fingerprint(CFG))
        self.assertEqual(stamped["doctor"]["gl_renderer"], GL["renderer"])
        self.assertEqual(stamped["doctor"]["font_used"], WORKER_FONT)

    def test_no_record_when_the_fixture_check_fails(self):
        a, b, c = self.patched_export()
        with a, b, c:
            report = self.run_stub("fail", gl=None)
        self.assertFalse(report["passed"])
        self.assertNotIn("worker_config_with_doctor_record", report)
        self.assertFalse((self.root / "fail" / "worker-config.passed.json").exists())

    def test_a_fixture_without_exported_arrays_does_not_pass(self):
        """The export and AR stage was skipped, and the self-test PASSED, when the fixture produced no parts.npz."""
        a, b, c = self.patched_export()
        with a, b, c:
            report = self.run_stub("noparts", gl=GL, parts=False)
        self.assertFalse(report["passed"])
        self.assertIn("parts.npz", report["export"]["error"])


@pytest.mark.slow
@pytest.mark.skipif(blender_executable() is None, reason="host Blender not installed")
class NativeFixture(SelfTestCase):
    def test_the_fixture_builds_prints_and_renders_on_the_host_blender(self):
        """The same fixture check on the host Blender: the text resolves a real font file, the view renders and the export
        passes the contract; the native worker runs no GL probe, so the one problem left is the missing GL record."""
        worker = executor.NativeFixtureWorker({executor.program_set_sha256({"frame": selftest.FIXTURE_FRAME})})
        check = selftest.run_check(worker, self.root, "fixture-build", selftest.FIXTURE_FRAME, expect_ok=True, renders=selftest.FIXTURE_RENDERS,
                                   fixture=True)
        self.assertTrue(check["worker_ok"], check)
        self.assertEqual([p for p in check["problems"] if "GL renderer" not in p], [], check["problems"])
        self.assertEqual(len(check["problems"]), 1, "native: no GL probe, so exactly the GL problem remains")
        self.assertTrue(Path(check["font_used"]).is_file(), check.get("font_used"))
        from modeler import export as mexport
        staging = self.root / "fixture-build" / "staging"
        rec = mexport.export_glb(staging / "parts.npz", staging / "materials.json", self.root / "fixture.glb", extras={"selftest": True})
        self.assertTrue(rec["contract"]["ok"], rec["contract"].get("failures"))
