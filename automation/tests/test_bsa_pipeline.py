"""bsa.pipeline: execution rule (missing / stale / forced / code changed), DEPS vs the reads in the code, code
provenance records, batched AR merge, M1 aggregation, review sheet, the S11 look wiring (options, re-run rules,
skips, summary row; bsa.look.main faked), plus a smoke test of the real m1 run when its summary exists."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from bsa import core, export, pipeline
from bsa.core import PRODUCTS, STAGES
from bsa.pipeline import ALL_STAGES, LOOK_STAGE


def _touch_result(root: Path, run: str, product: str, stage: str, t: float, payload: dict | None = None) -> Path:
    d = root / "runs" / run / product / stage
    d.mkdir(parents=True, exist_ok=True)
    p = d / "result.json"
    p.write_text(json.dumps(payload or {"stage": stage}))
    os.utime(p, ns=(int(t * 1e9), int(t * 1e9)))
    return p


class TempRun(unittest.TestCase):
    """Points core.BSA_DATA (and the pipeline's copy) at a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patches = [mock.patch.object(core, "BSA_DATA", self.root), mock.patch.object(pipeline, "BSA_DATA", self.root),
                         mock.patch.object(pipeline, "CODE_CHECK", False)]      # the artifact rule alone; see TestCodeProvenance
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class TestExecutionRule(TempRun):
    def test_missing_stale_forced(self):
        t = 1_700_000_000.0
        for i, st in enumerate(STAGES[:9]):                      # s0..s8 in order, 10 s apart
            _touch_result(self.root, "r", "vb", st, t + 10 * i)
        self.assertIsNone(pipeline.why_run("r", "vb", "s4_depth"))
        self.assertEqual(pipeline.why_run("r", "vb", "s9_export"), "missing")
        # S3 rewritten after S8: S4..S8 that read S3 are stale; S7 is also stale through S3
        _touch_result(self.root, "r", "vb", "s3_cameras", t + 1000)
        self.assertEqual(pipeline.why_run("r", "vb", "s8_lens"), "older_than:s3")
        self.assertEqual(pipeline.why_run("r", "vb", "s5_temples"), "older_than:s3")
        self.assertIsNone(pipeline.why_run("r", "vb", "s2_front"))
        # force applies only at/after --from
        self.assertIsNone(pipeline.why_run("r", "vb", "s2_front", from_stage="s4_depth", force=True))
        self.assertEqual(pipeline.why_run("r", "vb", "s4_depth", from_stage="s4_depth", force=True), "forced")

    def test_plan_propagates_downstream(self):
        t = 1_700_000_000.0
        for i, st in enumerate(ALL_STAGES):
            _touch_result(self.root, "r", "vb", st, t + 10 * i)
        self.assertEqual({k: v for k, v in pipeline.plan("r", "vb").items() if v}, {})
        # S5 re-run: the dry run must propagate to S6 (tuck material), S7, S8 (mirror calibration renders the
        # assembly), then S9, S10 and the S11 look (it reads S8/S9/S10) - but not upstream
        _touch_result(self.root, "r", "vb", "s5_temples", t + 1000)
        pl = {k: v for k, v in pipeline.plan("r", "vb").items() if v}
        self.assertEqual(set(pl), {"s6_assembly", "s7_texture", "s8_lens", "s9_export", "s10_gate", LOOK_STAGE})
        self.assertTrue(pl["s9_export"].startswith("older_than:") and "s7" in pl["s9_export"])
        self.assertEqual(pl[LOOK_STAGE], "older_than:s8,s9,s10")
        # --to s10 leaves S11 out of the plan; S11 is not in core.STAGES (core.py is in every stage's closure)
        self.assertNotIn(LOOK_STAGE, pipeline.plan("r", "vb", to_stage="s10_gate"))
        self.assertNotIn(LOOK_STAGE, STAGES)
        self.assertEqual(ALL_STAGES, STAGES + (LOOK_STAGE,))

    def test_deps_are_upstream_only(self):
        for st, deps in pipeline.DEPS.items():
            for d in deps:
                self.assertLess(ALL_STAGES.index(d), ALL_STAGES.index(st), (st, d))
        self.assertEqual(set(pipeline.DEPS), set(ALL_STAGES))
        self.assertEqual(set(pipeline.MODULES), set(ALL_STAGES))
        self.assertEqual(pipeline.stage_key("s4"), "s4_depth")
        self.assertEqual(pipeline.stage_key("s11"), LOOK_STAGE)
        with self.assertRaises(ValueError):
            pipeline.stage_key("s12")

    def test_s10_never_reads_s11(self):
        """The look runs after the gate and never feeds it: no stage lists S11 in DEPS, and no module of any S0-S10
        code closure names it (only bsa/look.py and the orchestrator do)."""
        for st, deps in pipeline.DEPS.items():
            self.assertNotIn(LOOK_STAGE, deps, st)
        closure = {m for st in STAGES for m in pipeline.code_modules(st)}
        self.assertNotIn("look", closure)
        for m in sorted(closure):
            self.assertNotIn(LOOK_STAGE, (pipeline.BSA_DIR / f"{m}.py").read_text(encoding="utf-8"), m)

    def test_deps_cover_the_reads(self):
        """Every earlier stage a stage's code reads (a stage-name literal, ``<module>.STAGE`` or a loader call) is in
        DEPS, so a re-run upstream stage always makes it stale. The scan covers the stage module AND every helper
        module of its code closure (``code_modules``; over-approximating like the code fingerprint), so a stage read
        added inside an imported helper is caught too (only reads of stages BEFORE the stage count)."""
        loaders = {"generator.load(": ("s1_generator",), "load_cameras(": ("s3_cameras",), "load_depth(": ("s4_depth",),
                   "hull_views(": ("s0_intake", "s3_cameras"), "source_view(": ("s2_front", "s3_cameras")}
        stage_of = {m: st for st, m in pipeline.MODULES.items()}

        def reads_of(mod):
            src = (pipeline.BSA_DIR / f"{mod}.py").read_text(encoding="utf-8")
            r = {x for x in re.findall(r'"(s\d+_[a-z]+)"', src) if x in ALL_STAGES}
            r |= {stage_of[m] for m in re.findall(r"\b(\w+)\.STAGE\b", src) if m in stage_of}
            for pat, sts in loaders.items():
                if pat in src:
                    r |= set(sts)
            return r
        for st, mod in pipeline.MODULES.items():
            reads = {x for x in reads_of(mod) if ALL_STAGES.index(x) < ALL_STAGES.index(st)}
            self.assertEqual(reads - set(pipeline.DEPS[st]), set(), st)
        # helper modules of the closure that are not stage modules (core, raster, donor, archeck, ground_truth, ...)
        # (core only DEFINES the stage names - its STAGES tuple - and reads none)
        helpers = {m for st in ALL_STAGES for m in pipeline.code_modules(st)} - set(pipeline.MODULES.values()) - {"__init__", "core"}
        for st in ALL_STAGES:
            for m in set(pipeline.code_modules(st)) & helpers:
                reads = {x for x in reads_of(m) if ALL_STAGES.index(x) < ALL_STAGES.index(st)}
                self.assertEqual(reads - set(pipeline.DEPS[st]), set(), (st, m))
        # the fix-round reads that were missing before 2026-09-24
        self.assertIn("s4_depth", pipeline.DEPS["s5_temples"])
        self.assertIn("s5_temples", pipeline.DEPS["s6_assembly"])
        self.assertIn("s6_assembly", pipeline.DEPS["s8_lens"])
        self.assertIn("s4_depth", pipeline.DEPS["s10_gate"])
        # the decision reads the S7/S8 fallback flags (2026-09-24 final round)
        self.assertIn("s7_texture", pipeline.DEPS["s10_gate"])
        self.assertIn("s8_lens", pipeline.DEPS["s10_gate"])
        # S11 freezes S0's photo boxes, S8's lens summary, the S9 GLB and the S10 decision (bsa.look.prepare_inputs)
        self.assertEqual(set(pipeline.DEPS[LOOK_STAGE]), {"s0_intake", "s8_lens", "s9_export", "s10_gate"})

    def test_look_external_covers_the_pinned_shared_helpers(self):
        from bsa import look
        before = pipeline.look_external_digest()
        changed = {**look.pinned_functions(), "reconstruction.job._write": "0" * 64}
        with mock.patch.object(look, "pinned_functions", return_value=changed):
            self.assertNotEqual(pipeline.look_external_digest(), before)
        self.assertEqual(pipeline.look_external_digest(), before)

    def test_ar_runtime_is_part_of_the_render_stages_code(self):
        """S7/S8 choose parameters from renders of the ar/ runtime and S9 judges in it: a runtime change must make
        them stale, so their fingerprint carries an ``ar_runtime`` digest; the other stages' does not."""
        for st in pipeline.AR_RUNTIME_STAGES:
            self.assertIn("ar_runtime", pipeline.code_fingerprint(st)["modules"], st)
        for st in ("s6_assembly", "s10_gate"):
            self.assertNotIn("ar_runtime", pipeline.code_fingerprint(st)["modules"], st)
        # S11 observes every revision in the runtime; its reconstruction/ code (the files bsa.look pins in its own
        # session guard) is fingerprinted too, and only for S11
        self.assertIn(LOOK_STAGE, pipeline.AR_RUNTIME_STAGES)
        fp = pipeline.code_fingerprint(LOOK_STAGE)["modules"]
        self.assertIn("look_external", fp)
        self.assertIn("look", fp)
        self.assertNotIn("look_external", pipeline.code_fingerprint("s9_export")["modules"])
        from bsa import look
        self.assertEqual(set(pipeline.LOOK_EXTERNAL_FILES),
                         {f for f in look.implementation()["files"] if not f.startswith("bsa/")})
        if pipeline.ar_runtime_files():
            self.assertEqual(len(pipeline.ar_runtime_digest()), 64)
        # the harness itself is part of it (the old automation/qa/provider-comparison* glob matched nothing): the pages
        # and driver provider-comparison.mjs snapshots, and the Python that writes the manifest and runs it; not docs
        names = {str(f.relative_to(pipeline.AUTOMATION.parent)).replace("\\", "/") for f in pipeline.ar_runtime_files()}
        for rel in ("ar/qa/provider-comparison.mjs", "ar/qa/provider-comparison-ar.html",
                    "ar/qa/provider-comparison-lighting.mjs", "automation/qa/provider_comparison.py"):
            if (pipeline.AUTOMATION.parent / rel).exists():
                self.assertIn(rel, names)
        self.assertFalse(any(n.endswith(".md") for n in names))

    def test_run_upstream_executes_in_order_and_forces_stale(self):
        t = 1_700_000_000.0
        for i, st in enumerate(STAGES[:9]):
            _touch_result(self.root, "r", "vb", st, t + 10 * i)
        _touch_result(self.root, "r", "vb", "s3_cameras", t + 1000)
        calls = []

        def fake(product, stage, run, force):
            calls.append((stage, force))
            _touch_result(self.root, run, product, stage, time.time())
            return {}
        with mock.patch.object(pipeline, "run_stage", side_effect=fake):
            ex = pipeline.run_upstream("vb", "r", from_stage="s0_intake", to_stage="s10_gate", force=False, log=lambda *a: None)
        self.assertEqual([c[0] for c in calls], ["s4_depth", "s5_temples", "s6_assembly", "s7_texture", "s8_lens"])
        self.assertTrue(all(f for _, f in calls))          # an existing result is only recomputed with force=True
        self.assertEqual(ex[0]["reason"], "older_than:s3")

    def test_a_failing_stage_stops_only_its_product(self):
        t = 1_700_000_000.0
        for i, st in enumerate(STAGES[:4]):
            _touch_result(self.root, "r", "vb", st, t + i)
        calls = []

        def fake(product, stage, run, force):
            calls.append(stage)
            if stage == "s5_temples":
                raise RuntimeError("boom")
            _touch_result(self.root, run, product, stage, time.time())
            return {}
        with mock.patch.object(pipeline, "run_stage", side_effect=fake):
            ex = pipeline.run_upstream("vb", "r", from_stage="s0_intake", to_stage="s8_lens", force=False, log=lambda *a: None)
        self.assertEqual(calls, ["s4_depth", "s5_temples"])
        self.assertEqual(ex[-1]["error"], "RuntimeError: boom")


class TestCodeProvenance(TempRun):
    """Code-aware staleness on a fake package: imports (module- and function-level), fingerprints, records."""

    FILES = {"__init__": "", "core": "X = 1\n", "intake": "from .core import X\nimport bsa.generator\n",
             "front": "from . import core, intake\n\n\ndef f():\n    from .raster import render\n    return render\n",
             "raster": "from bsa.core import X\n", "generator": "from bsa import raster\n",
             "pipeline": "from . import front\n", "gate": "from . import front\n"}

    def setUp(self):
        super().setUp()
        self.pkg = self.root / "pkg"
        self.pkg.mkdir()
        for m, src in self.FILES.items():
            (self.pkg / f"{m}.py").write_text(src)
        self._p2 = [mock.patch.object(pipeline, "BSA_DIR", self.pkg), mock.patch.object(pipeline, "CODE_CHECK", True)]
        for p in self._p2:
            p.start()

    def tearDown(self):
        for p in self._p2:
            p.stop()
        super().tearDown()

    def _age_modules(self, t: float) -> None:
        for m in self.FILES:
            os.utime(self.pkg / f"{m}.py", (t, t))

    def _edit(self, mod: str, src: str) -> None:
        f = self.pkg / f"{mod}.py"
        f.write_text(src)
        t = time.time() + 5                    # strictly newer than any result written so far
        os.utime(f, (t, t))

    def test_imports_and_closure(self):
        self.assertEqual(pipeline.module_imports("front"), {"core", "intake", "raster"})
        self.assertEqual(pipeline.module_imports("intake"), {"core", "generator"})
        self.assertEqual(pipeline.code_modules("s2_front"), ["__init__", "core", "front", "generator", "intake", "raster"])
        a = pipeline.code_fingerprint("s2_front")
        self.assertEqual(a, pipeline.code_fingerprint("s2_front"))
        self._edit("raster", "from bsa.core import X\nY = 2\n")               # a function-level import's module
        self.assertNotEqual(a["code_sha256"], pipeline.code_fingerprint("s2_front")["code_sha256"])

    def test_status_and_reasons(self):
        t = time.time() - 100
        self._age_modules(t - 50)
        _touch_result(self.root, "r", "vb", "s0_intake", t)
        _touch_result(self.root, "r", "vb", "s2_front", t + 1)
        # no record, every module older than the result: not stale, but unproven
        self.assertEqual(pipeline.code_status("r", "vb", "s2_front")["state"], "unrecorded")
        self.assertIsNone(pipeline.why_run("r", "vb", "s2_front"))
        # recorded with the code on disk: current
        pipeline.record_code("r", "vb", "s2_front", pipeline.code_fingerprint("s2_front"))
        self.assertEqual(pipeline.code_status("r", "vb", "s2_front")["state"], "current")
        # the code changes: stale, naming the module (reached through a function-level import)
        self._edit("raster", "from bsa.core import X\nY = 3\n")
        cs = pipeline.code_status("r", "vb", "s2_front")
        self.assertEqual((cs["state"], cs["modules"]), ("changed", ["raster"]))
        self.assertEqual(pipeline.why_run("r", "vb", "s2_front"), "code_changed:raster")
        # the dry run: raster is also in S0's code (intake -> generator -> raster), so S0 re-runs and S2 follows it
        pl = pipeline.plan("r", "vb", to_stage="s2_front")
        self.assertEqual((pl["s0_intake"], pl["s2_front"]), ("code_newer:raster", "older_than:s0;code_changed:raster"))
        # a result rewritten outside the pipeline invalidates the record; then mtimes decide
        _touch_result(self.root, "r", "vb", "s2_front", time.time() + 10)
        self.assertEqual(pipeline.code_status("r", "vb", "s2_front")["state"], "unrecorded")
        _touch_result(self.root, "r", "vb", "s2_front", t + 2)
        cs = pipeline.code_status("r", "vb", "s2_front")
        self.assertEqual((cs["state"], cs["modules"]), ("newer", ["raster"]))
        # reasons combine: an older-than reason does not hide a code reason
        _touch_result(self.root, "r", "vb", "s0_intake", t + 50)
        self.assertEqual(pipeline.why_run("r", "vb", "s2_front"), "older_than:s0;code_newer:raster")

    def test_refresh_after_a_merge(self):
        t = time.time() - 100
        self._age_modules(t - 50)
        _touch_result(self.root, "r", "vb", "s10_gate", t)
        fp = pipeline.code_fingerprint("s10_gate")
        pipeline.record_code("r", "vb", "s10_gate", fp)
        _touch_result(self.root, "r", "vb", "s10_gate", t + 5, {"refinalized": True})
        self.assertEqual(pipeline.code_status("r", "vb", "s10_gate")["state"], "unrecorded")
        self.assertTrue(pipeline.refresh_code_record("r", "vb", "s10_gate", fp))
        self.assertEqual(pipeline.code_status("r", "vb", "s10_gate")["state"], "current")
        other = dict(fp, code_sha256="0" * 64)                 # different code: the record is not moved
        self.assertFalse(pipeline.refresh_code_record("r", "vb", "s10_gate", other))

    def test_run_upstream_records_the_executed_code(self):
        t = time.time() - 100
        self._age_modules(t - 50)
        _touch_result(self.root, "r", "vb", "s0_intake", t)
        _touch_result(self.root, "r", "vb", "s1_generator", t)

        def fake(product, stage, run, force):
            _touch_result(self.root, run, product, stage, time.time())
            return {}
        code = {st: pipeline.code_fingerprint(st) for st in ("s0_intake", "s1_generator", "s2_front")}
        with mock.patch.object(pipeline, "run_stage", side_effect=fake), \
                mock.patch.object(pipeline, "MODULES", {**pipeline.MODULES, "s1_generator": "generator"}):
            pipeline.run_upstream("vb", "r", from_stage="s0_intake", to_stage="s2_front", force=False,
                                  log=lambda *a: None, code=code)
        self.assertEqual(pipeline.code_status("r", "vb", "s2_front")["state"], "current")
        self.assertEqual(pipeline.code_status("r", "vb", "s0_intake")["state"], "unrecorded")   # not executed


def _harness(tmp: Path, rows: dict, views=pipeline.AR_VIEWS) -> dict:
    man = tmp / "manifest.json"
    man.write_text(json.dumps({"ar_views": [dict(v) for v in views]}))
    return {"harness_status": "inspected", "manifest_path": str(man), "out_dir": str(tmp), "models": rows}


class TestArMerge(unittest.TestCase):
    def test_attach_archeck_sets_criterion_1(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            base = {"contract": {"ok": True}, "flags": ["x"], "glb": str(t / "m.glb")}
            ok = _harness(t, {"vb-bsa-m1": {"status": "runtime_compatible", "runtime_compatible": True,
                                             "optical_meshes_detected": 2, "renders": [], "model_sha256": "abc"}})
            r = export.attach_archeck(dict(base), ok, "vb-bsa-m1", "vb", "m1")
            self.assertTrue(r["m1_criterion_1"])
            self.assertEqual(r["archeck"]["views"], ["front", "angled", "rolled"])
            self.assertNotIn("ar_check_failed", r["flags"])
            # no lenses detected -> fails, and the flag is not duplicated on a second merge
            nolens = _harness(t, {"vb-bsa-m1": {"status": "runtime_compatible", "runtime_compatible": True,
                                                 "optical_meshes_detected": 0}})
            r2 = export.attach_archeck(dict(base), nolens, "vb-bsa-m1", "vb", "m1")
            r2 = export.attach_archeck(r2, nolens, "vb-bsa-m1", "vb", "m1")
            self.assertFalse(r2["m1_criterion_1"])
            self.assertEqual(r2["flags"].count("ar_check_failed"), 1)
            # a missing row (harness never reached the model) and a failed contract both fail
            self.assertFalse(export.attach_archeck(dict(base), _harness(t, {}), "vb-bsa-m1", "vb", "m1")["m1_criterion_1"])
            bad = dict(base, contract={"ok": False})
            self.assertFalse(export.attach_archeck(bad, ok, "vb-bsa-m1", "vb", "m1")["m1_criterion_1"])

    def test_ar_row_is_current_only_for_the_same_bytes_and_views(self):
        with tempfile.TemporaryDirectory() as t:
            g = Path(t) / "model.glb"
            g.write_bytes(b"glb-bytes")
            s9 = {"glb": str(g), "archeck": {"model_sha256": core.sha256_file(g), "views": ["front", "angled", "rolled"]}}
            self.assertTrue(pipeline._ar_current(s9))
            self.assertFalse(pipeline._ar_current({**s9, "archeck": {**s9["archeck"], "views": ["front", "angled"]}}))
            g.write_bytes(b"other bytes")
            self.assertFalse(pipeline._ar_current(s9))
            self.assertFalse(pipeline._ar_current({"glb": str(g)}))


class TestProductSummary(TempRun):
    def test_row_from_artifacts(self):
        t = 1_700_000_000.0
        for i, st in enumerate(STAGES[:9]):
            _touch_result(self.root, "r", "vb", st, t + i, {"flags": ["f"]} if st == "s2_front" else {})
        glb = self.root / "runs" / "r" / "vb" / "s9_export" / "model.glb"
        glb.parent.mkdir(parents=True)
        glb.write_bytes(b"glb")
        sha = core.sha256_file(glb)
        s9 = {"status": "exported", "glb": str(glb), "flags": [],
              "export": {"sha256": sha, "bytes": 3, "triangles": 10, "parts": {"frame": {"faces_out": 10}}},
              "contract": {"ok": True, "failures": [], "summary": {"width_m": 0.14}},
              "archeck": {"status": "runtime_compatible", "optical_meshes_detected": 2, "continuity_failure": "gap"}}
        _touch_result(self.root, "r", "vb", "s9_export", t + 20, s9)
        s10 = {"decision": {"decision": "RETRY", "reasons": ["failed:c3_heldout_angled_front_piece"]}, "flags": [],
               "m1_criteria": {"bsa": {"c1_contract_ar_lenses": {"pass": True},
                                       "c2_lens_edge_vs_gt": {"pass": True, "value_mm": 0.2, "limit_mm": 0.3, "photo": "front"},
                                       "c3_heldout_angled_front_piece": {"pass": False, "value_pct_w": 0.5, "previous_pct_w": 0.3,
                                                                         "limit_pct_w": 0.4},
                                       "c4_zero_seam_gaps": {"pass": True, "gap_pixels": 0}},
                               "previous": {"c2_lens_edge_vs_gt": {"value_mm": 0.9}, "c4_zero_seam_gaps": {"gap_pixels": 7}}},
               "models": {"bsa": {"info": {"provenance": {"s9_export/model.glb": {"sha256": sha}}}, "seam": {"gap_area_mm2": 0.0},
                                  "lens": {"gt": {"criterion_photo": "front", "entries": [
                                      {"photo": "front", "reference_to_model": {"signed_mean_mm": 0.1},
                                       "all_types_symmetric_mean_mm": 0.2}]}}}}}
        _touch_result(self.root, "r", "vb", "s10_gate", t + 30, s10)
        row = pipeline.product_summary("vb", "r")
        c = row["m1_criteria"]
        self.assertTrue(c["c1_contract_ar_lenses"]["pass"])
        self.assertEqual(c["c2_lens_edge_vs_gt"]["previous_mm"], 0.9)
        self.assertEqual(c["c2_lens_edge_vs_gt"]["signed_mean_mm"], 0.1)
        self.assertFalse(c["c3_heldout_angled_front_piece"]["pass"])
        self.assertEqual(c["c4_zero_seam_gaps"]["previous_gap_pixels"], 7)
        self.assertTrue(row["gate"]["scored_current_glb"])
        self.assertEqual(row["stale"], {})
        self.assertIn("s2:f", row["flags"])
        self.assertIn("ar:continuity_failure", row["flags"])
        self.assertNotIn("s10:c1_differs_from_s9", row["flags"])
        # the export changes after the gate scored it: the row says so, and the plan marks S10 stale
        glb.write_bytes(b"new glb")
        _touch_result(self.root, "r", "vb", "s9_export", t + 40, {**s9, "export": {**s9["export"], "sha256": core.sha256_file(glb)}})
        row = pipeline.product_summary("vb", "r")
        self.assertIn("s10:scored_glb_is_not_the_current_export", row["flags"])
        self.assertEqual(row["stale"], {"s10_gate": "older_than:s9"})


def _row(c1=True, c2=True, c3=True, c4=True, c2_applicable=True):
    return {"decision": "READY", "m1_criteria": {"c1_contract_ar_lenses": {"pass": c1},
                                                 "c2_lens_edge_vs_gt": {"pass": c2, "applicable": c2_applicable},
                                                 "c3_heldout_angled_front_piece": {"pass": c3},
                                                 "c4_zero_seam_gaps": {"pass": c4}}}


class TestAggregate(unittest.TestCase):
    def test_m1_rules(self):
        rows = {p: _row() for p in PRODUCTS}
        a = pipeline.aggregate(rows)
        self.assertTrue(all(a[k]["pass"] for k in ("c1_contract_ar_lenses", "c2_lens_edge_vs_gt",
                                                    "c3_heldout_angled_front_piece", "c4_zero_seam_gaps")))
        # c3 needs 4/5: one failure is tolerated, two are not; an unevaluated c3 counts as not passed
        rows["vb"] = _row(c3=False)
        self.assertTrue(pipeline.aggregate(rows)["c3_heldout_angled_front_piece"]["pass"])
        rows["miu"] = _row(c3=None)
        self.assertFalse(pipeline.aggregate(rows)["c3_heldout_angled_front_piece"]["pass"])
        # c2: a product whose ground truth has no frame-bounded segment is NOT APPLICABLE, not a failure ...
        rows = {p: _row() for p in PRODUCTS}
        rows["miu"] = _row(c2=None, c2_applicable=False)
        a = pipeline.aggregate(rows)
        self.assertTrue(a["c2_lens_edge_vs_gt"]["pass"])
        self.assertEqual(a["c2_lens_edge_vs_gt"]["not_measurable"], ["miu"])
        rows["vb"] = _row(c2=False)
        self.assertFalse(pipeline.aggregate(rows)["c2_lens_edge_vs_gt"]["pass"])
        # ... but an APPLICABLE c2 that was not evaluated (no S10, a failed measurement) fails the run (review
        # finding: it used to be dropped silently, so c2 could pass on the products that happened to be measured)
        rows = {p: _row() for p in PRODUCTS}
        rows["invu"] = _row(c2=None)
        a = pipeline.aggregate(rows)
        self.assertFalse(a["c2_lens_edge_vs_gt"]["pass"])
        self.assertEqual(a["c2_lens_edge_vs_gt"]["unevaluated_applicable"], ["invu"])
        rows["invu"] = {"decision": None, "m1_criteria": {}}                 # no S10 at all
        self.assertFalse(pipeline.aggregate(rows)["c2_lens_edge_vs_gt"]["pass"])
        # a partial run (fewer than 5 products) has no run-level verdict: pass None, not a misleading False
        rows = {p: _row() for p in list(PRODUCTS)[:4]}
        a = pipeline.aggregate(rows)
        for k in ("c1_contract_ar_lenses", "c2_lens_edge_vs_gt", "c3_heldout_angled_front_piece", "c4_zero_seam_gaps"):
            self.assertIsNone(a[k]["pass"], k)
        self.assertIn("4/5", a["partial_run"])
        rows = {p: _row() for p in PRODUCTS}
        rows["rayban"] = _row(c4=None)
        self.assertFalse(pipeline.aggregate(rows)["c4_zero_seam_gaps"]["pass"])


class TestReviewSheet(unittest.TestCase):
    def test_sheet_layout(self):
        from bsa import archeck
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            renders = {}
            for kind, w in (("bsa", 200), ("prev", 260)):       # different content widths per row
                renders[kind] = []
                for v in pipeline.AR_VIEWS:
                    im = archeck.checker_background()
                    im[200:260, 360 - w // 2:360 + w // 2] = (20, 30, 90)
                    p = t / f"{kind}_{v['id']}.png"
                    Image.fromarray(im).save(p)
                    renders[kind].append(str(p))
            row = {"decision": "RETRY", "run": "m1", "triangles": 1000, "bytes": 2_000_000, "contract": {"ok": True},
                   "ar": {"status": "runtime_compatible", "optical_meshes_detected": 2, "renders": renders["bsa"]},
                   "previous_ar": {"renders": renders["prev"]}, "decision_reasons": ["failed:c3"],
                   "m1_criteria": _row(c3=False)["m1_criteria"]}
            out = pipeline.review_sheet("vb", row, t / "review.png", h=120)
            im = np.asarray(Image.open(out))
            self.assertLessEqual(im.shape[0], 118 + 3 * (120 + 10))
            self.assertGreater(im.shape[0], 118 + 2 * (120 + 10))
            # both render rows share one crop per column, so the columns line up at the same scale
            self.assertGreater(im.shape[1], 230 + 5 * 50)
            row["previous_ar"] = {"renders": []}               # a missing row is drawn as placeholders, not a crash
            pipeline.review_sheet("vb", row, t / "review2.png", h=120)


def _look_ready(root: Path, run: str, product: str, t: float) -> None:
    """S0/S8/S9 (+ model.glb)/S10 results in time order: what the look stage needs."""
    for i, st in enumerate(("s0_intake", "s8_lens", "s9_export", "s10_gate")):
        _touch_result(root, run, product, st, t + i, {"decision": {"decision": "READY", "reasons": []}} if st == "s10_gate" else {})
    (root / "runs" / run / product / "s9_export" / "model.glb").write_bytes(b"glb")


class FakeLook:
    """Stands in for ``bsa.look.main``: records the argv, writes a result.json like the real one (a ``failed`` one
    and an exception for the products in ``fail``)."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, argv):
        self.calls.append(list(argv))
        prod, drv, run = (argv[argv.index(k) + 1] for k in ("--product", "--driver", "--run"))
        sd = core.stage_dir(run, prod, LOOK_STAGE)
        if prod in self.fail:
            sd.save({"stage": LOOK_STAGE, "status": "failed", "driver": {"name": drv}, "error": "harness down"})
            raise RuntimeError("harness down")
        client = {}
        if drv == "scripted":
            client = {"script": {"sha256": core.sha256_file(Path(argv[argv.index("--script") + 1]))}}
        res = {"stage": LOOK_STAGE, "status": "delivered", "verdict": "not_edited" if drv == "none" else "improved",
               "driver": {"name": drv, "client": client}, "final_revision": "r0000", "turns_completed": 0,
               "paid_calls_used": 0, "final_decision": "READY", "flags": [], "input_glb_sha256": "x", "glb_sha256": "x"}
        sd.save(res)
        return res


class TestLookStage(TempRun):
    """S11 wiring: options, argv, the re-run rules and skips of ``look_all`` (bsa.look.main faked: no harness)."""

    def test_options_refuse_contradictions(self):
        script = self.root / "plans.json"
        script.write_text("[]")
        self.assertEqual(pipeline.look_options()["driver"], "none")
        for kw in ({"driver": "manual"}, {"script": script}, {"authorize_paid_astra": True},
                   {"driver": "scripted"}, {"driver": "scripted", "script": self.root / "absent.json"},
                   {"driver": "scripted", "script": script, "max_turns": 11},
                   {"driver": "scripted", "script": script, "astra_maximum_calls": 2},
                   {"driver": "astra", "astra_maximum_calls": 2},                      # not authorized
                   {"driver": "astra", "authorize_paid_astra": True},                  # no cap
                   {"driver": "astra", "authorize_paid_astra": True, "astra_maximum_calls": 11},
                   {"driver": "astra", "authorize_paid_astra": True, "astra_maximum_calls": 2, "script": script},
                   {"driver": "astra", "authorize_paid_astra": True, "astra_maximum_calls": 2},     # no ledger
                   {"driver": "astra", "authorize_paid_astra": True, "astra_maximum_calls": 2,         # ledger in s11_look
                    "astra_budget": self.root / "runs" / "r" / "vb" / LOOK_STAGE / "b.json"},
                   {"driver": "none", "astra_budget": self.root / "b.json"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                pipeline.look_options(**kw)

    def test_argv_per_driver(self):
        plans = self.root / "plans"
        plans.mkdir()
        (plans / "vb.json").write_text("[]")
        self.assertEqual(pipeline.look_argv("r", "vb", pipeline.look_options()),
                         ["--run", "r", "--product", "vb", "--driver", "none", "--fresh"])
        sc = pipeline.look_options("scripted", script=plans, max_turns=3)
        a = pipeline.look_argv("r", "vb", sc)
        self.assertEqual(Path(a[a.index("--script") + 1]), (plans / "vb.json").resolve())
        self.assertEqual(a[a.index("--max-turns") + 1], "3")
        self.assertIsNone(pipeline.look_script(sc, "miu"))                    # a folder without miu.json
        # astra: authorized, one cap for the whole authorization, and ONE owner-named ledger outside every s11_look
        # folder shared by every product (a ledger inside s11_look was renamed away by every --fresh re-run)
        ledger = self.root / "ledgers" / "a.json"
        op = pipeline.look_options("astra", authorize_paid_astra=True, astra_maximum_calls=4, astra_max_turns=3,
                                   astra_budget=ledger)
        a = pipeline.look_argv("r", "vb", op)
        self.assertIn("--authorize-paid-astra", a)
        self.assertEqual(a[a.index("--astra-maximum-calls") + 1], "4")
        self.assertEqual(Path(a[a.index("--astra-budget") + 1]), ledger.resolve())
        self.assertEqual(a[a.index("--astra-budget") + 1], pipeline.look_argv("r", "miu", op)[a.index("--astra-budget") + 1])
        self.assertNotIn("--astra-env", a)
        # both argvs pass bsa.look's own refusals (its _run is stopped before any work: no harness, no request)
        from bsa import look
        for opts in (sc, op):
            with self.subTest(driver=opts["driver"]), \
                    mock.patch.object(look, "_run", side_effect=RuntimeError("stop before any work")), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "stop before"):
                look.main(pipeline.look_argv("r", "vb", opts))

    def test_look_all_runs_skips_and_reruns(self):
        t = 1_700_000_000.0
        for p in ("vb", "rayban"):
            _look_ready(self.root, "r", p, t)
        _touch_result(self.root, "r", "oakley", "s9_export", t)               # no S10: skipped, not failed
        fake = FakeLook(fail={"rayban"})
        from bsa import look
        quiet = dict(from_stage="s0_intake", force=False, log=lambda *a: None)
        with mock.patch.object(look, "main", fake):
            ex = pipeline.look_all(["vb", "rayban", "oakley"], "r", opts=pipeline.look_options(), **quiet)
            by = {e["product"]: e for e in ex}
            self.assertEqual((by["vb"]["reason"], by["vb"]["status"], by["vb"]["verdict"]), ("missing", "delivered", "not_edited"))
            self.assertIn("harness down", by["rayban"]["error"])
            self.assertIn("no S10", by["oakley"]["skipped"])
            self.assertTrue(all("--fresh" in c for c in fake.calls))
            # a recorded failure re-runs; a delivered observe-only result does not
            fake.fail.clear()
            ex = pipeline.look_all(["vb", "rayban"], "r", opts=pipeline.look_options(), **quiet)
            self.assertEqual([(e["product"], e["reason"]) for e in ex], [("rayban", "failed_before")])
            # an editing driver displaces an observe-only look; the same script again does not; a changed one does
            script = self.root / "plans.json"
            script.write_text('[{"note": "a", "operations": []}]')
            sc = pipeline.look_options("scripted", script=script)
            ex = pipeline.look_all(["vb"], "r", opts=sc, **quiet)
            self.assertEqual(ex[0]["reason"], "driver:none->scripted")
            self.assertEqual(pipeline.look_all(["vb"], "r", opts=sc, **quiet), [])
            script.write_text('[{"note": "b", "operations": []}]')
            self.assertEqual(pipeline.look_all(["vb"], "r", opts=sc, **quiet)[0]["reason"], "script_changed")
            # observe-only never displaces an edited look, not even a stale one (it may hold paid work): it is kept
            # and reported; --force does replace it
            self.assertEqual(pipeline.look_all(["vb"], "r", opts=pipeline.look_options(), **quiet), [])
            _touch_result(self.root, "r", "vb", "s10_gate", time.time() + 3)
            n = len(fake.calls)
            ex = pipeline.look_all(["vb"], "r", opts=pipeline.look_options(), **quiet)
            self.assertIn("stale edited look kept (older_than:s10; driver scripted", ex[0]["skipped"])
            self.assertEqual(len(fake.calls), n)
            ex = pipeline.look_all(["vb"], "r", opts=pipeline.look_options(), from_stage=LOOK_STAGE, force=True,
                                   log=lambda *a: None)
            self.assertEqual(ex[0]["reason"], "forced")
            # a product listed twice is looked at once
            ex = pipeline.look_all(["vb", "vb"], "r", opts=pipeline.look_options(), from_stage=LOOK_STAGE, force=True,
                                   log=lambda *a: None)
            self.assertEqual(len(ex), 1)
            _touch_result(self.root, "r", "vb", "s10_gate", time.time() + 5)
            self.assertEqual(pipeline.look_all(["vb"], "r", opts=pipeline.look_options(), **quiet)[0]["reason"],
                             "older_than:s10")
            # the real m1 (owner-rated record) is skipped without calling bsa.look, unless allowed (a hermetic
            # stand-in: core.DATA and core.BSA_DATA point at a temp data/ whose bsa/runs/m1 is "the real m1")
            n = len(fake.calls)
            data = self.root / "fake-data"
            _look_ready(data / "bsa", "m1", "vb", t)
            with mock.patch.object(core, "DATA", data), mock.patch.object(core, "BSA_DATA", data / "bsa"):
                for run in ("m1", "M1", "vb/../m1"):
                    with self.subTest(run=run):
                        ex = pipeline.look_all(["vb"], run, opts=pipeline.look_options(), **quiet)
                        self.assertIn("owner-rated", ex[0]["skipped"])
            self.assertEqual(len(fake.calls), n)

    def test_the_real_m1_is_refused_for_every_stage(self):
        """Until 2026-09-25 --run defaulted to m1 and only S11 was guarded (by string equality): a bare --all would
        have rewritten m1's S7-S10 (stale since the ar_runtime glob fix)."""
        from bsa.look import is_real_m1
        data = self.root / "fake-data"
        (data / "bsa" / "runs" / "m1").mkdir(parents=True)
        (data / "bsa" / "runs" / "m2").mkdir(parents=True)
        called = []
        with mock.patch.object(core, "DATA", data), mock.patch.object(core, "BSA_DATA", data / "bsa"), \
                mock.patch.object(pipeline, "load_code", side_effect=lambda: called.append(1) or {}):
            self.assertEqual([is_real_m1(r) for r in ("m1", "M1", "m2/../m1", "m2", "m1-copy")],
                             [True, os.name == "nt", True, False, False])
            for run in ("m1", "m2/../m1"):
                with self.subTest(run=run), self.assertRaisesRegex(ValueError, "owner-rated m1 record"):
                    pipeline.run_pipeline(["vb"], run, sheets=False, log=lambda *a: None)
            self.assertEqual(called, [])                        # refused before any code is loaded or stage runs
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pipeline.main(["--all"])                        # --run is required: no default run
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pipeline.main(["--run", "m1", "--product", "vb"])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                pipeline.main(["--run", "m1", "--product", "vb", "--plan"])      # read-only: allowed
            self.assertIn("a run would be refused", out.getvalue())
        # a scratch copy of m1 elsewhere is not the record
        with mock.patch.object(core, "DATA", data), mock.patch.object(core, "BSA_DATA", self.root):
            (self.root / "runs" / "m1").mkdir(parents=True)
            self.assertFalse(is_real_m1("m1"))

    def test_plan_states_the_paid_calls_a_command_may_make(self):
        for p in ("vb", "miu"):
            _look_ready(self.root, "r", p, 1_700_000_000.0)
        ledger = self.root / "ledgers" / "owner.json"
        ledger.parent.mkdir()
        ledger.write_text(json.dumps({"protocol": "segmented_astra_responses_v1", "maximum_calls": 5, "reservations": [
            {"ordinal": 1, "request_dir": "x", "request_sha256": "0" * 64, "reserved_unix": 0}]}))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            pipeline.main(["--run", "r", "--product", "vb", "--product", "miu", "--product", "vb", "--plan",
                           "--look-driver", "astra", "--authorize-paid-astra", "--look-astra-maximum-calls", "5",
                           "--look-astra-budget", str(ledger), "--look-astra-max-turns", "3"])
        text = out.getvalue()
        self.assertEqual(sum(line.startswith(("vb ", "miu ")) for line in text.splitlines()), 2)   # vb once
        self.assertIn("1 of 5 calls used; this command would make at most 4 paid call(s) (2 product session(s), <= 3 each)", text)

    def test_run_pipeline_looks_only_after_a_good_gate(self):
        """S11 gets the gated products minus those whose S10 failed now; --to s10 runs no look."""
        seen = []
        gated = [{"product": "vb", "stage": "s10_gate", "returncode": 1}, {"product": "rayban", "stage": "s10_gate", "returncode": 0}]
        patches = [mock.patch.object(pipeline, "load_code", return_value={st: {"modules": {}} for st in ALL_STAGES}),
                   mock.patch.object(pipeline, "run_upstream", return_value=[]),
                   mock.patch.object(pipeline, "export_all", return_value=([], [])),
                   mock.patch.object(pipeline, "ar_check_exports", return_value=None),
                   mock.patch.object(pipeline, "previous_ar", return_value={}),
                   mock.patch.object(pipeline, "gate_all", return_value=gated),
                   mock.patch.object(pipeline, "look_all", side_effect=lambda ps, *a, **k: seen.append(list(ps)) or []),
                   mock.patch.object(pipeline, "save_summary")]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)
        s = pipeline.run_pipeline(["vb", "rayban", "miu"], "r", sheets=False, log=lambda *a: None)
        self.assertEqual(seen, [["rayban", "miu"]])
        self.assertIn("vb", s["failed_products"])
        pipeline.run_pipeline(["rayban"], "r", to_stage="s10", sheets=False, log=lambda *a: None)
        self.assertEqual(len(seen), 1)

    def test_summary_row_and_review_sheet(self):
        from bsa import archeck
        t = 1_700_000_000.0
        for i, st in enumerate(STAGES):
            _touch_result(self.root, "r", "vb", st, t + i)
        # without S11 the chain is still verified (S11 is optional) and the row has no look
        row = pipeline.product_summary("vb", "r")
        self.assertIsNone(row["look"])
        self.assertEqual(row["code"][LOOK_STAGE], "missing")
        # an S11 result with a session: the row reads the final revision's try-on renders through the pinned records
        ses = self.root / "session"
        ses.mkdir()
        tr = {}
        for v in pipeline.TRYON_IDS:
            im = archeck.checker_background()
            im[200:260, 300:420] = (120, 60, 30)
            Image.fromarray(im).save(ses / f"{v}.png")
            tr[v] = {"path": str(ses / f"{v}.png"), "sha256": "-"}
        (ses / "obs.json").write_text(json.dumps({"tryon": tr}))
        (ses / "rev.json").write_text(json.dumps({"observation": {"path": str(ses / "obs.json")}}))
        (ses / "state.json").write_text(json.dumps({"revisions": {"r0001": {"path": str(ses / "rev.json")}}}))
        _touch_result(self.root, "r", "vb", LOOK_STAGE, t + 20, {
            "status": "delivered", "verdict": "improved", "driver": {"name": "scripted"}, "final_revision": "r0001",
            "session": str(ses), "glb_sha256": "b", "input_glb_sha256": "a", "flags": ["look_x"], "final_decision": "READY"})
        row = pipeline.product_summary("vb", "r")
        lk = row["look"]
        self.assertEqual((lk["driver"], lk["verdict"], lk["edited"]), ("scripted", "improved", True))
        self.assertEqual(lk["renders"], [tr[v]["path"] for v in pipeline.TRYON_IDS])
        self.assertIn("s11:look_x", row["flags"])
        self.assertNotIn(LOOK_STAGE, row["m1_criteria"])
        # the review sheet gets a 4th row for the look (same crop per column as the other render rows)
        row.update(run="r", ar={"renders": lk["renders"]}, previous_ar={"renders": lk["renders"]})
        out = pipeline.review_sheet("vb", row, self.root / "review.png", h=120)
        H = np.asarray(Image.open(out)).shape[0]
        self.assertGreater(H, 118 + 19 + 3 * (120 + 10))
        self.assertLessEqual(H, 118 + 19 + 4 * (120 + 10))


REAL = core.BSA_DATA / "runs" / "m1" / "summary.json"


@unittest.skipUnless(REAL.exists(), "no m1 summary (data/ missing or pipeline not run)")
class TestRealSummary(unittest.TestCase):
    def test_summary_is_consistent_with_the_artifacts(self):
        s = json.loads(REAL.read_text())
        self.assertEqual(set(s["products"]), set(PRODUCTS))
        for p, row in s["products"].items():
            with self.subTest(product=p):
                self.assertIn(row["decision"], ("READY", "RETRY", "REVIEW"))
                self.assertEqual(set(row["m1_criteria"]), {"c1_contract_ar_lenses", "c2_lens_edge_vs_gt",
                                                           "c3_heldout_angled_front_piece", "c4_zero_seam_gaps"})
                self.assertTrue(Path(row["review_sheet"]).exists())
                glb = Path(row["glb"])
                self.assertTrue(glb.exists())
                self.assertEqual(core.sha256_file(glb), row["sha256"])        # the summary describes these bytes
                self.assertTrue(row["gate"]["scored_current_glb"])            # and S10 scored these bytes
                self.assertEqual(row["stale"], {})
                self.assertLessEqual(row["triangles"], 100_000)
        self.assertEqual(pipeline.aggregate(s["products"]), s["m1"])

    def test_shipped_glbs_meet_c1_and_c4(self):
        """The shipped artifact, not only the verdict: every exported GLB passes contract.check NOW, loaded as
        runtime_compatible with every lens node detected, and the gate counted zero seam-gap pixels on it (c4 once
        failed on the S9 export while the S6 arrays were gap-free)."""
        from bsa import contract, export
        s = json.loads(REAL.read_text())
        for p, row in s["products"].items():
            with self.subTest(product=p):
                chk = contract.check(row["glb"])
                self.assertTrue(chk["ok"], chk["failures"])
                s9 = json.loads((core.run_dir("m1", p) / "s9_export" / "result.json").read_text())
                self.assertEqual(s9["archeck"]["status"], "runtime_compatible")
                self.assertGreaterEqual(s9["archeck"]["optical_meshes_detected"], len(export.lens_nodes(s9)))
                self.assertTrue(export.m1_criterion_1(s9))
                s10 = json.loads((core.run_dir("m1", p) / "s10_gate" / "result.json").read_text())
                self.assertEqual(s10["m1_criteria"]["bsa"]["c4_zero_seam_gaps"]["gap_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
