"""Binding of evaluations, render caches, calibration rows and owner verdicts to the exact asset bytes, the AR runtime,
the evaluator and the protocol (audit 2026-09-27: F10, F11, F12). Missing required evidence fails closed to
'unmeasured' / 'not adopted', never to accept; legacy records without a binding are read as unverified, never upgraded.
Nothing here touches data/: temporary job folders and temporary calibration files only; the AR harness is stubbed."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from modeler import calibration, evaluate as mevaluate, evaluate_asset, job as mjob
from modeler.owner_verdict import apply_verdict


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _eval(overall="accept"):
    return {"discrepancies": [], "identity_checklist": [{"feature": "f", "verdict": "present"}],
            "resemblance_0_10": {"mirror_overall": 8}, "overall": overall, "summary": "s"}


# --------------------------------------------------------------------------- F11: an unmeasured gate never passes
class UnmeasuredGate(unittest.TestCase):
    def test_audit_reproduction_is_not_accepted(self):
        s = mevaluate.decide_status(candidate_valid=True, metrics={"ar_continuity_failure": False}, heldout=None,
                                    evaluation={"overall": "accept", "discrepancies": [], "identity_checklist": []},
                                    input_flags=[], protocol_calibrated=True)
        self.assertNotEqual(s["status"], "accepted")
        self.assertEqual(s["status"], "best_effort")
        self.assertEqual(s["automatic_verdict"], "unmeasured")
        self.assertFalse(s["clean"])
        self.assertIn("required gate metric lens_outline_mean_mm is unmeasured", s["reasons"])
        self.assertIsNone(s["provisional"]["lens_outline_mean_mm"]["pass"])
        self.assertTrue(s["provisional"]["lens_outline_mean_mm"]["gate"])

    def test_definite_failures_stay_rejects_and_measured_gates_still_accept(self):
        base = dict(candidate_valid=True, heldout=None, input_flags=[], protocol_calibrated=True)
        rejected = mevaluate.decide_status(metrics={"ar_continuity_failure": False}, evaluation=_eval("reject"), **base)
        self.assertEqual(rejected["automatic_verdict"], "reject")
        failed = mevaluate.decide_status(metrics={"ar_continuity_failure": "posterior arm missing"}, evaluation=_eval(), **base)
        self.assertEqual(failed["automatic_verdict"], "reject")
        self.assertTrue(any("gate failed" in r for r in failed["reasons"]))
        self.assertTrue(any("unmeasured" in r for r in failed["reasons"]))
        ok = mevaluate.decide_status(metrics={"lens_outline_mean_mm": 0.3, "ar_continuity_failure": None}, evaluation=_eval(), **base)
        self.assertEqual(ok["automatic_verdict"], "accept")
        self.assertEqual(ok["status"], "accepted")
        # a report-only lens gate (the intake review's override) is not a required gate: its missing value is not unmeasured
        free = mevaluate.decide_status(metrics={"ar_continuity_failure": None}, evaluation=_eval(),
                                       gate_overrides={"lens_outline_mean_mm": {"mode": "report_only", "reason": "clipped"}}, **base)
        self.assertEqual(free["automatic_verdict"], "accept")


# --------------------------------------------------------------------------- F10: calibration rows need a bound record
class CalibrationBinding(unittest.TestCase):
    def _asset(self, jobs: Path, job: str, name: str, *, record=None, kind: str = "delivered", summary: dict | None = None) -> dict:
        """One owner-judged asset with its own bytes (rows are one per asset digest); ``record`` is the stored
        evaluation record, or a callable of the asset's digest that builds one."""
        d = jobs / job / ("baselines" if kind == "baseline" else "candidates") / name
        (d / "observe").mkdir(parents=True)
        summary = summary or {"front_contour_mean_mm": 0.2, "lens_outline_mean_mm": 0.3, "ar_runtime_compatible": True, "ar_continuity_failure": None}
        (d / "observe" / "observation.json").write_text(json.dumps({"summary": summary}), encoding="utf-8")
        glb_bytes = f"glb-{job}-{name}".encode()
        glb = d / ("b.glb" if kind == "baseline" else "model.glb")
        glb.write_bytes(glb_bytes)
        if kind == "baseline":
            (d / "baseline.json").write_text(json.dumps({"glb": str(glb)}), encoding="utf-8")
        if record is not None:
            (d / "evaluation_v2").mkdir()
            (d / "evaluation_v2" / "evaluation.json").write_text(json.dumps(record(_sha(glb_bytes)) if callable(record) else record), encoding="utf-8")
        return {"kind": kind, "job": job, "product_id": "p", ("baseline" if kind == "baseline" else "candidate"): name,
                "asset_sha256": _sha(glb_bytes), "verdict": "accept", "note": ""}

    @staticmethod
    def _bound(sha: str, overall: str = "accept") -> dict:
        return {"evaluation": _eval(overall), "asset_sha256": sha, "meta": {"protocol": mevaluate.PROTOCOL}}

    def test_bound_record_is_evaluated_and_unbound_ones_are_not(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td)
            r_ok = self._asset(jobs, "j0", "c0000", record=self._bound)
            r_legacy = self._asset(jobs, "j1", "c0000", record={"evaluation": _eval()})
            r_changed = self._asset(jobs, "j2", "c0000", record=self._bound(_sha(b"other bytes")))
            r_proto = self._asset(jobs, "j3", "c0000", record=lambda sha: dict(self._bound(sha), meta={"protocol": "modeler_evaluator_v2.1"}))
            r_none = self._asset(jobs, "j4", "c0000", record=None)
            r_base = self._asset(jobs, "j5", "bsa-m3", kind="baseline", record=self._bound)
            rows = [r_ok, r_legacy, r_changed, r_proto, r_none, r_base]
            st = {r["job"]: calibration.automatic_verdict_status(r, jobs) for r in rows}
            self.assertIsNone(st["j0"]["reason"])
            self.assertEqual(st["j0"]["verdict"]["verdict"], "accept")
            self.assertEqual(st["j0"]["verdict"]["asset_sha256"], _sha(b"glb-j0-c0000"))
            self.assertEqual(st["j1"], {"verdict": None, "reason": "legacy_unbound"})
            self.assertEqual(st["j2"]["reason"], "asset_changed")
            self.assertEqual(st["j3"]["reason"], "protocol_mismatch")
            self.assertEqual(st["j4"]["reason"], "no_evaluation")
            self.assertIsNone(st["j5"]["reason"], "a baseline's glb comes from baseline.json")
            self.assertIsNone(calibration.automatic_verdict(r_legacy, jobs))
            s = calibration.summary(rows, jobs=jobs)
            self.assertEqual(s["unevaluated"], 4)
            self.assertEqual(s["owner_accepts_evaluated"], 2)
            self.assertEqual(s["unevaluated_by_reason"], {"asset_changed": 1, "legacy_unbound": 1, "no_evaluation": 1, "protocol_mismatch": 1})
            self.assertTrue(any("j1/c0000 (legacy_unbound)" in r and "j2/c0000 (asset_changed)" in r for r in s["reasons"]), s["reasons"])
            self.assertFalse(s["calibrated"])
            self.assertEqual(next(t for t in s["table"] if t["job"] == "j2")["automatic_reason"], "asset_changed")
            self.assertIsNone(next(t for t in s["table"] if t["job"] == "j0")["automatic_reason"])

    def test_unmeasured_automatic_verdict_agrees_with_nobody(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td)
            r = self._asset(jobs, "j0", "c0000", record=self._bound, summary={"front_contour_mean_mm": 0.2, "ar_runtime_compatible": True, "ar_continuity_failure": None})
            s = calibration.summary([r], jobs=jobs)
            self.assertEqual(s["table"][0]["automatic"]["verdict"], "unmeasured")
            self.assertEqual(s["disagreements"], 1)
            self.assertFalse(s["calibrated"])
            self.assertTrue(any("automatic unmeasured" in x for x in s["reasons"]), s["reasons"])


# --------------------------------------------------------------------------- F10: the wearer render cache is bound
def _fake_archeck(calls: list):
    def run(glb_paths, out_dir, **kw):
        calls.append(dict(kw, glb=str(glb_paths["candidate"])))
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        renders = []
        for v in kw["ar_views"]:
            p = out / f"candidate__actual-ar__{v['id']}.png"
            p.write_bytes(f"render-{len(calls)}-{v['id']}".encode())
            renders.append(str(p))
        res = {"models": {"candidate": {"runtime_compatible": True, "optical_meshes_detected": 2, "renders": renders}}}
        (out / "archeck.json").write_text(json.dumps(res), encoding="utf-8")
        return res
    return run


class WearerRenderCache(unittest.TestCase):
    def test_cache_is_bound_to_bytes_runtime_settings_and_render_files(self):
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / "c0000"
            asset.mkdir()
            glb = asset / "model.glb"
            glb.write_bytes(b"first")
            calls: list = []
            with mock.patch.object(mevaluate.archeck, "run", _fake_archeck(calls)), mock.patch.object(mevaluate, "ar_runtime_digest", return_value="rt-1"):
                m1 = mevaluate.wearer_renders(asset, glb, 140.0)
                self.assertEqual(len(calls), 1)
                cached = json.loads((asset / "observe" / "ar_wearer" / "archeck.json").read_text(encoding="utf-8"))
                self.assertEqual(cached["cache_key"]["asset_sha256"], _sha(b"first"))
                self.assertEqual(cached["cache_key"]["ar_runtime_digest"], "rt-1")
                self.assertEqual(cached["cache_key"]["width_mm"], 140.0)
                self.assertEqual(cached["cache_key"]["background_color"], mevaluate.WEARER_BACKGROUND)
                self.assertEqual(set(cached["render_sha256"]), set(m1["renders"]))
                self.assertEqual(mevaluate.wearer_renders(asset, glb, 140.0)["renders"], m1["renders"])
                self.assertEqual(len(calls), 1, "same bytes, runtime and settings: reused")
                glb.write_bytes(b"second")
                mevaluate.wearer_renders(asset, glb, 140.0)
                self.assertEqual(len(calls), 2, "other bytes: re-rendered")
                mevaluate.wearer_renders(asset, glb, 141.0)
                self.assertEqual(len(calls), 3, "other stated width: re-rendered")
                Path(m1["renders"][0]).write_bytes(b"tampered")
                mevaluate.wearer_renders(asset, glb, 141.0)
                self.assertEqual(len(calls), 4, "a render file that is not the recorded one: re-rendered")
                mevaluate.wearer_renders(asset, glb, 141.0, force=True)
                self.assertEqual(len(calls), 5)
            with mock.patch.object(mevaluate.archeck, "run", _fake_archeck(calls)), mock.patch.object(mevaluate, "ar_runtime_digest", return_value="rt-2"):
                mevaluate.wearer_renders(asset, glb, 141.0)
                self.assertEqual(len(calls), 6, "the AR runtime changed: re-rendered")

    def test_legacy_cache_without_a_key_is_re_rendered(self):
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / "c0000"
            out = asset / "observe" / "ar_wearer"
            out.mkdir(parents=True)
            glb = asset / "model.glb"
            glb.write_bytes(b"bytes")
            legacy = out / "candidate__actual-ar__front.png"
            legacy.write_bytes(b"a render of some other bytes")
            (out / "archeck.json").write_text(json.dumps({"models": {"candidate": {"renders": [str(legacy)]}}}), encoding="utf-8")
            calls: list = []
            with mock.patch.object(mevaluate.archeck, "run", _fake_archeck(calls)), mock.patch.object(mevaluate, "ar_runtime_digest", return_value="rt-1"):
                m = mevaluate.wearer_renders(asset, glb, 140.0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(m["renders"]), 3)
            self.assertIn("cache_key", json.loads((out / "archeck.json").read_text(encoding="utf-8")))

    def test_runtime_digest_has_a_fallback_over_the_renderer_sources(self):
        with mock.patch.dict("sys.modules", {"bsa.pipeline": None}):        # the import fails: the fallback fingerprints ar/src/render + eyewear
            d = mevaluate.ar_runtime_digest()
        self.assertEqual(len(d), 64)
        self.assertEqual(len(mevaluate.ar_runtime_digest()), 64)


class CollectBinding(unittest.TestCase):
    def test_collect_records_asset_sha_protocol_and_runtime_digest(self):
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / "candidates" / "c0001"
            out = asset / "evaluation_v2"
            out.mkdir(parents=True)
            (out / "request.json").write_text(json.dumps({"asset_sha256": "ab" * 32, "ar_runtime_digest": "rt-1", "images": []}), encoding="utf-8")
            (out / "response.json").write_text(json.dumps(_eval()), encoding="utf-8")
            rec = evaluate_asset.collect(Path(td), asset)
            self.assertEqual(rec["asset_sha256"], "ab" * 32)
            self.assertEqual(rec["meta"]["protocol"], mevaluate.PROTOCOL)
            self.assertEqual(rec["meta"]["ar_runtime_digest"], "rt-1")
            # without a digest in the request the wearer cache's key is the source; a legacy cache has none
            (out / "request.json").write_text(json.dumps({"asset_sha256": "ab" * 32, "images": []}), encoding="utf-8")
            self.assertIsNone(evaluate_asset.collect(Path(td), asset)["meta"]["ar_runtime_digest"])
            wearer = asset / "observe" / "ar_wearer"
            wearer.mkdir(parents=True)
            (wearer / "archeck.json").write_text(json.dumps({"cache_key": {"ar_runtime_digest": "rt-9"}}), encoding="utf-8")
            self.assertEqual(evaluate_asset.collect(Path(td), asset)["meta"]["ar_runtime_digest"], "rt-9")
            self.assertEqual(evaluate_asset.wearer_runtime_digest(asset), "rt-9")


# --------------------------------------------------------------------------- F12: the owner verdict binds to the bytes
def _manifest(job: Path, status="best_effort", overall="reject", glb_bytes=b"glb bytes") -> Path:
    deliver = job / "deliverable"
    deliver.mkdir(parents=True, exist_ok=True)
    glb = deliver / "p.glb"
    glb.write_bytes(glb_bytes)
    m = {"job": job.name, "product_id": "p", "written": "2026-09-27T00:00:00", "status": status,
         "status_detail": {"status": status, "reasons": ["evaluator overall: reject"], "automatic_verdict": "reject", "clean": False,
                           "provisional": {"lens_outline_mean_mm": {"value": 0.9, "limit_mm": 0.8, "pass": False, "gate": True}}},
         "visual_bar_calibrated": False, "delivered_candidate": "c0001",
         "asset": {"path": str(glb), "sha256": _sha(glb_bytes), "bytes": len(glb_bytes), "triangles": 1, "contract": {"ok": True}},
         "measurements": {"author_visible": {"lens_outline_mean_mm": 0.9, "ar_runtime_compatible": True}, "held_out": None},
         "evaluation": {"overall": overall, "discrepancies": [], "identity_checklist": []},
         "scale": {"front_width_mm": 140.0, "source": "assumed_default"}}
    (job / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return glb


class OwnerVerdictBinding(unittest.TestCase):
    def _m(self, job: Path) -> dict:
        return json.loads((job / "manifest.json").read_text(encoding="utf-8"))

    def test_verdict_hashes_the_delivered_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "j1"
            glb = _manifest(job)
            cal = Path(td) / "cal.jsonl"
            glb.write_bytes(b"other bytes")               # the deliverable changed after the manifest: no verdict against unknown bytes
            with self.assertRaises(ValueError):
                apply_verdict(job, "accept", "live", calibration_file=cal, write_report=False)
            glb.write_bytes(b"glb bytes")
            with self.assertRaises(ValueError):           # the --sha256 the owner names must be the bytes too
                apply_verdict(job, "accept", "live", sha256="cd" * 32, calibration_file=cal, write_report=False)
            glb.unlink()
            with self.assertRaises(FileNotFoundError):
                apply_verdict(job, "accept", "live", calibration_file=cal, write_report=False)
            self.assertFalse(cal.exists())
            self.assertEqual(self._m(job)["status"], "best_effort")
            self.assertNotIn("owner_verdict", self._m(job))

    def test_relative_manifest_path_resolves_to_the_job_deliverable(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "j1"
            _manifest(job)
            m = self._m(job)
            m["asset"]["path"] = "data/modeler/jobs/j1/deliverable/p.glb"        # a job writes the path relative to automation/
            (job / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            rec = apply_verdict(job, "borderline", "live", calibration_file=Path(td) / "cal.jsonl", write_report=False)
            self.assertEqual(rec["asset_sha256"], _sha(b"glb bytes"))
            self.assertTrue(self._m(job)["status_detail"]["owner_borderline"])

    def test_reject_after_accept_revokes_and_history_is_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "j1"
            _manifest(job)
            cal = Path(td) / "cal.jsonl"
            apply_verdict(job, "accept", "live", "perfect", sha256=_sha(b"glb bytes").upper(), when="2026-09-26T16:40:00+03:00",
                          calibration_file=cal, write_report=False)
            m = self._m(job)
            self.assertEqual(m["status"], "accepted")
            self.assertEqual(m["status_detail"]["accepted_by"], "owner")
            self.assertEqual(m["status_detail"]["previous_status"], "best_effort")
            self.assertEqual(m["status_detail"]["axes"], {"runtime_compatible": True, "automatic_verdict": "reject", "owner_verdict": "accept"})
            self.assertEqual([h["verdict"] for h in m["owner_verdict_history"]], ["accept"])
            apply_verdict(job, "reject", "live", "temples too long", when="2026-09-27T10:00:00+03:00", calibration_file=cal, write_report=False)
            m = self._m(job)
            self.assertEqual(m["status"], "best_effort")
            self.assertNotIn("accepted_by", m["status_detail"])
            self.assertEqual(m["status_detail"]["owner_revoked"], {"when": "2026-09-27T10:00:00+03:00", "verdict": "reject"})
            self.assertTrue(m["status_detail"]["owner_rejected"])
            self.assertEqual(m["owner_verdict"]["verdict"], "reject")
            self.assertEqual([h["verdict"] for h in m["owner_verdict_history"]], ["accept", "reject"])
            self.assertEqual(m["status_detail"]["axes"]["owner_verdict"], "reject")
            self.assertEqual(m["status_detail"]["axes"]["automatic_verdict"], "reject")
            self.assertIn("evaluator overall: reject", m["status_detail"]["reasons"])
            rows = [json.loads(l) for l in cal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["verdict"] for r in rows], ["accept", "reject"])
            self.assertEqual(rows[1]["status_before"], "accepted")
            for r in rows:                                     # the calibration row format is preserved
                for k in ("kind", "when", "verdict", "medium", "note", "job", "product_id", "candidate", "asset_path", "asset_sha256",
                          "status_before", "evaluator", "provisional", "metrics_mm", "heldout_mean_mm", "runtime", "scale", "tags"):
                    self.assertIn(k, r)
                self.assertEqual(r["asset_sha256"], _sha(b"glb bytes"))
            # a later accept restores the acceptance without losing the pre-owner status
            apply_verdict(job, "accept", "live", calibration_file=cal, write_report=False)
            m = self._m(job)
            self.assertEqual(m["status"], "accepted")
            self.assertEqual(m["status_detail"]["previous_status"], "best_effort")
            self.assertNotIn("owner_revoked", m["status_detail"])
            self.assertNotIn("owner_rejected", m["status_detail"])
            self.assertEqual(len(m["owner_verdict_history"]), 3)


# --------------------------------------------------------------------------- F10: the job binds what it adopts, reuses and keeps
EVIDENCE = {"views": {"front": {"view": "front", "flags": []}}, "held_out": [], "inputs": [],
            "scale": {"front_width_mm": 140.0, "source": "assumed_default"}, "front": {"layout": "pair", "rim_class": "full"}}


def _protocol(evaluator: str | None = "package") -> dict:
    return {"fit_views": ["front"], "held_out_views": [], "leakage": {}, "identity_checklist": [], "visual_bar_calibrated": True, "gate_overrides": {},
            "calibration": {"evaluator": evaluator, "calibrated": True, "coverage": {"pair": {"covered": True}, "rim_full": {"covered": True}}}}


def _job(root: Path, *, evaluator=None, intake_drivers=None) -> mjob.Job:
    request = SimpleNamespace(product_id="p", limits={}, photos=[], donor=None, notes="")
    return mjob.Job(root / "job", request, driver=SimpleNamespace(name="scripted"), ar=False, evaluator_driver=evaluator,
                    intake_drivers=intake_drivers, log=lambda *a: None)


def _candidate(job_dir: Path, cid: str = "c0000", glb_bytes: bytes = b"glb bytes", lens: float = 0.3) -> Path:
    root = job_dir / "candidates" / cid
    (root / "program").mkdir(parents=True)
    (root / "program.json").write_text(json.dumps({"id": cid, "turn": 0, "module_order": ["frame"], "modules": {}, "program_set_sha256": "x"}), encoding="utf-8")
    (root / "program" / "frame.py").write_text("x = 1\n", encoding="utf-8")
    (root / "model.glb").write_bytes(glb_bytes)
    (root / "export.json").write_text(json.dumps({"sha256": _sha(glb_bytes), "bytes": len(glb_bytes), "contract": {"ok": True, "failures": []},
                                                  "receipt": {"triangles": 12}}), encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (root / "build" / "materials.json").write_text(json.dumps({"materials": {"frame": {"kind": "acetate"}}}), encoding="utf-8")
    (root / "observe").mkdir()
    (root / "observe" / "observation.json").write_text(json.dumps({
        # ar_report_valid: Candidate.valid() (2026-09-27) accepts only an observation from a validated harness run
        "summary": {"front_contour_mean_mm": 0.2, "lens_outline_mean_mm": lens, "ar_runtime_compatible": True, "ar_optical_meshes": 2,
                    "ar_continuity_failure": None, "ar_report_valid": True},
        "views": {"front": {"view": "front", "iou": 0.9, "contour_mean_mm": 0.2}}, "bbox_mm": [[-70, -20, -10], [70, 20, 10]],
        "ar": {"models": {"candidate": {"runtime_compatible": True, "optical_meshes_detected": 2, "continuity_failure": None}}}}), encoding="utf-8")
    return root


def _journal(job: mjob.Job) -> list[dict]:
    return [json.loads(l) for l in (job.dir / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


class JobEvaluationBinding(unittest.TestCase):
    def test_bound_external_evaluation_is_adopted_and_stored_with_its_binding(self):
        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td))
            root = _candidate(job.dir)
            (root / "evaluation_v2").mkdir()
            (root / "evaluation_v2" / "evaluation.json").write_text(json.dumps({"evaluation": _eval(), "asset_sha256": _sha(b"glb bytes"),
                                                                                "meta": {"protocol": mevaluate.PROTOCOL, "driver": "package"}}), encoding="utf-8")
            m = job.finalize(EVIDENCE, _protocol(), None)
            self.assertEqual(m["status"], "accepted")
            self.assertEqual(m["evaluation"]["overall"], "accept")
            self.assertEqual(m["asset"]["sha256"], _sha(b"glb bytes"))
            self.assertNotIn("export_record_sha256", m["asset"])
            stored = json.loads((job.dir / "evaluation" / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["asset_sha256"], _sha(b"glb bytes"))
            self.assertEqual(stored["candidate"], "c0000")
            self.assertEqual(stored["meta"]["protocol"], mevaluate.PROTOCOL)
            adopted = [r for r in _journal(job) if r["event"] == "evaluation_adopted"]
            self.assertEqual(len(adopted), 1)
            self.assertEqual(adopted[0]["asset_sha256"], _sha(b"glb bytes"))

    def test_unbound_changed_or_other_protocol_records_are_not_adopted(self):
        cases = {"legacy_unbound": {"evaluation": _eval(), "meta": {"protocol": mevaluate.PROTOCOL}},
                 "asset_changed": {"evaluation": _eval(), "asset_sha256": _sha(b"other"), "meta": {"protocol": mevaluate.PROTOCOL}},
                 "protocol_mismatch": {"evaluation": _eval(), "asset_sha256": _sha(b"glb bytes"), "meta": {"protocol": "modeler_evaluator_v2.1"}}}
        for reason, rec in cases.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as td:
                job = _job(Path(td))
                root = _candidate(job.dir)
                (root / "evaluation_v2").mkdir()
                (root / "evaluation_v2" / "evaluation.json").write_text(json.dumps(rec), encoding="utf-8")
                m = job.finalize(EVIDENCE, _protocol(), None)
                self.assertEqual(m["status"], "quality_unverified")
                self.assertIsNone(m["evaluation"])
                rows = [r for r in _journal(job) if r["event"] == "evaluation_not_adopted"]
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0]["reason"].startswith(reason), rows[0]["reason"])
                self.assertFalse((job.dir / "evaluation" / "evaluation.json").exists())

    def test_evaluator_name_selects_the_folder(self):
        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td), evaluator=SimpleNamespace(name="astra"))
            root = _candidate(job.dir)
            bound = {"evaluation": _eval("reject"), "asset_sha256": _sha(b"glb bytes"), "meta": {"protocol": mevaluate.PROTOCOL}}
            (root / "evaluation_v2").mkdir()
            (root / "evaluation_v2" / "evaluation.json").write_text(json.dumps(bound), encoding="utf-8")
            (root / "evaluation_v2_astra").mkdir()
            (root / "evaluation_v2_astra" / "evaluation.json").write_text(json.dumps(dict(bound, evaluation=_eval("accept"))), encoding="utf-8")
            m = job.finalize(EVIDENCE, _protocol("astra"), None)
            self.assertEqual(m["evaluation"]["overall"], "accept", "the astra evaluator's own folder, not the agent's")
            adopted = [r for r in _journal(job) if r["event"] == "evaluation_adopted"][0]
            self.assertTrue(adopted["source"].endswith(str(Path("evaluation_v2_astra") / "evaluation.json")), adopted["source"])
            self.assertEqual(adopted["evaluator"], "astra")
            # without a configured evaluator the protocol's frozen evaluator decides, then the journal's recovered driver
            other = _job(Path(td) / "two")
            self.assertEqual(other.evaluator_name({"calibration": {"evaluator": "astra"}}), "astra")
            other.recovered_driver_name = "package"
            self.assertEqual(other.evaluator_name({}), "package")
            self.assertEqual(calibration.evaluation_dir_for(other.evaluator_name({})), "evaluation_v2")

    def test_stored_job_evaluation_is_reused_only_when_bound_to_the_candidate_and_its_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td))
            _candidate(job.dir)
            (job.dir / "evaluation").mkdir()
            legacy = {"evaluation": _eval(), "meta": {"protocol": mevaluate.PROTOCOL}, "candidate": "c0000"}
            (job.dir / "evaluation" / "evaluation.json").write_text(json.dumps(legacy), encoding="utf-8")
            m = job.finalize(EVIDENCE, _protocol(), None)
            self.assertIsNone(m["evaluation"])
            self.assertEqual(m["status"], "quality_unverified")
            self.assertEqual([r["event"] for r in _journal(job) if r["event"].startswith("evaluation")], ["evaluation_not_reused"])
            (job.dir / "evaluation" / "evaluation.json").write_text(json.dumps(dict(legacy, asset_sha256=_sha(b"glb bytes"))), encoding="utf-8")
            m = job.finalize(EVIDENCE, _protocol(), None)
            self.assertEqual(m["evaluation"]["overall"], "accept")
            self.assertEqual(m["status"], "accepted")
            self.assertIn("evaluation_reused", [r["event"] for r in _journal(job)])
            (job.dir / "evaluation" / "evaluation.json").write_text(json.dumps(dict(legacy, asset_sha256=_sha(b"glb bytes"), candidate="c0007")), encoding="utf-8")
            m = job.finalize(EVIDENCE, _protocol(), None)
            self.assertIsNone(m["evaluation"], "a record of another candidate is not this candidate's evaluation")

    def test_fresh_evaluation_is_stored_with_the_bytes_digest(self):
        class Evaluator:
            name = "scripted"

            def decide(self, out_dir, request, images, **kw):
                return mevaluate.validate_evaluation(_eval()), {"driver": "scripted"}

        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td), evaluator=Evaluator())
            _candidate(job.dir)
            with mock.patch.object(mevaluate, "write_evaluator_package", return_value=({"protocol": mevaluate.PROTOCOL}, [])):
                m = job.finalize(EVIDENCE, _protocol(), None)
            stored = json.loads((job.dir / "evaluation" / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["asset_sha256"], _sha(b"glb bytes"))
            self.assertEqual(stored["meta"]["protocol"], mevaluate.PROTOCOL)
            self.assertEqual(m["status"], "accepted")


class OwnerVerdictOnRefinalize(unittest.TestCase):
    def test_kept_only_for_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td))
            root = _candidate(job.dir)
            m1 = job.finalize(EVIDENCE, _protocol(), None)
            self.assertEqual(m1["status"], "quality_unverified")
            apply_verdict(job.dir, "accept", "live", "perfect", calibration_file=Path(td) / "cal.jsonl", write_report=False)
            m2 = job.finalize(EVIDENCE, _protocol(), None)
            self.assertEqual(m2["status"], "accepted")
            self.assertEqual(m2["status_detail"]["accepted_by"], "owner")
            self.assertEqual(m2["owner_verdict"]["verdict"], "accept")
            self.assertEqual(len(m2["owner_verdict_history"]), 1)
            self.assertEqual(m2["status_detail"]["axes"]["owner_verdict"], "accept")
            self.assertIn("owner_verdict_kept", [r["event"] for r in _journal(job)])
            # the candidate's model.glb is rebuilt: the owner judged other bytes, so the verdict is stale and not applied
            (root / "model.glb").write_bytes(b"rebuilt bytes")
            m3 = job.finalize(EVIDENCE, _protocol(), None)
            self.assertEqual(m3["status"], "quality_unverified")
            self.assertNotIn("owner_verdict", m3)
            self.assertNotIn("accepted_by", m3["status_detail"])
            self.assertEqual(m3["owner_verdict_stale"]["verdict"], "accept")
            self.assertEqual(m3["owner_verdict_stale"]["asset_sha256"], _sha(b"glb bytes"))
            self.assertEqual(m3["asset"]["sha256"], _sha(b"rebuilt bytes"), "the manifest's digest is of the delivered bytes")
            self.assertEqual(m3["asset"]["export_record_sha256"], _sha(b"glb bytes"))
            events = [r["event"] for r in _journal(job)]
            self.assertIn("owner_verdict_stale", events)
            self.assertIn("asset_sha256_mismatch", events)


class EnsureEvidenceResume(unittest.TestCase):
    def test_interrupted_stage_runs_and_a_finished_one_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            job = _job(Path(td), intake_drivers=("reading-driver", "review-driver"))
            job.evidence_dir.mkdir(parents=True)
            (job.evidence_dir / "evidence.json").write_text(json.dumps(EVIDENCE), encoding="utf-8")
            calls: list = []

            def fake_stage(request, job_dir, evidence, reading_driver, review_driver, *, log=print):
                calls.append((reading_driver, review_driver))
                ev = dict(evidence, intake_stage={"reading": {"status": "complete"}, "review": {"status": "skipped"}, "actions": [], "errors": []})
                (Path(job_dir) / "evidence" / "evidence.json").write_text(json.dumps(ev), encoding="utf-8")
                return ev, ev["intake_stage"]

            with mock.patch("modeler.intake_astra.run_intake_stage", fake_stage):
                ev = job.ensure_evidence()
                self.assertEqual(calls, [("reading-driver", "review-driver")])
                self.assertEqual(ev["intake_stage"]["reading"]["status"], "complete")
                self.assertEqual(job.ensure_evidence(), ev)
                self.assertEqual(len(calls), 1, "a finished stage record is returned unchanged")
                cut = dict(EVIDENCE, intake_stage={"reading": {"status": "complete"}, "review": None})
                (job.evidence_dir / "evidence.json").write_text(json.dumps(cut), encoding="utf-8")
                job.ensure_evidence()
                self.assertEqual(len(calls), 2, "a record cut short before its review is an interrupted stage")
            self.assertEqual([r["event"] for r in _journal(job)].count("intake_stage_resumed"), 2)
            plain = _job(Path(td) / "plain")                    # no intake drivers: the existing behaviour
            plain.evidence_dir.mkdir(parents=True)
            (plain.evidence_dir / "evidence.json").write_text(json.dumps(cut), encoding="utf-8")
            self.assertEqual(plain.ensure_evidence(), cut)

    def test_finished_predicate(self):
        f = mjob.intake_stage_finished
        self.assertFalse(f({}))
        self.assertFalse(f({"intake_stage": {"reading": {"status": "complete"}, "review": None}}))
        self.assertFalse(f({"intake_stage": {"reading": None, "review": None}}))
        for reading in ("complete", "failed"):
            for review in ("complete", "failed", "skipped"):
                self.assertTrue(f({"intake_stage": {"reading": {"status": reading}, "review": {"status": review}}}))


if __name__ == "__main__":
    unittest.main()
