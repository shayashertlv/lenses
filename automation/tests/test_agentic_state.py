"""State-level tests of ``modeler.agentic``: the SQLite Store, the ArtifactStore and the crash matrix over the offline Session.

Every test works in a fresh folder under a short temp path (tests/test_agentic_support.py, ``lag-state``), uses the
ScriptedTransport or a fake worker only, and asserts what is observable from outside: rows read back through the
Store, files on disk, payloads the transport captured, job states. Nothing here talks to a network or to Docker.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from modeler.agentic import PACKAGE_PROTOCOL, SCHEMA_VERSION, config, demo, executor, runner
from modeler.agentic import tools as T
from modeler.agentic.artifacts import (AREA, AUTHOR_VISIBLE, HOST_ONLY, MAX_ARTIFACT_BYTES, SEALED, SYNTHETIC, AccessDenied, ArtifactError,
                                       ArtifactStore, contained, is_reparse_point)
from modeler.agentic.budget import Budget
from modeler.agentic.pricing import MODEL, Tariff
from modeler.agentic.responses import ENDPOINT, ScriptedTransport, canonical, sha256
from modeler.agentic.state import (JOB_STATES, TERMINAL_STATES, TRANSITIONS, LeaseError, StateError, Store, TransitionError, runner_identity,
                                   sha256_json)
from test_agentic_support import lag_root

AUTOMATION = Path(__file__).resolve().parents[1]
ROLE = runner.ROLE_AUTHOR


def _quiet(*_a, **_k) -> None:
    pass


class FakeClock:
    """An injectable clock: the Store and the Session read it through ``store.clock``."""

    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def job_record(**over) -> dict:
    rec = {"request": {"product_id": "state-test", "photos": []}, "policy": {"driver": "scripted", "worker": "fake"}, "fingerprints": {"test": "state"},
           "model": MODEL, "reasoning_effort": "high", "service_tier": "default", "endpoint": ENDPOINT, "tariff": Tariff.frozen().to_dict(),
           "cap_micro": 5_000_000, "inference_operation_cap": 12, "driver": "scripted", "worker": "fake"}
    rec.update(over)
    return rec


def png_bytes(colour=(200, 30, 30), size=(24, 16)) -> bytes:
    import io
    im = Image.new("RGB", size, colour)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make_junction(link: Path, target: Path) -> bool:
    """A Windows directory junction through cmd's mklink; False when it cannot be created here."""
    if os.name != "nt":
        return False
    try:
        p = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and is_reparse_point(link)


class TempCase(unittest.TestCase):
    """Fresh folders under the short lag-state root; stores closed and folders removed afterwards (junctions unlinked first)."""

    def setUp(self):
        self._dirs: list[Path] = []
        self._stores: list[Store] = []
        self._junctions: list[Path] = []

    def tearDown(self):
        for s in self._stores:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        for j in self._junctions:
            try:
                os.rmdir(j)
            except OSError:
                pass
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def fresh(self, prefix: str = "t") -> Path:
        d = Path(tempfile.mkdtemp(prefix=prefix + "-", dir=lag_root("state")))
        self._dirs.append(d)
        return d

    def track(self, store: Store) -> Store:
        self._stores.append(store)
        return store

    def new_store(self, clock=None, **over) -> Store:
        return self.track(Store.create(self.fresh("job") / "job", job_record(**over), clock=clock))

    def force_state(self, store: Store, state: str) -> None:
        """Put the job row into a state directly (test scaffolding: the transition table is the thing under test)."""
        store.conn.execute("UPDATE job SET state = ? WHERE id = 1", (state,))

    @staticmethod
    def rewrite_job_row(path: Path, sql: str, params: tuple = ()) -> None:
        """Edit the job row behind the Store's back with a connection that is closed again (sqlite3's context manager only commits)."""
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


# =========================================================================== Store: creation / opening
class StoreCreateOpenTest(TempCase):
    def test_create_refuses_non_empty_folder(self):
        folder = self.fresh("nonempty")
        (folder / "stray.txt").write_text("x")
        with self.assertRaises(StateError):
            Store.create(folder, job_record())
        self.assertFalse((folder / "job.sqlite3").exists())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["stray.txt"])

    def test_create_refuses_a_file_path(self):
        folder = self.fresh("file")
        f = folder / "notadir"
        f.write_text("x")
        with self.assertRaises(StateError):
            Store.create(f, job_record())

    def test_create_sets_pragmas_and_seeds_the_job_row(self):
        store = self.new_store()
        self.assertEqual(store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(store.conn.execute("PRAGMA synchronous").fetchone()[0], 2, "synchronous=FULL is 2")
        self.assertEqual(store.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        job = store.job()
        self.assertEqual(job["state"], "created")
        self.assertEqual(job["schema_version"], SCHEMA_VERSION)
        self.assertEqual(job["protocol"], PACKAGE_PROTOCOL)
        self.assertEqual(job["request_sha256"], sha256_json({"product_id": "state-test", "photos": []}))
        self.assertEqual(job["request"], {"product_id": "state-test", "photos": []})
        self.assertEqual(job["tariff"]["price_version"], Tariff.frozen().price_version)
        self.assertEqual(job["cap_micro"], 5_000_000)
        self.assertIsNone(job["lease_holder"])
        self.assertEqual(job["lease_fence"], 0)
        kinds = [e["kind"] for e in store.events()]
        self.assertEqual(kinds, ["job_created"])
        # a second connection sees the same WAL/FK configuration
        again = self.track(Store.open(store.job_dir))
        self.assertEqual(again.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(again.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        ro = self.track(Store.open(store.job_dir, readonly=True))
        self.assertEqual(ro.state(), "created")

    def test_create_requires_a_complete_job_record(self):
        folder = self.fresh("incomplete") / "job"
        rec = job_record()
        del rec["tariff"]
        with self.assertRaises(StateError):
            Store.create(folder, rec)
        with self.assertRaises(StateError):
            Store.open(folder)          # the rolled-back database has no job row

    def test_open_rejects_a_wrong_schema_version(self):
        store = self.new_store()
        path = store.path
        store.close()
        self.rewrite_job_row(path, "UPDATE job SET schema_version = ? WHERE id = 1", (SCHEMA_VERSION + 1,))
        with self.assertRaises(StateError) as cm:
            Store.open(path.parent)
        self.assertIn("migration required", str(cm.exception))

    def test_open_rejects_a_wrong_protocol(self):
        store = self.new_store()
        path = store.path
        store.close()
        self.rewrite_job_row(path, "UPDATE job SET protocol = 'someone_elses_v9' WHERE id = 1")
        with self.assertRaises(StateError):
            Store.open(path.parent)

    def test_open_needs_a_database(self):
        with self.assertRaises(StateError):
            Store.open(self.fresh("empty"))

    def test_failed_open_holds_no_database_handle(self):
        """A refused open must close the connection it made: on Windows an open handle keeps the job folder undeletable."""
        store = self.new_store()
        path = store.path
        store.close()
        self.rewrite_job_row(path, "UPDATE job SET protocol = 'someone_elses_v9' WHERE id = 1")
        with self.assertRaises(StateError) as cm:
            Store.open(path.parent)
        self.assertIn("migration required", str(cm.exception))
        path.unlink()                    # PermissionError (WinError 32) while the refused Store still holds its connection
        self.assertFalse(path.exists())
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [], "no -wal/-shm left behind either")

    def test_tx_rolls_back_nested_writes_on_error(self):
        store = self.new_store()
        with self.assertRaises(RuntimeError):
            with store.tx():
                store.event("inner_write", n=1)
                store.set_setting("k", 1)
                raise RuntimeError("boom")
        self.assertEqual(store.events("inner_write"), [])
        self.assertIsNone(store.setting("k"))
        with self.assertRaises(StateError):
            store.update_job(state="ready")        # the state column is not writable this way


# =========================================================================== Store: transitions
class TransitionTest(TempCase):
    def test_transition_table_is_closed_over_job_states(self):
        self.assertEqual(set(TRANSITIONS), set(JOB_STATES))
        for src, dsts in TRANSITIONS.items():
            self.assertTrue(dsts <= set(JOB_STATES), src)
            self.assertNotIn(src, dsts, f"{src} loops onto itself")

    def test_every_legal_transition_applies_and_is_journaled(self):
        store = self.new_store()
        applied = 0
        for src, dsts in TRANSITIONS.items():
            for dst in sorted(dsts):
                self.force_state(store, src)
                before = len(store.events("transition"))
                prev = store.transition(dst, f"{src}->{dst}", stop_reason=f"why_{dst}")
                self.assertEqual(prev, src)
                self.assertEqual(store.state(), dst)
                ev = store.events("transition")
                self.assertEqual(len(ev), before + 1)
                self.assertEqual(ev[-1]["data"], {"previous": src, "state": dst, "reason": f"{src}->{dst}"})
                self.assertEqual(store.job()["stop_reason"], f"why_{dst}")
                applied += 1
        self.assertGreater(applied, 50)

    def test_expected_state_guard(self):
        store = self.new_store()
        with self.assertRaises(TransitionError):
            store.transition("ready", "guarded", expected="ready_for_final")
        self.assertEqual(store.state(), "created")
        self.assertEqual(store.transition("ready", "guarded", expected=("evaluating", "created")), "created")
        self.assertEqual(store.state(), "ready")

    def test_illegal_transition_raises_and_leaves_the_state(self):
        store = self.new_store()
        with self.assertRaises(TransitionError):
            store.transition("delivered", "skip everything")
        self.assertEqual(store.state(), "created")
        self.assertEqual(store.events("transition"), [])
        self.assertIsNone(store.job()["stop_reason"])
        self.force_state(store, "delivered")
        with self.assertRaises(TransitionError):
            store.transition("ready", "resurrect")
        self.assertEqual(store.state(), "delivered")
        with self.assertRaises(TransitionError):
            store.transition("no_such_state", "typo")
        self.assertEqual(store.state(), "delivered")
        self.assertEqual(store.events("transition"), [])

    def test_terminal_states_have_no_exits(self):
        store = self.new_store()
        self.assertEqual(TERMINAL_STATES, {"delivered", "unresolved", "budget_exhausted", "cancelled", "failed"})
        for s in sorted(TERMINAL_STATES):
            self.assertEqual(TRANSITIONS[s], set(), s)
            self.force_state(store, s)
            for target in JOB_STATES:
                with self.assertRaises(TransitionError):
                    store.transition(target, "escape attempt")
                self.assertEqual(store.state(), s)
        self.assertNotIn("needs_attention", TERMINAL_STATES)
        self.assertIn("ready", TRANSITIONS["needs_attention"])


# =========================================================================== Store: lease
class LeaseTest(TempCase):
    def test_acquire_returns_increasing_fences_for_the_same_holder(self):
        store = self.new_store()
        fences = [store.acquire_lease("runner-a", 600) for _ in range(3)]
        self.assertEqual(fences, [1, 2, 3])
        job = store.job()
        self.assertEqual(job["lease_holder"], "runner-a")
        self.assertEqual(job["lease_fence"], 3)
        self.assertIsNotNone(job["lease_expires_utc"])
        acquired = store.events("lease_acquired")
        self.assertEqual([e["data"]["fence"] for e in acquired], [1, 2, 3])
        self.assertEqual(acquired[0]["data"]["previous_holder"], None)
        self.assertEqual(acquired[1]["data"]["previous_holder"], "runner-a")

    def test_live_foreign_holder_is_refused(self):
        clock = FakeClock()
        store = self.new_store(clock=clock)
        self.assertEqual(store.acquire_lease("runner-a", 600), 1)
        clock.advance(599)
        with self.assertRaises(LeaseError):
            store.acquire_lease("runner-b", 600)
        job = store.job()
        self.assertEqual((job["lease_holder"], job["lease_fence"]), ("runner-a", 1))
        self.assertEqual(len(store.events("lease_acquired")), 1)

    def test_expired_lease_can_be_taken_over(self):
        clock = FakeClock()
        store = self.new_store(clock=clock)
        store.acquire_lease("runner-a", 60)
        clock.advance(61)
        self.assertEqual(store.acquire_lease("runner-b", 600), 2)
        job = store.job()
        self.assertEqual((job["lease_holder"], job["lease_fence"]), ("runner-b", 2))
        self.assertEqual(store.events("lease_acquired")[-1]["data"]["previous_holder"], "runner-a")
        self.assertEqual(job["lease_expires_utc"], "2026-09-27T12:11:01+00:00")

    def test_require_fence_and_renew_reject_a_stale_holder(self):
        clock = FakeClock()
        store = self.new_store(clock=clock)
        store.acquire_lease("runner-a", 60)
        clock.advance(61)
        store.acquire_lease("runner-b", 600)
        expires = store.job()["lease_expires_utc"]
        with self.assertRaises(LeaseError):
            store.require_fence("runner-a", 1)
        with self.assertRaises(LeaseError):
            store.require_fence("runner-b", 1)        # right holder, old fence
        with self.assertRaises(LeaseError):
            store.renew_lease("runner-a", 1, 600)
        self.assertEqual(store.job()["lease_expires_utc"], expires, "a stale renew must not extend the lease")
        clock.advance(100)
        store.renew_lease("runner-b", 2, 600)
        self.assertEqual(store.job()["lease_expires_utc"], "2026-09-27T12:12:41+00:00")
        store.require_fence("runner-b", 2)
        store.release_lease("runner-a", 1)              # stale release: no-op
        self.assertEqual(store.job()["lease_holder"], "runner-b")
        store.release_lease("runner-b", 2)
        self.assertIsNone(store.job()["lease_holder"])
        self.assertEqual(store.job()["lease_fence"], 2, "the fence never goes backwards")
        with self.assertRaises(LeaseError):
            store.require_fence("runner-b", 2)
        self.assertEqual(store.acquire_lease("runner-c", 600), 3)


# =========================================================================== ArtifactStore
class ArtifactsTest(TempCase):
    def setUp(self):
        super().setUp()
        self.store = self.new_store()
        self.art = ArtifactStore(self.store)
        self.root = self.store.job_dir

    def test_add_bytes_layout_catalogue_and_read(self):
        data = png_bytes()
        digest = sha256(data)
        row = self.art.add_bytes(data, kind="photo", role=AUTHOR_VISIBLE, suffix=".PNG", label="front")
        self.assertEqual(row["id"], "img0001")
        self.assertEqual(row["rel_path"], f"artifacts/photo/img0001__{digest[:12]}.png")
        self.assertEqual(row["sha256"], digest)
        self.assertEqual(row["bytes"], len(data))
        self.assertEqual(row["media_type"], "image/png")
        self.assertEqual(row["role"], AUTHOR_VISIBLE)
        self.assertEqual(row["recipe"], {"width": 24, "height": 16})
        p = self.root / row["rel_path"]
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_bytes(), data)
        self.assertEqual([q.name for q in p.parent.iterdir()], [p.name], "no temporary sibling is left behind")
        got_row, got = self.art.read("img0001", allow_roles=(AUTHOR_VISIBLE,))
        self.assertEqual(got, data)
        self.assertEqual(got_row["id"], "img0001")
        # a second artifact of the same bytes gets its own id and path; nothing is replaced
        row2 = self.art.add_bytes(data, kind="photo", role=AUTHOR_VISIBLE, suffix=".png")
        self.assertEqual(row2["id"], "img0002")
        self.assertNotEqual(row2["rel_path"], row["rel_path"])
        self.assertTrue((self.root / row["rel_path"]).is_file() and (self.root / row2["rel_path"]).is_file())
        other = self.art.add_bytes(b"{}", kind="report", role=HOST_ONLY, suffix=".json", label="r")
        self.assertEqual(other["id"], "doc0003", "one sequence across kinds; the prefix names the kind")
        self.assertTrue(other["rel_path"].startswith("host/report/doc0003__"))
        self.assertEqual([a["id"] for a in self.store.artifacts()], ["img0001", "img0002", "doc0003"])
        self.assertEqual([a["id"] for a in self.store.artifacts(role=HOST_ONLY)], ["doc0003"])
        self.assertEqual(self.art.author_visible_ids(), ["img0001", "img0002"])

    def test_add_bytes_never_overwrites_an_existing_destination(self):
        data = b'{"a": 1}'
        dest = self.root / "host" / "report" / f"doc0001__{sha256(data)[:12]}.json"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"planted")
        with self.assertRaises(ArtifactError) as cm:
            self.art.add_bytes(data, kind="report", role=HOST_ONLY, suffix=".json")
        self.assertIn("never overwritten", str(cm.exception))
        self.assertEqual(dest.read_bytes(), b"planted")
        self.assertEqual(self.store.artifacts(), [], "the catalogue row was rolled back with the failed write")
        self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), [dest.name])

    def test_read_verifies_bytes_and_enforces_roles(self):
        data = png_bytes((255, 0, 255))
        sealed = self.art.add_bytes(data, kind="photo", role=SEALED, suffix=".png", label="held out")
        self.assertTrue(sealed["rel_path"].startswith("sealed/photo/"))
        with self.assertRaises(AccessDenied):
            self.art.read(sealed["id"], allow_roles=(AUTHOR_VISIBLE,))
        with self.assertRaises(AccessDenied):
            self.art.read(sealed["id"], allow_roles=(AUTHOR_VISIBLE, SYNTHETIC, HOST_ONLY))
        row, got = self.art.read(sealed["id"], allow_roles=(SEALED,))
        self.assertEqual(got, data)
        with self.assertRaises(ArtifactError):
            self.art.read("img9999", allow_roles=(AUTHOR_VISIBLE, SEALED))
        # tamper: same length, different bytes
        p = self.root / sealed["rel_path"]
        tampered = bytearray(data)
        tampered[-1] ^= 0xFF
        p.write_bytes(bytes(tampered))
        with self.assertRaises(ArtifactError) as cm:
            self.art.read(sealed["id"], allow_roles=(SEALED,))
        self.assertIn("changed on disk", str(cm.exception))
        self.assertEqual(self.store.artifact(sealed["id"])["sha256"], sha256(data), "the catalogue keeps the original digest")

    def test_parent_must_exist_and_lineage_is_recorded(self):
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"crop", kind="crop", role=AUTHOR_VISIBLE, suffix=".png", parent_id="img0042")
        self.assertEqual(self.store.artifacts(), [])
        parent = self.art.add_bytes(png_bytes(), kind="photo", role=AUTHOR_VISIBLE, suffix=".png")
        child = self.art.add_bytes(png_bytes((1, 2, 3), (8, 8)), kind="crop", role=AUTHOR_VISIBLE, suffix=".png", parent_id=parent["id"],
                                   revision_id=None, recipe={"crop_xyxy": [0, 0, 8, 8]})
        self.assertEqual(child["parent_id"], parent["id"])
        self.assertEqual(child["recipe"], {"crop_xyxy": [0, 0, 8, 8], "width": 8, "height": 8})
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert_artifact(kind="crop", role=AUTHOR_VISIBLE, rel_path="artifacts/crop/x.png", sha256="0" * 64, media_type="image/png",
                                       nbytes=1, parent_id="img0042")

    def test_synthetic_requires_the_synthetic_area_and_kinds_roles_sizes_are_checked(self):
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"x", kind="render", role=AUTHOR_VISIBLE, suffix=".png", synthetic=True)
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"x", kind="render", role=HOST_ONLY, suffix=".png", synthetic=True)
        row = self.art.add_bytes(b"x", kind="render", role=SYNTHETIC, suffix=".png", synthetic=True)
        self.assertTrue(row["rel_path"].startswith("synthetic/render/img0001__"))
        self.assertEqual(row["synthetic"], 1)
        self.assertEqual(AREA[SYNTHETIC], "synthetic")
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"x", kind="render", role="owner", suffix=".png")
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"x", kind="movie", role=AUTHOR_VISIBLE, suffix=".mp4")
        with self.assertRaises(ArtifactError):
            self.art.add_bytes(b"\0" * (MAX_ARTIFACT_BYTES + 1), kind="other", role=HOST_ONLY, suffix=".bin")
        self.assertEqual([a["id"] for a in self.store.artifacts()], ["img0001"])

    def test_path_of_refuses_a_rel_path_that_escapes_the_job_folder(self):
        row = self.art.add_bytes(b"hello", kind="log", role=HOST_ONLY, suffix=".log")
        self.assertEqual(self.art.path_of(row["id"]), self.root / row["rel_path"])
        for bad in ("../x", "host/../../x.log", "C:/Windows/notepad.exe"):
            self.store.conn.execute("UPDATE artifacts SET rel_path = ? WHERE id = ?", (bad, row["id"]))
            with self.assertRaises(ArtifactError, msg=bad) as cm:
                self.art.path_of(row["id"])
            self.assertIn("escapes", str(cm.exception))
            with self.assertRaises(ArtifactError, msg=bad):
                self.art.read(row["id"], allow_roles=(HOST_ONLY,))
        with self.assertRaises(ArtifactError):
            self.art.path_of("log9999")

    def test_contained_rejects_a_junction_component(self):
        root = self.fresh("junction")
        inside = root / "inside"
        inside.mkdir()
        (inside / "f.txt").write_text("f")
        outside = self.fresh("junction-target")
        (outside / "g.txt").write_text("g")
        link_out = root / "jout"
        if not make_junction(link_out, outside):
            self.skipTest("cannot create a directory junction here")
        self._junctions.append(link_out)
        self.assertTrue(is_reparse_point(link_out))
        self.assertFalse(is_reparse_point(inside))
        self.assertTrue(contained(inside / "f.txt", root))
        self.assertFalse(contained(link_out / "g.txt", root), "a junction leading outside the root is an escape")
        self.assertFalse(contained(link_out, root))
        self.assertFalse(contained(root / ".." / "x", root))
        # the docstring promises that no component below root is a reparse point, wherever the junction points
        link_in = root / "jin"
        self.assertTrue(make_junction(link_in, inside))
        self._junctions.append(link_in)
        self.assertFalse(contained(link_in / "f.txt", root), "a junction component below root is refused even when its target is inside")
        self.assertFalse(contained(link_in, root))
        self.assertTrue((inside / "f.txt").exists() and (outside / "g.txt").exists())

    def test_read_refuses_an_artifact_folder_turned_into_a_junction(self):
        row = self.art.add_bytes(b"log bytes", kind="log", role=HOST_ONLY, suffix=".log")
        area = self.root / "host" / "log"
        moved = self.fresh("moved-log")
        for p in area.iterdir():
            shutil.move(str(p), str(moved / p.name))
        area.rmdir()
        if not make_junction(area, moved):
            self.skipTest("cannot create a directory junction here")
        self._junctions.append(area)
        self.assertEqual((self.root / row["rel_path"]).read_bytes(), b"log bytes", "the bytes are reachable through the junction")
        with self.assertRaises(ArtifactError):
            self.art.read(row["id"], allow_roles=(HOST_ONLY,))
        with self.assertRaises(ArtifactError):
            self.art.path_of(row["id"])

    def test_verify_all_reports_missing_mismatch_and_orphans_without_deleting(self):
        a = self.art.add_bytes(b"one", kind="log", role=HOST_ONLY, suffix=".log")
        b = self.art.add_bytes(b"two", kind="log", role=HOST_ONLY, suffix=".log")
        c = self.art.add_bytes(png_bytes(), kind="render", role=SYNTHETIC, suffix=".png", synthetic=True)
        clean = self.art.verify_all()
        self.assertEqual(clean, {"problems": [], "orphans": [], "ok": True})
        (self.root / a["rel_path"]).unlink()
        (self.root / b["rel_path"]).write_bytes(b"TWO")
        orphan1 = self.root / "artifacts" / "photo" / "stray.png"
        orphan1.parent.mkdir(parents=True)
        orphan1.write_bytes(b"stray")
        orphan2 = self.root / "sealed" / "deep" / "er" / "leftover.bin"
        orphan2.parent.mkdir(parents=True)
        orphan2.write_bytes(b"leftover")
        report = self.art.verify_all()
        self.assertFalse(report["ok"])
        self.assertEqual(report["problems"], [{"artifact": a["id"], "problem": "missing", "path": a["rel_path"]},
                                              {"artifact": b["id"], "problem": "sha256_mismatch", "path": b["rel_path"]}])
        self.assertEqual(report["orphans"], ["artifacts/photo/stray.png", "sealed/deep/er/leftover.bin"])
        self.assertTrue(orphan1.is_file() and orphan2.is_file(), "orphans are reported, never deleted")
        self.assertEqual((self.root / b["rel_path"]).read_bytes(), b"TWO", "a mismatched file is left for audit")
        self.assertTrue((self.root / c["rel_path"]).is_file())
        # the job database itself is not an artifact area, so it is not an orphan
        self.assertNotIn("job.sqlite3", " ".join(report["orphans"]))

    def test_add_file_refuses_non_regular_paths(self):
        folder = self.fresh("addfile")
        with self.assertRaises(ArtifactError):
            self.art.add_file(folder, kind="other", role=HOST_ONLY)
        f = folder / "m.md"
        f.write_text("# hi", encoding="utf-8")
        row = self.art.add_file(f, kind="report", role=HOST_ONLY, label="md")
        self.assertTrue(row["rel_path"].endswith(".md"))
        self.assertEqual(row["media_type"], "text/markdown")
        with self.assertRaises((ArtifactError, OSError)):
            self.art.add_file(folder / "missing.txt", kind="other", role=HOST_ONLY)
        self.assertEqual([a["id"] for a in self.store.artifacts()], [row["id"]])


# =========================================================================== conversation items
class ConversationTest(TempCase):
    def test_append_needs_an_epoch_and_new_epoch_increments(self):
        store = self.new_store()
        self.assertEqual(store.epoch(ROLE), 0)
        with self.assertRaises(StateError):
            store.append_items(ROLE, [{"type": "message", "role": "user", "content": []}])
        self.assertEqual(store.new_epoch(ROLE, "initial"), 1)
        self.assertEqual(store.new_epoch(ROLE, "compaction", compact_request_id="q0009"), 2)
        self.assertEqual(store.epoch(ROLE), 2)
        self.assertEqual(store.epoch("critic"), 0)
        self.assertEqual(store.new_epoch("critic", "fresh"), 1)
        self.assertEqual([(e["data"]["role"], e["data"]["epoch"]) for e in store.events("epoch")], [(ROLE, 1), (ROLE, 2), ("critic", 1)])
        self.assertEqual(store.window(ROLE), [])

    def test_items_are_verbatim_and_window_is_the_latest_epoch(self):
        store = self.new_store()
        store.new_epoch(ROLE, "initial")
        opaque = "gAAAAB-opaque/" + "Zz9+" * 64 + "=="
        a = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "héllo — ünïcode ✓"}, {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"}]},
             {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": opaque, "unknown_future_field": {"nested": [1, 2.5, None, True, "x"]}},
             {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "list_evidence", "arguments": "{}", "status": "completed"}]
        seqs = store.append_items(ROLE, a, request_id="q0001")
        self.assertEqual(seqs, [1, 2, 3])
        rows = store.items(ROLE)
        self.assertEqual([r["kind"] for r in rows], ["message", "reasoning", "function_call"], "kind is the item type when present")
        self.assertEqual([r["item"] for r in rows], a)
        self.assertEqual(rows[1]["item"]["encrypted_content"], opaque)
        self.assertEqual({r["request_id"] for r in rows}, {"q0001"})
        self.assertEqual(store.window(ROLE), a)
        h1 = store.window_sha256(ROLE)
        out = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
        self.assertEqual(store.append_items(ROLE, [out]), [4])
        self.assertEqual(store.window(ROLE), a + [out])
        self.assertNotEqual(store.window_sha256(ROLE), h1)
        # a failing append writes nothing of its batch
        with self.assertRaises(StateError):
            store.append_items(ROLE, [{"type": "message", "role": "user", "content": []}, "not an item"])
        self.assertEqual(len(store.items(ROLE)), 4)
        # a new epoch starts an empty window; the old epoch stays readable
        self.assertEqual(store.new_epoch(ROLE, "compaction"), 2)
        self.assertEqual(store.window(ROLE), [])
        b = [{"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque-compaction"}, {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "checkpoint"}]}]
        self.assertEqual(store.append_items(ROLE, b, epoch=2), [1, 2])
        self.assertEqual(store.window(ROLE), b)
        self.assertEqual([r["item"] for r in store.items(ROLE, epoch=1)], a + [out])
        self.assertEqual([r["kind"] for r in store.items(ROLE)], ["compaction", "message"])
        self.assertEqual(store.window("critic"), [])
        # a type-less item is classified by its role, and by nothing at all as unknown; both are still stored verbatim
        odd = [{"role": "user", "content": "typeless"}, {"payload": 1}]
        store.append_items(ROLE, odd)
        self.assertEqual([r["kind"] for r in store.items(ROLE)][-2:], ["message:user", "unknown"])
        self.assertEqual(store.window(ROLE)[-2:], odd)


# =========================================================================== queues and ledgers
class QueuesAndLedgersTest(TempCase):
    def test_observation_queue_transitions_and_request_ids(self):
        store = self.new_store()
        art = ArtifactStore(store)
        a = art.add_bytes(png_bytes(), kind="render", role=SYNTHETIC, suffix=".png", synthetic=True, revision_id="r0001")
        b = art.add_bytes(png_bytes((9, 9, 9)), kind="render", role=SYNTHETIC, suffix=".png", synthetic=True, revision_id="r0002")
        row = store.enqueue_observation(a["id"], revision_id="r0001", operation_id="op0001", required=True)
        self.assertEqual((row["state"], row["required"], row["revision_id"], row["operation_id"]), ("pending", 1, "r0001", "op0001"))
        store.enqueue_observation(b["id"], revision_id="r0002", operation_id=None, required=False)
        self.assertEqual([o["artifact_id"] for o in store.observations(state="pending")], [a["id"], b["id"]])
        self.assertEqual([o["artifact_id"] for o in store.observations(revision_id="r0002")], [b["id"]])
        store.set_observation(a["id"], "included")
        self.assertIsNone(store.observation(a["id"])["included_request_id"])
        store.set_observation(a["id"], "included", included_request_id="q0003")
        self.assertEqual(store.observation(a["id"])["included_request_id"], "q0003")
        store.set_observation(a["id"], "acknowledged", acknowledged_request_id="q0003")
        o = store.observation(a["id"])
        self.assertEqual((o["state"], o["included_request_id"], o["acknowledged_request_id"]), ("acknowledged", "q0003", "q0003"))
        store.set_observation(a["id"], "pending")
        o = store.observation(a["id"])
        self.assertEqual((o["state"], o["included_request_id"], o["acknowledged_request_id"]), ("pending", "q0003", "q0003"), "ids are kept (COALESCE)")
        with self.assertRaises(StateError):
            store.set_observation(a["id"], "seen")
        with self.assertRaises(StateError):
            store.set_observation("img9999", "included")
        with self.assertRaises(sqlite3.IntegrityError):
            store.enqueue_observation(a["id"], revision_id="r0001", operation_id=None, required=False)      # one queue row per artifact
        with self.assertRaises(sqlite3.IntegrityError):
            store.enqueue_observation("img9999", revision_id=None, operation_id=None, required=False)       # the artifact must exist (FK)
        self.assertEqual(len(store.observations()), 2)

    def test_reservation_round_trip(self):
        store = self.new_store()
        r = store.insert_reservation(role="author", purpose="author", request_id=None, reserved_micro=123_456, price_version="pv", counts_operation=True)
        self.assertEqual((r["id"], r["state"], r["reserved_micro"], r["liability_micro"], r["settled_micro"], r["counts_operation"]), ("b0001", "held", 123_456, 0, None, 1))
        store.update_reservation("b0001", state="settled", settled_micro=20_050, request_id="q0001", usage_json={"input_tokens": 1200, "output_tokens": 300},
                                 settled_utc=store.now(), reason="settled from response usage")
        r = store.reservation("b0001")
        self.assertEqual((r["state"], r["settled_micro"], r["request_id"], r["usage"]), ("settled", 20_050, "q0001", {"input_tokens": 1200, "output_tokens": 300}))
        self.assertEqual(store.reservations(state="held"), [])
        self.assertEqual([x["id"] for x in store.reservations()], ["b0001"])
        with self.assertRaises(StateError):
            store.update_reservation("b0001", state="paid")
        with self.assertRaises(StateError):
            store.update_reservation("b0001", reserved_micro=1)
        with self.assertRaises(StateError):
            store.update_reservation("b0042", state="released")
        r2 = store.insert_reservation(role="critic", purpose="critic", request_id="q0002", reserved_micro=1, price_version="pv", counts_operation=False)
        self.assertEqual((r2["id"], r2["counts_operation"]), ("b0002", 0))

    def test_verdicts_are_append_only_round_trips(self):
        store = self.new_store()
        v1 = store.append_verdict(kind="critic", revision_id="r0001", asset_sha256=None, verdict="defects", bindings={"request": "q0004"}, record={"summary": "s", "defects": []})
        v2 = store.append_verdict(kind="final", revision_id="r0002", asset_sha256="ab" * 32, verdict="reject", bindings={"images": ["img0003"]}, record={"overall": "reject"})
        v3 = store.append_verdict(kind="owner", revision_id=None, asset_sha256="ab" * 32, verdict="accept", bindings={}, record={"medium": "iphone"})
        self.assertEqual([v["id"] for v in (v1, v2, v3)], [1, 2, 3])
        self.assertEqual([v["kind"] for v in store.verdicts()], ["critic", "final", "owner"])
        self.assertEqual(store.verdicts(kind="final")[0]["record"], {"overall": "reject"})
        self.assertEqual(store.verdicts(kind="final")[0]["bindings"], {"images": ["img0003"]})
        self.assertEqual(store.verdicts(kind="critic")[0]["revision_id"], "r0001")
        self.assertEqual(store.verdicts(kind="none"), [])

    def test_events_and_settings_round_trip(self):
        store = self.new_store()
        store.event("alpha", n=1, nested={"k": [1, 2, {"z": None}]}, text="ünïcode")
        store.event("beta")
        store.event("alpha", n=2)
        self.assertEqual([e["kind"] for e in store.events()], ["job_created", "alpha", "beta", "alpha"])
        alphas = store.events("alpha")
        self.assertEqual([e["data"] for e in alphas], [{"n": 1, "nested": {"k": [1, 2, {"z": None}]}, "text": "ünïcode"}, {"n": 2}])
        self.assertTrue(all("data_json" not in e and e["utc"] for e in alphas))
        self.assertIsNone(store.setting("missing"))
        self.assertEqual(store.setting("missing", "dflt"), "dflt")
        store.set_setting("pending_delivery", {"revision": "r0001", "auto": True})
        self.assertEqual(store.setting("pending_delivery"), {"revision": "r0001", "auto": True})
        store.set_setting("pending_delivery", None)
        self.assertIsNone(store.setting("pending_delivery", "dflt"), "an explicit null is stored, not the default")
        store.set_setting("consecutive_incomplete", 3)
        self.assertEqual(store.setting("consecutive_incomplete"), 3)

    def test_requests_operations_and_revisions_round_trip(self):
        store = self.new_store()
        with self.assertRaises(StateError):
            store.insert_request(role="author", purpose="author", epoch=1, input_sha256=None, reservation_id=None, state="flying")
        q = store.insert_request(role="author", purpose="author", epoch=1, input_sha256="ab" * 32, reservation_id="b0001", input_token_count=17)
        self.assertEqual((q["id"], q["state"], q["input_token_count"], q["reservation_id"]), ("q0001", "prepared", 17, "b0001"))
        store.update_request("q0001", state="sent", sent_utc=store.now())
        store.update_request("q0001", state="completed", usage_json={"input_tokens": 3, "output_tokens": 4}, http_status=200, provider_response_id="resp_x")
        q = store.request("q0001")
        self.assertEqual((q["state"], q["usage"], q["http_status"], q["provider_response_id"]), ("completed", {"input_tokens": 3, "output_tokens": 4}, 200, "resp_x"))
        with self.assertRaises(StateError):
            store.update_request("q0001", state="lost")
        with self.assertRaises(StateError):
            store.update_request("q0001", role="critic")
        with self.assertRaises(StateError):
            store.update_request("q0099", state="failed")
        store.insert_request(role="critic", purpose="critic", epoch=1, input_sha256=None, reservation_id=None)
        self.assertEqual([r["id"] for r in store.requests(state="prepared")], ["q0002"])
        self.assertEqual([r["id"] for r in store.requests(role="author")], ["q0001"])
        # operations: attempts count per call_id
        op1 = store.insert_operation(call_id="call_1", request_id="q0001", tool_name="list_evidence", schema_version="v", args={"a": 1}, source_revision_id=None, fence=3)
        self.assertEqual((op1["id"], op1["attempt"], op1["state"], op1["args"], op1["fence"], op1["output_committed"]), ("op0001", 1, "pending", {"a": 1}, 3, 0))
        op2 = store.insert_operation(call_id="call_1", request_id="q0001", tool_name="list_evidence", schema_version="v", args={"a": 1}, source_revision_id=None, fence=3)
        self.assertEqual((op2["id"], op2["attempt"]), ("op0002", 2))
        self.assertEqual([o["id"] for o in store.operations(call_id="call_1")], ["op0001", "op0002"])
        store.update_operation("op0001", state="completed", result_json={"ok": True}, output_committed=1)
        self.assertEqual(store.operation("op0001")["result"], {"ok": True})
        self.assertEqual([o["id"] for o in store.operations(state="pending")], ["op0002"])
        with self.assertRaises(StateError):
            store.update_operation("op0002", state="done")
        with self.assertRaises(StateError):
            store.update_operation("op0002", call_id="other")
        with self.assertRaises(sqlite3.IntegrityError):
            store.insert_operation(call_id="call_2", request_id="q0404", tool_name="x", schema_version="v", args={}, source_revision_id=None, fence=None)
        # revisions
        rev = store.insert_revision(parent_id=None, program_set_sha256="0" * 64, modules={"frame": {"sha256": "1" * 64}}, rationale="first", synthetic=True)
        self.assertEqual((rev["id"], rev["state"], rev["synthetic"], rev["modules"]), ("r0001", "created", 1, {"frame": {"sha256": "1" * 64}}))
        store.update_revision("r0001", state="building", operation_id="op0001")
        store.update_revision("r0001", state="compatible", compatibility_json={"compatible": True}, observation_json={"summary": {}})
        r = store.revision("r0001")
        self.assertEqual((r["state"], r["compatibility"], r["observation"], r["operation_id"]), ("compatible", {"compatible": True}, {"summary": {}}, "op0001"))
        with self.assertRaises(StateError):
            store.update_revision("r0001", state="shipped")
        with self.assertRaises(StateError):
            store.update_revision("r0001", parent_id="r0000")
        with self.assertRaises(StateError):
            store.update_revision("r0009", state="built")
        with self.assertRaises(sqlite3.IntegrityError):
            store.insert_revision(parent_id="r0009", program_set_sha256="0" * 64, modules={})
        self.assertEqual([x["id"] for x in store.revisions()], ["r0001"])

    def test_runner_identity_is_unique_ish(self):
        ids = {runner_identity() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for i in ids:
            self.assertTrue(i.startswith(f"{os.getpid()}@"), i)
            self.assertRegex(i, r"^\d+@.+:[0-9a-f]{8}$")


# =========================================================================== temp roots of the agentic suites (T3 / T4 / H04)
class TempRoots(unittest.TestCase):
    """Every test_agentic_* suite takes its folders from tests/test_agentic_support.py: LENSES_TEST_TMP or the system temp
    folder (never a hard-coded user profile: the repository is public), created on first use, not at import; a folder that
    cannot be removed is reported."""

    def test_the_root_follows_lenses_test_tmp_and_the_per_suite_override(self):
        import test_agentic_support as support
        base = Path(tempfile.mkdtemp(prefix="roots-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        with mock.patch.dict(os.environ, {"LENSES_TEST_TMP": str(base)}):
            self.assertEqual(support.lag_root("x"), base / "lag-x")
            self.assertTrue((base / "lag-x").is_dir())
            with mock.patch.dict(os.environ, {"LAG_X_TMP": str(base / "own")}):
                self.assertEqual(support.lag_root("x", env="LAG_X_TMP"), base / "own")
            d = support.fresh_dir(None, "y", "p-")
            self.assertEqual((d.parent, d.name[:2]), (base / "lag-y", "p-"))
        with mock.patch.dict(os.environ, {"LENSES_TEST_TMP": ""}):
            self.assertEqual(support.short_tmp(), Path(tempfile.gettempdir()))

    def test_fresh_dir_removes_its_folder_at_cleanup_and_reports_what_it_cannot(self):
        import test_agentic_support as support
        with mock.patch.dict(os.environ, {"LENSES_TEST_TMP": tempfile.gettempdir()}):
            inner = unittest.TestCase()
            d = support.fresh_dir(inner, "support-check", "c-")
            (d / "sub").mkdir()
            (d / "sub" / "f.txt").write_text("x", encoding="utf-8")
            inner.doCleanups()
        self.assertFalse(d.exists())
        keep = Path(tempfile.mkdtemp(prefix="held-"))
        self.addCleanup(shutil.rmtree, keep, ignore_errors=True)
        # an rmtree that cannot remove anything (a handle held open on Windows)
        with mock.patch.object(support.shutil, "rmtree", side_effect=lambda p, onexc=None, onerror=None: (onexc or onerror)(os.unlink, str(p), OSError("held"))):
            with self.assertWarns(support.LeftoverWarning):
                self.assertEqual(support.rmtree_reporting(keep), [str(keep)])
            with mock.patch.dict(os.environ, {"LENSES_TEST_STRICT_CLEANUP": "1"}), self.assertRaises(AssertionError):
                support.rmtree_reporting(keep)

    def test_no_agentic_test_hard_codes_a_user_profile_or_makes_folders_at_import(self):
        here = Path(__file__).resolve().parent
        for p in sorted(here.glob("test_agentic_*.py")):
            text = p.read_text(encoding="utf-8")
            with self.subTest(file=p.name):
                self.assertNotRegex(text, r"[A-Za-z]:[/\\]Users[/\\][^/\\\"']+[/\\]AppData", "a hard-coded user profile temp path")
                top = [ln for ln in text.splitlines() if ln and not ln[0].isspace()]
                self.assertEqual([ln for ln in top if ".mkdir(" in ln or "mkdtemp(" in ln], [], "a folder made at import time")


# =========================================================================== two processes
WRITER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from modeler.agentic.state import Store
store = Store.open(Path(sys.argv[2]))
go = Path(sys.argv[3])
deadline = time.time() + 30
while not go.exists():
    if time.time() > deadline:
        raise SystemExit(3)
    time.sleep(0.002)
for i in range(20):
    store.event("concurrent", writer=sys.argv[4], i=i)
store.close()
"""


class ConcurrencyTest(TempCase):
    def test_two_processes_write_events_without_corruption(self):
        store = self.new_store()
        go = store.job_dir.parent / "go"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        procs = [subprocess.Popen([sys.executable, "-B", "-c", WRITER, str(AUTOMATION), str(store.job_dir), str(go), tag], cwd=str(AUTOMATION), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for tag in ("p1", "p2")]
        go.write_text("go")
        outs = [p.communicate(timeout=90) for p in procs]
        for p, (out, err) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, err)
        rows = store.events("concurrent")
        self.assertEqual(len(rows), 40)
        for tag in ("p1", "p2"):
            self.assertEqual(sorted(r["data"]["i"] for r in rows if r["data"]["writer"] == tag), list(range(20)))
        self.assertEqual(store.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 41)


# =========================================================================== crash matrix over the offline Session
USAGE = demo._resp("call_0", "list_evidence", {}, rs="rs_0")["usage"]
SETTLE_MICRO = Tariff.frozen().settle_micro(USAGE)


class LeaseStealingWorker(executor.FakeWorker):
    """A fake worker whose run outlives the lease: while the runner waits, time passes and another runner takes the job."""

    def __init__(self, intruder_store: Store, clock: FakeClock):
        super().__init__({})
        self.intruder_store = intruder_store
        self.clock = clock
        self.stolen_fence = None

    def await_outcome(self, identity, *, deadline_s, cancel_check=None):
        self.clock.advance(runner.LEASE_TTL_S + 60)
        self.stolen_fence = self.intruder_store.acquire_lease("intruder@elsewhere:00000000", 600)
        return super().await_outcome(identity, deadline_s=deadline_s, cancel_check=cancel_check)


class CrashMatrixTest(TempCase):
    """Inject a crash-shaped state into a live offline session, then open a NEW Session over the same job folder."""

    def policy(self) -> dict:
        return config.build_policy(owner_review=False, driver="scripted", worker="fake", budget_usd="5", max_inference_requests=12, max_output_tokens=4000, max_revisions=4,
                                   max_worker_seconds=120, wall_minutes=30, images_per_request=6, intake="synthetic", critic="scripted", final_evaluator="scripted",
                                   ar=False)

    def new_session(self, clock: FakeClock, steps=(), scenario=None) -> runner.Session:
        root = self.fresh("crash")
        request = demo.demo_request(root / "inputs")
        translated = config.translate_request(request, root / "inputs")
        session = runner.Session.create(root / "job", translated=translated, policy=self.policy(), fingerprints={"test": "state"},
                                        worker=executor.FakeWorker(scenario or demo.demo_fake_scenario()), transport=ScriptedTransport(list(steps)),
                                        worker_config=None, log=_quiet, clock=clock)
        self.track(session.store)
        self.assertEqual(session.store.state(), "ready")
        return session

    def crash(self, session: runner.Session) -> Path:
        """The process dies: the lease is NOT released, the connection just goes away."""
        job_dir = session.store.job_dir
        session.store.close()
        return job_dir

    def resume(self, job_dir: Path, clock: FakeClock, steps=(), worker=None) -> runner.Session:
        clock.advance(runner.LEASE_TTL_S + 1)            # the dead runner's lease has expired; a new runner may take over
        store = self.track(Store.open(job_dir, clock=clock))
        return runner.Session(store, transport=ScriptedTransport(list(steps)), worker=worker or executor.FakeWorker(), log=_quiet)

    def reserve_and_prepare(self, session: runner.Session, *, purpose: str = "author") -> tuple[dict, dict]:
        res = session.budget.reserve(role=ROLE, purpose=purpose, input_tokens=5000, max_output_tokens=4000)
        req = session.store.insert_request(role=ROLE, purpose=purpose, epoch=session.store.epoch(ROLE), input_sha256="ab" * 32, reservation_id=res["id"],
                                           input_token_count=5000)
        session.budget.bind_request(res["id"], req["id"])
        return res, req

    def mark_sent(self, session: runner.Session, req: dict, *, response_body: dict | None = None) -> None:
        rdir = session.store.job_dir / "host" / "requests" / req["id"]
        payload = canonical({"model": MODEL, "input": session.store.window(ROLE)})
        runner.atomic_write(rdir / "payload.json", payload)
        fields = {"payload_sha256": sha256(payload), "payload_path": str(rdir / "payload.json"), "state": "sent", "sent_utc": session.store.now()}
        if response_body is not None:
            raw = canonical(response_body)
            runner.atomic_write(rdir / "response.json", raw)
            fields.update(response_path=str(rdir / "response.json"), response_sha256=sha256(raw), http_status=200)
        session.store.update_request(req["id"], **fields)

    def outputs_for(self, store: Store, call_id: str) -> list[dict]:
        return [r["item"] for r in store.items(ROLE) if r["item"].get("type") == "function_call_output" and r["item"].get("call_id") == call_id]

    # ------------------------------------------------------------------ (1)
    def test_1_prepared_request_with_held_reservation_is_released(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        res, req = self.reserve_and_prepare(s1)
        self.assertEqual((s1.store.request(req["id"])["state"], s1.store.reservation(res["id"])["state"]), ("prepared", "held"))
        self.assertEqual(Budget(s1.store).totals()["held_micro"], res["reserved_micro"])
        s1.store.transition("inference_pending", "author request", expected="ready")
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)
        s2.acquire()
        s2.reconcile()
        req2 = s2.store.request(req["id"])
        res2 = s2.store.reservation(res["id"])
        self.assertEqual(req2["state"], "released")
        self.assertIn("never sent", req2["error"])
        self.assertEqual((res2["state"], res2["liability_micro"], res2["settled_micro"]), ("released", 0, None))
        totals = Budget(s2.store).totals()
        self.assertEqual((totals["held_micro"], totals["unknown_liability_micro"], totals["settled_micro"], totals["operations_used"]), (0, 0, 0, 0))
        self.assertEqual([e["data"]["reservation"] for e in s2.store.events("budget_released")], [res["id"]])
        self.assertEqual(s2.store.state(), "ready")
        self.assertEqual(s2.transport.sent, [])
        # a second reconciliation changes nothing
        s2.reconcile()
        self.assertEqual(len(s2.store.events("budget_released")), 1)
        self.assertEqual(s2.store.state(), "ready")

    # ------------------------------------------------------------------ (2)
    def test_2a_sent_compaction_without_response_becomes_unknown_and_nothing_is_posted(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        res, req = self.reserve_and_prepare(s1, purpose="compaction")
        self.mark_sent(s1, req)                          # a compaction is sent while the job is 'ready'
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)
        state = s2.run()
        self.assertEqual(state, "needs_attention")
        job = s2.store.job()
        self.assertEqual(job["stop_reason"], "inference_unknown")
        req2 = s2.store.request(req["id"])
        res2 = s2.store.reservation(res["id"])
        self.assertEqual(req2["state"], "unknown")
        self.assertEqual((res2["state"], res2["liability_micro"]), ("unknown", res["reserved_micro"]))
        self.assertEqual(Budget(s2.store).totals()["unknown_liability_micro"], res["reserved_micro"])
        self.assertEqual(s2.transport.sent, [], "an unknown outcome is never re-posted")
        self.assertEqual([r["id"] for r in s2.store.requests()], [req["id"]], "no new request was prepared")
        self.assertEqual(runner.status_report(s2.store)["unknown_requests"], [req["id"]])
        self.assertIsNone(job["lease_holder"], "run() released its lease")

    def test_2b_sent_author_request_without_response_stops_in_needs_attention_without_posting(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        res, req = self.reserve_and_prepare(s1)
        s1.store.transition("inference_pending", "author request", expected="ready")     # author_step's state while request_once runs
        self.mark_sent(s1, req)
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)
        state = s2.run()
        req2 = s2.store.request(req["id"])
        res2 = s2.store.reservation(res["id"])
        self.assertEqual(req2["state"], "unknown")
        self.assertEqual((res2["state"], res2["liability_micro"]), ("unknown", res["reserved_micro"]))
        self.assertEqual(state, "needs_attention")
        self.assertEqual(s2.store.job()["stop_reason"], "inference_unknown")
        self.assertEqual(s2.transport.sent, [], "an unknown outcome is never re-posted and no new request is sent before the owner reconciles")
        self.assertEqual([r["id"] for r in s2.store.requests()], [req["id"]])

    # ------------------------------------------------------------------ (3)
    def test_3_sent_request_with_durable_response_is_completed_from_the_file_once(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        res, req = self.reserve_and_prepare(s1)
        s1.store.transition("inference_pending", "author request", expected="ready")
        body = demo._resp("call_1", "list_evidence", {}, rs="rs_1")
        self.mark_sent(s1, req, response_body=body)
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)
        s2.acquire()
        s2.reconcile()

        def check(session, store):
            req2 = store.request(req["id"])
            self.assertEqual((req2["state"], req2["provider_response_id"], req2["http_status"]), ("completed", "resp_call_1", 200))
            self.assertEqual(req2["usage"], USAGE)
            self.assertIsNotNone(req2["completed_utc"])
            res2 = store.reservation(res["id"])
            self.assertEqual((res2["state"], res2["settled_micro"], res2["request_id"]), ("settled", SETTLE_MICRO, req["id"]))
            self.assertEqual(SETTLE_MICRO, 20_050)
            self.assertEqual([e["data"]["reservation"] for e in store.events("budget_settled")], [res["id"]], "settled exactly once")
            self.assertEqual(store.events("settlement_problem"), [])
            self.assertEqual(Budget(store).totals()["settled_micro"], SETTLE_MICRO)
            mine = [r["item"] for r in store.items(ROLE) if r["request_id"] == req["id"]]
            self.assertEqual(mine, body["output"], "the response items are appended once, verbatim")
            ops = store.operations(call_id="call_1")
            self.assertEqual(len(ops), 1)
            self.assertEqual((ops[0]["state"], ops[0]["tool_name"], ops[0]["request_id"], ops[0]["attempt"]), ("pending", "list_evidence", req["id"], 1))
            self.assertEqual(len(store.operations()), 1)
            self.assertEqual(store.state(), "tool_pending")
            self.assertEqual(session.transport.sent, [])
            self.assertEqual(self.outputs_for(store, "call_1"), [], "the pending operation still owes its output; nothing fabricated")

        check(s2, s2.store)
        s2.reconcile()                                   # reconciling twice is idempotent
        check(s2, s2.store)
        clock.advance(1)
        s3 = self.resume(job_dir, clock)                 # and so is a third runner over the same folder
        s3.acquire()
        s3.reconcile()
        check(s3, s3.store)
        self.assertEqual(len(s3.store.events("inference_completed")), 1)

    # ------------------------------------------------------------------ (4)
    def test_4_completed_request_without_items_is_replayed_once(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        res, req = self.reserve_and_prepare(s1)
        s1.store.transition("inference_pending", "author request", expected="ready")
        body = demo._resp("call_1", "list_evidence", {}, rs="rs_1")
        self.mark_sent(s1, req, response_body=body)
        s1.budget.settle(res["id"], body["usage"], request_id=req["id"])
        s1.store.update_request(req["id"], state="completed", provider_response_id="resp_call_1", response_model=MODEL, usage_json=body["usage"], completed_utc=s1.store.now())
        self.assertEqual([r for r in s1.store.items(ROLE) if r["request_id"] == req["id"]], [], "crashed before handle_response")
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)
        s2.acquire()
        for _ in range(2):
            s2.reconcile()
            store = s2.store
            mine = [r["item"] for r in store.items(ROLE) if r["request_id"] == req["id"]]
            self.assertEqual(mine, body["output"])
            self.assertEqual(len(store.operations(call_id="call_1")), 1)
            self.assertEqual(store.operations(call_id="call_1")[0]["state"], "pending")
            self.assertEqual(len(store.operations()), 1)
            res2 = store.reservation(res["id"])
            self.assertEqual((res2["state"], res2["settled_micro"]), ("settled", SETTLE_MICRO))
            self.assertEqual(len(store.events("budget_settled")), 1, "no second settlement on replay")
            self.assertEqual(store.state(), "tool_pending")
            self.assertEqual(s2.transport.sent, [])

    # ------------------------------------------------------------------ (5)
    def test_5_pending_operation_is_executed_exactly_once_on_run(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        body = demo._resp("call_x", "list_evidence", {}, rs="rs_x")
        s1.store.append_items(ROLE, body["output"])
        op = s1.store.insert_operation(call_id="call_x", request_id=None, tool_name="list_evidence", schema_version=T.TOOLS_VERSION, args={}, source_revision_id=None,
                                       fence=s1.fence)
        s1.store.transition("tool_pending", "tool call(s) recorded", expected="ready")
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock)                 # the script is empty: after the tool runs, the next author request cannot be answered
        state = s2.run()
        store = s2.store
        ops = store.operations(call_id="call_x")
        self.assertEqual(len(ops), 1)
        self.assertEqual((ops[0]["id"], ops[0]["state"], ops[0]["output_committed"]), (op["id"], "completed", 1))
        self.assertIsNotNone(ops[0]["completed_utc"])
        self.assertEqual(ops[0]["result"]["summary"], {})
        outs = self.outputs_for(store, "call_x")
        self.assertEqual(len(outs), 1, "exactly one function_call_output per call")
        text = json.loads(outs[0]["output"][0]["text"])
        self.assertEqual(text["tools_version"], T.TOOLS_VERSION)
        self.assertIn("photos", text)
        self.assertEqual([e["data"]["operation"] for e in store.events("tool_completed")], [op["id"]])
        self.assertEqual(state, "needs_attention", "the empty script stops the run after the tool; nothing terminal was reached")
        self.assertEqual(store.job()["stop_reason"], "inference_refused")
        # the refused author request cost nothing and consumed no operation slot beyond the record of the attempt
        self.assertEqual([r["state"] for r in store.requests()], ["refused"])
        self.assertEqual([r["state"] for r in store.reservations()], ["released"])
        self.assertEqual([s["endpoint"] for s in s2.transport.sent], ["count", "responses"])

    # ------------------------------------------------------------------ (6)
    def test_6_running_operation_with_fake_identity_is_interrupted_with_one_output(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        rev = s1.store.insert_revision(parent_id=None, program_set_sha256="0" * 64, modules={"frame": {"sha256": "1" * 64, "bytes": 1, "source": "changed"}},
                                       rationale="first", synthetic=True)
        s1.store.update_revision(rev["id"], state="building")
        s1.store.update_job(current_revision=rev["id"])
        body = demo._resp("call_b", "build_candidate", {"revision_id": rev["id"], "deliver_if_compatible": False}, rs="rs_b")
        s1.store.append_items(ROLE, body["output"])
        op = s1.store.insert_operation(call_id="call_b", request_id=None, tool_name="build_candidate", schema_version=T.TOOLS_VERSION,
                                       args={"revision_id": rev["id"], "deliver_if_compatible": False}, source_revision_id=rev["id"], fence=s1.fence)
        s1.store.update_operation(op["id"], state="running", started_utc=s1.store.now(), worker_json={"kind": "fake", "operation_id": op["id"], "ordinal": 1, "fence": s1.fence})
        s1.store.transition("tool_pending", "recorded", expected="ready")
        s1.store.transition("tool_running", "executing", expected="tool_pending")
        job_dir = self.crash(s1)
        s2 = self.resume(job_dir, clock, worker=executor.FakeWorker())
        s2.acquire()
        for _ in range(2):
            s2.reconcile()
            store = s2.store
            ops = store.operations(call_id="call_b")
            self.assertEqual(len(ops), 1)
            self.assertEqual((ops[0]["state"], ops[0]["output_committed"], ops[0]["result"]["category"]), ("interrupted", 1, "interrupted"))
            self.assertIn("never ingested", ops[0]["result"]["error"])
            r = store.revision(rev["id"])
            self.assertEqual(r["state"], "interrupted")
            self.assertEqual(r["compatibility"], {"compatible": False, "reasons": ["interrupted"]})
            outs = self.outputs_for(store, "call_b")
            self.assertEqual(len(outs), 1, "exactly one output for the interrupted call")
            text = json.loads(outs[0]["output"][0]["text"])
            self.assertEqual((text["category"], text["tool"]), ("interrupted", "build_candidate"))
            self.assertEqual([e["data"]["status"] for e in store.events("operation_reattach")], ["lost"])
            self.assertEqual(store.state(), "ready")
            self.assertEqual(s2.transport.sent, [])

    # ------------------------------------------------------------------ (7)
    def test_7_stale_lease_ends_the_operation_as_fence_interrupted_and_blocks_execution(self):
        clock = FakeClock()
        s1 = self.new_session(clock)
        ctx = s1.context()
        edit = {"base_revision_id": None, "modules": demo.modules(demo.PROGRAM_A), "rationale": "first construction", "expected_changes": ["a front"],
                "build_now": False, "deliver_if_compatible": False}
        op_edit = s1.store.insert_operation(call_id="call_e", request_id=None, tool_name="edit_program", schema_version=T.TOOLS_VERSION, args=edit,
                                            source_revision_id=None, fence=s1.fence)
        created = T.execute(ctx, op_edit)
        self.assertEqual(created.text["revision"], "r0001")
        self.assertEqual(s1.store.revision("r0001")["state"], "created")
        intruder = self.track(Store.open(s1.store.job_dir, clock=clock))
        worker = LeaseStealingWorker(intruder, clock)
        s1.worker = worker
        ctx = s1.context()
        op_build = s1.store.insert_operation(call_id="call_b", request_id=None, tool_name="build_candidate", schema_version=T.TOOLS_VERSION,
                                             args={"revision_id": "r0001", "deliver_if_compatible": False}, source_revision_id="r0001", fence=s1.fence)
        result = T.execute(ctx, op_build)
        self.assertEqual(worker.stolen_fence, s1.fence + 1)
        job = s1.store.job()
        self.assertEqual((job["lease_holder"], job["lease_fence"]), ("intruder@elsewhere:00000000", s1.fence + 1))
        op = s1.store.operation(op_build["id"])
        self.assertEqual(op["state"], "interrupted")
        self.assertEqual(op["result"]["category"], "fence")
        self.assertIn("stale lease", op["result"]["error"])
        self.assertEqual(op["output_committed"], 0, "a stale runner commits nothing")
        self.assertEqual(result.text["built"], False)
        self.assertEqual(result.text["category"], "fence")
        self.assertIn("stale lease", result.text["error"])
        rev = s1.store.revision("r0001")
        # nothing of the revision is rewritten under the stale lease: it stays as the valid fence left it (building, no verdict, owned by
        # the operation) until the new holder's reconcile marks it interrupted through revision.operation_id
        self.assertEqual(rev["state"], "building")
        self.assertIsNone(rev["compatibility"])
        self.assertEqual(rev["operation_id"], op_build["id"])
        self.assertFalse(any((r.get("compatibility") or {}).get("compatible") for r in s1.store.revisions()), "no revision becomes compatible")
        self.assertFalse((s1.store.job_dir / "revisions" / "r0001" / "build").exists(), "the worker output was not promoted")
        # the first session's own execute path refuses to continue under the stale fence
        with self.assertRaises(LeaseError):
            s1.store.require_fence(s1.holder, s1.fence)
        with self.assertRaises(LeaseError):
            s1.renew()
        pending = s1.store.insert_operation(call_id="call_p", request_id=None, tool_name="list_evidence", schema_version=T.TOOLS_VERSION, args={},
                                            source_revision_id=None, fence=s1.fence)
        s1.store.transition("tool_pending", "recorded", expected="ready")
        with self.assertRaises(LeaseError):
            s1.execute_pending_operations()
        self.assertEqual(s1.store.operation(pending["id"])["state"], "pending", "nothing ran under the stale fence")
        self.assertEqual(self.outputs_for(s1.store, "call_p"), [])
        self.assertEqual(s1.store.events("tool_completed"), [])



# =========================================================================== sub-operations (review 2026-09-28, INF-18)
# tool_operations as every job before 2026-09-28 created it (test-pilot-001/002): no parent_operation_id
LEGACY_TOOL_OPERATIONS = """CREATE TABLE tool_operations (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, call_id TEXT NOT NULL, request_id TEXT REFERENCES inference_requests(id),
  tool_name TEXT NOT NULL, schema_version TEXT NOT NULL, args_sha256 TEXT NOT NULL, args_json TEXT NOT NULL, source_revision_id TEXT,
  attempt INTEGER NOT NULL, state TEXT NOT NULL, worker_json TEXT, deadline_utc TEXT, fence INTEGER, output_manifest_sha256 TEXT,
  result_json TEXT, output_committed INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL, started_utc TEXT, completed_utc TEXT,
  UNIQUE (call_id, attempt))"""
PILOT_JOBS = [AUTOMATION / "data" / "modeler" / "agentic" / n for n in ("test-pilot-001", "test-pilot-002")]


def _file_sha(p: Path) -> str | None:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


class SubOperationTest(TempCase):
    """A render sub-operation (tools.make_render_harness: render_only under a running build/edit) is its own operation:
    attempt 1, parent_operation_id naming the tool's operation, a call id derived from the parent's. test-pilot-002
    recorded all six as 'attempt 2' of the author's call, so status read like retries and container names carried '-2'."""

    def op(self, store: Store, call_id: str, tool: str = "edit_program", **kw) -> dict:
        return store.insert_operation(call_id=call_id, request_id=None, tool_name=tool, schema_version="v", args={"t": tool}, source_revision_id=None, fence=1, **kw)

    def test_a_render_under_a_running_tool_is_attempt_1_with_its_parent(self):
        store = self.new_store()
        parent = self.op(store, "call_1")
        store.update_operation(parent["id"], state="running")
        sub = self.op(store, "call_1", "render_only", state="running")      # exactly what tools.make_render_harness passes today
        sub2 = self.op(store, "call_1", "render_only", state="running")
        self.assertEqual((sub["attempt"], sub["parent_operation_id"]), (1, parent["id"]))
        self.assertEqual((sub2["attempt"], sub2["parent_operation_id"]), (1, parent["id"]))
        self.assertEqual((sub["call_id"], sub2["call_id"]), (f"call_1/{sub['id']}", f"call_1/{sub2['id']}"))
        self.assertIsNone(store.operation(parent["id"])["parent_operation_id"])
        # the author's call has one operation (runner.ensure_outputs_for_calls reads ITS state, not a render's)
        self.assertEqual([o["id"] for o in store.operations(call_id="call_1")], [parent["id"]])
        self.assertEqual([o["id"] for o in store.operations(parent_operation_id=parent["id"])], [sub["id"], sub2["id"]])
        # container names derive from the identity tools.run_worker_operation builds: unique, and none reads like a retry
        dw = executor.DockerWorker({"image_digest": "sha256:" + "0" * 64})
        names = [dw.container_name({"job_short": "0123456789ab", "operation_id": o["id"], "attempt": o["attempt"]}) for o in (parent, sub, sub2)]
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(n.endswith("-1") for n in names), names)

    def test_an_explicit_parent_is_checked(self):
        store = self.new_store()
        parent = self.op(store, "call_1")                                 # pending: only an explicit parent makes a sub-operation of it
        sub = self.op(store, "call_1", "render_only", state="running", parent_operation_id=parent["id"])
        self.assertEqual((sub["attempt"], sub["parent_operation_id"], sub["call_id"]), (1, parent["id"], f"call_1/{sub['id']}"))
        with self.assertRaises(StateError):
            self.op(store, "call_2", "render_only", parent_operation_id=parent["id"])     # another call's parent
        with self.assertRaises(StateError):
            self.op(store, "call_1", "render_only", parent_operation_id="op0404")
        with self.assertRaises(StateError):
            self.op(store, f"call_1/{sub['id']}", "render_only", parent_operation_id=sub["id"])   # no sub-operation of a sub-operation

    def test_a_retry_of_a_finished_call_is_still_the_next_attempt(self):
        store = self.new_store()
        first = self.op(store, "call_1")
        store.update_operation(first["id"], state="running")
        store.update_operation(first["id"], state="interrupted")
        again = self.op(store, "call_1")
        self.assertEqual((again["attempt"], again["parent_operation_id"], again["call_id"]), (2, None, "call_1"))
        self.assertEqual([o["id"] for o in store.operations(call_id="call_1")], [first["id"], again["id"]])

    def test_the_host_seed_build_and_the_rebuild_keep_their_host_call_ids(self):
        store = self.new_store()
        seed = self.op(store, "host_seed_build_r0001", "build_candidate", state="running")
        sub = self.op(store, "host_seed_build_r0001", "render_only", state="running")
        self.assertEqual((sub["parent_operation_id"], sub["attempt"]), (seed["id"], 1))
        self.assertTrue(sub["call_id"].startswith(runner.HOST_CALL_PREFIX), "recovery never answers a host call: the prefix is kept")


class LegacySchemaTest(TempCase):
    """Jobs created before parent_operation_id: a read-only open changes nothing and reads the column as None; a
    read-write open (resume, cancel, reconcile) adds the column once, journaled, and keeps every recorded row."""

    def legacy_store_path(self) -> Path:
        store = self.new_store()
        path = store.path
        store.close()
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP TABLE tool_operations")
            conn.execute(LEGACY_TOOL_OPERATIONS)
            now = "2026-09-27T12:00:00+00:00"
            for oid, seq, tool, attempt in (("op0001", 1, "edit_program", 1), ("op0002", 2, "render_only", 2)):
                conn.execute("INSERT INTO tool_operations (id, seq, call_id, tool_name, schema_version, args_sha256, args_json, attempt, state, "
                             "output_committed, created_utc) VALUES (?, ?, 'call_old', ?, 'v', ?, '{}', ?, 'completed', 1, ?)",
                             (oid, seq, tool, "0" * 64, attempt, now))
        finally:
            conn.close()
        self.assertNotIn("parent_operation_id", _columns(path, "tool_operations"))
        return path

    def test_read_only_open_changes_nothing(self):
        path = self.legacy_store_path()
        before = _file_sha(path)
        store = Store.open(path.parent, readonly=True)
        try:
            ops = store.operations()
            self.assertEqual([(o["id"], o["attempt"], o["parent_operation_id"]) for o in ops], [("op0001", 1, None), ("op0002", 2, None)])
            self.assertEqual(store.operation("op0002")["parent_operation_id"], None)
            self.assertEqual(store.operations(parent_operation_id="op0001"), [])
            self.assertEqual(store.events("schema_migrated"), [])
        finally:
            store.close()
        self.assertEqual(_file_sha(path), before)
        self.assertNotIn("parent_operation_id", _columns(path, "tool_operations"))

    def test_read_write_open_adds_the_column_once_and_keeps_history(self):
        path = self.legacy_store_path()
        store = self.track(Store.open(path.parent))
        self.assertIn("parent_operation_id", _columns(path, "tool_operations"))
        self.assertEqual([e["data"] for e in store.events("schema_migrated")], [{"added": ["tool_operations.parent_operation_id"]}])
        self.assertEqual([(o["id"], o["attempt"], o["parent_operation_id"], o["call_id"]) for o in store.operations()],
                         [("op0001", 1, None, "call_old"), ("op0002", 2, None, "call_old")])
        parent = store.insert_operation(call_id="call_new", request_id=None, tool_name="edit_program", schema_version="v", args={}, source_revision_id=None, fence=None,
                                        state="running")
        sub = store.insert_operation(call_id="call_new", request_id=None, tool_name="render_only", schema_version="v", args={}, source_revision_id=None, fence=None,
                                     state="running")
        self.assertEqual((sub["attempt"], sub["parent_operation_id"]), (1, parent["id"]))
        store.close()
        again = self.track(Store.open(path.parent))
        self.assertEqual(len(again.events("schema_migrated")), 1, "migrated once")

    @unittest.skipUnless(all((j / "job.sqlite3").is_file() for j in PILOT_JOBS), "the pilot jobs are not on this machine")
    def test_the_pilot_jobs_open_from_copies(self):
        """test-pilot-001/002 are never written: their databases are copied, and the copies open read-only unchanged and
        read-write migrated."""
        for job in PILOT_JOBS:
            with self.subTest(job=job.name):
                names = [n for n in ("job.sqlite3", "job.sqlite3-wal", "job.sqlite3-shm") if (job / n).is_file()]
                source = {n: _file_sha(job / n) for n in names}
                copies = []
                for label in ("ro", "rw"):
                    d = self.fresh(f"{job.name}-{label}") / "job"
                    d.mkdir()
                    for n in names:
                        shutil.copyfile(job / n, d / n)
                    copies.append(d)
                ro = Store.open(copies[0], readonly=True)
                try:
                    ops = ro.operations()
                    subs = [o for o in ops if o["tool_name"] == "render_only"]
                    self.assertTrue(subs and all(o["attempt"] == 2 and o["parent_operation_id"] is None for o in subs), "history as recorded")
                    self.assertTrue(ro.job()["policy"]["cache_mode"])
                    self.assertTrue(ro.revisions())
                finally:
                    ro.close()
                self.assertNotIn("parent_operation_id", _columns(copies[0] / "job.sqlite3", "tool_operations"))
                rw = self.track(Store.open(copies[1]))
                self.assertEqual(len(rw.operations()), len(ops))
                self.assertEqual(len(rw.events("schema_migrated")), 1)
                rw.close()
                self.assertEqual({n: _file_sha(job / n) for n in names}, source, "the pilot job itself is untouched")


if __name__ == "__main__":
    unittest.main()
