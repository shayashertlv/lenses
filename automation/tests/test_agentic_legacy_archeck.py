"""F14: the AR harness's GLOBAL verdict must reach every consumer (bsa.archeck.validate_ar_result and its users).

Offline: report.json files and render PNGs are written by hand (no browser); the harness subprocess is faked where
``archeck.run`` / ``texture.ar_render`` are exercised. BSA_DATA is patched to a temp dir (no stage dir is built here,
but nothing under automation/data may be touched by a test).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from bsa import archeck, core, export, texture

VIEWS = ["front", "angled", "rolled"]
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_report(out_dir: Path, cases: list[tuple[str, str | None]], views: list[str], *, status="inspected", stable=True,
                 errors=(), row_status="runtime_compatible", optical=2, row_error=None, drop_views=(), bad_sha_view=None,
                 missing_file_view=None, colour=(120, 60, 30)) -> None:
    """A harness report.json + actual-ar PNGs (sha256 per render, as provider-comparison.mjs writes them)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid, model_sha in cases:
        renders = []
        for v in views:
            if v in drop_views:
                continue
            fn = f"{cid}__actual-ar__{v}.png"
            Image.new("RGB", (16, 12), colour).save(out_dir / fn)
            digest = _sha((out_dir / fn).read_bytes())
            if v == bad_sha_view:
                digest = "0" * 64
            if v == missing_file_view:
                (out_dir / fn).unlink()
            renders.append({"filename": fn, "view": v, "mode": "actual-ar", "background": "checker", "environment": "room",
                            "sha256": digest, "camera": {"projection_matrix": IDENTITY, "view_matrix": IDENTITY, "width": 16, "height": 12},
                            "spatial": {"asset_to_world": IDENTITY, "camera_origin_in_asset": [0, 0, 1], "lenses": []}})
        rows.append({"id": cid, "status": row_status, "model_sha256": model_sha, "optical_meshes_detected": optical,
                     "error": row_error, "renders": renders, "synthetic_fit_ready": True, "continuity_failure": None})
    report = {"schema_version": 1, "status": status, "cases": rows, "errors": list(errors), "warnings": [],
              "source_snapshot_stable": stable, "compatible_count": sum(r["status"] == "runtime_compatible" for r in rows)}
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


def parsed(out_dir: Path, ids: dict, returncode=0) -> dict:
    return {**archeck.parse_report(out_dir, ids), "returncode": returncode}


def fake_harness(**report_kw):
    """A stand-in for ``subprocess.run`` of ``qa.provider_comparison --ar-check``: writes the report from the manifest."""
    rc = report_kw.pop("returncode", 0)

    def run(cmd, **kw):
        manifest = Path(cmd[cmd.index("--manifest") + 1])
        out = Path(cmd[cmd.index("--output") + 1])
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        write_report(out, [(c["id"], c["model_sha256"]) for c in doc["cases"]], [v["id"] for v in doc["ar_views"]], **report_kw)
        return SimpleNamespace(returncode=rc, stdout="", stderr="")
    return run


class ValidatorTest(unittest.TestCase):
    """validate_ar_result on hand-written reports."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.ids = {"a": "model A"}
        self.expected = {"model A": "ab" * 32}

    def validate(self, views=VIEWS, returncode=0, expected=None, **kw):
        write_report(self.t, [("a", "ab" * 32)], views, **{k: v for k, v in kw.items() if k != "expected_views"})
        return archeck.validate_ar_result(parsed(self.t, self.ids, returncode), expected_models=expected or self.expected,
                                          expected_views=kw.get("expected_views", VIEWS))

    def test_complete_good_report_is_ok(self):
        v = self.validate()
        self.assertTrue(v["ok"], v)
        self.assertTrue(v["harness_ok"])
        self.assertEqual(v["reasons"], [])
        self.assertTrue(v["models"]["model A"]["ok"])

    def test_parse_report_carries_each_render_sha(self):
        write_report(self.t, [("a", "ab" * 32)], VIEWS)
        row = archeck.parse_report(self.t, self.ids)["models"]["model A"]
        self.assertEqual([r["view"] for r in row["render_files"]], VIEWS)
        for r in row["render_files"]:
            self.assertEqual(r["sha256"], _sha(Path(r["path"]).read_bytes()))
        self.assertEqual(row["renders"], [r["path"] for r in row["render_files"]])      # the sheet writers' list

    def test_global_failed_status_with_a_good_row(self):
        v = self.validate(status="failed")
        self.assertFalse(v["ok"])
        self.assertFalse(v["harness_ok"])
        self.assertTrue(v["models"]["model A"]["ok"])               # the row itself is fine: the RUN is not
        self.assertTrue(any("status 'failed'" in r for r in v["reasons"]), v["reasons"])

    def test_returncode_1(self):
        v = self.validate(returncode=1)
        self.assertFalse(v["ok"])
        self.assertTrue(any("returncode 1" in r for r in v["reasons"]), v["reasons"])

    def test_returncode_none_timeout(self):
        self.assertFalse(self.validate(returncode=None)["ok"])

    def test_unstable_snapshot(self):
        v = self.validate(stable=False)
        self.assertFalse(v["ok"])
        self.assertTrue(any("snapshot" in r for r in v["reasons"]), v["reasons"])
        self.assertFalse(self.validate(stable=None)["ok"])           # absent is not stable

    def test_console_errors(self):
        v = self.validate(errors=["THREE.WebGLRenderer: context lost"])
        self.assertFalse(v["ok"])
        self.assertTrue(any("harness errors" in r for r in v["reasons"]), v["reasons"])

    def test_subset_of_views(self):
        v = self.validate(drop_views=("rolled",))
        self.assertFalse(v["ok"])
        self.assertTrue(v["harness_ok"])
        self.assertTrue(any("'rolled'" in r for r in v["models"]["model A"]["reasons"]), v)

    def test_zero_views(self):
        v = self.validate(drop_views=tuple(VIEWS))
        self.assertFalse(v["ok"])
        self.assertEqual(len(v["models"]["model A"]["reasons"]), 3)

    def test_extra_view_is_not_coverage(self):
        write_report(self.t, [("a", "ab" * 32)], VIEWS + ["back"])
        v = archeck.validate_ar_result(parsed(self.t, self.ids), expected_models=self.expected, expected_views=VIEWS)
        self.assertFalse(v["ok"])
        self.assertTrue(any("unexpected views" in r for r in v["models"]["model A"]["reasons"]), v)

    def test_wrong_render_sha(self):
        v = self.validate(bad_sha_view="angled")
        self.assertFalse(v["ok"])
        self.assertTrue(any("'angled'" in r and "sha256" in r for r in v["models"]["model A"]["reasons"]), v)

    def test_render_file_missing(self):
        v = self.validate(missing_file_view="front")
        self.assertFalse(v["ok"])
        self.assertTrue(any("'front'" in r and "missing" in r for r in v["models"]["model A"]["reasons"]), v)

    def test_wrong_model_sha(self):
        v = self.validate(expected={"model A": "cd" * 32})
        self.assertFalse(v["ok"])
        self.assertTrue(any("model sha256" in r for r in v["models"]["model A"]["reasons"]), v)
        # no expectation given: the sha is not checked, everything else still is
        self.assertTrue(self.validate(expected={"model A": None})["ok"])

    def test_rejected_row_and_error(self):
        self.assertFalse(self.validate(row_status="asset_handover_rejected", row_error="Error: x")["ok"])
        self.assertFalse(self.validate(row_error="Error: late")["ok"])

    def test_optical_meshes_required(self):
        self.assertFalse(self.validate(optical=0)["ok"])
        write_report(self.t, [("a", "ab" * 32)], VIEWS, optical=0)
        self.assertTrue(archeck.validate_ar_result(parsed(self.t, self.ids), expected_models=self.expected, expected_views=VIEWS,
                                                   require_optical=False)["ok"])

    def test_missing_model_and_empty_expectations(self):
        write_report(self.t, [("a", "ab" * 32)], VIEWS)
        p = parsed(self.t, self.ids)
        v = archeck.validate_ar_result(p, expected_models={"model A": "ab" * 32, "model B": None}, expected_views=VIEWS)
        self.assertFalse(v["ok"])
        self.assertEqual(v["models"]["model B"]["reasons"], ["missing from the report"])
        self.assertFalse(archeck.validate_ar_result(p, expected_models={}, expected_views=VIEWS)["ok"])
        self.assertFalse(archeck.validate_ar_result(p, expected_models=self.expected, expected_views=[])["ok"])
        self.assertFalse(archeck.validate_ar_result({"returncode": 0, "harness_status": "inspected", "harness_errors": [],
                                                     "source_snapshot_stable": True, "models": {}},
                                                    expected_models=self.expected, expected_views=VIEWS)["ok"])

    def test_legacy_row_without_hashed_renders_is_invalid(self):
        row = {"status": "runtime_compatible", "runtime_compatible": True, "optical_meshes_detected": 2, "model_sha256": "ab" * 32,
               "renders": [str(self.t / f"a__actual-ar__{v}.png") for v in VIEWS]}
        res = {"returncode": 0, "harness_status": "inspected", "harness_errors": [], "source_snapshot_stable": True,
               "models": {"model A": row}}
        v = archeck.validate_ar_result(res, expected_models=self.expected, expected_views=VIEWS)
        self.assertFalse(v["ok"])
        self.assertTrue(any("no sha256" in r for r in v["models"]["model A"]["reasons"]), v)

    def test_no_report(self):
        v = archeck.validate_ar_result(parsed(self.t, self.ids), expected_models=self.expected, expected_views=VIEWS)
        self.assertFalse(v["ok"])
        self.assertEqual(v["models"]["model A"]["reasons"][0], "status 'not_run'")

    def test_batch_separates_the_run_from_a_rejected_row(self):
        write_report(self.t, [("a", "ab" * 32)], VIEWS)
        rep = json.loads((self.t / "report.json").read_text())
        rep["cases"].append({"id": "b", "status": "asset_handover_rejected", "error": "Error: bad", "renders": [], "model_sha256": "cd" * 32})
        (self.t / "report.json").write_text(json.dumps(rep))
        v = archeck.validate_ar_result(parsed(self.t, {"a": "model A", "b": "model B"}),
                                       expected_models={"model A": "ab" * 32, "model B": None}, expected_views=VIEWS)
        self.assertTrue(v["harness_ok"])
        self.assertTrue(v["models"]["model A"]["ok"])
        self.assertFalse(v["models"]["model B"]["ok"])
        self.assertFalse(v["ok"])


class RunTest(unittest.TestCase):
    """archeck.run: all_compatible_with_lenses is the validator's verdict, not the rows'."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.glb = self.t / "synthetic.glb"
        export.write_glb(*export.synthetic_parts(), self.glb)

    def test_failed_run_with_compatible_rows_is_not_compatible(self):
        with mock.patch.object(archeck.subprocess, "run", fake_harness(status="failed", errors=["console error"])):
            r = archeck.run({"synthetic": self.glb}, self.t / "ar")
        self.assertEqual(r["models"]["synthetic"]["status"], "runtime_compatible")
        self.assertEqual(r["harness_status"], "failed")
        self.assertFalse(r["all_compatible_with_lenses"])
        self.assertFalse(r["validation"]["ok"])
        saved = json.loads((self.t / "ar" / "archeck.json").read_text())
        self.assertFalse(saved["all_compatible_with_lenses"])
        self.assertIn("validation", saved)

    def test_nonzero_returncode_is_not_compatible(self):
        with mock.patch.object(archeck.subprocess, "run", fake_harness(returncode=1)):
            r = archeck.run({"synthetic": self.glb}, self.t / "ar")
        self.assertFalse(r["all_compatible_with_lenses"])

    def test_good_run_is_compatible_and_hashed(self):
        with mock.patch.object(archeck.subprocess, "run", fake_harness()):
            r = archeck.run({"synthetic": self.glb}, self.t / "ar")
        self.assertTrue(r["all_compatible_with_lenses"], r["validation"])
        self.assertEqual(r["models"]["synthetic"]["model_sha256"], _sha(self.glb.read_bytes()))
        self.assertEqual(len(r["models"]["synthetic"]["render_files"]), 2)


class ArRenderTest(unittest.TestCase):
    """texture.ar_render carries the run-level fields and a validation through."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        g = self.t / "s.glb"
        export.write_glb(*export.synthetic_parts(), g)
        self.data = g.read_bytes()

    def render(self, **kw):
        with mock.patch("subprocess.run", fake_harness(**kw)):
            return texture.ar_render({"cand": self.data}, self.t / "fit", views=texture.AR_FIT_VIEWS, width_mm=140.0)

    def test_fields_and_validation(self):
        res = self.render()
        for k in ("returncode", "harness_status", "harness_errors", "source_snapshot_stable", "validation"):
            self.assertIn(k, res)
        self.assertEqual(res["harness_status"], "inspected")
        self.assertTrue(res["source_snapshot_stable"])
        self.assertTrue(res["validation"]["ok"], res["validation"])
        self.assertEqual(set(res["models"]["cand"]["views"]), {v["id"] for v in texture.AR_FIT_VIEWS})

    def test_failed_run_is_carried_not_dropped(self):
        res = self.render(status="failed", errors=["boom"], stable=False)
        self.assertEqual(res["models"]["cand"]["status"], "runtime_compatible")
        self.assertEqual(res["harness_errors"], ["boom"])
        self.assertFalse(res["source_snapshot_stable"])
        self.assertFalse(res["validation"]["ok"])

    def test_no_report(self):
        def silent(cmd, **kw):
            return SimpleNamespace(returncode=2, stdout="", stderr="node missing")
        with mock.patch("subprocess.run", silent):
            res = texture.ar_render({"cand": self.data}, self.t / "fit", views=texture.AR_FIT_VIEWS, width_mm=140.0)
        self.assertEqual(res["harness_status"], "no_report")
        self.assertFalse(res["validation"]["ok"])


class AttachArcheckTest(unittest.TestCase):
    """export.attach_archeck / m1_criterion_1 need the run AND the row."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._patch = mock.patch.object(core, "BSA_DATA", Path(cls._tmp.name))   # hermetic: never automation/data
        cls._patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        cls._tmp.cleanup()

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(dir=self._tmp.name))
        self.glb = self.t / "model.glb"
        export.write_glb(*export.synthetic_parts(), self.glb)
        self.name = "vb-bsa-m1"
        self.base = {"contract": {"ok": True}, "flags": ["x"], "glb": str(self.glb),
                     "export": {"nodes": ["frame", "temple_R", "temple_L", "lens_R", "lens_L"]}}

    def harness(self, cases=None, returncode=0, **kw) -> dict:
        out = self.t / "ar"
        cases = cases or [(archeck.safe_id(self.name), _sha(self.glb.read_bytes()))]
        write_report(out, cases, VIEWS, **kw)
        (out / "manifest.json").write_text(json.dumps({"ar_views": [{"id": v} for v in VIEWS]}))
        ids = {cid: (self.name if cid == archeck.safe_id(self.name) else cid) for cid, _ in cases}
        return {**parsed(out, ids, returncode), "manifest_path": str(out / "manifest.json"), "out_dir": str(out)}

    def test_good_run_and_row_pass(self):
        r = export.attach_archeck(dict(self.base), self.harness(), self.name, "vb", "m1")
        self.assertTrue(r["m1_criterion_1"], r["archeck"]["validation"])
        self.assertNotIn("ar_check_failed", r["flags"])
        self.assertEqual(r["archeck"]["views"], VIEWS)
        self.assertTrue(r["archeck"]["validation"]["ok"])

    def test_failed_global_harness_fails_m1(self):
        r = export.attach_archeck(dict(self.base), self.harness(status="failed", errors=["console error"]), self.name, "vb", "m1")
        self.assertEqual(r["archeck"]["status"], "runtime_compatible")     # the row is kept for diagnosis
        self.assertFalse(r["m1_criterion_1"])
        self.assertIn("ar_check_failed", r["flags"])
        self.assertFalse(r["archeck"]["validation"]["harness_ok"])
        self.assertFalse(export.m1_criterion_1(r))

    def test_returncode_and_snapshot(self):
        self.assertFalse(export.attach_archeck(dict(self.base), self.harness(returncode=1), self.name, "vb", "m1")["m1_criterion_1"])
        self.assertFalse(export.attach_archeck(dict(self.base), self.harness(stable=False), self.name, "vb", "m1")["m1_criterion_1"])

    def test_row_for_other_bytes_fails_m1(self):
        h = self.harness()
        self.glb.write_bytes(self.glb.read_bytes() + b"\0")          # the GLB changed after the run
        r = export.attach_archeck(dict(self.base), h, self.name, "vb", "m1")
        self.assertFalse(r["m1_criterion_1"])
        self.assertTrue(any("model sha256" in x for x in r["archeck"]["validation"]["model"]["reasons"]))

    def test_subset_of_views_fails_m1(self):
        r = export.attach_archeck(dict(self.base), self.harness(drop_views=("rolled",)), self.name, "vb", "m1")
        self.assertFalse(r["m1_criterion_1"])

    def test_another_products_rejection_is_not_this_products(self):
        cid = archeck.safe_id(self.name)
        h = self.harness(cases=[(cid, _sha(self.glb.read_bytes())), ("other", "cd" * 32)])
        rep = json.loads((self.t / "ar" / "report.json").read_text())
        for row in rep["cases"]:
            if row["id"] == "other":
                row["status"], row["error"] = "asset_handover_rejected", "Error: bad"
        (self.t / "ar" / "report.json").write_text(json.dumps(rep))
        h = {**h, **parsed(self.t / "ar", {cid: self.name, "other": "other"})}
        r = export.attach_archeck(dict(self.base), h, self.name, "vb", "m1")
        self.assertTrue(r["m1_criterion_1"], r["archeck"]["validation"])

    def test_a_row_without_validation_is_a_legacy_unverified_record(self):
        # a record without the harness validation (an S9 result.json written before 2026-09-27, a hand-built row) is
        # read as legacy/unverified, never silently upgraded to compatibility; tests/test_bsa_export.py::test_c1 agrees
        r = {"contract": {"ok": True}, "export": {"nodes": ["frame", "lens_R", "lens_L"]},
             "archeck": {"status": "runtime_compatible", "optical_meshes_detected": 2}}
        self.assertFalse(export.m1_criterion_1(r))
        r["archeck"]["validation"] = {"ok": True, "harness_ok": True, "model": {"ok": True}}
        self.assertTrue(export.m1_criterion_1(r))
        r["archeck"]["validation"] = {"ok": False, "harness_ok": False, "model": {"ok": True}}
        self.assertFalse(export.m1_criterion_1(r))
        r["archeck"]["validation"] = {"ok": False, "harness_ok": True, "model": {"ok": False}}
        self.assertFalse(export.m1_criterion_1(r))


class TextureVerificationTest(unittest.TestCase):
    """The S7 verification decision: complete planned-view coverage per candidate, else verification_render_failed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)
        self.planned = ["front", "left"]

    def ver(self, per_model: dict[str, list[str]], harness_ok=True, row_ok=None) -> dict:
        models, rows = {}, {}
        for name, views in per_model.items():
            vw = {}
            for v in views:
                p = self.t / f"{name}__actual-ar__{v}.png"
                Image.new("RGB", (4, 4)).save(p)
                vw[v] = {"png": str(p)}
            models[name] = {"status": "runtime_compatible", "views": vw}
            rows[name] = {"ok": (row_ok or {}).get(name, True), "reasons": []}
        return {"models": models, "harness_status": "inspected" if harness_ok else "failed", "returncode": 0,
                "validation": {"ok": harness_ok and all(r["ok"] for r in rows.values()), "harness_ok": harness_ok,
                               "reasons": [] if harness_ok else ["harness status 'failed'"], "models": rows}}

    def test_candidate_missing_one_view_is_excluded(self):
        ver = self.ver({"g08500": ["front", "left"], "g10000": ["front"], "g11500": ["front", "left"]})
        names = {0.85: "g08500", 1.0: "g10000", 1.15: "g11500"}
        complete = texture.complete_verifications(ver, names, self.planned)
        self.assertEqual(sorted(complete), [0.85, 1.15])
        self.assertEqual(set(complete[0.85]), set(self.planned))
        self.assertEqual(texture.verification_status(complete), "runtime_compatible")

    def test_none_complete_is_verification_render_failed(self):
        ver = self.ver({"g08500": ["front"], "g10000": [], "g11500": ["left"]})
        complete = texture.complete_verifications(ver, {0.85: "g08500", 1.0: "g10000", 1.15: "g11500"}, self.planned)
        self.assertEqual(complete, {})
        self.assertEqual(texture.verification_status(complete), texture.VERIFICATION_FAILED)
        self.assertEqual(texture.VERIFICATION_FAILED, "verification_render_failed")

    def test_failed_run_excludes_every_candidate(self):
        ver = self.ver({"s085": ["front", "left"], "s100": ["front", "left"]}, harness_ok=False)
        self.assertEqual(texture.complete_verifications(ver, {0.85: "s085", 1.0: "s100"}, self.planned), {})

    def test_invalid_row_is_excluded(self):
        ver = self.ver({"s085": ["front", "left"], "s100": ["front", "left"]}, row_ok={"s100": False})
        self.assertEqual(sorted(texture.complete_verifications(ver, {0.85: "s085", 1.0: "s100"}, self.planned)), [0.85])

    def test_missing_png_file_is_not_coverage(self):
        ver = self.ver({"s100": ["front", "left"]})
        Path(ver["models"]["s100"]["views"]["left"]["png"]).unlink()
        self.assertEqual(texture.complete_verifications(ver, {1.0: "s100"}, self.planned), {})

    def test_no_validation_key_is_not_success(self):
        ver = self.ver({"s100": ["front", "left"]})
        ver.pop("validation")
        self.assertEqual(texture.complete_verifications(ver, {1.0: "s100"}, self.planned), {})


class CandidateValidTest(unittest.TestCase):
    """modeler Candidate.valid needs summary.ar_report_valid True; a legacy observation is invalid."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "c0000"
        (self.root / "observe").mkdir(parents=True)
        (self.root / "export.json").write_text(json.dumps({"contract": {"ok": True}, "sha256": "ab" * 32}))
        self.obs = {"ar": {"models": {"candidate": {"runtime_compatible": True, "optical_meshes_detected": 2, "continuity_failure": None}}},
                    "summary": {}, "views": {}}

    def write(self, summary: dict):
        (self.root / "observe" / "observation.json").write_text(json.dumps({**self.obs, "summary": summary}))
        from modeler.candidates import Candidate
        return Candidate(self.root)

    def test_legacy_observation_without_the_key_is_invalid(self):
        self.assertFalse(self.write({}).valid())

    def test_false_is_invalid_and_true_is_valid(self):
        self.assertFalse(self.write({"ar_report_valid": False}).valid())
        self.assertTrue(self.write({"ar_report_valid": True}).valid())

    def test_the_row_gates_still_apply(self):
        self.obs["ar"]["models"]["candidate"]["continuity_failure"] = "gap"
        self.assertFalse(self.write({"ar_report_valid": True}).valid())


class SummarizeTest(unittest.TestCase):
    """modeler observe.summarize writes ar_report_valid from the shared validator."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = Path(self._tmp.name)

    def result(self, **kw) -> dict:
        from modeler.observe import AR_VIEWS
        write_report(self.t, [("candidate", "ab" * 32)], [v["id"] for v in AR_VIEWS], **kw)
        return parsed(self.t, {"candidate": "candidate"}, kw.get("returncode", 0))

    def test_valid_and_invalid(self):
        from modeler import observe
        s = observe.summarize({}, {}, self.result(), glb_sha256="ab" * 32)
        self.assertTrue(s["ar_report_valid"])
        self.assertTrue(s["ar_runtime_compatible"])
        self.assertNotIn("ar_report_reasons", s)
        s2 = observe.summarize({}, {}, self.result(status="failed"), glb_sha256="ab" * 32)
        self.assertFalse(s2["ar_report_valid"])
        self.assertTrue(s2["ar_runtime_compatible"])                 # the row alone said yes: that is the defect
        self.assertTrue(s2["ar_report_reasons"])
        s3 = observe.summarize({}, {}, self.result(), glb_sha256="cd" * 32)
        self.assertFalse(s3["ar_report_valid"])
        self.assertNotIn("ar_report_valid", observe.summarize({}, {}, None))


if __name__ == "__main__":
    unittest.main()
