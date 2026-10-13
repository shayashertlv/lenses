"""Post-profile failures restore the whole seating transaction, not just lens coordinates."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from glasses3d.g3d_host import blender

HERE = Path(__file__).resolve().parent
KINDS = ("crystal", "nylor_nocord")
MODES = ("rollback", "exception", "lookup_exception", "late_exception")
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")]


@pytest.fixture(scope="module")
def rollback_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("seat_rollback")
    scenes = root / "scenes"
    built = blender.run(None, HERE / "blender/seat_build.py", [scenes, *KINDS], timeout_s=300)
    assert all((scenes / f"{kind}.blend").is_file() for kind in KINDS), built.stdout + built.stderr

    def one(case):
        kind, mode = case
        source = scenes / f"{kind}.blend"
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        copy = root / f"{kind}-{mode}.blend"
        output = copy.with_suffix(".json")
        shutil.copyfile(source, copy)
        proc = blender.run(copy, HERE / "blender/seat_rollback.py", [mode, kind, output], timeout_s=300)
        assert output.is_file(), proc.stdout + proc.stderr
        result = json.loads(output.read_text(encoding="utf-8"))
        assert "error" not in result, result
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
        return case, result

    with ThreadPoolExecutor(3) as pool:
        return dict(pool.map(one, [(kind, mode) for kind in KINDS for mode in MODES]))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", MODES)
def test_post_profile_failure_restores_mesh_objects_modifiers_and_backups(rollback_runs, kind, mode):
    record = rollback_runs[kind, mode]
    result = record["result"]
    assert result["status"] == "refused" and result["rolled_back"] is True, result
    assert result["code"] == ("E_POSTCHECK" if mode == "rollback" else "E_INTERNAL"), result
    observed = record["observed"]
    if kind == "crystal":
        assert observed["cutter"] and observed["modifiers"] and observed["cord"] is None, observed
    else:
        assert observed["cord"] and observed["cutter"] is None, observed
    if mode == "late_exception":
        assert observed["seat_property_written"] is True
    assert record["before"] == record["after"]
    assert record["remaining"] == {"cutter": None, "modifiers": [], "cord": None}
