"""Lab process-path isolation uses the native helper's existing interface."""
import json
from pathlib import Path

import pytest

from glasses3d.agent.tools import arview
from glasses3d.lens_lab import capture


@pytest.mark.parametrize("outcome", [(0, False), (None, True), (1, False), "exception"])
def test_short_process_directory_preserves_arguments_result_and_log(tmp_path, monkeypatch, outcome):
    base = tmp_path / "lab"
    base.mkdir()
    monkeypatch.setattr(capture, "DATA", base)
    out = base / ("deep-" * 12) / "trial-001-replacement/job/work/arview/call-worn-process"
    argv = ["node", "capture", "model.glb", "--out", str(out.parent / "images")]
    seen = []
    def run(args, process_out, *, timeout_s):
        seen.append(process_out)
        assert args is argv and timeout_s == 123
        assert process_out.parent == base / "_capture"
        assert len(str(process_out)) < len(str(out))
        (process_out / "capture.log").write_bytes(b"native log\r\n")
        (process_out / "temp").mkdir()
        if outcome == "exception":
            raise RuntimeError("native failure")
        return outcome
    monkeypatch.setattr(arview, "run_capture", run)
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="native failure"):
            capture.short_run_capture(argv, out, timeout_s=123)
    else:
        assert capture.short_run_capture(argv, out, timeout_s=123) == outcome
    assert (out / "capture.log").read_bytes() == b"native log\r\n"
    record = json.loads(next(out.glob("lab-capture-process-*.json")).read_text())
    assert record["scratch_removed"] and not seen[0].exists()
    assert record["temp_directory"] == str(seen[0] / "temp")


def test_process_output_cannot_escape_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "DATA", tmp_path / "lab")
    monkeypatch.setattr(arview, "run_capture", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="inside lens-lab"):
        capture.short_run_capture([], tmp_path / "outside", timeout_s=1)


def test_repeated_capture_batches_append_logs_and_keep_each_process_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "DATA", tmp_path)
    count = 0
    def run(args, directory, **kwargs):
        nonlocal count
        count += 1
        (directory / "capture.log").write_bytes(f"batch {count}\n".encode())
        return count - 1, False
    monkeypatch.setattr(arview, "run_capture", run)
    out = tmp_path / "look"
    assert capture.short_run_capture([], out, timeout_s=10) == (0, False)
    assert capture.short_run_capture([], out, timeout_s=10) == (1, False)
    assert (out / "capture.log").read_bytes() == b"batch 1\nbatch 2\n"
    records = [json.loads(p.read_text()) for p in out.glob("lab-capture-process-*.json")]
    assert sorted(r["exit_code"] for r in records) == [0, 1]
    assert len({r["temp_directory"] for r in records}) == 2
