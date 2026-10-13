"""The trusted seating path on synthetic scene copies, through the real worker and checkpoint journal."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from glasses3d.agent.journal import BlenderSession
from glasses3d.g3d_host import blender
from glasses3d.tests.fixtures import seat_fixtures

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
BUILD = Path(__file__).parent / "blender" / "seat_build.py"


@pytest.fixture(scope="module")
def scenes(tmp_path_factory):
    root = tmp_path_factory.mktemp("trusted_seating")
    blender.run(None, BUILD, [root, "metal", "browline"], timeout_s=300)
    return root


@pytest.fixture(params=["metal", "browline"])
def session(scenes, tmp_path, request):
    original = scenes / f"{request.param}.blend"
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    seed = tmp_path / "seed.blend"
    shutil.copy2(original, seed)
    with_session = BlenderSession(tmp_path / "job", seed_blend=seed)
    with_session.start()
    try:
        yield request.param, with_session
    finally:
        with_session.close()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest


def test_trusted_seating_tools_checkpoint_read_and_restore_without_snippet_access(session):
    name, s = session

    def mutate(op, kwargs):
        result = s.call_mutating(op, "g3d_call", {"op": op, "kwargs": kwargs}, deadline_s=120)
        assert s.fingerprint() == s.head["fingerprint"]
        return result

    def check(kwargs):
        before = s.head.copy()
        result = s.call_read("check_lens_seating", "g3d_call", {"op": "check_lens_seating", "kwargs": kwargs},
                             deadline_s=120, call_id=s.new_call_id("check_lens_seating"))
        assert result["status"] == "ok" and result["restored_to"] is None
        worker = result["result"]
        assert worker["status"] == "ok" and worker["unchanged"] is True
        assert worker["fingerprint_before"] == worker["fingerprint_after"] == before["fingerprint"]
        assert s.head == before
        return worker["result"]

    ranges = [list(seat_fixtures.browline_scene()["range"])]
    initial_check = check({"frame_type": "metal"} if name == "metal" else
                          {"frame_type": "browline", "hidden_x_range_mm": ranges})
    assert initial_check["ok"] is False and initial_check["lenses"]
    start = s.head["id"]
    if name == "metal":
        applied = mutate("seat_lens", {"lens": "Lens_L", "frame_type": "metal"})
        assert applied["status"] == "ok" and applied["result"]["status"] == "seated", applied
        assert applied["checkpoint"] != start and applied["restored_to"] is None
        assert check({"lenses": "Lens_L", "frame_parts": "Eyewire"})["ok"] is True
        again = mutate("seat_lens", {"lens": "Lens_L", "frame_type": "metal"})
        assert again["status"] == "ok" and again["result"]["status"] == "already_seated", again
        undo_op, undo_kwargs = "unseat_lens", {"lens": "Lens_L"}
    else:
        kwargs = {"eyewire": "Eyewire", "under": "Brow", "hidden_x_range_mm": ranges}
        applied = mutate("hide_eyewire", kwargs)
        assert applied["status"] == "ok" and applied["result"]["status"] == "lifted", applied
        assert applied["checkpoint"] != start and applied["restored_to"] is None
        again = mutate("hide_eyewire", kwargs)
        assert again["status"] == "ok" and again["result"]["status"] == "already_hidden", again
        undo_op, undo_kwargs = "unhide_eyewire", {"eyewire": "Eyewire", "lens": "Lens_L"}

    # A domain refusal ran inside _mutating, so the ordinary journal restores the exact pre-call head.
    before = s.head.copy()
    refused = mutate("seat_lens", {"lens": "missing lens", "frame_type": "metal"})
    assert refused["status"] == "refused" and refused["result"]["code"] == "E_ARGS", refused
    assert refused["restored_to"] == before["id"] and s.head == before
    unknown = mutate("export_glb", {})
    assert unknown["status"] == "refused" and unknown["code"] == "E_ARGS"
    assert unknown["restored_to"] is None and s.head == before

    undone = mutate(undo_op, undo_kwargs)
    assert undone["status"] == "ok" and undone["result"]["status"] == "restored", undone
    assert undone["checkpoint"] != before["id"]
    # Once unhidden there is no recorded held lens. Asking to restore a named lens again is rightly refused;
    # the no-op check names only the eyewire, as the domain API requires.
    empty_kwargs = undo_kwargs if name == "metal" else {"eyewire": "Eyewire"}
    assert mutate(undo_op, empty_kwargs)["result"]["status"] == "nothing_to_restore"
    missing = check({"lenses": "missing lens"})
    assert missing["ok"] is False and missing["error"]
    assert s.defects == []

    before = s.head.copy()
    for op in ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire"):
        refused = s.execute(f"result = g3d.{op}()")
        assert refused["status"] == "refused" and s.head == before
