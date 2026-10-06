"""procs (fix round 1): the serial pool path and Progress.heartbeat are never silent (the A7 serial bake ran 836 s
without a line); results stay in job order."""
import time

from glasses3d.g3d_host.photoprint import procs


def _slow(x):
    time.sleep(0.35)
    return x * 2


def test_serial_pool_prints_while_a_long_job_runs(monkeypatch):
    monkeypatch.setattr(procs, "PROGRESS_EVERY_S", 0.1)
    lines = []
    p = procs.Progress("t", log=lines.append, every_s=0.1)
    assert procs.pool_map(_slow, [(1,), (2,)], 1, progress=p) == [2, 4]
    assert any("jobs done (serial, running job 1)" in ln for ln in lines), lines
    assert any("running job 2" in ln for ln in lines), lines


def test_heartbeat_prints_during_silent_work_and_stops():
    lines = []
    p = procs.Progress("t", log=lines.append, every_s=0.1)
    with p.heartbeat():
        p("stage A", force=True)
        time.sleep(0.4)
    n = len(lines)
    assert any("still working (last: stage A)" in ln for ln in lines), lines
    assert not any("still working (last: still working" in ln for ln in lines)
    time.sleep(0.3)
    assert len(lines) == n                       # the thread stopped with the block


def test_silent_progress_never_prints():
    p = procs.Progress("t", log=None, every_s=0.05)
    with p.heartbeat():
        time.sleep(0.15)
    assert procs.pool_map(_slow, [(3,)], 1, progress=p) == [6]
