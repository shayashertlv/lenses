"""Worker receipts are untrusted: ``modeler.agentic.tools`` must validate every id and path a worker result names
before the host touches its filesystem with them.

The forging worker below is the honest ``FakeWorker`` with a tampered ``result.json``: it appends render rows whose id
is a relative escape, an absolute path, a duplicate or an unrequested name (each pointing ``path`` at a file that WAS
ingested, so the basename cross-check of ``executor.result_is_untrusted_receipt`` is satisfied), or names a
``parts_npz`` the harness never writes. Before the fix the host resolved ``<build>/<id>.png`` with the forged id,
read a PNG planted outside the job folder, catalogued it as an author-visible (synthetic) artifact and queued it as an
observation. After the fix the operation fails with category ``worker_receipt`` and nothing is registered or copied.

Offline: no network, no Blender, no Docker. Temp roots under a short temp path (tests/test_agentic_support.py, lag-forged-render-ids).
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from bsa import archeck as bsa_archeck
from bsa import contract as bsa_contract
from modeler import author as mauthor
from modeler.agentic import config, demo, evaluation, executor, responses, runner
from modeler.agentic import tools as T
from modeler.agentic.artifacts import AUTHOR_VISIBLE, HOST_ONLY, atomic_write
from modeler.observe import AR_VIEWS, canonical_render_specs
from test_agentic_support import fresh_dir

NULL_LOG = lambda *a, **k: None   # noqa: E731
CANONICAL_IDS = [s["id"] for s in canonical_render_specs()]
CANONICAL_RENDERS = len(CANONICAL_IDS)          # 7: what one honest synthetic build queues for the author
# revisions/<rid>/build -> three ".." reach the job folder, the fourth its parent (renders sit beside result.json, the harness layout)
ESCAPE = "../" * 4
EXTRA_VIEW = {"id": "extra", "kind": "clay", "yaw": 30.0, "pitch": 10.0, "roll": 0.0, "ortho": False, "px_per_mm": 4.0, "target": "bbox"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plant_png(path: Path, colour=(255, 0, 255)) -> str:
    """A conspicuous PNG the host must never ingest; returns its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (96, 64), colour)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return sha(buf.getvalue())


class ForgingFakeWorker(executor.FakeWorker):
    """The honest synthetic worker whose ``result.json`` is tampered with before ingestion. ``forge`` maps the
    operation ordinal (1-based) to {"rows": [render rows appended], "set": {key: value written into the result}}."""

    def __init__(self, scenario: dict | None = None, *, forge: dict | None = None):
        super().__init__(scenario)
        self.forge = dict(forge or {})

    def ingest(self, identity, staging):
        run = self.runs[identity["operation_id"]]
        out = Path(run["work_dir"]) / "out"
        # the honest worker writes out/ and ingests into a scratch staging; the tampered output is then ingested for real
        super().ingest(identity, Path(run["work_dir"]) / "scratch_staging")
        tamper = self.forge.get(identity.get("ordinal"))
        if tamper:
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            result["renders"] = list(result.get("renders") or []) + list(tamper.get("rows") or [])
            result.update(tamper.get("set") or {})
            atomic_write(out / "result.json", json.dumps(result, indent=1).encode())
        ing = executor.validate_and_ingest_dir(out, staging)
        ing.synthetic = True
        return ing


def forged_row(render_id: str, *, path: str = "tex_front.png") -> dict:
    """A render row whose id is forged and whose ``path`` names an honest, ingested file (so the basename check passes)."""
    return {"id": render_id, "path": path, "kind": "textured", "width": 720, "height": 480, "camera": {"type": "orbit", "yaw": 0, "pitch": 0},
            "error": None, "seconds": 0.0}


class ToolsCase(unittest.TestCase):
    def setUp(self):
        self.root = fresh_dir(None, "forged-render-ids", "t-", env="LAG_TOOLS_TMP")
        self.sessions: list[runner.Session] = []

    def tearDown(self):
        for s in self.sessions:
            try:
                s.store.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.root, ignore_errors=True)

    def policy(self, **over) -> dict:
        base = dict(driver="scripted", worker="fake", intake="synthetic", critic="none", final_evaluator="scripted", ar=False, budget_usd="5",
                    max_inference_requests=12, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30, images_per_request=6)
        base.update(over)
        return config.build_policy(**{"owner_review": False, **base})

    def make_session(self, worker: executor.Worker, name: str = "job") -> runner.Session:
        inputs = self.root / f"{name}.in"
        translated = config.translate_request(demo.demo_request(inputs), inputs)
        session = runner.Session.create(self.root / name, translated=translated, policy=self.policy(), fingerprints={"test": "tools"},
                                        worker=worker, transport=responses.ScriptedTransport([]), worker_config=None, log=NULL_LOG)
        self.sessions.append(session)
        return session

    def run_tool(self, session: runner.Session, call_id: str, name: str, args: dict) -> tuple[dict, T.ToolResult]:
        op = session.store.insert_operation(call_id=call_id, request_id=None, tool_name=name, schema_version=T.TOOLS_VERSION, args=args,
                                            source_revision_id=None, fence=session.fence)
        result = T.execute(session.context(), op)
        return session.store.operation(op["id"]), result

    def build(self, session: runner.Session, call_id: str = "call_1", program: str = demo.PROGRAM_A) -> tuple[dict, T.ToolResult]:
        return self.run_tool(session, call_id, "edit_program", {"base_revision_id": None, "modules": demo.modules(program), "rationale": "unit build",
                                                                "expected_changes": ["a front"], "build_now": True, "deliver_if_compatible": False})

    # shared assertions
    def assert_receipt_refused(self, session: runner.Session, op: dict, result: T.ToolResult, planted_sha: str | None = None) -> None:
        store = session.store
        self.assertFalse(result.text.get("built"), result.text)
        self.assertEqual(result.text.get("category"), "worker_receipt", result.text)
        self.assertEqual((op.get("result") or {}).get("category"), "worker_receipt", op.get("result"))
        self.assertFalse((op.get("result") or {}).get("ok"))
        self.assertEqual(store.observations(), [], "a refused receipt must queue no image for the author")
        images = [a for a in store.artifacts() if a["kind"] in ("render", "sheet", "crop")]
        self.assertEqual(images, [], "a refused receipt must register no image artifact")
        self.assertEqual(result.images, [])
        if planted_sha is not None:
            self.assertNotIn(planted_sha, {a["sha256"] for a in store.artifacts()}, "the planted PNG outside the job was ingested")
        job_root = store.job_dir.resolve()
        for a in store.artifacts():
            self.assertTrue((store.job_dir / a["rel_path"]).resolve().is_relative_to(job_root), a["rel_path"])


# =========================================================================== forged render ids on a build
class ForgedRenderIdsOnBuild(ToolsCase):
    def test_relative_escape_id_is_refused_and_the_planted_png_stays_outside(self):
        planted = self.root / "outside" / "leak.png"
        planted_sha = plant_png(planted)
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row(f"{ESCAPE}outside/leak")]}})
        session = self.make_session(worker)
        # the forged id really resolves to the planted file from where the host would look
        build_dir = session.store.job_dir / "revisions" / "r0001" / "build"
        self.assertEqual((build_dir / f"{ESCAPE}outside/leak.png").resolve(), planted.resolve())
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result, planted_sha)
        self.assertEqual(session.store.revision("r0001")["state"], "build_failed")
        self.assertIn("ingestion issues", str(result.text.get("error")))

    def test_absolute_path_id_is_refused(self):
        planted = self.root / "outside" / "abs.png"
        planted_sha = plant_png(planted, (0, 255, 255))
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row(str(planted.with_suffix("")).replace("\\", "/"))]}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result, planted_sha)

    def test_duplicate_id_is_refused(self):
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row("tex_front")]}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result)

    def test_unrequested_id_is_refused(self):
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row("tex_extra")]}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result)

    def test_wrong_path_for_a_requested_id_is_refused(self):
        # a requested id whose row points the host at another ingested file
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row("clay_extra", path="clay_front.png")]}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result)

    def test_forged_parts_npz_name_is_refused(self):
        worker = ForgingFakeWorker(forge={1: {"set": {"parts_npz": "../../evidence/author_visible.json", "materials_json": "materials.json"}}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result)

    def test_parts_npz_with_the_right_name_but_not_ingested_is_refused(self):
        worker = ForgingFakeWorker(forge={1: {"set": {"parts_npz": "/work/out/parts.npz", "materials_json": "/work/out/materials.json"}}})
        session = self.make_session(worker)
        op, result = self.build(session)
        self.assert_receipt_refused(session, op, result)

    def test_honest_demo_scenario_still_builds_and_queues_its_renders(self):
        worker = executor.FakeWorker(demo.demo_fake_scenario())
        session = self.make_session(worker)
        op1, r1 = self.build(session, "call_1", demo.PROGRAM_A)
        self.assertFalse(r1.text["built"])
        self.assertEqual(r1.text["category"], "program_error")
        self.assertEqual(session.store.revision("r0001")["state"], "build_failed")
        op2, r2 = self.run_tool(session, "call_2", "edit_program", {"base_revision_id": "r0001", "modules": demo.modules(demo.PROGRAM_B), "rationale": "repair",
                                                                     "expected_changes": ["builds"], "build_now": True, "deliver_if_compatible": False})
        self.assertTrue(r2.text["built"], r2.text)
        self.assertNotIn("images_queued", r2.text)
        self.assertEqual(r2.text["images_attached"], len(r2.images))
        self.assertEqual(r2.text["images_attached"] + r2.text["deferred_images"], CANONICAL_RENDERS)
        self.assertEqual(session.store.revision("r0002")["state"], "compatible")
        pending = session.store.observations(state="pending")
        self.assertEqual(len(pending), CANONICAL_RENDERS)
        self.assertEqual(sorted(session.store.artifact(o["artifact_id"])["recipe"]["view"] for o in pending), sorted(CANONICAL_IDS))
        self.assertEqual((op2.get("result") or {}).get("category"), None)
        self.assertTrue((op2.get("result") or {}).get("ok"))


# =========================================================================== forged render ids on render_views
class ForgedRenderIdsOnRenderViews(ToolsCase):
    def built_session(self, forge: dict, name: str = "job") -> runner.Session:
        session = self.make_session(ForgingFakeWorker(forge=forge), name)
        op, result = self.build(session)
        self.assertTrue(result.text["built"], result.text)
        return session

    def test_relative_escape_id_is_refused(self):
        planted = self.root / "outside" / "views.png"
        planted_sha = plant_png(planted, (255, 255, 0))
        # worker/<op>/staging -> three ".." reach the job folder, the fourth its parent
        session = self.built_session({2: {"rows": [forged_row("../../../../outside/views", path="extra.png")]}})
        before = len(session.store.observations())
        op, result = self.run_tool(session, "call_2", "render_views", {"revision_id": "r0001", "views": [EXTRA_VIEW]})
        self.assertEqual(result.text.get("rendered"), [], result.text)
        self.assertEqual(result.text.get("category"), "worker_receipt", result.text)
        self.assertEqual(result.images, [])
        self.assertEqual(len(session.store.observations()), before)
        self.assertNotIn(planted_sha, {a["sha256"] for a in session.store.artifacts()}, "the planted PNG outside the job was ingested")
        self.assertEqual([a for a in session.store.artifacts() if a["recipe"].get("view") not in CANONICAL_IDS and a["kind"] == "render"], [])

    def test_unrequested_and_duplicate_ids_are_refused(self):
        for name, ordinal_rows in (("unrequested", [forged_row("unrequested", path="extra.png")]), ("duplicate", [forged_row("extra", path="extra.png")])):
            session = self.built_session({2: {"rows": ordinal_rows}}, name)
            op, result = self.run_tool(session, "call_2", "render_views", {"revision_id": "r0001", "views": [EXTRA_VIEW]})
            self.assertEqual(result.text.get("category"), "worker_receipt", result.text)
            self.assertEqual(result.images, [])
            self.assertEqual(len(session.store.observations()), CANONICAL_RENDERS)

    def test_honest_render_views_registers_the_requested_view(self):
        session = self.built_session({})
        op, result = self.run_tool(session, "call_2", "render_views", {"revision_id": "r0001", "views": [EXTRA_VIEW]})
        self.assertEqual([r["id"] for r in result.text["rendered"]], ["extra"], result.text)
        self.assertEqual(len(result.images), 1)
        self.assertEqual(len(session.store.observations()), CANONICAL_RENDERS + 1)


# =========================================================================== the receipt verifier and the defensive use-site check
class ReceiptVerifier(ToolsCase):
    def ingested(self, staging: Path, files: list[str], result: dict) -> executor.Ingested:
        rows = []
        for rel in files:
            p = staging / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * 8)
            rows.append({"rel": rel, "sha256": sha(b"x" * 8), "bytes": 8})
        return executor.Ingested(staging=staging, files=rows, result=result, manifest_sha256="0" * 64, synthetic=True)

    def test_honest_receipt_has_no_issues(self):
        staging = self.root / "staging"
        ing = self.ingested(staging, ["a.png", "b.png", "result.json", "candidate.blend", "parts.npz", "materials.json"],
                            {"ok": True, "renders": [{"id": "a", "path": "a.png", "error": None}, {"id": "b", "path": "b.png", "error": None}],
                             "blend": "/work/out/candidate.blend", "parts_npz": "/work/out/parts.npz", "materials_json": "C:\\w\\out\\materials.json"})
        self.assertEqual(T.verify_worker_receipt(ing, requested_renders=[{"id": "a"}, {"id": "b"}]), [])

    def test_every_forgery_is_an_issue(self):
        staging = self.root / "staging"
        ing = self.ingested(staging, ["a.png", "result.json"], {"ok": True, "renders": [{"id": "a", "path": "a.png", "error": None}]})
        requested = [{"id": "a"}]
        cases = {"escape": {"renders": [{"id": "../../x", "path": "a.png", "error": None}]},
                 "absolute": {"renders": [{"id": "C:/evidence/x", "path": "a.png", "error": None}]},
                 "duplicate": {"renders": [{"id": "a", "path": "a.png", "error": None}, {"id": "a", "path": "a.png", "error": None}]},
                 "unrequested": {"renders": [{"id": "b", "path": "a.png", "error": None}]},
                 "wrong_path": {"renders": [{"id": "a", "path": "other.png", "error": None}]},
                 "not_ingested": {"renders": [{"id": "a", "path": None, "error": None}], "files": []},
                 "uppercase": {"renders": [{"id": "A", "path": "A.png", "error": None}]},
                 "not_a_list": {"renders": {"id": "a"}},
                 "row_not_a_dict": {"renders": ["a"]},
                 "parts_name": {"renders": [], "parts_npz": "../../evidence/leak.json"},
                 "parts_not_ingested": {"renders": [], "parts_npz": "/work/out/parts.npz"},
                 "blend_name": {"renders": [], "blend": "a.png"},
                 "materials_type": {"renders": [], "materials_json": ["materials.json"]}}
        for name, patch in cases.items():
            files = patch.pop("files", None)
            i = self.ingested(self.root / f"st-{name}", ["a.png", "result.json"] if files is None else files, dict({"ok": True}, **patch))
            issues = T.verify_worker_receipt(i, requested_renders=requested)
            self.assertTrue(issues, name)
            self.assertTrue(all(isinstance(s, str) for s in issues), name)
        self.assertEqual(T.verify_worker_receipt(ing, requested_renders=requested), [])

    def test_a_failed_render_row_needs_no_file_but_still_a_valid_id(self):
        staging = self.root / "staging"
        ing = self.ingested(staging, ["result.json"], {"ok": False, "renders": [{"id": "a", "path": None, "error": "boom"}]})
        self.assertEqual(T.verify_worker_receipt(ing, requested_renders=[{"id": "a"}]), [])
        ing = self.ingested(self.root / "st2", ["result.json"], {"ok": False, "renders": [{"id": "../a", "path": None, "error": "boom"}]})
        self.assertTrue(T.verify_worker_receipt(ing, requested_renders=[{"id": "a"}]))

    def test_render_file_path_is_contained(self):
        folder = self.root / "renders_root"
        folder.mkdir(parents=True)
        self.assertEqual(T.render_file_path(folder, "tex_front"), folder / "tex_front.png")
        for bad in ("../x", "../../evidence/leak", "C:/evidence/leak", "/tmp/x", "a/b", "", "A", "x" * 41, None, 3):
            self.assertIsNone(T.render_file_path(folder, bad), bad)

    def test_use_site_refuses_an_escape_even_when_the_receipt_check_is_bypassed(self):
        """Defense in depth: with the receipt verifier disabled, the build's use site still registers nothing outside its folder."""
        planted = self.root / "outside" / "bypass.png"
        planted_sha = plant_png(planted, (0, 0, 255))
        worker = ForgingFakeWorker(forge={1: {"rows": [forged_row(f"{ESCAPE}outside/bypass")]}})
        session = self.make_session(worker)
        original = T.verify_worker_receipt
        T.verify_worker_receipt = lambda *a, **k: []
        try:
            op, result = self.build(session)
        finally:
            T.verify_worker_receipt = original
        self.assertTrue(result.text["built"], result.text)
        self.assertEqual(result.text["images_attached"] + result.text["deferred_images"], CANONICAL_RENDERS)
        self.assertNotIn(planted_sha, {a["sha256"] for a in session.store.artifacts()}, "the use site read the planted PNG")
        self.assertEqual(len(session.store.observations()), CANONICAL_RENDERS)
        self.assertTrue([e for e in session.store.events() if e["kind"] == "worker_render_refused"], "the refusal must be journaled")


if __name__ == "__main__":
    unittest.main()


# =========================================================================== lessons of test-pilot-001: cheap turns, no dead ends
class PilotLessons(ToolsCase):
    def test_read_program_before_any_program_is_a_note_not_an_error(self):
        session = self.make_session(executor.FakeWorker())
        op, result = self.run_tool(session, "call_1", "read_program", {"revision_id": None})
        self.assertNotIn("error", result.text, result.text)
        self.assertIsNone(result.text["revision"])
        self.assertIn("edit_program", result.text["note"])
        self.assertEqual(op["state"], "pending", "the runner commits the state; the tool itself raised nothing")

    def test_patch_occurrence_and_helpful_refusals(self):
        nl = chr(10)
        src = nl.join(["a = 1", "b = 2", "a = 1", "c = 3", ""])
        self.assertEqual(T.apply_patch(src, [{"find": "a = 1", "replace": "a = 9", "occurrence": 2}]), nl.join(["a = 1", "b = 2", "a = 9", "c = 3", ""]))
        self.assertEqual(T.apply_patch(src, [{"find": "b = 2", "replace": "b = 8"}]), nl.join(["a = 1", "b = 8", "a = 1", "c = 3", ""]))
        with self.assertRaises(T.ToolError) as cm:
            T.apply_patch(src, [{"find": "a = 1", "replace": "x"}])
        self.assertIn("occurs 2 times at lines [1, 3]", str(cm.exception))
        self.assertIn("occurrence", str(cm.exception))
        with self.assertRaises(T.ToolError) as cm:
            T.apply_patch(src, [{"find": "b = 22", "replace": "x"}])
        self.assertIn("occurs 0 times", str(cm.exception))
        self.assertIn("b = 2", str(cm.exception), "the closest line is named")
        with self.assertRaises(T.ToolError):
            T.apply_patch(src, [{"find": "a = 1", "replace": "x", "occurrence": 3}])
        with self.assertRaises(T.ToolError):
            T.apply_patch(src, [{"find": "a = 1", "replace": "x", "occurrence": 0}])
        with self.assertRaises(T.ToolError):
            T.apply_patch(src, [{"find": "a = 1", "replace": "x", "extra": 1}])

    def test_state_summary_carries_the_operation_budget_and_a_last_turn_notice(self):
        session = self.make_session(executor.FakeWorker())
        summary = T.state_summary(session.context())
        self.assertEqual((summary["operations_used"], summary["operations_cap"], summary["operations_remaining"]), (0, 12, 12))
        self.assertIn("deliver with at least two left", summary["budget_notice"])
        for _ in range(10):
            session.budget.reserve(role="author", purpose="author", input_tokens=100, max_output_tokens=300)
        summary = T.state_summary(session.context())
        self.assertEqual(summary["operations_remaining"], 2)
        self.assertIn("two inference operations remain", summary["budget_notice"])
        session.budget.reserve(role="author", purpose="author", input_tokens=100, max_output_tokens=300)
        summary = T.state_summary(session.context())
        self.assertEqual(summary["operations_remaining"], 1)
        self.assertIn("LAST inference operation", summary["budget_notice"])
        self.assertIn("request_delivery", summary["budget_notice"])
        self.assertEqual(summary["budget_cap_usd"], "5.000000")


# =========================================================================== tools ergonomics: every author turn counts
SHEET_KEYS = ["photo_match", "clay", "textured", "ar", "see_through"]
MATCH_IDS = ["match_front", "match_left", "match_back"]
SINGLE_IDS = ["clay_front", "clay_left", "tex_front", "tex_angled"]
LENSES_SRC = "gl.note('lenses module')\nlens = 1\n"
NL = chr(10)


class RealishFakeWorker(executor.FakeWorker):
    """The synthetic FakeWorker made to look like a real one to ``build_revision``: it claims no synthetic images, and its
    build names ``parts.npz`` / ``materials.json`` (ingested at the root) so the host reaches the export + observation
    branch; export and observation are then replaced by fakes in the test."""
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
            result["parts_npz"] = "parts.npz"
            result["materials_json"] = "materials.json"
            result["inventory"] = [{"object": "front_plate", "part": "frame", "component": "front", "triangles": 12, "boundary_edges": 0, "nonmanifold_edges": 0, "misoriented_edges": 0},
                                   {"object": "temple_R_arm", "part": "temple_R", "component": "arm", "triangles": 40, "boundary_edges": 6, "nonmanifold_edges": 2, "misoriented_edges": 0}]
            atomic_write(out / "parts.npz", b"not a real npz")
            atomic_write(out / "materials.json", b"{}")
            atomic_write(out / "result.json", json.dumps(result, indent=1).encode())
        ing = executor.validate_and_ingest_dir(out, staging)
        ing.synthetic = False
        return ing


def fake_export(*, contract_ok: bool, write_glb: bool = True):
    def export_glb(npz_path, materials_json, out_path, *, extras=None):
        raw = b"GLB-FAKE-" + json.dumps(extras or {}, sort_keys=True).encode()
        if write_glb:
            atomic_write(Path(out_path), raw)
        checks = {"triangles": {"pass": True, "value": 52, "limit": 100000},
                  "watertight_parts": {"pass": contract_ok, "value": {"frame": True, "temple_R": contract_ok}, "limit": "frame/temples closed; lens closed or +Z front sheet"}}
        parts = {"frame": {"role": "frame", "watertight": True, "pass": True}, "temple_R": {"role": "temple", "watertight": contract_ok, "pass": contract_ok}}
        return {"glb": str(out_path), "sha256": sha(raw), "bytes": len(raw), "contract": {"ok": contract_ok, "checks": checks,
                                                                                            "failures": [] if contract_ok else ["watertight_parts"], "parts": parts},
                "notes": ["temple_R_arm: dropped 3 degenerate triangles before export (object was not closed)"], "parts": {}}
    return export_glb


def fake_observe(*, match: bool, with_heldout: bool, calls: list):
    """A stand-in for ``modeler.observe.observe_candidate`` that writes the sheets and the single renders and records its kwargs."""
    def write(p: Path) -> str:
        plant_png(p, (20, 200, 20))
        return str(p)

    def body(cand_dir, build, evidence, evidence_dir, **kw):
        calls.append(kw)
        obs_dir = Path(cand_dir) / "observe"
        sheets = {k: write(obs_dir / f"sheet_{k}.png") for k in SHEET_KEYS}
        renders = {k: write(obs_dir / "renders" / f"{k}.png") for k in SINGLE_IDS + (MATCH_IDS if match else [])}
        return {"seconds": 1.0, "triangles": 52, "bbox_mm": [[-70, -25, -3], [70, 25, 3]], "views": {}, "sheets": sheets, "renders": renders,
                "summary": {"status": "measured"}}

    if with_heldout:
        def observe_candidate(cand_dir, build, evidence, evidence_dir, *, held_out_ids, previous_cameras=None, ar=True, glb_path=None,
                              time_limit_s=300, render_harness=None, heldout=False):
            return body(cand_dir, build, evidence, evidence_dir, held_out_ids=held_out_ids, ar=ar, glb_path=glb_path, heldout=heldout)
    else:
        def observe_candidate(cand_dir, build, evidence, evidence_dir, *, held_out_ids, previous_cameras=None, ar=True, glb_path=None,
                              time_limit_s=300, render_harness=None):
            return body(cand_dir, build, evidence, evidence_dir, held_out_ids=held_out_ids, ar=ar, glb_path=glb_path)
    return observe_candidate


def expected_rules(session) -> list:
    """The rules the first message states: runner.agentic_rules over the shared rules when it exists, else the shared rules."""
    rules = getattr(runner, "agentic_rules", None)
    return rules(mauthor.RULES, T.compact_evidence_safe(session.evidence())) if rules else list(mauthor.RULES)


class ErgonomicsLessons(ToolsCase):
    def make_session(self, worker: executor.Worker, name: str = "job", **over) -> runner.Session:
        inputs = self.root / f"{name}.in"
        translated = config.translate_request(demo.demo_request(inputs), inputs)
        session = runner.Session.create(self.root / name, translated=translated, policy=self.policy(**over), fingerprints={"test": "tools"},
                                        worker=worker, transport=responses.ScriptedTransport([]), worker_config=None, log=NULL_LOG)
        self.sessions.append(session)
        return session

    def two_module_revision(self, session: runner.Session) -> T.ToolResult:
        mods = demo.modules(demo.PROGRAM_A)
        mods["lenses"] = {"mode": "replace", "content": LENSES_SRC, "expected_base_sha256": None}
        op, result = self.run_tool(session, "call_1", "edit_program", {"base_revision_id": None, "modules": mods, "rationale": "two modules",
                                                                       "expected_changes": [], "build_now": False, "deliver_if_compatible": False})
        self.assertNotIn("error", result.text, result.text)
        return result

    # ---- 1. one refusal names every failing edit of every module
    def test_a_bad_patch_reports_every_failing_edit_of_every_module_in_one_turn(self):
        session = self.make_session(executor.FakeWorker())
        created = self.two_module_revision(session)
        shas = created.text["modules"]
        frame_patch = json.dumps([{"find": "demo program A", "replace": "demo program C"},          # fine
                                  {"find": "no such text", "replace": "x"},                          # fails
                                  {"find": "gl.", "replace": "gl2."}])                                  # ambiguous: fails
        lenses_patch = json.dumps([{"find": "lens = 2", "replace": "lens = 3"}])                       # fails
        mods = {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in T.MODULE_KEYS}
        mods["frame"] = {"mode": "patch", "content": frame_patch, "expected_base_sha256": shas["frame"]}
        mods["lenses"] = {"mode": "patch", "content": lenses_patch, "expected_base_sha256": shas["lenses"]}
        op, result = self.run_tool(session, "call_2", "edit_program", {"base_revision_id": "r0001", "modules": mods, "rationale": "bad patch",
                                                                       "expected_changes": [], "build_now": True, "deliver_if_compatible": False})
        err = result.text.get("error")
        self.assertIsNotNone(err, result.text)
        self.assertIn("frame: edit #2 of 3:", err)
        self.assertIn("frame: edit #3 of 3:", err)
        self.assertIn("lenses: edit #1 of 1:", err)
        self.assertNotIn("edit #1 of 3", err, "the good edit is not a failure")
        self.assertEqual(len(session.store.revisions()), 1, "a refused patch creates nothing")
        self.assertEqual(session.store.operations(state="running"), [], "nothing was built")

    def test_apply_patch_numbers_edits_from_one(self):
        src = NL.join(["a = 1", "b = 2", "a = 1", ""])
        with self.assertRaises(T.ToolError) as cm:
            T.apply_patch(src, [{"find": "b = 2", "replace": "b = 3"}, {"find": "zz", "replace": "y"}, {"find": "a = 1", "replace": "q"}])
        msg = str(cm.exception)
        self.assertIn("edit #2 of 3:", msg)
        self.assertIn("edit #3 of 3:", msg)
        self.assertNotIn("edit #1 of 3", msg)
        self.assertNotIn("edit 0", msg)

    # ---- 2. every edit / build result carries the per-module sha256
    def test_edit_and_build_results_carry_the_module_sha256(self):
        session = self.make_session(executor.FakeWorker())
        created = self.two_module_revision(session)
        self.assertEqual(created.text["modules"], {"frame": sha(demo.PROGRAM_A.encode()), "lenses": sha(LENSES_SRC.encode())})
        op, built = self.run_tool(session, "call_2", "build_candidate", {"revision_id": "r0001", "deliver_if_compatible": False})
        self.assertTrue(built.text["built"], built.text)
        self.assertEqual(built.text["modules"], created.text["modules"])
        op, r2 = self.run_tool(session, "call_3", "edit_program", {"base_revision_id": "r0001", "modules": demo.modules(demo.PROGRAM_B), "rationale": "b",
                                                                   "expected_changes": [], "build_now": True, "deliver_if_compatible": False})
        expected = {"frame": sha(demo.PROGRAM_B.encode()), "lenses": sha(LENSES_SRC.encode())}
        self.assertNotIn("modules", r2.text["created"], "a built reply carries the module hashes once, at the top level (round 6)")
        self.assertEqual(r2.text["modules"], expected)
        # the sha is what a patch needs: no read_program round trip
        mods = {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in T.MODULE_KEYS}
        mods["lenses"] = {"mode": "patch", "content": json.dumps([{"find": "lens = 1", "replace": "lens = 5"}]), "expected_base_sha256": expected["lenses"]}
        op, r3 = self.run_tool(session, "call_4", "edit_program", {"base_revision_id": "r0002", "modules": mods, "rationale": "patch by sha",
                                                                   "expected_changes": [], "build_now": False, "deliver_if_compatible": False})
        self.assertNotIn("error", r3.text, r3.text)
        self.assertEqual(r3.text["modules"]["lenses"], sha(LENSES_SRC.replace("lens = 1", "lens = 5").encode()))

    # ---- 3 / 5 / 6. the build reply carries the sheets and the photo-camera renders; singles are host-only; labels tell the truth
    def realish_build(self, *, contract_ok: bool, match: bool, with_heldout: bool, ar: bool = True, name: str = "job"):
        calls: list = []
        session = self.make_session(RealishFakeWorker(), name, ar=ar, images_per_request=14, intake="none")
        with mock.patch.object(T.mexport, "export_glb", fake_export(contract_ok=contract_ok)), \
                mock.patch("modeler.observe.observe_candidate", fake_observe(match=match, with_heldout=with_heldout, calls=calls)):
            op, result = self.build(session)
        return session, result, calls

    def test_build_reply_carries_sheets_then_match_renders_and_queues_nothing(self):
        session, result, calls = self.realish_build(contract_ok=True, match=True, with_heldout=True)
        self.assertTrue(result.text["built"], result.text)
        self.assertNotIn("deferred_images", result.text)
        views = [a["recipe"]["view"] for a in result.images]
        self.assertEqual(views, SHEET_KEYS + MATCH_IDS, "the sheets first, then the photo-camera renders")
        obs = session.store.observations(revision_id="r0001")
        self.assertEqual(sorted(o["artifact_id"] for o in obs), sorted(a["id"] for a in result.images), "everything carried is an observation, nothing else")
        required = {session.store.artifact(o["artifact_id"])["recipe"]["view"] for o in obs if o["required"]}
        self.assertEqual(required, set(SHEET_KEYS), "only the sheets are required for delivery")
        singles = [a for a in session.store.artifacts(revision_id="r0001", kind="render") if a["recipe"].get("view") in SINGLE_IDS]
        self.assertEqual(sorted(a["recipe"]["view"] for a in singles), sorted(SINGLE_IDS), "the clay / textured singles are still catalogued")
        self.assertTrue(all(a["role"] == HOST_ONLY for a in singles), "singles are host-only: never queued for the author")
        for a in result.images:
            self.assertEqual(a["role"], AUTHOR_VISIBLE)
        labels = {a["recipe"]["view"]: a["label"] for a in result.images}
        self.assertIn("(geometry only)", labels["clay"])
        self.assertIn("EEVEE preview", labels["textured"])
        self.assertIn("judge materials on the ar sheet", labels["textured"])
        self.assertNotIn("repair_hints", result.text, "a passing contract has nothing to repair")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["ar"], "the AR harness runs on an exported GLB")
        self.assertTrue(calls[0]["heldout"], "heldout follows the contract verdict when observe_candidate accepts it")

    def test_build_reply_without_match_renders_carries_exactly_the_sheets(self):
        session, result, calls = self.realish_build(contract_ok=True, match=False, with_heldout=False)
        self.assertEqual([a["recipe"]["view"] for a in result.images], SHEET_KEYS)
        self.assertEqual(len(session.store.observations(revision_id="r0001")), len(SHEET_KEYS))
        self.assertNotIn("heldout", calls[0], "observe_candidate without the parameter is called without it")

    def test_failed_contract_still_runs_the_harness_and_leads_with_repair_hints(self):
        session, result, calls = self.realish_build(contract_ok=False, match=True, with_heldout=True)
        self.assertTrue(result.text["built"])
        self.assertFalse(result.text["compatibility"]["compatible"])
        self.assertTrue(calls[0]["ar"], "a GLB was exported: the harness runs even when the contract failed")
        self.assertFalse(calls[0]["heldout"])
        hints = result.text["repair_hints"]
        self.assertIsInstance(hints, list)
        self.assertTrue(all(isinstance(h, str) for h in hints), hints)
        joined = NL.join(hints)
        self.assertIn("dropped 3 degenerate triangles", joined)
        self.assertIn("temple_R_arm", joined)
        self.assertIn("6 boundary", joined)
        self.assertIn("2 nonmanifold", joined)
        self.assertIn("watertight_parts", joined)
        self.assertNotIn("front_plate", joined, "a closed object is not a hint")
        item, ids = T.output_for(session.context(), "call_1", result)
        self.assertTrue(item["output"][0]["text"].startswith('{"repair_hints": ['), item["output"][0]["text"][:120])
        self.assertEqual(json.loads(item["output"][0]["text"])["repair_hints"], hints)
        self.assertEqual([a["recipe"]["view"] for a in result.images], SHEET_KEYS + MATCH_IDS)

    def test_no_glb_means_no_harness(self):
        calls: list = []
        session = self.make_session(RealishFakeWorker(), "noglb", ar=True, images_per_request=14, intake="none")
        with mock.patch.object(T.mexport, "export_glb", fake_export(contract_ok=False, write_glb=False)), \
                mock.patch("modeler.observe.observe_candidate", fake_observe(match=False, with_heldout=True, calls=calls)):
            op, result = self.build(session)
        self.assertFalse(calls[0]["ar"])
        self.assertIn("repair_hints", result.text)

    # ---- 4. repair hints, the shape
    def test_repair_hints_shape(self):
        export = {"error": None, "contract": {"ok": False, "failures": ["watertight_parts", "triangles"],
                                              "checks": {"watertight_parts": {"pass": False, "value": {"frame": False, "temple_L": True}, "limit": "closed"},
                                                         "triangles": {"pass": False, "value": 120000, "limit": 100000},
                                                         "width": {"pass": True, "value": 140, "limit": [100, 200]}}},
                  "notes": ["a: dropped 2 degenerate triangles before export (object was not closed)", "b: unassigned material slot -> acetate"]}
        inventory = [{"object": "a", "part": "frame", "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 1},
                     {"object": "b", "part": "temple_L", "boundary_edges": 0, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "c", "part": "lens_R"}]
        hints = T.repair_hints(export, inventory)
        self.assertTrue(hints and all(isinstance(h, str) and h for h in hints))
        joined = NL.join(hints)
        self.assertIn("watertight_parts", joined)
        self.assertIn("triangles", joined)
        self.assertNotIn("width", joined)
        self.assertIn("dropped 2 degenerate", joined)
        self.assertIn("a (frame): 4 boundary, 0 nonmanifold, 1 misoriented", joined)
        self.assertNotIn("b (temple_L)", joined)
        self.assertEqual(T.repair_hints({"contract": {"ok": True}, "notes": []}, inventory), [], "nothing failed: no hints")
        self.assertEqual(T.repair_hints({"error": "ValueError: boom", "contract": {"ok": False, "failures": ["export_exception"]}}, None)[0], "export failed: ValueError: boom")

    # ---- 8. the first message is the cached prefix; list_evidence is slim
    def test_list_evidence_is_slim_and_points_at_the_first_message(self):
        session = self.make_session(executor.FakeWorker())
        op, result = self.run_tool(session, "call_1", "list_evidence", {"reference": None})
        self.assertNotIn("helper_reference", result.text)
        self.assertNotIn("rules", result.text)
        self.assertNotIn("evidence", result.text)
        self.assertIn("photos", result.text)
        self.assertIn("state", result.text)
        self.assertIn("first message", result.text["note"])
        self.assertLess(len(json.dumps(result.text, default=str)), 4000)

    def test_first_message_carries_the_rules_and_the_helper_reference(self):
        session = self.make_session(executor.FakeWorker())
        first = session.store.items("author")[0]["item"]
        text = first["content"][0]["text"]
        head = json.loads(text.split(NL, 1)[1])
        # the first message states the shared rules through runner.agentic_rules when the runner has it (AT-13, 2026-09-28)
        self.assertEqual(head["rules"], expected_rules(session))
        self.assertEqual(head["helper_reference"], mauthor.helper_reference())
        self.assertIn("edit_program", head["how_to_start"])
        self.assertNotIn("call list_evidence", head["how_to_start"])
        op, note = self.run_tool(session, "call_1", "read_program", {"revision_id": None})
        self.assertIn("first message", note.text["note"])


# =========================================================================== round 3 (2026-09-28): what the adversarial verifiers found
PILOT_R0001 = Path(__file__).resolve().parents[1] / "data" / "modeler" / "agentic" / "test-pilot-001" / "revisions" / "r0001"
OBJECT_LINE = " boundary, "         # the inventory line's marker: '<object> (<part>): b boundary, nm nonmanifold, mo misoriented edges ...'


def object_lines(hints: list[str]) -> list[str]:
    return [h for h in hints if OBJECT_LINE in h and not h.startswith(("contract check", "export note", "export failed"))]


def contract_part(role: str, *, ok: bool, profile: str | None = None) -> dict:
    rep = {"role": role, "watertight": ok and profile in (None, "solid"), "boundary_edges": 0, "nonmanifold_edges": 0, "misoriented_edges": 0, "pass": ok}
    if profile is not None:
        rep["profile"] = profile
    return rep


class RepairHintsRound3(unittest.TestCase):
    """repair_hints listed every open inventory object, whatever the contract said about its part: replayed on test-pilot-001's
    r0001 it told the author to close six temple_L / temple_R objects although the contract passed both temples (only the
    frame failed), and a +Z lens front sheet the contract accepts would have been flagged the same way."""

    def test_replay_of_test_pilot_001_r0001_names_only_the_failed_frame(self):
        export_path, host_path = PILOT_R0001 / "export.json", PILOT_R0001 / "build" / "result.host.json"
        if not (export_path.is_file() and host_path.is_file()):
            self.skipTest("test-pilot-001 evidence is not on this machine")
        export = json.loads(export_path.read_text(encoding="utf-8"))
        inventory = json.loads(host_path.read_text(encoding="utf-8"))["inventory"]
        parts = export["contract"]["parts"]
        # the evidence the finding rests on: the contract failed the frame only, the temples passed, the lenses are accepted sheets
        self.assertEqual({k for k, r in parts.items() if not r["pass"]}, {"frame"})
        self.assertEqual({parts["lens_R"]["profile"], parts["lens_L"]["profile"]}, {"front_sheet"})
        self.assertTrue([r for r in inventory if r["part"].startswith("temple_") and r["nonmanifold_edges"]], "the replay must contain open temple objects")
        hints = T.repair_hints(export, inventory)      # no parts file: the inventory fallback (the exported counts: RepairHintsExportedTopology)
        self.assertLessEqual(len(hints), 24)
        joined = NL.join(hints)
        for part in ("temple_L", "temple_R", "lens_R", "lens_L"):
            self.assertNotIn(f"({part})", joined, f"the contract passed {part}: nothing of it is to be closed or welded")
        objs = object_lines(hints)
        self.assertEqual(sorted(h.split(" ", 1)[0] for h in objs),
                         sorted(r["object"] for r in inventory if r["part"] == "frame" and (r["boundary_edges"] or r["nonmanifold_edges"] or r["misoriented_edges"])))
        self.assertTrue(all("(frame)" in h for h in objs), objs)
        self.assertIn("crystal_rim_R (frame): 0 boundary, 10 nonmanifold, 20 misoriented", joined)
        self.assertTrue(hints[0].startswith("contract check watertight_parts failed"), hints[0])
        notes = [i for i, h in enumerate(hints) if h.startswith("export note")]
        self.assertTrue(notes, hints)
        self.assertLess(max(hints.index(h) for h in objs), min(notes), "the real defects come before the informational export notes")

    def test_an_accepted_lens_front_sheet_and_a_passing_temple_are_not_flagged(self):
        export = {"contract": {"ok": False, "failures": ["watertight_parts"],
                               "checks": {"watertight_parts": {"pass": False, "value": {"frame": False, "temple_R": True, "lens_R": "front_sheet", "lens_L": "open"},
                                                               "limit": "frame/temples closed; lens closed or +Z front sheet"}},
                               "parts": {"frame": contract_part("frame", ok=False), "temple_R": contract_part("temple", ok=True),
                                         "lens_R": contract_part("lens", ok=True, profile="front_sheet"), "lens_L": contract_part("lens", ok=False, profile="open")}}}
        inventory = [{"object": "rim", "part": "frame", "boundary_edges": 0, "nonmanifold_edges": 2, "misoriented_edges": 0},
                     {"object": "arm_R", "part": "temple_R", "boundary_edges": 0, "nonmanifold_edges": 4, "misoriented_edges": 8},
                     {"object": "sheet_R", "part": "lens_R", "boundary_edges": 192, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "sheet_L", "part": "lens_L", "boundary_edges": 190, "nonmanifold_edges": 0, "misoriented_edges": 3}]
        objs = object_lines(T.repair_hints(export, inventory))
        self.assertEqual([h.split(" ", 1)[0] for h in objs], ["rim", "sheet_L"], objs)
        self.assertTrue(bsa_contract.__name__)          # the per-part report read here is bsa.contract's own structure

    def test_the_check_value_stands_in_when_the_per_part_report_is_absent(self):
        export = {"contract": {"ok": False, "failures": ["watertight_parts"],
                               "checks": {"watertight_parts": {"pass": False, "value": {"frame": True, "temple_L": False, "lens_R": "front_sheet"}, "limit": "x"}}}}
        inventory = [{"object": "rim", "part": "frame", "boundary_edges": 3, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "arm_L", "part": "temple_L", "boundary_edges": 5, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "sheet_R", "part": "lens_R", "boundary_edges": 99, "nonmanifold_edges": 0, "misoriented_edges": 0}]
        self.assertEqual([h.split(" ", 1)[0] for h in object_lines(T.repair_hints(export, inventory))], ["arm_L"])

    def test_every_open_row_is_listed_when_no_per_part_data_exists(self):
        export = {"error": "ValueError: boom", "contract": {"ok": False, "failures": ["export_exception"]}}
        inventory = [{"object": "rim", "part": "frame", "boundary_edges": 3, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "arm_L", "part": "temple_L", "boundary_edges": 0, "nonmanifold_edges": 1, "misoriented_edges": 0},
                     {"object": "closed", "part": "temple_L", "boundary_edges": 0, "nonmanifold_edges": 0, "misoriented_edges": 0}]
        hints = T.repair_hints(export, inventory)
        self.assertEqual(hints[0], "export failed: ValueError: boom")
        self.assertEqual([h.split(" ", 1)[0] for h in object_lines(hints)], ["rim", "arm_L"])

    def test_untrusted_inventory_counts_never_raise(self):
        """The inventory is the worker's receipt: a count that is not an integer is skipped, never raised on."""
        export = {"contract": {"ok": False, "failures": ["export_exception"]}}          # no per-part data: every row is considered
        weird = ["x", [1], {"a": 1}, float("nan"), float("inf"), "1e999", object()]
        inventory = [{"object": f"bad{i}", "part": "frame", "boundary_edges": v, "nonmanifold_edges": 0, "misoriented_edges": 0} for i, v in enumerate(weird)]
        inventory += [{"object": "strings", "part": "frame", "boundary_edges": "7", "nonmanifold_edges": None, "misoriented_edges": "2"},
                      {"object": "negative", "part": "frame", "boundary_edges": -3, "nonmanifold_edges": 0, "misoriented_edges": 0},
                      "not a row", None, {"object": "no counts", "part": "frame"}]
        hints = T.repair_hints(export, inventory)
        self.assertEqual([h.split(" ", 1)[0] for h in object_lines(hints)], ["strings"], hints)
        self.assertIn("strings (frame): 7 boundary, 0 nonmanifold, 2 misoriented", NL.join(hints))
        # and the same rows under a per-part report
        export["contract"] = {"ok": False, "failures": ["watertight_parts"], "checks": {"watertight_parts": {"pass": False, "value": {"frame": False}, "limit": "x"}},
                              "parts": {"frame": {"role": "frame", "pass": "yes"}, "temple_L": "not a dict"}}
        self.assertEqual([h.split(" ", 1)[0] for h in object_lines(T.repair_hints(export, inventory))], ["strings"])

    def test_defects_come_before_informational_notes_within_the_cap(self):
        notes = [f"part_{i}: unassigned material slot -> frame_default" for i in range(30)]
        notes.insert(5, "declared bridge underside [0.0, 1.0, 0.0] disagrees with the geometry [0.0, 0.0, 0.0] (method); the derived point is used")
        notes.insert(20, "no lens part registered")
        export = {"contract": {"ok": False, "failures": ["watertight_parts"],
                               "checks": {"watertight_parts": {"pass": False, "value": {"frame": False}, "limit": "x"}},
                               "parts": {"frame": contract_part("frame", ok=False)}},
                  "notes": notes}
        inventory = [{"object": "rim", "part": "frame", "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0}]
        hints = T.repair_hints(export, inventory)
        self.assertEqual(len(hints), 24)
        self.assertTrue(hints[0].startswith("contract check watertight_parts failed"), hints)
        self.assertEqual(hints[1], "export defect: no lens part registered")
        self.assertTrue(hints[2].startswith("rim (frame): 4 boundary"), hints[2])
        self.assertTrue(all(h.startswith("export note: ") for h in hints[3:]), hints[3:])
        self.assertNotIn("export note: no lens part registered", hints)


class Round3Tools(ToolsCase):
    # ---- 2. the author's own render_views renders at 32 samples; the build keeps the observation default
    def test_render_views_asks_the_worker_for_32_samples(self):
        worker = executor.FakeWorker()
        session = self.make_session(worker)
        op1, built = self.build(session)
        self.assertTrue(built.text["built"], built.text)
        op2, result = self.run_tool(session, "call_2", "render_views", {"revision_id": "r0001", "views": [EXTRA_VIEW]})
        self.assertEqual([r["id"] for r in result.text["rendered"]], ["extra"], result.text)
        self.assertEqual(worker.runs[op2["id"]]["operation"]["samples"], T.AUTHOR_VIEW_SAMPLES)
        self.assertEqual(T.AUTHOR_VIEW_SAMPLES, 32)
        self.assertEqual(worker.runs[op1["id"]]["operation"]["samples"], executor.DEFAULT_SAMPLES, "the build keeps the harness default")

    # ---- 3. the build text names the images for what they are
    def test_build_text_counts_the_attached_and_the_deferred_images(self):
        session = self.make_session(executor.FakeWorker(demo.demo_fake_scenario()))
        self.build(session, "call_1", demo.PROGRAM_A)
        op, r2 = self.run_tool(session, "call_2", "edit_program", {"base_revision_id": "r0001", "modules": demo.modules(demo.PROGRAM_B), "rationale": "repair",
                                                                     "expected_changes": ["builds"], "build_now": True, "deliver_if_compatible": False})
        self.assertTrue(r2.text["built"], r2.text)
        self.assertNotIn("images_queued", r2.text)
        self.assertEqual(r2.text["images_attached"], 6)
        self.assertEqual(len(r2.images), 6)
        self.assertEqual(r2.text["deferred_images"], CANONICAL_RENDERS - 6)
        item, ids = T.output_for(session.context(), "call_2", r2)
        self.assertEqual(len(ids), json.loads(item["output"][0]["text"])["images_attached"])
        prompt = runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("images_queued", prompt)
        self.assertIn("images_attached", prompt)
        self.assertIn("deferred_images", prompt)

    # ---- 5. after compaction the rules and the helper reference can be fetched again
    def test_list_evidence_reference_returns_the_rules_and_the_helper_reference(self):
        session = self.make_session(executor.FakeWorker())
        op, full = self.run_tool(session, "call_1", "list_evidence", {"reference": True})
        self.assertEqual(full.text["rules"], expected_rules(session), "the copy fetched after a compaction is the first message's copy")
        first = json.loads(session.store.items("author")[0]["item"]["content"][0]["text"].split(chr(10), 1)[1])
        self.assertEqual(full.text["rules"], first["rules"])
        self.assertEqual(full.text["helper_reference"], mauthor.helper_reference())
        self.assertIn("photos", full.text)
        for value in (False, None):
            op, slim = self.run_tool(session, f"call_{value}", "list_evidence", {"reference": value})
            self.assertNotIn("rules", slim.text)
            self.assertNotIn("helper_reference", slim.text)
        self.assertIn("list_evidence", T.FIRST_MESSAGE_POINTER)
        self.assertIn("reference", T.FIRST_MESSAGE_POINTER)
        self.assertIn("reference: true", runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8"))

    def test_list_evidence_schema_stays_strict(self):
        params = T.TOOLS["list_evidence"]["parameters"]
        responses.validate_tools_schema(params)
        self.assertEqual(params["required"], ["reference"])
        for args in ({"reference": True}, {"reference": False}, {"reference": None}):
            got, err = responses.validate_call_arguments(self.call("list_evidence", args), T.TOOLS)
            self.assertIsNone(err, err)
            self.assertEqual(got, args)
        for args in ({"reference": "yes"}, {"reference": True, "more": 1}):
            got, err = responses.validate_call_arguments(self.call("list_evidence", args), T.TOOLS)
            self.assertIsNone(got)
            self.assertIn("strict schema of list_evidence", err)

    def test_the_runner_reads_only_the_legacy_empty_list_evidence_call_as_reference_null(self):
        """A response recorded before the argument existed (and the offline demo script) calls list_evidence with {}: the
        runner reads exactly that shape as reference null; everything else meets the strict validator unchanged."""
        self.assertEqual(runner.validate_author_call(self.call("list_evidence", {})), ({"reference": None}, None))
        self.assertEqual(runner.validate_author_call(self.call("list_evidence", {"reference": True})), ({"reference": True}, None))
        for name, args in (("list_evidence", {"reference": 1}), ("list_evidence", {"other": None}), ("read_program", {}), ("fetch_pending_images", {})):
            got, err = runner.validate_author_call(self.call(name, args))
            self.assertIsNone(got, (name, args))
            self.assertIn(f"strict schema of {name}", err)
        body = demo._resp("call_b", "list_evidence", {}, rs="rs_b")
        body["output"][-1]["arguments"] = "{"
        broken = responses.parse_response_body(responses.canonical(body), model="gpt-6-astra").function_calls[0]
        got, err = runner.validate_author_call(broken)
        self.assertIsNone(got)
        self.assertTrue(err.startswith("arguments rejected:"), err)

    @staticmethod
    def call(name: str, args: dict):
        return responses.parse_response_body(responses.canonical(demo._resp("call_s", name, args, rs="rs_s")), model="gpt-6-astra").function_calls[0]

    # ---- 6. the author prompt tells the truth about translucent temples, lens sheets and the one-piece front
    def test_author_prompt_round3_wording(self):
        prompt = runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8")
        flat = " ".join(prompt.split())
        self.assertNotIn("opaque temple", flat)
        self.assertIn("Continuous temple geometry", flat)
        self.assertIn("gl.material_translucent", flat)
        self.assertIn("hardware", flat)
        self.assertIn("single-sided", flat)
        self.assertIn("lens descriptor", flat)
        self.assertIn("+Z front sheet", flat)
        self.assertIn("gl.front_outline(", flat)
        self.assertIn("gl.plate_with_holes(", flat)
        self.assertNotIn("every part a closed 2-manifold", flat)
        tools_src = Path(T.__file__).read_text(encoding="utf-8")
        self.assertNotIn("opaque temple", " ".join(tools_src.split()))


class CriticBindingRound3(ErgonomicsLessons):
    """The critic verdict's bindings.images listed every sheet/render of the revision that was not sealed, the host-only
    clay / textured singles included, which evaluation.critic_blocks never shows the critic."""

    def test_the_binding_lists_exactly_the_images_the_critic_was_sent(self):
        critique = {"defects": [], "matches": ["front"], "uncertainty": "low", "suggested_repairs": [], "summary": "fine"}
        inputs = self.root / "critic.in"
        translated = config.translate_request(demo.demo_request(inputs), inputs)
        transport = responses.ScriptedTransport([{"endpoint": "responses", "expect": {"tools": ["report_critique"], "has_image": True},
                                                  "body": demo._resp("call_c", "report_critique", critique, rs="rs_c")}])
        session = runner.Session.create(self.root / "critic", translated=translated, policy=self.policy(critic="scripted", ar=True, images_per_request=14, intake="none"),
                                        fingerprints={"test": "tools"}, worker=RealishFakeWorker(), transport=transport, worker_config=None, log=NULL_LOG)
        self.sessions.append(session)
        calls: list = []
        with mock.patch.object(T.mexport, "export_glb", fake_export(contract_ok=True)), \
                mock.patch("modeler.observe.observe_candidate", fake_observe(match=True, with_heldout=True, calls=calls)):
            self.build(session)
        store = session.store
        host_only = [a["id"] for a in store.artifacts(revision_id="r0001") if a["kind"] == "render" and a["role"] == HOST_ONLY]
        self.assertEqual(len(host_only), len(SINGLE_IDS), "the fixture must hold host-only singles for the binding to over-claim")
        op, result = self.run_tool(session, "call_2", "request_critic", {"revision_id": "r0001", "question": "what is wrong?"})
        self.assertNotIn("error", result.text, result.text)
        payload = [x["payload"] for x in transport.sent if x["endpoint"] == "responses"][-1]
        sent = []
        for item in payload["input"]:
            content = item.get("content") if isinstance(item.get("content"), list) else []
            for prev, block in zip(content, content[1:]):
                if block.get("type") == "input_image":
                    sent.append(json.loads(prev["text"])["image_id"])
        self.assertEqual(len(sent), sum(1 for i in payload["input"] for b in (i.get("content") if isinstance(i.get("content"), list) else [])
                                        if b.get("type") == "input_image"))
        binding = store.verdicts(kind="critic")[0]["bindings"]["images"]
        self.assertEqual(binding, sent, "the binding is exactly the images the critic was sent, in order")
        self.assertFalse(set(binding) & set(host_only), "a host-only single the critic never saw is not bound")
        visible = [a["id"] for a in store.artifacts(revision_id="r0001") if a["kind"] in ("sheet", "render") and a["role"] == AUTHOR_VISIBLE]
        self.assertTrue(set(visible) <= set(binding))
        blocks = evaluation.critic_blocks(store, session.artifacts, session.evidence(), store.revision("r0001"), "q")
        self.assertEqual(runner.sent_image_ids(blocks), sent)


# =========================================================================== round 4 (2026-09-28): the exported topology, not the worker's counts
TET_V = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]]
TET_F = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]            # one consistently oriented closed tetrahedron
PILOT_FILES = {"export.json": "export.json", "result.host.json": "build/result.host.json", "parts.npz": "build/parts.npz", "materials.json": "build/materials.json"}


def write_parts(folder: Path, objects: dict) -> tuple[Path, Path]:
    """A minimal ingested parts file in the harness layout (modeler.export.load_parts): {name: (part, V, F)}."""
    arrays, meta = {}, {"objects": {}, "materials": {"m": {"base_color_linear": [0.5, 0.5, 0.5], "roughness": 0.5}}, "declarations": {}}
    for name, (part, V, F) in objects.items():
        F = np.asarray(F, np.int64)
        arrays[f"{name}__V"], arrays[f"{name}__F"], arrays[f"{name}__M"] = np.asarray(V, float), F, np.zeros(len(F), np.int64)
        meta["objects"][name] = {"object": name, "part": part, "materials": ["m"]}
    np.savez(folder / "parts.npz", **arrays)
    (folder / "materials.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder / "parts.npz", folder / "materials.json"


def two_tets_sharing_an_edge() -> tuple[list, list]:
    """Closed tetrahedra welded along one edge: 0 boundary, 1 nonmanifold, 2 misoriented edges (a pinched solid)."""
    V = TET_V + [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, -10.0, 0.0], [0.0, 0.0, -10.0]]
    return V, TET_F + [[a + 4, b + 4, c + 4] for a, b, c in TET_F]


class RepairHintsExportedTopology(unittest.TestCase):
    """After round 3, repair_hints still read the worker's pre-export inventory counts: replayed on test-pilot-001's r0001, four of
    its five object lines (gold_T_crossbar_L/R, gold_T_front_bar_L/R) told the author to close objects that export watertight once
    the exporter drops their degenerate triangles, and crystal_rim_R was reported 10/20 where the contract measured 4/8."""

    def setUp(self):
        self.root = fresh_dir(None, "forged-render-ids", "topo-", env="LAG_TOOLS_TMP")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def minimal(self) -> tuple[dict, list, Path, Path]:
        pinched = two_tets_sharing_an_edge()
        open_tet = TET_F[:3]
        npz, mats = write_parts(self.root, {
            "rim": ("frame", *pinched),                                  # still pinched after the export: the real defect
            "bar": ("frame", TET_V, TET_F + [[0, 0, 1]]),                # open only through a degenerate triangle the exporter drops
            "rim2": ("frame", TET_V, open_tet + [[1, 1, 2]]),            # open after the drop as well
            "arm": ("temple_R", TET_V, open_tet),                        # open, but the contract passed its part
            "plate": ("temple_L", TET_V, TET_F + [[2, 3, 3]]),           # passed part, cleaned by the exporter
            "sheet": ("lens_R", TET_V, [[0, 1, 2]])})                    # an accepted +Z front sheet
        export = {"contract": {"ok": False, "failures": ["watertight_parts"],
                               "checks": {"watertight_parts": {"pass": False, "value": {"frame": False, "temple_R": True, "temple_L": True, "lens_R": "front_sheet"},
                                                               "limit": "frame/temples closed; lens closed or +Z front sheet"}},
                               "parts": {"frame": contract_part("frame", ok=False), "temple_R": contract_part("temple", ok=True),
                                         "temple_L": contract_part("temple", ok=True), "lens_R": contract_part("lens", ok=True, profile="front_sheet")}},
                  "receipt": {"origin": {"method": "supplied", "origin_mm": [0.0, 1.25, -0.4]}},
                  "notes": [f"{n}: dropped 1 degenerate triangles before export (object was not closed)" for n in ("bar", "rim2", "plate")]
                  + ["rim: unassigned material slot -> frame_default"]}
        inventory = [{"object": "rim", "part": "frame", "boundary_edges": 0, "nonmanifold_edges": 10, "misoriented_edges": 20},
                     {"object": "bar", "part": "frame", "boundary_edges": 0, "nonmanifold_edges": 16, "misoriented_edges": 32},
                     {"object": "rim2", "part": "frame", "boundary_edges": 9, "nonmanifold_edges": 0, "misoriented_edges": 0},
                     {"object": "arm", "part": "temple_R", "boundary_edges": 3, "nonmanifold_edges": 0, "misoriented_edges": 0}]
        return export, inventory, npz, mats

    def test_the_fixture_reproduces_the_pilot_situation(self):
        V, F = two_tets_sharing_an_edge()
        topo = bsa_contract.topology(np.asarray(V) / 1000.0, np.asarray(F))
        self.assertEqual((topo["boundary_edges"], topo["nonmanifold_edges"], topo["misoriented_edges"]), (0, 1, 2))
        from modeler import export as mexport
        self.assertFalse(mexport.is_closed(np.asarray(TET_V), np.asarray(TET_F + [[0, 0, 1]])), "the degenerate triangle opens it before the drop")

    def test_object_lines_come_from_the_exported_topology(self):
        export, inventory, npz, mats = self.minimal()
        hints = T.repair_hints(export, inventory, parts_npz=npz, materials_json=mats)
        objs = object_lines(hints)
        self.assertEqual([h.split(" ", 1)[0] for h in objs], ["rim", "rim2"], hints)
        self.assertTrue(objs[0].startswith("rim (frame): 0 boundary, 1 nonmanifold, 2 misoriented edges"), objs[0])
        self.assertTrue(objs[1].startswith("rim2 (frame): 3 boundary, 0 nonmanifold, 0 misoriented edges"), objs[1])
        joined = NL.join(hints)
        self.assertNotIn("bar", joined, "bar exports watertight: neither a repair line nor a dropped-triangles note")
        self.assertNotIn("plate", joined, "plate's part passed and it exports watertight: no note")
        self.assertIn("rim2: dropped 1 degenerate", joined, "the drop that left the object open stays as context")
        self.assertIn("export note: rim: unassigned material slot -> frame_default", hints)
        self.assertTrue(hints[0].startswith("contract check watertight_parts failed"), hints[0])

    def test_the_inventory_is_not_needed_for_the_object_lines(self):
        export, inventory, npz, mats = self.minimal()
        for inv in (None, [], 5, object(), "rows", {"rim": 1}):
            hints = T.repair_hints(export, inv, parts_npz=npz, materials_json=mats)
            self.assertEqual([h.split(" ", 1)[0] for h in object_lines(hints)], ["rim", "rim2"], (inv, hints))

    def test_a_missing_or_unreadable_parts_file_falls_back_to_the_inventory_and_never_raises(self):
        export, inventory, npz, mats = self.minimal()
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "parts.npz").write_bytes(b"not a real npz")
        (bad / "materials.json").write_text("{}", encoding="utf-8")
        cases = [(None, None), (self.root / "absent.npz", mats), (npz, self.root / "absent.json"), (bad / "parts.npz", bad / "materials.json"),
                 (npz, bad / "materials.json"), ("", ""), (5, object())]
        for p, m in cases:
            hints = T.repair_hints(export, inventory, parts_npz=p, materials_json=m)
            self.assertEqual([h.split(" ", 1)[0] for h in object_lines(hints)], ["rim", "bar", "rim2"], (p, m, hints))
            self.assertIn("rim (frame): 0 boundary, 10 nonmanifold, 20 misoriented", NL.join(hints))
            for inv in (5, object(), None):
                T.repair_hints(export, inv, parts_npz=p, materials_json=m)          # a non-iterable inventory never raises
        self.assertEqual([h.split(" ", 1)[0] for h in object_lines(T.repair_hints(export, inventory))], ["rim", "bar", "rim2"])

    def test_a_passing_contract_still_has_nothing_to_repair(self):
        export, inventory, npz, mats = self.minimal()
        export["contract"] = {"ok": True}
        self.assertEqual(T.repair_hints(export, inventory, parts_npz=npz, materials_json=mats), [])

    def test_replay_of_test_pilot_001_r0001_with_its_parts_file(self):
        if not all((PILOT_R0001 / src).is_file() for src in PILOT_FILES.values()):
            self.skipTest("test-pilot-001 evidence is not on this machine")
        for dst, src in PILOT_FILES.items():            # the evidence folder is read-only: the replay works on copies
            shutil.copyfile(PILOT_R0001 / src, self.root / dst)
        export = json.loads((self.root / "export.json").read_text(encoding="utf-8"))
        inventory = json.loads((self.root / "result.host.json").read_text(encoding="utf-8"))["inventory"]
        hints = T.repair_hints(export, inventory, parts_npz=self.root / "parts.npz", materials_json=self.root / "materials.json")
        objs = object_lines(hints)
        self.assertEqual(len(objs), 1, hints)
        self.assertTrue(objs[0].startswith("crystal_rim_R (frame): 0 boundary, 4 nonmanifold, 8 misoriented edges"), objs[0])
        frame = export["contract"]["parts"]["frame"]
        self.assertEqual((frame["boundary_edges"], frame["nonmanifold_edges"], frame["misoriented_edges"]), (0, 4, 8), "exactly the contract's frame report")
        joined = NL.join(hints)
        for name in ("gold_T_crossbar", "gold_T_front_bar", "inner_gold_plate", "gold_T_arm", "gold_T_side_cross"):
            self.assertNotIn(name, joined, f"{name} exports watertight: nothing to close, weld or note")
        self.assertTrue(hints[0].startswith("contract check watertight_parts failed"), hints[0])
        self.assertIn("export note: crystal_rim_R: unassigned material slot -> frame_default", hints)


class SeeThroughSheetLabel(ToolsCase):
    """The see_through sheet is the AR harness's (modeler/see_through.py via modeler/observe.py): the front on two solid fixtures
    and, for translucent temples, 'see-through temples <fixture>' tiles; it was labelled a two-fixture front and tagged blender-eevee."""
    make_session = ErgonomicsLessons.make_session         # borrowed, not inherited: ErgonomicsLessons' own tests are not run twice
    realish_build = ErgonomicsLessons.realish_build

    def test_the_label_names_the_temple_tiles_and_the_ar_renderer(self):
        label = T.sheet_label("see_through")
        self.assertIn("see-through temples", label)
        self.assertIn("AR renderer", label)
        self.assertNotIn("EEVEE", label)

    def test_the_sheet_recipe_names_the_ar_harness(self):
        session, result, calls = self.realish_build(contract_ok=True, match=False, with_heldout=True)
        renderers = {a["recipe"]["view"]: a["recipe"]["renderer"] for a in result.images}
        self.assertEqual(renderers["see_through"], "actual-ar")
        self.assertEqual(renderers["ar"], "actual-ar")
        self.assertEqual({renderers[k] for k in ("clay", "textured", "photo_match")}, {"blender-eevee"})

    def test_the_build_hands_the_ingested_parts_file_to_repair_hints(self):
        with mock.patch.object(T, "repair_hints", wraps=T.repair_hints) as spy:
            session, result, calls = self.realish_build(contract_ok=False, match=False, with_heldout=True)
        kw = spy.call_args.kwargs
        self.assertEqual((Path(kw["parts_npz"]).name, Path(kw["materials_json"]).name), ("parts.npz", "materials.json"))
        self.assertEqual(Path(kw["parts_npz"]).parent, session.store.job_dir / "revisions" / "r0001" / "build")
        self.assertIn("temple_R_arm (temple_R): 6 boundary", NL.join(result.text["repair_hints"]), "an unreadable parts file falls back to the inventory")


# =========================================================================== round 5 (2026-09-28): the exporter's audit reaches the author
AUDIT = {"flags": ["faceted_sweep", "planar_mirror_lens"], "parts": {"temple_R": {"hard_edge_mm": 693.2}, "lens_R": {"normal_span_deg": 3.85}},
         "notes": ["temple_R: 693 mm of hard edges below 60 deg; build the sweep with gl.tube_along_path rounded sections",
                   "lens_R: a flat mirror-coated lens (3.9 x 0.0 deg normal span); give it a base curve"]}


def export_with_receipt(audit):
    """fake_export whose receipt carries ``audit`` (bsa.export write_glb: receipt['audit'] = {flags, parts, notes}); None: no audit block."""
    inner = fake_export(contract_ok=True)

    def export_glb(npz_path, materials_json, out_path, *, extras=None):
        out = inner(npz_path, materials_json, out_path, extras=extras)
        out["receipt"] = {"origin": {"origin_mm": [0.0, 0.0, 0.0]}}
        if audit is not None:
            out["receipt"]["audit"] = audit
        return out
    return export_glb


class ExportAuditInTheBuildReply(ToolsCase):
    """The owner saw faceted temples and a flat mirror slab on test-pilot-002's r0006 that no number the author received
    named. The exporter's receipt gains an audit block; the build reply the author sees must carry its flags and notes."""
    make_session = ErgonomicsLessons.make_session

    def build_with(self, audit, name="job"):
        calls: list = []
        session = self.make_session(RealishFakeWorker(), name, ar=True, images_per_request=14, intake="none")
        with mock.patch.object(T.mexport, "export_glb", export_with_receipt(audit)), \
                mock.patch("modeler.observe.observe_candidate", fake_observe(match=False, with_heldout=True, calls=calls)):
            op, result = self.build(session)
        return session, result

    def test_the_build_reply_carries_the_audit_flags_and_notes(self):
        session, result = self.build_with(AUDIT)
        self.assertTrue(result.text["built"], result.text)
        self.assertEqual(result.text["export"]["audit"], {"flags": AUDIT["flags"], "notes": AUDIT["notes"]})
        item, ids = T.output_for(session.context(), "call_1", result)
        sent = json.loads(item["output"][0]["text"])
        self.assertEqual(sent["export"]["audit"]["flags"], AUDIT["flags"], "the JSON the author receives carries the flags")
        self.assertEqual(sent["export"]["audit"]["notes"], AUDIT["notes"])
        op, scene = self.run_tool(session, "call_2", "inspect_scene", {"revision_id": "r0001"})
        self.assertEqual(scene.text["export"]["audit"], {"flags": AUDIT["flags"], "notes": AUDIT["notes"]})

    def test_an_empty_audit_still_says_it_ran(self):
        session, result = self.build_with({"flags": [], "parts": {}, "notes": []})
        self.assertEqual(result.text["export"]["audit"], {"flags": [], "notes": []})

    def test_no_audit_block_means_no_audit_key(self):
        session, result = self.build_with(None)
        self.assertTrue(result.text["built"], result.text)
        self.assertNotIn("audit", result.text["export"])

    def test_the_audit_is_bounded_and_only_strings_pass(self):
        noisy = {"flags": ["ok_flag", 3, None, "x" * 500] + [f"f{i}" for i in range(60)], "notes": ["n" * 2000, {"a": 1}] + ["note"] * 60}
        audit = T.export_audit({"receipt": {"audit": noisy}})
        self.assertLessEqual(len(audit["flags"]), T.MAX_AUDIT_ITEMS)
        self.assertLessEqual(len(audit["notes"]), T.MAX_AUDIT_ITEMS)
        self.assertTrue(all(isinstance(f, str) for f in audit["flags"] + audit["notes"]))
        self.assertEqual(audit["flags"][0], "ok_flag")
        self.assertLessEqual(max(len(n) for n in audit["notes"]), 400)
        self.assertIsNone(T.export_audit({"receipt": {}}))
        self.assertIsNone(T.export_audit({"receipt": None}))
        self.assertIsNone(T.export_audit({}))

    def test_the_author_prompt_points_at_the_new_rules_without_repeating_them(self):
        flat = " ".join(runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8").split())
        for needle in ("audit", "base curve", "tube_along_path", "lens_transmission_recommended", "embedded hardware"):
            self.assertIn(needle, flat)
        self.assertIn("the rules", flat)


# =========================================================================== round 6 (2026-09-28): the tool replies of test-pilot-002 (run 2)
PILOT2 = Path(__file__).resolve().parents[1] / "data" / "modeler" / "agentic" / "test-pilot-002" / "revisions"
CLIP_ORIGIN = [0.0, 0.6, -2.7]          # test-pilot-002 r0006's export origin (the bridge underside)
TIP_ROWS = [{"object": "Crystal temple L", "part": "temple_L", "component": "acetate_L", "triangles": 4480, "boundary_edges": 0, "nonmanifold_edges": 0,
             "misoriented_edges": 0, "closed": True, "bbox_mm": [[-72.04, -31.57, -165.07], [-57.64, 6.53, -5.25]]},
            {"object": "Tip inscription L", "part": "temple_L", "component": "tip_print_L", "triangles": 1480, "boundary_edges": 0, "nonmanifold_edges": 0,
             "misoriented_edges": 373, "closed": True, "bbox_mm": [[-62.59, -26.38, -159.07], [-61.89, -23.19, -155.47]]},
            {"object": "front_plate", "part": "frame", "component": "front", "triangles": 12, "boundary_edges": 0, "nonmanifold_edges": 0,
             "misoriented_edges": 0, "closed": True, "bbox_mm": [[-68.0, -31.0, -8.0], [68.0, 12.0, 0.0]]}]


def camera(yaw=0.0, pitch=15.7, roll=0.5, perspective=0.14, scale=1211.0):
    return {"yaw": yaw, "pitch": pitch, "roll": roll, "perspective": perspective, "scale": scale, "center_x": 1000.0, "center_y": 580.0}


def view_fit(view, cam, contour, *, ppm=7.75, **extra):
    return dict({"view": view, "camera": cam, "px_per_mm": ppm, "contour_mean_mm": contour, "contour_p95_mm": contour * 3, "iou": 0.78}, **extra)


AR_SUMMARY = {"ar_runtime_compatible": True, "ar_report_valid": True, "ar_optical_meshes": 2, "ar_continuity_failure": None}


def fake_observe_seq(observations: list, calls: list, *, sleep_s: float = 0.0, probe=None):
    """``observe_candidate`` returning ``observations[k]`` ({summary, views}) on its k-th call, writing the five sheets, the three
    match_* singles and an AR harness folder (manifest, per-view renders, archeck.json) exactly where observe.py puts them."""
    def observe_candidate(cand_dir, build, evidence, evidence_dir, *, held_out_ids, previous_cameras=None, ar=True, glb_path=None,
                          time_limit_s=300, render_harness=None, heldout=False, heartbeat=None):
        calls.append({"ar": ar, "glb_path": glb_path, "heartbeat": heartbeat, "previous_cameras": previous_cameras})
        if probe is not None:
            probe("start")
        if sleep_s:
            time.sleep(sleep_s)
        if probe is not None:
            probe("end")
        spec = observations[min(len(calls), len(observations)) - 1]
        obs_dir = Path(cand_dir) / "observe"
        sheets = {k: str(obs_dir / f"sheet_{k}.png") for k in SHEET_KEYS + list(spec.get("extra_sheets", []))}
        for p in sheets.values():
            plant_png(Path(p), (20, 200, 20))
        renders = {}
        for k in SINGLE_IDS + MATCH_IDS:
            renders[k] = str(obs_dir / "renders" / f"{k}.png")
            plant_png(Path(renders[k]), (30, 30, 200))
        if glb_path is not None and Path(glb_path).is_file():
            write_ar_folder(obs_dir / "ar", Path(glb_path), AR_VIEWS, timing=spec.get("ar_timing"), report_sha=spec.get("report_sha"))
        return {"seconds": 1.0, "triangles": 52, "bbox_mm": spec.get("bbox_mm", [[-70, -25, -160], [70, 25, 3]]), "views": spec.get("views", {}),
                "sheets": sheets, "renders": renders, "summary": dict(AR_SUMMARY, **spec.get("summary", {}))}
    return observe_candidate


def write_ar_folder(out: Path, glb: Path, views, *, colour=(200, 120, 40), timing=None, report_sha=None) -> dict:
    """What ``bsa.archeck.run`` leaves in its folder (manifest.json, candidate__actual-ar__<view>.png, archeck.json) and returns;
    with ``timing`` (one harness timing dict per view, or one for all) also the harness report.json whose renders carry it."""
    out.mkdir(parents=True, exist_ok=True)
    glb_sha = sha(glb.read_bytes())
    if timing is not None:
        per_view = timing if isinstance(timing, list) else [timing] * len(views)
        report = {"cases": [{"id": "candidate", "model_sha256": report_sha or glb_sha,
                             "renders": [{"view": v["id"], "timing": dict(t)} for v, t in zip(views, per_view)]}]}
        (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    files = []
    for v in views:
        p = out / f"candidate__actual-ar__{v['id']}.png"
        files.append({"view": v["id"], "filename": p.name, "path": str(p), "sha256": plant_png(p, colour), "environment": None})
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"id": "candidate", "model_sha256": glb_sha}], "ar_views": [dict(v) for v in views]}), encoding="utf-8")
    result = {"returncode": 0, "harness_status": "rendered", "harness_errors": [], "source_snapshot_stable": True, "manifest_path": str(manifest),
              "models": {"candidate": {"status": "runtime_compatible", "runtime_compatible": True, "error": None, "optical_meshes_detected": 2,
                                       "model_sha256": glb_sha, "renders": [f["path"] for f in files], "render_files": files}}}
    result["validation"] = bsa_archeck.validate_ar_result(result, expected_models={"candidate": glb_sha}, expected_views=[v["id"] for v in views])
    (out / "archeck.json").write_text(json.dumps(result), encoding="utf-8")
    return result


class TipWorker(RealishFakeWorker):
    """The realish worker whose inventory carries test-pilot-002's crystal temple and 0.7 mm tip inscription (behind the clip)."""

    def ingest(self, identity, staging):
        ing = super().ingest(identity, staging)
        p = Path(ing.staging) / "result.json"
        if ing.result and ing.result.get("inventory"):
            ing.result["inventory"] = [dict(r) for r in TIP_ROWS]
            atomic_write(p, json.dumps(ing.result, indent=1).encode())
        return ing


def export_with_origin(*, contract_ok=True, declared=None):
    inner = fake_export(contract_ok=contract_ok)

    def export_glb(npz_path, materials_json, out_path, *, extras=None):
        out = inner(npz_path, materials_json, out_path, extras=extras)
        out["receipt"] = {"origin": {"method": "supplied", "origin_mm": list(CLIP_ORIGIN)}}
        if declared is not None:
            out["declarations"] = {"temple_clip_z_mm": declared}
        out["notes"] = ["font 'sans' not found; Blender's default font is used"] * 3
        return out
    return export_glb


class Round6Replies(ToolsCase):
    make_session = ErgonomicsLessons.make_session

    def session_with(self, observations, *, worker=None, name="job", declared=None, sleep_s=0.0, probe=None, contract_ok=True):
        calls: list = []
        session = self.make_session(worker or TipWorker(), name, ar=True, images_per_request=14, intake="none")
        patches = [mock.patch.object(T.mexport, "export_glb", export_with_origin(contract_ok=contract_ok, declared=declared)),
                   mock.patch("modeler.observe.observe_candidate", fake_observe_seq(observations, calls, sleep_s=sleep_s, probe=probe))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return session, calls

    def second_build(self, session, call_id="call_2", base="r0001"):
        return self.run_tool(session, call_id, "edit_program", {"base_revision_id": base, "modules": demo.modules(demo.PROGRAM_B), "rationale": "b",
                                                                  "expected_changes": [], "build_now": True, "deliver_if_compatible": False})

    # ---- AT-06: host_state never counts the images its own reply attaches
    def test_host_state_does_not_count_the_images_this_reply_attaches(self):
        session, calls = self.session_with([{}])
        op, built = self.build(session)
        self.assertEqual(built.text["images_attached"], len(built.images))
        ctx = session.context()
        item, ids = T.output_for(ctx, "call_1", built, state_note=T.state_summary(ctx))
        sent = json.loads(item["output"][0]["text"])
        self.assertEqual(len(session.store.observations(state="pending")), len(ids), "the fixture: every attached image is still pending in the store")
        self.assertEqual(sent["host_state"]["pending_images"], 0, "the attached images are in this reply, not pending")

    def test_host_state_still_counts_the_deferred_images(self):
        session = ToolsCase.make_session(self, executor.FakeWorker(demo.demo_fake_scenario()))
        self.build(session, "call_1", demo.PROGRAM_A)
        op, r2 = self.second_build(session)
        ctx = session.context()
        item, ids = T.output_for(ctx, "call_2", r2, state_note=T.state_summary(ctx))
        sent = json.loads(item["output"][0]["text"])
        self.assertEqual(sent["host_state"]["pending_images"], r2.text["deferred_images"])
        self.assertGreater(sent["host_state"]["pending_images"], 0)

    # ---- F5: keep what the author acts on
    def test_build_reply_drops_inventory_rows_hashes_and_repeats(self):
        session, calls = self.session_with([{}, {}])
        op, r1 = self.build(session)
        op, r2 = self.second_build(session)
        for r in (r1, r2):
            text = r.text
            self.assertIsInstance(text["inventory"], dict, "the inventory is a digest, not the row list")
            self.assertEqual(text["inventory"]["objects"], len(TIP_ROWS))
            self.assertIn("inspect_scene", text["inventory"]["all_rows"])
            self.assertNotIn("glb_sha256", text["export"])
            self.assertNotIn("glb_sha256", text["compatibility"])
            self.assertEqual(set(text["worker"]), {"kind"}, "container name and image digest are host facts")
            self.assertEqual(text["export"]["notes"], ["font 'sans' not found; Blender's default font is used (x3)"])
        self.assertTrue(session.store.revision("r0002")["compatibility"]["glb_sha256"], "the stored compatibility keeps the hash")
        # the second build names only what changed since its parent: nothing (the fake inventory is identical)
        self.assertEqual(r2.text["inventory"]["changed_since_parent"], [])
        self.assertEqual(r2.text["inventory"]["unchanged_since_parent"], len(TIP_ROWS))
        self.assertNotIn("changed_since_parent", r1.text["inventory"], "a first build has no parent to compare with")
        self.assertEqual(r1.text["inventory"]["objects_by_part"], {"frame": 1, "temple_L": 2})
        # the module hashes travel once, at the top level
        self.assertEqual(set(r2.text["created"]), {"revision", "parent", "changed_modules"})
        self.assertEqual(r2.text["modules"], T.module_shas(session.store.revision("r0002")))
        # inspect_scene still returns every row (patches target objects by name)
        op, scene = self.run_tool(session, "call_3", "inspect_scene", {"revision_id": "r0002"})
        self.assertEqual([r["object"] for r in scene.text["inventory"]], [r["object"] for r in TIP_ROWS])

    def test_inventory_digest_lists_changed_and_removed_rows(self):
        parent = [dict(r) for r in TIP_ROWS]
        rows = [dict(TIP_ROWS[0], triangles=4600), dict(TIP_ROWS[2]), {"object": "Hinge L", "part": "temple_L", "triangles": 80}]
        d = T.inventory_digest(rows, parent, revision="r0002")
        self.assertEqual(d["objects"], 3)
        self.assertEqual([ln.split(" [", 1)[0] for ln in d["changed_since_parent"]], ["Crystal temple L", "Hinge L"])
        self.assertIn("4600 tri", d["changed_since_parent"][0])
        self.assertEqual(d["removed_since_parent"], ["Tip inscription L"])
        self.assertEqual(d["unchanged_since_parent"], 1)
        self.assertIsNone(T.inventory_digest(None, None, revision="r0001"))
        many = [{"object": f"o{i}", "part": "frame", "triangles": i} for i in range(200)]
        d = T.inventory_digest(many, [], revision="r0003")
        self.assertLessEqual(len(d["changed_since_parent"]), T.MAX_INVENTORY_LINES + 1)
        self.assertIn("inspect_scene", d["changed_since_parent"][-1])

    def test_collapse_notes_counts_repeats_in_order(self):
        self.assertEqual(T.collapse_notes(["a", "b", "a", "a", 3, None, ""]), ["a (x3)", "b"])
        self.assertEqual(T.collapse_notes(None), [])

    def test_host_state_revisions_are_one_line_each_without_rationales(self):
        session, calls = self.session_with([{}, {}])
        self.build(session)
        op, r2 = self.second_build(session)
        ctx = session.context()
        item, ids = T.output_for(ctx, "call_2", r2, state_note=T.state_summary(ctx))
        revs = json.loads(item["output"][0]["text"])["host_state"]["revisions"]
        self.assertEqual(revs, ["r0001: compatible", "r0002 (from r0001): compatible"])
        self.assertIn("rationale", json.dumps(T.state_summary(ctx)["revisions"]), "the checkpoint's own summary is unchanged")

    def test_image_captions_carry_no_hash_or_recipe(self):
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        item, ids = T.output_for(session.context(), "call_1", r1)
        captions = [json.loads(b["text"]) for b in item["output"][1:] if b.get("type") == "input_text" and b["text"].startswith('{"image_id"')]
        self.assertEqual(len(captions), len(ids))
        for c in captions:
            self.assertEqual(set(c) - {"size"}, {"image_id", "label", "revision"}, c)
        self.assertEqual(runner.sent_image_ids(item["output"]), ids, "the runner still binds every image to its caption")

    def test_an_error_reply_carries_the_state_once(self):
        session, calls = self.session_with([{}])
        ctx = session.context()
        op = session.store.insert_operation(call_id="call_x", request_id=None, tool_name="read_program", schema_version=T.TOOLS_VERSION,
                                            args={"revision_id": "r0009"}, source_revision_id=None, fence=session.fence)
        result = T.execute(ctx, op)
        self.assertIn("state", result.text)
        item, ids = T.output_for(ctx, "call_x", result, state_note=T.state_summary(ctx))
        sent = json.loads(item["output"][0]["text"])
        self.assertIn("host_state", sent)
        self.assertNotIn("state", sent, "the host_state already carries it")

    # ---- F8: the match_* singles are attached on the first build only
    def test_match_singles_are_attached_on_the_first_build_only(self):
        session, calls = self.session_with([{}, {}])
        op, r1 = self.build(session)
        self.assertEqual([a["recipe"]["view"] for a in r1.images], SHEET_KEYS + MATCH_IDS)
        op, r2 = self.second_build(session)
        self.assertEqual([a["recipe"]["view"] for a in r2.images], SHEET_KEYS, "the photo_match sheet already shows the fitted-camera renders")
        listed = r2.text["match_renders"]["images"]
        self.assertEqual(sorted(listed), sorted(MATCH_IDS))
        self.assertIn("crop_image", r2.text["match_renders"]["note"])
        queued = {o["artifact_id"] for o in session.store.observations(revision_id="r0002")}
        self.assertFalse(queued & {v["image_id"] for v in listed.values()}, "not queued: fetch_pending_images never delivers them unasked")
        # on request, crop_image at full size returns one
        mid = listed["match_front"]["image_id"]
        w, h = listed["match_front"]["size"]
        op, crop = self.run_tool(session, "call_3", "crop_image", {"artifact_id": mid, "x0": 0, "y0": 0, "x1": w, "y1": h})
        self.assertNotIn("error", crop.text, crop.text)
        self.assertEqual(len(crop.images), 1)

    # ---- AT-05: deltas against the parent, camera moves told apart from shape changes
    def test_build_reply_carries_deltas_and_camera_moves_against_the_parent(self):
        first = {"summary": {"lens_outline_mean_mm": 0.846, "front_contour_mean_mm": 1.059, "back_contour_mean_mm": 1.596, "front_iou": 0.80},
                 "views": {"front": view_fit("front", camera(pitch=15.7), 1.059), "back": view_fit("back", camera(yaw=180.0, pitch=0.5), 1.596, ppm=6.4)},
                 "bbox_mm": [[-72, -31, -156.2], [72, 12, 0]]}
        second = {"summary": {"lens_outline_mean_mm": 1.918, "front_contour_mean_mm": 1.233, "back_contour_mean_mm": 1.60, "front_iou": 0.79},
                  "views": {"front": view_fit("front", camera(pitch=17.0, scale=1281.0), 1.233),
                            "back": view_fit("back", camera(yaw=180.0, pitch=0.5), 1.60, ppm=6.4, reliable=False, reason="extent ratio 1.13: camera suspect")},
                  "bbox_mm": [[-72, -31, -165.1], [72, 12, 0]]}
        session, calls = self.session_with([first, second])
        self.build(session)
        op, r2 = self.second_build(session)
        vs = r2.text["vs_parent"]
        self.assertEqual(vs["parent"], "r0001")
        self.assertEqual(vs["changed_modules"], ["frame"])
        lo = vs["metrics"]["lens_outline_mean_mm"]
        self.assertEqual((lo["parent"], lo["now"]), (0.846, 1.918))
        self.assertAlmostEqual(lo["delta"], 1.072, places=3)
        self.assertTrue(lo["worse"])
        self.assertTrue(vs["cameras"]["front"]["moved"])
        self.assertAlmostEqual(vs["cameras"]["front"]["pitch"], 1.3, places=3)
        self.assertNotIn("scale", vs["cameras"]["front"], "the normalised scale moves with the bounding box, never reported")
        self.assertFalse(vs["cameras"]["back"]["moved"])
        worsened = " | ".join(vs["worsened"])
        self.assertIn("lens_outline_mean_mm", worsened)
        self.assertIn("front camera moved", worsened, "a camera move is named, so it is not read as a shape change")
        self.assertIn("bbox_extent_mm", vs)
        self.assertNotIn("back_contour_mean_mm", worsened, "0.3% is not a regression")
        # unreliable metrics are marked where they are shown
        self.assertIn("camera suspect", r2.text["measurements_unreliable"]["back_contour_mean_mm"])
        self.assertIn("unreliable", vs["metrics"]["back_contour_mean_mm"])
        # the best-so-far points back at r0001 for the lens outline
        self.assertEqual(vs["best_so_far"]["lens_outline_mean_mm"], {"value": 0.846, "revision": "r0001"})

    def test_vs_parent_is_absent_on_a_first_build(self):
        session, calls = self.session_with([{"summary": {"lens_outline_mean_mm": 0.9}}])
        op, r1 = self.build(session)
        self.assertNotIn("vs_parent", r1.text)

    def test_unreliable_metrics_read_views_lens_outline_and_summary_blocks(self):
        obs = {"views": {"front": {"view": "front", "lens_outline": {"status": "measured", "reliable": False, "reason": "lens print holes"}},
                         "left": {"view": "left", "reliable": False, "reason": "matte covers 30%"}},
               "summary": {"front_contour_mean_mm": {"value": 1.2, "reliable": False, "reason": "why"}, "back_contour_mean_mm": 1.0}}
        got = T.unreliable_metrics(obs)
        # an unreliable view also marks the mean over every fit view, as modeler.observe.summarize does (round 8)
        self.assertEqual(set(got), {"lens_outline_mean_mm", "lens_outline_p95_mm", "side_contour_mean_mm", "front_contour_mean_mm",
                                    "mean_contour_mm_all_fit_views"})
        self.assertEqual(got["side_contour_mean_mm"], "left: matte covers 30%")
        self.assertEqual(T.unreliable_metrics({}), {})

    # ---- AT-01: the reply names the measurement that gates the automatic verdict
    def test_build_reply_names_the_automatic_gate(self):
        session, calls = self.session_with([{"summary": {"lens_outline_mean_mm": 1.969}}])
        op, r1 = self.build(session)
        gate = r1.text["automatic_gate"]
        self.assertEqual(gate["lens_outline_mean_mm"]["limit_mm"], 0.8)
        self.assertFalse(gate["lens_outline_mean_mm"]["passes"])
        self.assertTrue(gate["lens_outline_mean_mm"]["gate"])
        self.assertIn("continuity", gate["note"])

    def test_measure_candidate_names_the_gate_and_the_unreliable_metrics(self):
        session, calls = self.session_with([{"summary": {"lens_outline_mean_mm": 0.5},
                                             "views": {"front": view_fit("front", camera(), 1.0, lens_outline={"status": "measured", "reliable": False, "reason": "print holes"})}}])
        self.build(session)
        op, m = self.run_tool(session, "call_2", "measure_candidate", {"revision_id": "r0001"})
        self.assertTrue(m.text["automatic_gate"]["lens_outline_mean_mm"]["passes"])
        self.assertIn("print holes", m.text["measurements_unreliable"]["lens_outline_mean_mm"])
        self.assertIn("print holes", m.text["automatic_gate"]["lens_outline_mean_mm"]["unreliable"])
        self.assertNotIn("glb_sha256", m.text["compatibility"])

    def test_a_report_only_override_is_shown(self):
        session, calls = self.session_with([{"summary": {"lens_outline_mean_mm": 1.969}}])
        with session.store.tx():
            session.store.set_setting("protocol", dict(session.store.setting("protocol") or {},
                                                       gate_overrides={"lens_outline_mean_mm": {"mode": "report_only", "reason": "rim"}}))
        op, r1 = self.build(session)
        self.assertFalse(r1.text["automatic_gate"]["lens_outline_mean_mm"]["gate"])

    # ---- MVP-08: where the runtime clips the temples, in the author's coordinates
    def test_build_reply_states_the_runtime_temple_clip_and_what_is_never_drawn(self):
        session, calls = self.session_with([{}], declared=-154.0)
        op, r1 = self.build(session)
        clip = r1.text["temple_clip"]
        self.assertAlmostEqual(clip["z_mm"], CLIP_ORIGIN[2] + T.RUNTIME_TEMPLE_CLIP_Z_M * 1000.0, places=3)
        self.assertEqual(clip["objects_never_drawn"], ["Tip inscription L"])
        self.assertIn("never", clip["note"])
        self.assertIn("camera", clip["note"], "edits behind the clip still move the fitted cameras")
        self.assertEqual(clip["declared_z_mm"], -154.0)
        self.assertIn("set_temple_clip_z", clip["declared_note"])

    def test_list_evidence_states_the_clip_the_owner_instruction_and_the_editable_modules(self):
        session, calls = self.session_with([{}])
        session.policy["editable_modules"] = ["materials", "lenses"]
        session.policy["owner_instruction"] = "Other than the lens color the model is perfect."
        op, r = self.run_tool(session, "call_1", "list_evidence", {"reference": None})
        self.assertEqual(r.text["owner_instruction"], "Other than the lens color the model is perfect.")
        self.assertEqual(r.text["editable_modules"], ["materials", "lenses"])
        self.assertIn("-0.115", r.text["temple_clip"]["rule"])

    # ---- INF-08: photos without a fitted camera are named
    def test_build_reply_names_the_photos_without_a_fitted_camera(self):
        session, calls = self.session_with([{"views": {"front": view_fit("front", camera(), 1.0), "back": view_fit("back", camera(yaw=180), 1.5)}}])
        op, r1 = self.build(session)
        unfitted = r1.text["photos_without_a_fitted_camera"]
        visible = {k: v.get("view") for k, v in session.evidence()["views"].items()}
        self.assertEqual(unfitted["photos"], {k: v for k, v in visible.items() if k not in ("front", "back")})
        self.assertIn("no match render", unfitted["note"])

    # ---- AT-09: a failing tiny detail gets its own repair hint; a failing temple object behind the clip is named as never drawn
    def test_a_failing_tiny_detail_gets_the_tiny_detail_hint(self):
        screw = {"object": "Screw L", "part": "temple_L", "triangles": 96, "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0,
                 "bbox_mm": [[-60.0, -1.0, -20.0], [-58.42, -0.8, -18.42]]}     # test-pilot-002's 1.58 x 0.2 x 1.58 mm screw
        export = {"contract": {"ok": False, "failures": ["watertight_parts"],
                               "checks": {"watertight_parts": {"pass": False, "value": {"temple_L": False}, "limit": "closed"}},
                               "parts": {"temple_L": contract_part("temple", ok=False)}}}
        hints = T.repair_hints(export, [dict(r) for r in TIP_ROWS] + [screw])
        line = next(h for h in hints if h.startswith("Screw L"))
        self.assertIn("tiny detail", line)
        self.assertIn("1.6 x 0.2 x 1.6 mm", line)
        self.assertIn("weld", line)
        self.assertFalse([h for h in hints if h.startswith("Crystal temple L")], "a clean object is not a hint")
        # the 0.7 x 3.2 x 3.6 mm tip inscription is not small in every dimension: without a clip plane it is an ordinary open object
        tip = next(h for h in hints if h.startswith("Tip inscription L"))
        self.assertNotIn("tiny detail", tip)
        self.assertNotIn("leave it out", tip)
        # with the runtime plane it lies wholly behind the clip: never drawn
        hints = T.repair_hints(export, [dict(r) for r in TIP_ROWS], clip_z_mm=-117.7)
        tip = next(h for h in hints if h.startswith("Tip inscription L"))
        self.assertIn("behind the runtime temple clip", tip)
        self.assertIn("-117.7", tip)

    def test_replay_of_test_pilot_002_r0003_names_the_inscriptions_as_never_drawn(self):
        src = PILOT2 / "r0003"
        files = {"export.json": "export.json", "result.host.json": "build/result.host.json", "parts.npz": "build/parts.npz", "materials.json": "build/materials.json",
                 "report.json": "observe/ar/report.json"}
        if not all((src / f).is_file() for f in files.values()):
            self.skipTest("test-pilot-002 evidence is not on this machine")
        for dst, f in files.items():           # the job folder is read-only: the replay works on copies
            shutil.copyfile(src / f, self.root / dst)
        export = json.loads((self.root / "export.json").read_text(encoding="utf-8"))
        inventory = json.loads((self.root / "result.host.json").read_text(encoding="utf-8"))["inventory"]
        clip = T.temple_clip_block(export, inventory, recorded=T.recorded_temple_clip(self.root / "report.json", export.get("sha256")))
        self.assertEqual(clip["z_mm"], -117.7)
        hints = T.repair_hints(export, inventory, parts_npz=self.root / "parts.npz", materials_json=self.root / "materials.json", clip_z_mm=clip["z_mm"])
        tips = [h for h in hints if h.startswith("Tip inscription")]
        self.assertEqual(len(tips), 2, hints)
        self.assertTrue(all("behind the runtime temple clip" in h and "-117.7" in h for h in tips), tips)

    # ---- sheets of the shared interface are labelled
    def test_new_sheets_are_labelled_and_tagged_as_the_ar_renderer(self):
        for key in ("lens_backdrop", "pose_sweep"):
            self.assertIn(key, T.SHEET_LABELS)
            self.assertEqual(T.SHEET_RENDERERS[key], "actual-ar")
        self.assertIn("lens_reflection", T.sheet_label("pose_sweep"))
        self.assertIn("colour", T.sheet_label("lens_backdrop"))
        for vid in AR_VIEWS:
            self.assertIn(vid["id"], T.sheet_label("ar"))

    # ---- editable_modules: a change outside the list is refused at no build
    def test_edit_program_refuses_a_module_outside_the_editable_list(self):
        session, calls = self.session_with([{}, {}])
        self.build(session)
        session.policy["editable_modules"] = ["materials"]
        ops_before = len(session.store.operations())
        op, refused = self.second_build(session)
        err = refused.text.get("error")
        self.assertIsNotNone(err, refused.text)
        self.assertIn("frame", err)
        self.assertIn("editable modules: materials", err)
        self.assertIn("nothing was built", err)
        self.assertEqual(len(session.store.revisions()), 1)
        self.assertEqual(len(session.store.operations()), ops_before + 1, "only the refused call itself: no worker operation")
        self.assertEqual(len(calls), 1, "no observation ran")
        # inheriting everything else and changing an editable module is fine
        mods = {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in T.MODULE_KEYS}
        mods["materials"] = {"mode": "replace", "content": "gl.note('materials')\n", "expected_base_sha256": None}
        op, ok = self.run_tool(session, "call_3", "edit_program", {"base_revision_id": "r0001", "modules": mods, "rationale": "lens colour",
                                                                   "expected_changes": [], "build_now": False, "deliver_if_compatible": False})
        self.assertNotIn("error", ok.text, ok.text)
        self.assertEqual(ok.text["changed_modules"], ["materials"])

    def test_editable_modules_null_allows_every_module(self):
        session, calls = self.session_with([{}, {}])
        self.build(session)
        session.policy["editable_modules"] = None
        op, r2 = self.second_build(session)
        self.assertTrue(r2.text.get("built"), r2.text)

    # ---- INF-11: host phases renew the lease
    def test_host_observation_phase_renews_the_lease(self):
        seen = {}
        holder = {}

        def probe(when):
            seen[when] = holder["session"].store.job()["lease_expires_utc"]
            seen[f"{when}_threads"] = [t.name for t in threading.enumerate() if t.name.startswith("lease-renewal")]
        # the lease timestamps have one-second resolution: 1.3 s of host work spans a renewal at least one second later
        session, calls = self.session_with([{}], sleep_s=1.3, probe=probe)
        holder["session"] = session
        with mock.patch.object(T, "HOST_RENEW_INTERVAL_S", 0.05):
            op, r1 = self.build(session)
        self.assertTrue(r1.text["built"], r1.text)
        self.assertTrue(seen["start_threads"], "a renewal thread runs during the host phase")
        self.assertGreater(seen["end"], seen["start"], "the lease was renewed while the host observed")
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("lease-renewal")], "the renewal thread stops with the phase")

    def test_no_heartbeat_means_no_renewal_thread(self):
        session, calls = self.session_with([{}])
        ctx = session.context()
        ctx.heartbeat = None
        with T.lease_renewed(ctx) as state:
            self.assertFalse([t for t in threading.enumerate() if t.name.startswith("lease-renewal")])
        self.assertEqual(state, {})

    def test_observe_candidate_receives_the_heartbeat_when_it_accepts_one(self):
        session, calls = self.session_with([{}])
        self.build(session)
        self.assertTrue(callable(calls[0]["heartbeat"]))


class Round6ArViews(ToolsCase):
    """AT-04 / INF-04: render_ar_views could only return the four views already on the ar sheet (op0010 bought pixel-identical copies)."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    def built(self):
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        self.assertTrue(session.store.revision("r0001")["compatibility"]["contract_ok"], r1.text)
        return session

    @staticmethod
    def view(vid, yaw=0.0, pitch=0.0, roll=0.0, kind="pose"):
        return {"id": vid, "yaw": yaw, "pitch": pitch, "roll": roll, "type": kind}

    def test_the_schema_takes_poses_and_a_background(self):
        params = T.TOOLS["render_ar_views"]["parameters"]
        responses.validate_tools_schema(params)
        ok = {"revision_id": "r0001", "views": [self.view("side", 70.0, 10.0)], "background": "skin"}
        got, err = responses.validate_call_arguments(Round3Tools.call("render_ar_views", ok), T.TOOLS)
        self.assertIsNone(err, err)
        for bad in ({"revision_id": "r0001", "views": [self.view("x", 95.0)], "background": "checker"},
                    {"revision_id": "r0001", "views": [self.view("x", 0.0, 61.0)], "background": "checker"},
                    {"revision_id": "r0001", "views": ["front"], "background": "checker"}):
            got, err = responses.validate_call_arguments(Round3Tools.call("render_ar_views", bad), T.TOOLS)
            self.assertIsNotNone(err, bad)
        for vid in AR_VIEWS:
            self.assertIn(vid["id"], T.TOOLS["render_ar_views"]["description"], "the description names the views the ar sheet already carries")

    def test_views_already_on_the_sheet_are_returned_without_a_harness_run(self):
        session = self.built()
        on_sheet = [self.view("front"), self.view("angled", 35.0), self.view("back", kind="asset-back")]
        with mock.patch("bsa.archeck.run", side_effect=AssertionError("the harness must not run")):
            op, r = self.run_tool(session, "call_2", "render_ar_views", {"revision_id": "r0001", "views": on_sheet, "background": "checker"})
        self.assertNotIn("error", r.text, r.text)
        self.assertEqual([x["view"] for x in r.text["rendered"]], ["front", "angled", "back"])
        self.assertTrue(all(x["reused"] for x in r.text["rendered"]))
        self.assertIn("already on the ar sheet", r.text["note"])
        self.assertEqual(len(r.images), 3)
        obs_dir = session.store.job_dir / "revisions" / "r0001" / "observe" / "ar"
        for a, v in zip(r.images, ("front", "angled", "back")):
            self.assertEqual(a["sha256"], sha((obs_dir / f"candidate__actual-ar__{v}.png").read_bytes()))

    def test_a_new_pose_runs_the_harness_for_the_new_views_only(self):
        session = self.built()
        seen = {}

        def run(glbs, out_dir, *, ar_views, width_mm=None, background="checker", background_color=None, timeout_s=None, **kw):
            seen.update(views=[dict(v) for v in ar_views], background=background, background_color=background_color, timeout_s=timeout_s)
            return write_ar_folder(Path(out_dir), Path(glbs["candidate"]), ar_views, colour=(9, 9, 9))
        with mock.patch("bsa.archeck.run", side_effect=run):
            op, r = self.run_tool(session, "call_2", "render_ar_views", {"revision_id": "r0001", "views": [self.view("front"), self.view("side", 70.0, 8.0, 0.0)],
                                                                          "background": "checker"})
        self.assertNotIn("error", r.text, r.text)
        self.assertEqual(seen["views"], [{"id": "side", "yaw_degrees": 70.0, "pitch_degrees": 8.0, "roll_degrees": 0.0}])
        self.assertLessEqual(seen["timeout_s"], T.AR_VIEWS_TIMEOUT_S)
        self.assertEqual({x["view"]: x["reused"] for x in r.text["rendered"]}, {"front": True, "side": False})
        self.assertTrue(r.text["report_valid"])

    def test_a_solid_background_is_a_new_render_of_a_sheet_pose(self):
        session = self.built()
        seen = {}

        def run(glbs, out_dir, *, ar_views, background="checker", background_color=None, **kw):
            seen.update(background=background, background_color=background_color, views=[v["id"] for v in ar_views])
            return write_ar_folder(Path(out_dir), Path(glbs["candidate"]), ar_views)
        with mock.patch("bsa.archeck.run", side_effect=run):
            op, r = self.run_tool(session, "call_2", "render_ar_views", {"revision_id": "r0001", "views": [self.view("front")], "background": "skin"})
        self.assertEqual((seen["background"], seen["background_color"], seen["views"]), ("solid", "#cba68d", ["front"]))
        self.assertFalse(r.text["rendered"][0]["reused"])

    def test_asset_back_with_angles_and_duplicate_ids_are_refused(self):
        session = self.built()
        with mock.patch("bsa.archeck.run", side_effect=AssertionError("the harness must not run")):
            op, r = self.run_tool(session, "call_2", "render_ar_views", {"revision_id": "r0001", "views": [self.view("b", 10.0, kind="asset-back")], "background": "checker"})
            self.assertIn("asset-back", r.text.get("error", ""))
            op, r = self.run_tool(session, "call_3", "render_ar_views", {"revision_id": "r0001", "views": [self.view("a"), self.view("a", 20.0)], "background": "checker"})
            self.assertIn("unique", r.text.get("error", ""))


class Round6AuthorPrompt(unittest.TestCase):
    def test_the_prompt_names_the_gate_the_sheets_and_the_owner_instruction(self):
        flat = " ".join(runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8").split())
        for needle in ("lens_outline_mean_mm", "0.8 mm", "reliable", "pose_sweep", "lens_reflection", "lens_backdrop", "owner_instruction",
                       "editable_modules", "temple_clip", "vs_parent", "inspect_scene", "match_renders", "photos_without_a_fitted_camera"):
            self.assertIn(needle, flat)
        self.assertNotIn("they never decide for you", flat, "a gate decides the automatic verdict")


# =========================================================================== round 8 (2026-09-28): the tools verifier's must-fix items
PILOT2_R0006 = PILOT2 / "r0006"
# test-pilot-002's recorded harness timing: templeEndMaximumZM = max(-0.14, min(-0.055, -0.14 + 0.025, ...)) on every render
RUN2_TIMING = {"templeEndMaximumZM": -0.11500000000000002, "templeEndNegativeZM": -0.11500000000000002, "templeEndPositiveZM": -0.11500000000000002,
               "templeEndState": "fallback/fallback"}
THIN_ROWS = [  # test-pilot-002 r0006's thin but large visible parts: the branding and the front T hardware
    {"object": "Lens print", "part": "frame", "triangles": 5000, "boundary_edges": 12, "nonmanifold_edges": 0, "misoriented_edges": 0,
     "bbox_mm": [[20.0, 5.0, 1.0], [31.87, 9.63, 1.82]]},
    {"object": "T front upright L", "part": "frame", "triangles": 60, "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0,
     "bbox_mm": [[-66.0, -2.0, -1.0], [-64.94, 2.8, -0.73]]},
    {"object": "T front return L", "part": "frame", "triangles": 60, "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0,
     "bbox_mm": [[-68.0, -1.0, -3.0], [-65.21, 0.06, -2.56]]},
    {"object": "Rear hinge anchor L", "part": "frame", "triangles": 60, "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0,
     "bbox_mm": [[-70.0, -2.0, -6.0], [-66.2, 2.5, -5.2]]}]


def failing_frame_export() -> dict:
    return {"contract": {"ok": False, "failures": ["watertight_parts"], "checks": {"watertight_parts": {"pass": False, "value": {"frame": False}, "limit": "closed"}},
                         "parts": {"frame": contract_part("frame", ok=False)}}}


class Round8TempleClip(ToolsCase):
    """MUST FIX 1: the author was told the arm is drawn back to origin z - 140 mm; the runtime draws to templeEndMaximumZM
    (-0.115 m for the -0.14 base: HIDDEN_TAIL_M 0.025) and fades over END_FADE_M (5 mm). Every run-2 render recorded -0.115."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    def test_the_fallback_constants_are_the_runtimes(self):
        self.assertAlmostEqual(T.RUNTIME_TEMPLE_CLIP_Z_M, -0.115, places=9)
        self.assertEqual(T.TEMPLE_CLIP_FADE_MM, 5)
        self.assertAlmostEqual(T.runtime_temple_end_m(-0.14), -0.115, places=9)
        self.assertAlmostEqual(T.runtime_temple_end_m(-0.07), -0.055, places=9, msg="the runtime never extends past -0.055 m")
        self.assertAlmostEqual(T.runtime_temple_end_m(-0.05), -0.05, places=9, msg="an already-short frame never grows a tail")

    def test_build_reply_states_the_clip_the_harness_recorded(self):
        timing = [dict(RUN2_TIMING), dict(RUN2_TIMING), dict(RUN2_TIMING, templeEndNegativeZM=-0.105, templeEndState="tracked/fallback"), dict(RUN2_TIMING)]
        session, calls = self.session_with([{"ar_timing": timing}], declared=-154.0)
        op, r1 = self.build(session)
        clip = r1.text["temple_clip"]
        self.assertEqual(clip["z_mm"], -117.7, "origin z -2.7 mm - 115 mm")
        self.assertEqual(clip["fade_mm"], 5)
        self.assertIn("report", clip["source"])
        self.assertEqual(clip["z_mm_range"], [-117.7, -107.7], "one render's endpoint report cut one side 10 mm earlier")
        self.assertIn("-117.7", clip["note"])
        self.assertIn("5 mm", clip["note"])
        self.assertEqual(clip["objects_never_drawn"], ["Tip inscription L"])
        self.assertIn("not used", clip["declared_note"])

    def test_without_a_report_the_runtime_constants_stand_in(self):
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        clip = r1.text["temple_clip"]
        self.assertEqual(clip["z_mm"], -117.7)
        self.assertIn("constants", clip["source"])

    def test_a_report_for_another_glb_is_not_used(self):
        other = dict(RUN2_TIMING, templeEndMaximumZM=-0.09, templeEndNegativeZM=-0.09, templeEndPositiveZM=-0.09)
        session, calls = self.session_with([{"ar_timing": other, "report_sha": "0" * 64}])
        op, r1 = self.build(session)
        self.assertEqual(r1.text["temple_clip"]["z_mm"], -117.7)
        self.assertIn("constants", r1.text["temple_clip"]["source"])

    def test_objects_never_drawn_use_the_same_plane(self):
        rows = [dict(TIP_ROWS[0]), {"object": "Arm tail L", "part": "temple_L", "bbox_mm": [[-60, -2, -130.0], [-58, 2, -119.0]]},
                {"object": "Arm bend L", "part": "temple_L", "bbox_mm": [[-60, -2, -125.0], [-58, 2, -110.0]]}]
        export = {"receipt": {"origin": {"origin_mm": CLIP_ORIGIN}}}
        block = T.temple_clip_block(export, rows)
        self.assertEqual(block["objects_never_drawn"], ["Arm tail L"], "wholly behind -117.7 is never drawn; straddling it is")
        recorded = {"z_m": -0.1, "z_m_max": -0.1, "renders": 4, "states": ["tracked/tracked"]}
        block = T.temple_clip_block(export, rows, recorded=recorded)
        self.assertEqual(block["z_mm"], -102.7)
        self.assertEqual(block["objects_never_drawn"], ["Arm tail L", "Arm bend L"])

    def test_the_real_r0006_report_gives_minus_117_7(self):
        files = {"export.json": "export.json", "report.json": "observe/ar/report.json", "result.host.json": "build/result.host.json", "model.glb": "model.glb"}
        if not all((PILOT2_R0006 / f).is_file() for f in files.values()):
            self.skipTest("test-pilot-002 evidence is not on this machine")
        for dst, f in files.items():           # the job folder is read-only: the probe works on copies
            shutil.copyfile(PILOT2_R0006 / f, self.root / dst)
        export = json.loads((self.root / "export.json").read_text(encoding="utf-8"))
        inventory = json.loads((self.root / "result.host.json").read_text(encoding="utf-8"))["inventory"]
        recorded = T.recorded_temple_clip(self.root / "report.json", sha((self.root / "model.glb").read_bytes()))
        self.assertIsNotNone(recorded)
        self.assertAlmostEqual(recorded["z_m"], -0.115, places=6)
        self.assertEqual(recorded["renders"], 4)
        block = T.temple_clip_block(export, inventory, recorded=recorded)
        self.assertEqual(block["z_mm"], -117.7)
        self.assertEqual(sorted(block["objects_never_drawn"]), ["Capsule tip plaque L", "Capsule tip plaque R", "Tip inscription L", "Tip inscription R"])
        self.assertEqual(block["declared_z_mm"], -154.0)
        self.assertIsNone(T.recorded_temple_clip(self.root / "report.json", "0" * 64), "a report of another GLB is not this revision's")

    def test_list_evidence_gives_the_rule_and_the_current_revisions_plane(self):
        session, calls = self.session_with([{"ar_timing": RUN2_TIMING}])
        op, r = self.run_tool(session, "call_0", "list_evidence", {"reference": None})
        self.assertIn("115 mm", r.text["temple_clip"]["rule"])
        self.assertIn("5 mm", r.text["temple_clip"]["rule"])
        self.assertNotIn("z_mm", r.text["temple_clip"], "nothing is built yet")
        self.build(session)
        op, r = self.run_tool(session, "call_2", "list_evidence", {"reference": None})
        self.assertEqual((r.text["temple_clip"]["revision"], r.text["temple_clip"]["z_mm"]), ("r0001", -117.7))
        self.assertIn("report", r.text["temple_clip"]["source"])

    def test_the_prompt_states_the_runtime_plane(self):
        flat = " ".join(runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8").split())
        self.assertIn("-0.115", flat)
        self.assertIn("115 mm", flat)
        self.assertIn("5 mm", flat)
        self.assertNotIn("-140 mm", flat)
        self.assertNotIn("local z -0.14 m behind", flat)
        self.assertNotIn("-140", T.TEMPLE_CLIP_POINTER)


class Round8OwnerLock(ToolsCase):
    """MUST FIX 2: base_revision_id null dropped every locked module and built a parentless revision."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    @staticmethod
    def inherit_all(**changes) -> dict:
        mods = {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in T.MODULE_KEYS}
        mods.update(changes)
        return mods

    @staticmethod
    def materials():
        return {"mode": "replace", "content": "gl.note('materials')\n", "expected_base_sha256": None}

    def edit(self, session, call_id, base, mods, build_now=True):
        return self.run_tool(session, call_id, "edit_program", {"base_revision_id": base, "modules": mods, "rationale": "owner edit",
                                                                "expected_changes": [], "build_now": build_now, "deliver_if_compatible": False})

    def test_a_null_base_is_refused_when_modules_are_locked(self):
        session, calls = self.session_with([{}, {}])
        self.build(session)
        session.policy["editable_modules"] = ["materials"]
        ops_before = len(session.store.operations())
        op, r = self.edit(session, "call_2", None, self.inherit_all(materials=self.materials()))
        err = r.text.get("error") or ""
        self.assertIn("base_revision_id", err)
        self.assertIn("frame", err, "the locked module a null base would drop is named")
        self.assertIn("nothing was built", err)
        self.assertEqual([x["id"] for x in session.store.revisions()], ["r0001"])
        self.assertEqual(len(session.store.operations()), ops_before + 1, "only the refused call itself")
        self.assertEqual(len(calls), 1, "no observation ran")

    def test_a_base_lacking_a_locked_module_is_refused(self):
        session, calls = self.session_with([{}, {}, {}])
        mods = demo.modules(demo.PROGRAM_A)
        mods["finish"] = {"mode": "replace", "content": "gl.note('finish')\n", "expected_base_sha256": None}
        self.edit(session, "call_1", None, mods, build_now=False)
        # before the lock (an ordinary job): r0002 drops the finish module
        op, r2 = self.edit(session, "call_2", "r0001", self.inherit_all(finish={"mode": "remove", "content": None, "expected_base_sha256": None}), build_now=False)
        self.assertNotIn("error", r2.text, r2.text)
        session.policy["editable_modules"] = ["materials"]
        op, r = self.edit(session, "call_3", "r0002", self.inherit_all(materials=self.materials()))
        err = r.text.get("error") or ""
        self.assertIn("finish", err)
        self.assertIn("r0002", err)
        self.assertIn("nothing was built", err)
        self.assertEqual(len(session.store.revisions()), 2)
        self.assertEqual(calls, [], "no observation ran")
        # the owner's revision carries it: accepted
        op, ok = self.edit(session, "call_4", "r0001", self.inherit_all(materials=self.materials()), build_now=False)
        self.assertNotIn("error", ok.text, ok.text)
        self.assertEqual(set(session.store.revision(ok.text["revision"])["modules"]), {"frame", "finish", "materials"})

    def test_a_null_base_stays_allowed_without_a_lock(self):
        session, calls = self.session_with([{}])
        session.policy["editable_modules"] = None
        op, r1 = self.build(session)
        self.assertTrue(r1.text.get("built"), r1.text)


class Round8AutomaticGate(ToolsCase):
    """MUST FIX 3: automatic_gate must say what evaluate.decide_status does: an unreliable metric is reported, never gated."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    @staticmethod
    def evaluator_gate(summary: dict) -> dict:
        from modeler import evaluate as mevaluate
        out = mevaluate.decide_status(candidate_valid=True, metrics=summary, heldout=None, evaluation=None, input_flags=[], protocol_calibrated=False)
        return out["provisional"]["lens_outline_mean_mm"]

    def test_an_unreliable_lens_outline_is_not_gated(self):
        session, calls = self.session_with([{}])
        ctx = session.context()
        for summary, unreliable in (({"lens_outline_mean_mm": 1.9, "unreliable_metrics": {"lens_outline_mean_mm": "rim invisible to the matte"}}, None),
                                    ({"lens_outline_mean_mm": 1.9}, {"lens_outline_mean_mm": "front lens outline: print holes"})):
            row = T.automatic_gate(ctx, summary, unreliable)["lens_outline_mean_mm"]
            self.assertFalse(row["gate"], row)
            self.assertFalse(row["reliable"])
            self.assertTrue(row["reason"])
            self.assertIn("never gated", row["gate_note"])
        summary = {"lens_outline_mean_mm": 1.9, "unreliable_metrics": {"lens_outline_mean_mm": "rim invisible to the matte"}}
        self.assertEqual(T.automatic_gate(ctx, summary)["lens_outline_mean_mm"]["gate"], self.evaluator_gate(summary)["gate"])
        summary = {"lens_outline_mean_mm": 1.9}
        self.assertTrue(T.automatic_gate(ctx, summary)["lens_outline_mean_mm"]["gate"])
        self.assertEqual(T.automatic_gate(ctx, summary)["lens_outline_mean_mm"]["gate"], self.evaluator_gate(summary)["gate"])

    def test_the_build_reply_and_measure_candidate_show_gate_false(self):
        session, calls = self.session_with([{"summary": {"lens_outline_mean_mm": 1.2},
                                             "views": {"front": view_fit("front", camera(), 1.0, lens_outline={"status": "measured", "reliable": False, "reason": "print holes"})}}])
        op, r1 = self.build(session)
        self.assertFalse(r1.text["automatic_gate"]["lens_outline_mean_mm"]["gate"])
        op, m = self.run_tool(session, "call_2", "measure_candidate", {"revision_id": "r0001"})
        self.assertFalse(m.text["automatic_gate"]["lens_outline_mean_mm"]["gate"])
        self.assertIn("print holes", m.text["automatic_gate"]["lens_outline_mean_mm"]["reason"])

    def test_unreliable_metrics_read_the_summary_list_and_the_all_views_mean(self):
        obs = {"views": {"back": {"view": "back", "reliable": False, "reason": "matte covers 40%"}},
               "summary": {"unreliable_metrics": {"side_contour_mean_mm": "left matte covers 30%", "bogus": 3}}}
        got = T.unreliable_metrics(obs)
        self.assertEqual(got["side_contour_mean_mm"], "left matte covers 30%")
        self.assertIn("back", got["mean_contour_mm_all_fit_views"])
        self.assertEqual(got["back_contour_mean_mm"], "back: matte covers 40%")
        self.assertNotIn("bogus", got, "a reason must be a string")

    def test_a_carried_lens_camera_is_not_blamed_on_the_front_refit(self):
        lens = {"status": "measured", "camera": {"origin": "carried", "pitch_deg": 15.7}}
        first = {"summary": {"lens_outline_mean_mm": 0.8}, "views": {"front": view_fit("front", camera(pitch=15.7), 1.0, lens_outline=dict(lens))}}
        second = {"summary": {"lens_outline_mean_mm": 1.4}, "views": {"front": view_fit("front", camera(pitch=17.0), 1.0, lens_outline=dict(lens))}}
        session, calls = self.session_with([first, second])
        self.build(session)
        op, r2 = Round6Replies.second_build(self, session)
        worsened = " | ".join(r2.text["vs_parent"]["worsened"])
        self.assertIn("lens_outline_mean_mm", worsened)
        self.assertNotIn("front camera moved", worsened, "the lens outline was measured under the carried camera: a lens change, not a refit")


class Round8TinyDetails(unittest.TestCase):
    """MUST FIX 4: thin but large visible parts (branding, the front T) were told 'invisible ... leave it out'."""

    def test_thin_but_large_parts_are_never_told_to_leave_out(self):
        hints = T.repair_hints(failing_frame_export(), [dict(r) for r in THIN_ROWS], clip_z_mm=-117.7)
        lines = [h for h in hints if h.split(" (", 1)[0] in {r["object"] for r in THIN_ROWS}]
        self.assertEqual(len(lines), len(THIN_ROWS), hints)
        for h in lines:
            for banned in ("leave it out", "invisible", "tiny detail", "never drawn"):
                self.assertNotIn(banned, h)

    def test_a_front_part_behind_the_plane_is_not_a_temple_tail(self):
        row = {"object": "Deep frame lug", "part": "frame", "triangles": 60, "boundary_edges": 4, "nonmanifold_edges": 0, "misoriented_edges": 0,
               "bbox_mm": [[-70.0, -2.0, -140.0], [-60.0, 2.5, -120.0]]}
        hints = T.repair_hints(failing_frame_export(), [row], clip_z_mm=-117.7)
        self.assertTrue(hints)
        self.assertFalse([h for h in hints if "never drawn" in h], "only the temples are clipped")


class Round8Provenance(ToolsCase):
    """INF-18: the build tool's row carried a deadline for its build container only; the observation now carries the
    bundle's measurement fingerprint."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    def test_the_build_tool_row_carries_no_container_deadline(self):
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        self.assertTrue(r1.text["built"], r1.text)
        self.assertIsNone(op["deadline_utc"], "the build tool runs export and observation past its container: no single deadline covers it")

    def test_a_render_views_row_keeps_its_container_deadline(self):
        session = ToolsCase.make_session(self, executor.FakeWorker())
        self.build(session)
        op, r = self.run_tool(session, "call_2", "render_views", {"revision_id": "r0001", "views": [EXTRA_VIEW]})
        self.assertTrue(op["deadline_utc"])

    def test_the_observation_carries_the_measurement_fingerprint(self):
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        bundle = json.loads((session.store.job_dir / "worker" / op["id"] / "bundle" / "operation.json").read_text(encoding="utf-8"))
        obs = session.store.revision("r0001")["observation"]
        self.assertEqual(obs["measurement_fingerprint_sha256"], bundle["measurement_fingerprint"]["sha256"])
        op, m = self.run_tool(session, "call_2", "measure_candidate", {"revision_id": "r0001"})
        self.assertNotIn("measurement_fingerprint_sha256", m.text["measurements"], "a host provenance hash, not a measurement")


class Round8RulesAndAppearance(ToolsCase):
    """AT-13 and the appearance fixer's shared interface (sheets.material_match, summary.appearance)."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with

    APPEARANCE = {"materials": {"Gold hardware": {"role": "metal", "photo": {"lab": [72.0, 4.0, 30.0], "px": 5123}, "render": {"lab": [60.0, 2.0, 18.0], "px": 4999},
                                                  "deltas": {"lightness": -12.0, "chroma": -11.9}, "recommended": {"base_color": "#c9a04a", "roughness": 0.3},
                                                  "flags": ["metal_too_dark"]},
                                "Crystal acetate": {"role": "crystal", "photo": {"lab": [80.0, 1.0, 2.0]}, "render": {"lab": [78.0, 1.0, 2.0]},
                                                    "deltas": {"lightness": -2.0}, "recommended": {}, "flags": []}},
                  "hardware_area": {"hinge": {"photo_share": 0.021, "render_share": 0.009, "ratio": 0.43, "flag": "hardware_too_small"}},
                  "flags": ["metal_too_dark", "hardware_too_small"], "reliable": True, "reason": None}

    def test_list_evidence_rules_are_the_first_messages(self):
        session = self.make_session(executor.FakeWorker())
        first = session.store.items("author")[0]["item"]
        head = json.loads(first["content"][0]["text"].split(NL, 1)[1])
        op, r = self.run_tool(session, "call_1", "list_evidence", {"reference": True})
        self.assertEqual(r.text["rules"], head["rules"])
        self.assertNotIn("request_views", json.dumps(r.text["rules"]), "this route's tool is render_views")

    def test_material_match_is_labelled_and_tagged_as_the_ar_renderer(self):
        self.assertIn("material_match", T.SHEET_LABELS)
        self.assertEqual(T.SHEET_RENDERERS["material_match"], "actual-ar")
        self.assertIn("appearance", T.sheet_label("material_match"))

    def test_the_build_reply_carries_the_appearance_compactly(self):
        session, calls = self.session_with([{"summary": {"appearance": self.APPEARANCE}, "extra_sheets": ["material_match"]}])
        op, r1 = self.build(session)
        sheets = {a["recipe"]["view"]: a for a in r1.images if a["kind"] == "sheet"}
        self.assertEqual(sheets["material_match"]["recipe"]["renderer"], "actual-ar")
        self.assertIn("appearance", sheets["material_match"]["label"])
        app = r1.text["measurements"]["appearance"]
        self.assertEqual(app["flags"], ["metal_too_dark", "hardware_too_small"])
        gold = app["materials"]["Gold hardware"]
        self.assertEqual(gold["role"], "metal")
        self.assertEqual(gold["recommended"], {"base_color": "#c9a04a", "roughness": 0.3})
        self.assertEqual(gold["deltas"], {"lightness": -12.0, "chroma": -11.9})
        self.assertEqual(gold["flags"], ["metal_too_dark"])
        self.assertNotIn("photo", gold, "raw statistics stay in the observation")
        self.assertNotIn("render", gold)
        self.assertEqual(app["hardware_area"]["hinge"], {"ratio": 0.43, "flag": "hardware_too_small"})
        self.assertTrue(app["reliable"])
        # the stored observation keeps the full block (measure_candidate returns it)
        self.assertIn("photo", session.store.revision("r0001")["observation"]["summary"]["appearance"]["materials"]["Gold hardware"])

    def test_absent_or_malformed_appearance_is_tolerated(self):
        self.assertIsNone(T.appearance_digest(None))
        self.assertIsNone(T.appearance_digest("broken"))
        d = T.appearance_digest({"materials": {"x": "not a dict", "y": {"role": 3, "flags": "nope"}}, "hardware_area": [], "flags": None, "reliable": False,
                                 "reason": "photo matte too small"})
        self.assertEqual(d["flags"], [])
        self.assertEqual(d["materials"], {"y": {"role": "3"}})
        self.assertFalse(d["reliable"])
        self.assertEqual(d["reason"], "photo matte too small")
        session, calls = self.session_with([{}])
        op, r1 = self.build(session)
        self.assertNotIn("appearance", r1.text["measurements"])


def failing_temple_export(origin=CLIP_ORIGIN) -> dict:
    return {"contract": {"ok": False, "failures": ["watertight_parts"],
                         "checks": {"watertight_parts": {"pass": False, "value": {"temple_L": False}, "limit": "closed"}},
                         "parts": {"temple_L": contract_part("temple", ok=False)}},
            "receipt": {"origin": {"method": "supplied", "origin_mm": list(origin)}}}


def temple_row(name, z0, z1, *, x=(-72.0, -58.0), failing=False, part="temple_L"):
    return {"object": name, "part": part, "triangles": 400, "boundary_edges": 6 if failing else 0, "nonmanifold_edges": 0, "misoriented_edges": 0,
            "bbox_mm": [[x[0], -3.0, z0], [x[1], 3.0, z1]]}


class Round9ContinuityPlane(ToolsCase):
    """Round 8 verifier MUST FIX: the behind-the-clip clause told the author to leave out ANY failing temple object wholly
    behind the DRAWN plane (-117.7 mm on run 2). The runtime continuity model (ar/src/render/continuity.ts:96-122) samples
    an arm cross-section at every station down to the REGISTERED cutoff (templeClipLocalZM -0.14 m: -142.7 mm in the
    author's coordinates on run 2) and throws 'An original posterior arm cross-section is missing.' where none exists,
    which is ar_continuity_failure, a gate. Only an object the continuity model does not need may be left out."""
    make_session = ErgonomicsLessons.make_session
    session_with = Round6Replies.session_with
    CLIP, CUT = -117.7, -142.7

    def hint(self, hints, name):
        return next(h for h in hints if h.startswith(name))

    def test_the_verifiers_tip_segment_must_be_repaired(self):
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Temple tip segment L", -156.0, -118.5, x=(-70.0, -60.0), failing=True)]
        for cut in (self.CUT, None):            # without the cutoff every z the object spans is taken as needed: never less safe
            with self.subTest(continuity_z_mm=cut):
                line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=cut), "Temple tip segment L")
                self.assertNotIn("leave it out", line)
                self.assertIn("never drawn", line)
                self.assertIn("carries the arm's continuity", line)
                self.assertIn("repair it", line)
                self.assertIn("ar_continuity_failure", line)
                self.assertIn("-117.7", line)
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Temple tip segment L")
        self.assertIn("-142.7", line, "the registered cutoff is named")
        self.assertIn("z -142.7 to -118.5 mm", line, "the uncovered span is named")

    def test_a_segment_other_geometry_covers_may_be_left_out(self):
        rows = [temple_row("Crystal temple L", -165.0, -5.0), temple_row("Temple tip segment L", -156.0, -118.5, x=(-70.0, -60.0), failing=True)]
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Temple tip segment L")
        self.assertIn("leave it out rather than repair it", line)
        self.assertIn("covers", line)
        self.assertNotIn("carries the arm's continuity", line)

    def test_a_coverer_inside_the_lateral_minimum_does_not_count(self):
        # continuity.ts skips every crossing at |x| <= lateralMinM (45 mm): geometry there carries no arm section
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Inner strut L", -165.0, -100.0, x=(-40.0, -30.0)),
                temple_row("Temple tip segment L", -156.0, -118.5, x=(-70.0, -60.0), failing=True)]
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Temple tip segment L")
        self.assertIn("carries the arm's continuity", line)
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Inner tip L", -156.0, -118.5, x=(-40.0, -30.0), failing=True)]
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Inner tip L")
        self.assertIn("leave it out", line, "an object inside |x| 45 mm carries nothing the continuity model samples")

    def test_an_object_wholly_behind_the_registered_cutoff_may_be_left_out(self):
        rows = [temple_row("Temple shaft L", -143.0, -5.0), temple_row("Tip plaque L", -160.0, -143.5, x=(-63.0, -61.0), failing=True)]
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Tip plaque L")
        self.assertIn("leave it out rather than repair it", line)
        self.assertIn("-142.7", line)
        self.assertIn("registered", line)

    def test_two_failing_segments_never_excuse_each_other(self):
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Tip segment A L", -156.0, -118.5, failing=True),
                temple_row("Tip segment B L", -150.0, -119.0, failing=True)]
        hints = T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT)
        for name in ("Tip segment A L", "Tip segment B L"):
            self.assertIn("carries the arm's continuity", self.hint(hints, name), "each could be left out only if the other stays")
        # a CLEAN segment spanning the whole needed range does excuse a failing one
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Tip segment A L", -156.0, -118.5, failing=True),
                temple_row("Tip core L", -150.0, -110.0)]
        hints = T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT)
        self.assertIn("leave it out", self.hint(hints, "Tip segment A L"))

    def test_the_other_temple_does_not_cover(self):
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Crystal temple R", -165.0, -5.0, x=(58.0, 72.0), part="temple_R"),
                temple_row("Temple tip segment L", -156.0, -118.5, x=(-70.0, -60.0), failing=True)]
        line = self.hint(T.repair_hints(failing_temple_export(), rows, clip_z_mm=self.CLIP, continuity_z_mm=self.CUT), "Temple tip segment L")
        self.assertIn("carries the arm's continuity", line)

    def test_the_block_names_both_planes_and_the_objects_that_carry_continuity(self):
        rows = [temple_row("Temple shaft L", -118.5, -5.0), temple_row("Temple tip segment L", -156.0, -118.5, x=(-70.0, -60.0)),
                temple_row("Tip plaque L", -160.0, -154.0, x=(-63.0, -61.0))]
        block = T.temple_clip_block({"receipt": {"origin": {"origin_mm": CLIP_ORIGIN}}}, rows)
        self.assertEqual((block["z_mm"], block["continuity_z_mm"]), (self.CLIP, self.CUT))
        self.assertEqual(block["objects_never_drawn"], ["Temple tip segment L", "Tip plaque L"])
        self.assertEqual(block["objects_carrying_continuity"], ["Temple tip segment L"])
        self.assertIn("-142.7", block["continuity_note"])
        self.assertIn("ar_continuity_failure", block["continuity_note"])
        # the recorded drawn plane moves; the registered continuity cutoff does not
        block = T.temple_clip_block({"receipt": {"origin": {"origin_mm": CLIP_ORIGIN}}}, rows,
                                    recorded={"z_m": -0.1, "z_m_max": -0.1, "renders": 4, "states": ["tracked/tracked"]})
        self.assertEqual((block["z_mm"], block["continuity_z_mm"]), (-102.7, self.CUT))

    def test_the_build_reply_passes_the_continuity_plane_to_the_repair_hints(self):
        session, calls = self.session_with([{"ar_timing": RUN2_TIMING}], contract_ok=False)
        with mock.patch.object(T, "repair_hints", wraps=T.repair_hints) as spy:
            op, r1 = self.build(session)
        self.assertEqual(spy.call_args.kwargs["clip_z_mm"], self.CLIP)
        self.assertEqual(spy.call_args.kwargs["continuity_z_mm"], self.CUT)
        self.assertEqual(r1.text["temple_clip"]["continuity_z_mm"], self.CUT)
        self.assertIn("continuity_note", r1.text["temple_clip"])

    def test_list_evidence_names_both_planes(self):
        session, calls = self.session_with([{"ar_timing": RUN2_TIMING}])
        op, r = self.run_tool(session, "call_0", "list_evidence", {"reference": None})
        rule = r.text["temple_clip"]["rule"]
        self.assertIn("115 mm", rule)
        self.assertIn("140 mm", rule)
        self.assertIn("-0.14 m", rule)
        self.assertIn("ar_continuity_failure", rule)
        self.build(session)
        op, r = self.run_tool(session, "call_2", "list_evidence", {"reference": None})
        clip = r.text["temple_clip"]
        self.assertEqual((clip["z_mm"], clip["continuity_z_mm"]), (self.CLIP, self.CUT))
        self.assertNotIn("objects_carrying_continuity", clip, "TIP_ROWS' inscription is covered by the crystal temple")

    def test_the_real_r0003_replay_keeps_the_inscriptions_left_out(self):
        src = PILOT2 / "r0003"
        files = {"export.json": "export.json", "result.host.json": "build/result.host.json", "parts.npz": "build/parts.npz", "materials.json": "build/materials.json",
                 "report.json": "observe/ar/report.json"}
        if not all((src / f).is_file() for f in files.values()):
            self.skipTest("test-pilot-002 evidence is not on this machine")
        for dst, f in files.items():           # the job folder is read-only: the replay works on copies
            shutil.copyfile(src / f, self.root / dst)
        export = json.loads((self.root / "export.json").read_text(encoding="utf-8"))
        inventory = json.loads((self.root / "result.host.json").read_text(encoding="utf-8"))["inventory"]
        clip = T.temple_clip_block(export, inventory, recorded=T.recorded_temple_clip(self.root / "report.json", export.get("sha256")))
        self.assertEqual((clip["z_mm"], clip["continuity_z_mm"]), (self.CLIP, self.CUT))
        self.assertNotIn("objects_carrying_continuity", clip, "the plaques and inscriptions lie behind -142.7 mm")
        hints = T.repair_hints(export, inventory, parts_npz=self.root / "parts.npz", materials_json=self.root / "materials.json",
                               clip_z_mm=clip["z_mm"], continuity_z_mm=clip["continuity_z_mm"])
        tips = [h for h in hints if h.startswith("Tip inscription")]
        self.assertEqual(len(tips), 2, hints)
        for h in tips:
            self.assertIn("leave it out rather than repair it", h)
            self.assertIn("-142.7", h)
            self.assertNotIn("carries the arm's continuity", h)

    def test_the_prompt_states_both_planes(self):
        flat = " ".join(runner.AUTHOR_PROMPT_PATH.read_text(encoding="utf-8").split())
        self.assertIn("origin's z minus 115 mm", flat)
        self.assertIn("origin's z minus 140 mm", flat)
        self.assertIn("ar_continuity_failure", flat)
        self.assertIn("objects_carrying_continuity", flat)
        self.assertNotIn("Geometry behind the plane only carries the arm's continuity: it is never drawn, so tip", flat,
                         "the old wording let every object behind the drawn plane go")
