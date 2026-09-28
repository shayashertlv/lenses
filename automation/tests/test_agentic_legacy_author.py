"""Regressions for the 2026-09-27 audit of the author drivers (F02, F04 author half, F06 both halves): the strict tool
call keeps every argument, module files cannot leave the turn folder by prefix, junction or symlink, package images
with one basename stay two files, and the transcript audit judges containment by path and says it is a heuristic.
No Blender, no network."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from modeler import audit_transcript
from modeler import author as mauthor
from modeler import author_astra


# --------------------------------------------------------------------------- F02: the strict tool call keeps every argument
def all_modules(**given):
    mods = {m: None for m in author_astra.MODULE_ORDER}
    mods.update(given)
    return mods


def valid_args(tool: str) -> dict:
    """One valid argument set per tool with every required property present (what the strict schema guarantees)."""
    return {"submit_program": {"modules": all_modules(frame="x = 1\n"), "base": "c0002", "rationale": "why", "expected_changes": ["e1"],
                               "deliver_if_valid": True},
            "request_views": {"candidate": "c0001", "rationale": "look", "views": [{"id": "hinge_r", "kind": "textured", "yaw": -60, "pitch": 5,
                                                                                    "roll": 0, "ortho": False, "px_per_mm": 8, "target": [1, 2, 3]}]},
            "finish": {"deliver": "c0003", "status_claim": "improved", "note": "done"}}[tool]


class ToolCallConversion(unittest.TestCase):
    def test_deliver_if_valid_passes_through(self):
        args = valid_args("submit_program")
        d = author_astra._tool_call_to_decision("submit_program", args)
        self.assertIs(d["deliver_if_valid"], True)
        self.assertEqual(d["modules"], {"frame": "x = 1\n"})
        self.assertEqual(d["base"], "c0002")
        d2 = author_astra._tool_call_to_decision("submit_program", dict(args, deliver_if_valid=False))
        self.assertIs(d2["deliver_if_valid"], False)

    def test_every_required_property_of_every_tool_is_consumed(self):
        """A property the strict schema requires must reach the decision: a newly required flag that the converter
        does not carry fails here instead of being defaulted silently."""
        for name, tool in author_astra.TOOLS.items():
            required = set(tool["parameters"]["required"])
            self.assertTrue(required, name)
            args = valid_args(name)
            self.assertTrue(required <= set(args), f"{name}: the fixture lacks {required - set(args)}")
            decision = author_astra._tool_call_to_decision(name, args)
            self.assertEqual(decision["decision"], name)
            self.assertTrue(required <= set(decision), f"{name}: required {required - set(decision)} did not reach the decision")
            for key in required - {"modules", "views"}:
                self.assertEqual(decision[key], args[key], f"{name}.{key} changed on the way")
            self.assertEqual(len(decision.get("views", [])), len(args.get("views", [])))

    def test_missing_required_argument_is_refused_not_defaulted(self):
        args = valid_args("submit_program")
        del args["deliver_if_valid"]
        with self.assertRaises(ValueError):
            author_astra._tool_call_to_decision("submit_program", args)
        with self.assertRaises(ValueError):
            author_astra._tool_call_to_decision("finish", {"deliver": "incumbent", "note": "n"})
        with self.assertRaises(ValueError):
            author_astra._tool_call_to_decision("no_such_tool", {})

    def test_live_deliver_on_submit_reaches_the_decision(self):
        """End to end through the driver with a mocked HTTP session: the response's deliver_if_valid: true is what
        the job sees (until 2026-09-27 the converter dropped it and validate_decision defaulted it to False)."""
        import numpy as np
        from PIL import Image

        class FakeResponse:
            status_code = 200

            def __init__(self, body):
                self._raw = json.dumps(body).encode("utf-8")

            def iter_content(self, chunk_size=65536):
                yield self._raw

            def close(self):
                pass

        class FakeSession:
            def __init__(self, body):
                self.body, self.posts = body, []

            def post(self, url, **kw):
                self.posts.append(kw)
                return FakeResponse(self.body)

        body = {"status": "completed", "model": "gpt-6-astra", "id": "resp_1", "usage": {"input_tokens": 10, "output_tokens": 5},
                "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "submit_program", "status": "completed",
                            "arguments": json.dumps({"modules": all_modules(materials="m = 1\n"), "base": "incumbent", "rationale": "r",
                                                     "expected_changes": [], "deliver_if_valid": True})}]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "photo.jpg"
            Image.fromarray(np.full((20, 30, 3), 200, np.uint8)).save(img)
            session = FakeSession(body)
            driver = author_astra.AstraAuthorDriver("sk-test-secret", budget_path=root / "ledger.json", maximum_calls=1, cap_usd=16.0, session=session)
            decision, _ = driver.decide(root / "turn-0000", {"task": "t"}, [{"id": "photo_front", "label": "front", "path": str(img)}], log=lambda *a: None)
            self.assertEqual(len(session.posts), 1)
            self.assertIs(decision["deliver_if_valid"], True)
            written = json.loads((root / "turn-0000" / "response.json").read_text(encoding="utf-8"))
            self.assertIs(written["deliver_if_valid"], True)


# --------------------------------------------------------------------------- F06: module files stay inside the turn folder
def make_junction(link: Path, target: Path) -> bool:
    """A directory junction (creatable without privileges on Windows); False when it cannot be made here."""
    if os.name != "nt":
        return False
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        try:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode != 0:
            return False
    return link.exists()


class ModuleFileContainment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.turn = self.root / "turn-0000"
        self.turn.mkdir()
        (self.turn / "frame.py").write_text("FRAME = 1\n", encoding="utf-8")
        (self.turn / "sub").mkdir()
        (self.turn / "sub" / "temples.py").write_text("TEMPLES = 2\n", encoding="utf-8")
        self.sibling = self.root / "turn-0000-other"
        self.sibling.mkdir()
        (self.sibling / "setup.py").write_text("x = 7\n", encoding="utf-8")
        (self.root / "outside.py").write_text("x = 9\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, **files):
        return mauthor.resolve_module_files({"decision": "submit_program", "modules": {k: {"file": v} for k, v in files.items()}}, self.turn)

    def test_file_inside_the_folder_resolves(self):
        d = self.submit(frame="frame.py", temples="sub/temples.py")
        self.assertEqual(d["modules"], {"frame": "FRAME = 1\n", "temples": "TEMPLES = 2\n"})
        # an absolute path INSIDE the folder and a plain string module are still accepted
        d2 = mauthor.resolve_module_files({"decision": "submit_program", "modules": {"frame": {"file": str(self.turn / "frame.py")}, "lenses": "L = 1\n"}}, self.turn)
        self.assertEqual(d2["modules"], {"frame": "FRAME = 1\n", "lenses": "L = 1\n"})

    def test_sibling_prefix_escape_is_refused(self):
        """turn-0000-other shares the string prefix of turn-0000; the prefix check let this pass until 2026-09-27."""
        with self.assertRaises(ValueError) as cm:
            self.submit(setup="../turn-0000-other/setup.py")
        self.assertIn("setup", str(cm.exception))
        with self.assertRaises(ValueError):
            self.submit(setup=str(self.sibling / "setup.py"))

    def test_dotdot_escape_is_refused(self):
        with self.assertRaises(ValueError):
            self.submit(setup="../outside.py")
        with self.assertRaises(ValueError):
            self.submit(setup="sub/../../outside.py")
        with self.assertRaises(ValueError):
            self.submit(setup=str(self.root / "outside.py"))

    def test_missing_file_and_directory_are_refused(self):
        with self.assertRaises(ValueError):
            self.submit(frame="nope.py")
        with self.assertRaises(ValueError):
            self.submit(frame="sub")
        with self.assertRaises(ValueError):
            mauthor.resolve_module_files({"decision": "submit_program", "modules": {"frame": {"file": "frame.py"}}}, self.root / "turn-9999")

    @unittest.skipUnless(os.name == "nt", "directory junctions are a Windows feature")
    def test_junction_inside_the_turn_folder_is_refused(self):
        """A junction under the turn folder redirects the read elsewhere; refused whichever way it points, and the
        one pointing outside is refused even before the reparse check because its resolved path is outside."""
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "setup.py").write_text("x = 11\n", encoding="utf-8")
        if not make_junction(self.turn / "junc_out", outside):
            self.skipTest("could not create a directory junction in this environment")
        with self.assertRaises(ValueError):
            self.submit(setup="junc_out/setup.py")
        if make_junction(self.turn / "junc_in", self.turn / "sub"):
            with self.assertRaises(ValueError) as cm:
                self.submit(temples="junc_in/temples.py")
            self.assertIn("junction", str(cm.exception).lower())
        # the real file beside the junction is still fine
        self.assertEqual(self.submit(temples="sub/temples.py")["modules"]["temples"], "TEMPLES = 2\n")

    def test_symlink_inside_the_turn_folder_is_refused(self):
        try:
            os.symlink(str(self.root / "outside.py"), str(self.turn / "link.py"))
        except (OSError, NotImplementedError, AttributeError) as e:
            self.skipTest(f"symlinks cannot be created here without privileges ({type(e).__name__}); the junction test covers the reparse rule")
        with self.assertRaises(ValueError):
            self.submit(setup="link.py")
        try:
            os.symlink(str(self.turn / "frame.py"), str(self.turn / "link_in.py"))
        except OSError:
            return
        with self.assertRaises(ValueError):
            self.submit(frame="link_in.py")


# --------------------------------------------------------------------------- F04 (author half): package images keep their identity
class PackageImageNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a").mkdir()
        (self.root / "b").mkdir()
        (self.root / "a" / "sheet_ar.png").write_bytes(b"first sheet")
        (self.root / "b" / "sheet_ar.png").write_bytes(b"second sheet")
        self.images = [{"id": "last_ar", "label": "last", "path": str(self.root / "a" / "sheet_ar.png"), "sha256": hashlib.sha256(b"first sheet").hexdigest()},
                       {"id": "incumbent_ar", "label": "incumbent", "path": str(self.root / "b" / "sheet_ar.png")}]     # sha256 absent: computed

    def tearDown(self):
        self.tmp.cleanup()

    def test_name_carries_id_and_content(self):
        names = [mauthor.package_image_name(im) for im in self.images]
        self.assertEqual(names[0], "last_ar__" + hashlib.sha256(b"first sheet").hexdigest()[:12] + ".png")
        self.assertEqual(names[1], "incumbent_ar__" + hashlib.sha256(b"second sheet").hexdigest()[:12] + ".png")
        self.assertNotEqual(names[0], names[1])
        self.assertNotIn("sha256", self.images[1], "the helper must not mutate the caller's image record")

    def run_package_driver(self, turn: Path, request: dict, images: list[dict]) -> dict:
        turn.mkdir(parents=True, exist_ok=True)
        resp = turn / "response.json"
        resp.write_text(json.dumps({"decision": "finish", "deliver": "incumbent", "status_claim": "best_effort", "note": "n"}), encoding="utf-8")
        os.utime(resp, (time.time() - 30, time.time() - 30))                 # quiet long enough to be read at once
        decision, _ = mauthor.PackageDriver(timeout_s=20, poll_s=0.01).decide(turn, request, images, log=lambda *a: None)
        return decision

    def test_two_images_with_one_basename_both_land_and_match_request_json(self):
        request = mauthor.build_request(job_meta={"job": "j"}, evidence={"product_id": "p", "scale": {}, "conventions": {}, "views": {}, "held_out": {}},
                                        state={"turn": 0}, images=self.images)
        turn = self.root / "turn-0000"
        decision = self.run_package_driver(turn, request, self.images)
        self.assertEqual(decision["decision"], "finish")
        written = json.loads((turn / "request.json").read_text(encoding="utf-8"))
        files = [im["file"] for im in written["images"]]
        self.assertEqual(len(set(files)), 2)
        on_disk = sorted(p.name for p in (turn / "images").iterdir())
        self.assertEqual(on_disk, sorted(files))
        self.assertEqual((turn / "images" / files[0]).read_bytes(), b"first sheet")
        self.assertEqual((turn / "images" / files[1]).read_bytes(), b"second sheet")
        self.assertNotIn("path", written["images"][0])

    def test_existing_copy_is_kept_when_identical_and_replaced_when_not(self):
        turn = self.root / "turn-0001"
        img_dir = turn / "images"
        img_dir.mkdir(parents=True)
        same = img_dir / mauthor.package_image_name(self.images[0])
        same.write_bytes(b"first sheet")
        old = time.time() - 5000
        os.utime(same, (old, old))
        stale = img_dir / mauthor.package_image_name(self.images[1])
        stale.write_bytes(b"an interrupted or foreign copy")
        self.run_package_driver(turn, {"task": "t"}, self.images)
        self.assertAlmostEqual(same.stat().st_mtime, old, delta=2, msg="an identical copy is left alone")
        self.assertEqual(stale.read_bytes(), b"second sheet", "a copy whose bytes differ is replaced, never skipped")
        self.assertEqual(len(list(img_dir.iterdir())), 2)


# --------------------------------------------------------------------------- F06 (audit half): containment by path, and an honest scope
class TranscriptAuditContainment(unittest.TestCase):
    def rows(self, *paths):
        return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": p}}]}} for p in paths]

    def test_sibling_folder_is_outside_inside_is_not_and_result_is_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "turn-0000"
            pkg.mkdir()
            sibling = root / "turn-0000-other"
            rows = self.rows(str(pkg / "request.json"), str(pkg / "images" / "x.png"), str(pkg).upper() + "\\README.md",
                             str(pkg / "images" / ".." / "response.json"))
            t = root / "clean.jsonl"
            t.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            res = audit_transcript.audit(t, pkg)
            self.assertTrue(res["clean"], res["outside_package"])
            self.assertIs(res["heuristic"], True)
            self.assertIsInstance(res["scope"], str)
            self.assertIn("cannot certify", res["scope"])
            rows += self.rows(str(sibling / "setup.py"), str(pkg / ".." / "turn-0000-other" / "frame.py"))
            t2 = root / "dirty.jsonl"
            t2.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            res2 = audit_transcript.audit(t2, pkg)
            self.assertFalse(res2["clean"])
            self.assertEqual(len(res2["outside_package"]), 2, res2["outside_package"])
            self.assertTrue(all("turn-0000-other" in o["path"] for o in res2["outside_package"]))
            self.assertIs(res2["heuristic"], True)

    def test_inside_package_helper(self):
        pkg = Path(r"C:\jobs\x\turns\turn-0000")
        self.assertTrue(audit_transcript.inside_package(r"c:\jobs\x\turns\turn-0000\request.json", pkg))
        self.assertTrue(audit_transcript.inside_package(r"C:/jobs/x/turns/turn-0000/images/a.png", pkg))
        self.assertFalse(audit_transcript.inside_package(r"c:\jobs\x\turns\turn-0000-other\setup.py", pkg))
        self.assertFalse(audit_transcript.inside_package(r"c:\jobs\x\turns\turn-0000\..\turn-0001\request.json", pkg))
        self.assertFalse(audit_transcript.inside_package(r"d:\jobs\x\turns\turn-0000\request.json", pkg))


if __name__ == "__main__":
    unittest.main()
